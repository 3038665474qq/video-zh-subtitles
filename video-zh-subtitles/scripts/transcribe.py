#!/usr/bin/env python3
"""用 faster-whisper 转写 wav，支持多进程分片并行。

单进程多线程在笔记本上通常只吃满 2-3 个核；改成把音频按时间切成 N 段、
每段一个进程（各用自己的线程数），墙钟时间能明显下降。

用法：
  PYTHONPATH=./pylibs python transcribe.py --wav audio16k.wav \\
      --model ./models/faster-whisper-small.en --out segments.json \\
      --parts 4 --threads 4 --language en

参数说明：
  --parts 1      单进程（短视频够用）
  --language     英语模型可省略；非英语写 zh/ja/...
  --vad          是否用 VAD 过滤静音（默认开）
"""
import argparse
import json
import multiprocessing as mp
import os
import subprocess
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))


def wav_info(path):
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()


def read_slice(path, start_sec, end_sec):
    import numpy as np

    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        if sw != 2:
            raise SystemExit(f"需要 16-bit PCM wav，当前 sampwidth={sw}；请用 ffmpeg -c:a pcm_s16le")
        a = int(start_sec * sr)
        b = int(end_sec * sr) if end_sec else n
        a, b = max(0, a), min(n, b)
        w.setpos(a)
        raw = w.readframes(b - a)
    x = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x


def transcribe_slice(args, start_sec, end_sec):
    from faster_whisper import WhisperModel

    audio = read_slice(args.wav, start_sec, end_sec)
    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type=args.compute_type,
        cpu_threads=args.threads,
        num_workers=1,
    )
    segs, info = model.transcribe(
        audio,
        language=args.language,
        beam_size=args.beam_size,
        best_of=1,
        vad_filter=args.vad,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        without_timestamps=False,
    )
    out = []
    for s in segs:
        text = s.text.strip()
        if not text:
            continue
        out.append({"start": round(s.start + start_sec, 3), "end": round(s.end + start_sec, 3), "text": text})
    return out


def worker(args, index, start_sec, end_sec, out_path):
    segs = transcribe_slice(args, start_sec, end_sec)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False, indent=1)
    print(f"[part {index}] {len(segs)} segments -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--model", required=True, help="本地模型目录，或 HF repo id（会自动下载）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--language", default=None, help="en / zh / ja …；英语 .en 模型可省略")
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--beam-size", type=int, default=1)
    ap.add_argument("--vad", action="store_true", default=True)
    ap.add_argument("--no-vad", dest="vad", action="store_false")
    # 内部用：worker 模式
    ap.add_argument("--worker", type=int, default=None)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--part-out", default=None)
    args = ap.parse_args()

    if args.worker is not None:
        worker(args, args.worker, args.start, args.end, args.part_out)
        return

    sr, nframes, ch, sw = wav_info(args.wav)
    dur = nframes / sr
    print(f"音频 {dur/60:.2f} min, {sr} Hz, {ch} ch")

    parts = max(1, args.parts)
    outdir = os.path.dirname(os.path.abspath(args.out)) or "."
    os.makedirs(outdir, exist_ok=True)

    if parts == 1:
        segs = transcribe_slice(args, 0.0, dur)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(segs, f, ensure_ascii=False, indent=1)
        print(f"[done] {len(segs)} segments，最后一条 end={segs[-1]['end'] if segs else 0:.1f}s / {dur:.1f}s")
        return

    base = os.path.splitext(os.path.abspath(args.out))[0]
    step = dur / parts
    procs = []
    for i in range(parts):
        s, e = i * step, (dur if i == parts - 1 else (i + 1) * step)
        part_out = f"{base}.part_{i}.json"
        cmd = [
            sys.executable, os.path.abspath(__file__),
            "--wav", args.wav, "--model", args.model, "--out", args.out,
            "--threads", str(args.threads), "--compute-type", args.compute_type,
            "--beam-size", str(args.beam_size), "--worker", str(i),
            "--start", str(s), "--end", str(e), "--part-out", part_out,
        ]
        if args.language:
            cmd += ["--language", args.language]
        if not args.vad:
            cmd += ["--no-vad"]
        print(f"[spawn] part {i}: {s:.1f}s - {e:.1f}s")
        procs.append(subprocess.Popen(cmd))

    for p in procs:
        p.wait()
    failed = [p.returncode for p in procs if p.returncode != 0]
    if failed:
        raise SystemExit(f"有 worker 失败：{failed}")

    merged = []
    for i in range(parts):
        part_out = f"{base}.part_{i}.json"
        with open(part_out, encoding="utf-8") as f:
            merged.extend(json.load(f))
    # 排序 + 去掉分片边界上重复的段落
    merged.sort(key=lambda x: x["start"])
    dedup = []
    for s in merged:
        if dedup and s["start"] < dedup[-1]["end"] - 0.05 and s["text"] == dedup[-1]["text"]:
            continue
        dedup.append(s)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(dedup, f, ensure_ascii=False, indent=1)
    print(f"\n[done] {len(dedup)} segments -> {args.out}")
    print(f"最后一条 end={dedup[-1]['end']:.1f}s / 音频 {dur:.1f}s")
    if dedup and dedup[-1]["end"] < dur - 30:
        print("警告：结尾缺失超过 30s，可能被 VAD 吞掉或音频损坏，建议 --no-vad 重跑该段")

    for i in range(parts):
        try:
            os.remove(f"{base}.part_{i}.json")
        except OSError:
            pass


if __name__ == "__main__":
    main()
