#!/usr/bin/env python3
"""不依赖 pip、直接把 wheel 解压到指定目录（应对 pip 被 ACL/代理挡住的环境）。

原理：从 PyPI 镜像（默认阿里云）读 simple 索引 -> 选与当前解释器兼容的 wheel
-> 下载 -> 读 wheel 里的 METADATA 得到 Requires-Dist -> 递归处理依赖
-> 用 zipfile 解压进 --dest，运行时用 PYTHONPATH 指过去。

用法：
  python getwheels.py --dest ./pylibs --packages faster-whisper
  python getwheels.py --dest ./pylibs --packages faster-whisper numpy --index https://pypi.tuna.tsinghua.edu.cn/simple
"""
import argparse
import io
import os
import re
import sys
import urllib.request
import zipfile

DEFAULT_INDEX = "https://mirrors.aliyun.com/pypi/simple"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def fetch(url, tries=4, timeout=30):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"下载失败 {url}: {last}")


def version_key(v):
    parts = re.split(r"[.\-+_]", v)
    out = []
    for p in parts:
        m = re.match(r"^(\d+)(.*)$", p)
        if m:
            out.append((0, int(m.group(1)), m.group(2)))
        else:
            out.append((1, 0, p))
    return out


def py_tags():
    v = sys.version_info
    tags = {f"cp{v.major}{v.minor}", f"py{v.major}{v.minor}", "py3", f"py{v.major}"}
    abi = {f"cp{v.major}{v.minor}", "abi3", "none"}
    plat = {"win_amd64", "win32", "any", "win_arm64"}
    if sys.platform.startswith("linux"):
        plat = {"manylinux", "linux", "any"}
    if sys.platform == "darwin":
        plat = {"macosx", "any"}
    return tags, abi, plat


def wheel_score(fn):
    """给 wheel 文件名打分，越大越优先；不兼容返回 None。"""
    m = re.match(r"^([^-]+)-([^-]+)(?:-([^-]+))?-([^-]+)-([^-]+)-([^-]+)\.whl$", fn)
    if not m:
        return None
    _, ver, build, pytag, abitag, plattag = m.groups()
    tags, abis, plats = py_tags()
    py_tags_set = set(pytag.split("."))
    abi_set = set(abitag.split("."))
    plat_set = set(plattag.split("."))

    if f"cp{sys.version_info.major}{sys.version_info.minor}t" in py_tags_set:
        return None  # free-threaded 解释器专用，普通 CPython 不能用
    if not (py_tags_set & tags) and not (py_tags_set & {"py2.py3"}):
        return None
    if not (abi_set & ({"none", "abi3"} | abis)):
        return None
    if plattag != "any" and not (plat_set & plats) and not plattag.startswith(("manylinux", "macosx", "win")):
        return None
    if plattag == "any":
        base = 10
    elif "win_amd64" in plat_set or (sys.platform.startswith("linux") and "manylinux" in plattag):
        base = 20
    else:
        base = 15
    if "abi3" in abitag:
        base += 2
    return base - 0.001 * len(ver)


def list_wheels(pkg, index):
    url = f"{index.rstrip('/')}/{pkg}/"
    html = fetch(url).decode("utf-8", "ignore")
    files = re.findall(r'href="([^"#]+\.whl)[^"]*"', html)
    out = []
    for f in files:
        fn = f.rsplit("/", 1)[-1]
        sc = wheel_score(fn)
        if sc is None:
            continue
        ver = fn.split("-")[1]
        out.append((sc, version_key(ver), fn, f if f.startswith("http") else url + f))
    return out


def req_name(spec):
    m = re.match(r"^\s*([A-Za-z0-9_.\-]+)", spec)
    return m.group(1).lower().replace("_", "-") if m else None


def read_requires(whl_bytes):
    with zipfile.ZipFile(io.BytesIO(whl_bytes)) as z:
        for n in z.namelist():
            if n.endswith(".dist-info/METADATA"):
                meta = z.read(n).decode("utf-8", "ignore")
                reqs = []
                for line in meta.splitlines():
                    if line.startswith("Requires-Dist:"):
                        spec = line.split(":", 1)[1].strip()
                        if 'extra ==' in spec:  # optional extras 不要
                            continue
                        reqs.append(spec)
                return reqs
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="解压目标目录（运行时加到 PYTHONPATH）")
    ap.add_argument("--packages", nargs="+", required=True)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--no-deps", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    queue, done, failed = list(args.packages), set(), []

    while queue:
        pkg = queue.pop(0).lower().replace("_", "-")
        if pkg in done:
            continue
        try:
            cands = list_wheels(pkg, args.index)
        except Exception as e:  # noqa: BLE001
            print(f"[ERR ] {pkg}: 索引读取失败 {e}")
            failed.append(pkg)
            continue
        if not cands:
            print(f"[ERR ] {pkg}: 没有与当前 Python 兼容的 wheel")
            failed.append(pkg)
            done.add(pkg)
            continue
        # 取版本最高的；同版本优先更高分（原生轮子 > pure）
        best_ver = max(c[1] for c in cands)
        pool = [c for c in cands if c[1] == best_ver]
        pool.sort(key=lambda c: c[0], reverse=True)
        sc, ver, fn, url = pool[0]
        print(f"[GET ] {pkg} {fn.split('-')[1]} ({'原生/兼容轮子' if sc >= 20 else '纯 Python'})")
        try:
            data = fetch(url, timeout=120)
        except Exception as e:  # noqa: BLE001
            print(f"[ERR ] {pkg}: 下载失败 {e}")
            failed.append(pkg)
            done.add(pkg)
            continue
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(args.dest)
        done.add(pkg)
        if not args.no_deps:
            for spec in read_requires(data):
                nm = req_name(spec)
                if nm and nm not in done:
                    queue.append(nm)

    print("\n完成，已处理：", ", ".join(sorted(done)))
    if failed:
        print("失败：", ", ".join(failed))
        sys.exit(1)
    print(f"\n用法：PYTHONPATH={args.dest} python your_script.py")


if __name__ == "__main__":
    main()
