#!/usr/bin/env python3
"""把 subagent 润色后的分片合并回译文 JSON。

每个分片形如 {"0": "润色后中文", "1": ...}，或 [{"i":0,"zh":"..."}]。
合并到 --base 指定的译文文件上（原地覆盖，先备份为 *.mt.bak）。

用法：
  python merge_polish.py --base groups_zh.json --polish "polish_*.json"
"""
import argparse
import glob
import json
import os
import shutil


def load_pairs(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    pairs = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, str) and v.strip():
                pairs[str(k)] = v.strip()
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and "zh" in it and str(it.get("i", "")).strip() != "":
                if str(it["zh"]).strip():
                    pairs[str(it["i"])] = str(it["zh"]).strip()
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--polish", required=True, help="glob，例如 'polish_*.json'")
    ap.add_argument("--backup", action="store_true", default=True)
    args = ap.parse_args()

    base = json.load(open(args.base, encoding="utf-8"))
    files = sorted(glob.glob(args.polish))
    if not files:
        raise SystemExit(f"没有匹配到文件：{args.polish}")

    merged, dup = {}, []
    for p in files:
        pairs = load_pairs(p)
        print(f"  {p}: {len(pairs)} 条")
        for k, v in pairs.items():
            if k in merged:
                dup.append(k)
            merged[k] = v

    if args.backup:
        bak = os.path.splitext(args.base)[0] + ".mt.bak"
        shutil.copyfile(args.base, bak)
        print(f"  原文件已备份 -> {bak}")

    changed = 0
    for k, v in merged.items():
        if k in base and base[k] != v:
            changed += 1
        base[k] = v
    with open(args.base, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=1)

    missing = [k for k in base if not str(base.get(k, "")).strip()]
    print(f"合并 {len(files)} 个分片，写入 {len(merged)} 条（{changed} 条有改动）")
    if dup:
        print(f"警告：{len(dup)} 个键在多个分片里重复出现，以最后处理的为准：{dup[:8]}")
    if missing:
        print(f"警告：{len(missing)} 条译文为空：{missing[:10]}")
    print(f"-> {args.base}")


if __name__ == "__main__":
    main()
