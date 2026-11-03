#!/usr/bin/env python3
"""环境自检：跑流水线之前先确认工具链和网络是否可用。

用法：python env_check.py
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

OK = "[ok]  "
BAD = "[FAIL]"


def run(cmd):
    """执行命令取输出；用文件重定向而不是 PIPE（受限环境下 CreatePipe 会 WinError 5）。"""
    import tempfile

    log = os.path.join(tempfile.gettempdir(), "_envchk_%d.log" % os.getpid())
    try:
        with open(log, "wb") as f:
            rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=20)
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            return rc, f.read()
    except Exception as e:  # noqa: BLE001
        return -1, str(e)
    finally:
        try:
            os.remove(log)
        except OSError:
            pass


def check_binary(name):
    path = shutil.which(name)
    if not path:
        print(BAD, name, "未找到（PATH 里没有）")
        return False
    rc, out = run([name, "--version"])
    if rc != 0 or not out.strip():
        rc, out = run([name, "-version"])
    ver = out.splitlines()[0].strip() if out else "?"
    print((OK if rc == 0 else BAD), name, "->", path, "|", ver[:70])
    return rc == 0


def check_ffmpeg_features():
    rc, out = run(["ffmpeg", "-hide_banner", "-filters"])
    has_sub = " subtitles " in out or " ass " in out
    rc2, out2 = run(["ffmpeg", "-hide_banner", "-encoders"])
    has_mov = "mov_text" in out2
    has_mux = "mov_text" in out2
    print(OK if has_sub else BAD, "ffmpeg subtitles/ass 滤镜:", has_sub)
    print(OK if has_mov else BAD, "ffmpeg mov_text 编码器:", has_mov)
    print(OK if has_mux else BAD, "ffmpeg mp4 软字幕 mux:", has_mux)
    return has_sub and has_mov


def check_module(name):
    try:
        __import__(name)
        print(OK, "python 模块", name, "可导入")
        return True
    except Exception as e:  # noqa: BLE001
        print(BAD, "python 模块", name, "缺失:", e)
        return False


def http_ok(url, method="GET", data=None, timeout=12):
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(200)
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:120]


def check_network():
    checks = [
        ("hf-mirror（Whisper 模型）", "https://hf-mirror.com/Systran/faster-whisper-small.en/resolve/main/config.json"),
        ("阿里云 PyPI 镜像（依赖 wheel）", "https://mirrors.aliyun.com/pypi/simple/faster-whisper/"),
    ]
    for label, url in checks:
        st, body = http_ok(url)
        print(OK if st else BAD, label, "->", st or body)

    body = {
        "header": {
            "fn": "auto_translation",
            "client_key": "browser-chrome-110.0.0-Mac OS-df4bd4c5-a65d-44b2-a40f-42f34f3535f2-1677486696487",
        },
        "type": "plain",
        "model_category": "normal",
        "source": {"lang": "en", "text_list": ["Upgrading to Unity 6 improves runtime performance."]},
        "target": {"lang": "zh"},
    }
    st, resp = http_ok("https://transmart.qq.com/api/imt", "POST", body)
    if st == 200:
        try:
            print(OK, "腾讯 Transmart 翻译 ->", json.loads(resp.decode("utf-8", "ignore") or "{}").get("auto_translation"))
        except Exception:  # noqa: BLE001
            print(OK, "腾讯 Transmart 翻译 -> 200")
    else:
        print(BAD, "腾讯 Transmart 翻译 ->", st or resp)


def main():
    print("== python ==")
    print("   ", sys.version.replace("\n", " "))
    print("   ", sys.executable)
    if sys.version_info < (3, 9):
        print(BAD, "需要 Python >= 3.9")

    print("\n== 二进制 ==")
    ff = check_binary("ffmpeg")
    check_binary("ffprobe")
    node = check_binary("node")
    if ff:
        check_ffmpeg_features()
    else:
        print("提示：Windows 上可用 winget install Gyan.FFmpeg，或用便携版并把它加进 PATH")

    print("\n== python 依赖 ==")
    missing = [m for m in ("numpy", "ctranslate2", "faster_whisper") if not check_module(m)]
    if missing:
        print("  提示：若这些包是用 getwheels.py 装到 ./pylibs 的，运行时需要 PYTHONPATH=./pylibs")

    print("\n== 网络 ==")
    check_network()

    if not node:
        print("\n提示：translate.js 需要 node；也可以用 LLM 分批翻译替代（见 SKILL.md 第 5 步）")


if __name__ == "__main__":
    main()
