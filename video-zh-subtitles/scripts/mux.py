#!/usr/bin/env python3
"""把字幕封装进 MP4（软字幕，不重新编码），并做验收。

用法：
  python mux.py --video source.mp4 --zh subs/v.zh.srt --bilingual subs/v.zh-en.srt \\
      --out v.zh.mp4 --preview-at 600

要点：
- -c copy 只复制流，几秒完成、画质无损；字幕用 mov_text 才能进 mp4。
- 验收时烧帧必须带 -copyts：输入 seek 会把时间戳归零，导致 subtitles 滤镜找不到字幕。
"""
import argparse
import json
import os
import subprocess
import sys


def run(cmd):
    """执行外部命令并把输出写到临时文件再读回。

    为什么不用 capture_output：某些 Windows 沙箱/受限环境里 CreatePipe 会
    报 PermissionError: [WinError 5] 拒绝访问，改用文件重定向就没事。
    """
    print("$", " ".join(cmd))
    log = os.path.join(os.getcwd(), "_cmd_%d.log" % os.getpid())
    with open(log, "wb") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT)
    try:
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            out = f.read()
    finally:
        try:
            os.remove(log)
        except OSError:
            pass
    if rc != 0:
        print(out[-3000:])
        raise SystemExit(f"命令失败，退出码 {rc}")
    return out


def probe(path):
    out = run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ]
    )
    return json.loads(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--zh", required=True)
    ap.add_argument("--bilingual", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default="zho", help="字幕语言标记（zho/chi/eng…）")
    ap.add_argument("--preview-at", type=float, default=None, help="在该秒数烧一帧 PNG 检查排版")
    ap.add_argument("--preview-out", default=None)
    args = ap.parse_args()

    cmd = ["ffmpeg", "-y", "-i", args.video, "-i", args.zh]
    if args.bilingual:
        cmd += ["-i", args.bilingual]
    cmd += ["-map", "0", "-map", "1"]
    if args.bilingual:
        cmd += ["-map", "2"]
    cmd += ["-c", "copy", "-c:s", "mov_text"]
    cmd += ["-metadata:s:s:0", f"language={args.lang}", "-metadata:s:s:0", "title=Chinese"]
    if args.bilingual:
        cmd += ["-metadata:s:s:1", f"language={args.lang}", "-metadata:s:s:1", "title=Chinese-English"]
    cmd += [args.out]
    run(cmd)

    info = probe(args.out)
    src = probe(args.video)
    subs = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
    print("\n== 验收 ==")
    print(f"  输出 {args.out}  {os.path.getsize(args.out)/1e6:.1f} MB")
    print(f"  字幕轨 {len(subs)} 条：" + ", ".join(f"#{s['index']}({s.get('tags',{}).get('language','?')})" for s in subs))
    d1 = float(info["format"]["duration"])
    d2 = float(src["format"]["duration"])
    print(f"  时长 {d1:.2f}s / 源 {d2:.2f}s  {'OK' if abs(d1-d2) < 1 else '不一致!'}")
    if not subs:
        print("  错误：没有字幕轨")
        sys.exit(1)

    if args.preview_at is not None:
        png = args.preview_out or os.path.splitext(args.out)[0] + ".preview.png"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(args.preview_at), "-copyts", "-i", args.video,
            "-vf", f"subtitles={args.zh}", "-frames:v", "1", "-update", "1", png,
        ])
        print(f"  预览帧 {png}（打开确认中文能渲染、不压画面）")


if __name__ == "__main__":
    main()
