#!/usr/bin/env python3
"""从 huggingface 镜像下载 faster-whisper 模型（无需 huggingface_hub）。

默认走 https://hf-mirror.com（国内可用）。断点续传、自动重试、跳过已存在文件。

用法：
  python getmodel.py --repo Systran/faster-whisper-small.en --dest ./models/faster-whisper-small.en
  python getmodel.py --repo Systran/faster-whisper-large-v3 --dest ./models/large-v3 --mirror https://huggingface.co
"""
import argparse
import os
import sys
import time
import urllib.request

FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def download(url, dst, tries=5):
    for i in range(tries):
        try:
            tmp = dst + ".part"
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            if have:
                req.add_header("Range", f"bytes={have}-")
            with urllib.request.urlopen(req, timeout=60) as r:
                mode = "ab" if have and r.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                total = int(r.headers.get("Content-Length", 0)) + have
                t0 = time.time()
                with open(tmp, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if total:
                            pct = have * 100 / total
                            speed = have / max(1e-6, time.time() - t0) / 1e6
                            print(f"  {pct:5.1f}%  {have/1e6:6.1f}MB  {speed:5.1f}MB/s", end="\r", flush=True)
            os.replace(tmp, dst)
            print(" " * 60, end="\r")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"\n  重试 {i + 1}/{tries}: {str(e)[:100]}")
            time.sleep(2)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="例如 Systran/faster-whisper-small.en")
    ap.add_argument("--dest", required=True)
    ap.add_argument("--mirror", default="https://hf-mirror.com")
    ap.add_argument("--files", nargs="*", default=FILES)
    args = ap.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    for fn in args.files:
        dst = os.path.join(args.dest, fn)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            print(f"[skip] {fn} 已存在 ({os.path.getsize(dst) / 1e6:.1f}MB)")
            continue
        url = f"{args.mirror.rstrip('/')}/{args.repo}/resolve/main/{fn}"
        print(f"[GET ] {fn}  <-  {url}")
        if not download(url, dst):
            print(f"[FAIL] {fn} 下载失败；可换 --mirror，或手动下载后放到 {args.dest}")
            sys.exit(1)
        print(f"[ok  ] {fn} {os.path.getsize(dst) / 1e6:.1f}MB")
    print("模型就绪：", args.dest)


if __name__ == "__main__":
    main()
