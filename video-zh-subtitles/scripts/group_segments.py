#!/usr/bin/env python3
"""把 ASR 的碎分段合并成整句，供整句翻译使用。

Whisper 按停顿切段，一句话常被切成 2-3 段；逐段翻译会让中文断在词中间。
合并规则：遇到句末标点（. ? ! …），或时长/词数超阈值就收尾。

用法：
  python group_segments.py --segments segments.json --out groups.json --max-dur 14 --max-words 45
"""
import argparse
import json
import re

END_PUNCT = re.compile(r"[.?!…。？！]['\"”’)]?$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-dur", type=float, default=14.0, help="一组最长秒数")
    ap.add_argument("--max-words", type=int, default=45)
    ap.add_argument("--min-dur", type=float, default=0.0, help="短于该时长的组不单独成句（并入下一组）")
    args = ap.parse_args()

    segs = json.load(open(args.segments, encoding="utf-8"))
    groups, cur = [], None

    def flush():
        nonlocal cur
        if cur and cur["text"].strip():
            groups.append(cur)
        cur = None

    for s in segs:
        text = s["text"].strip()
        if not text:
            continue
        if cur is None:
            cur = {"start": s["start"], "end": s["end"], "text": text}
        else:
            cur["end"] = s["end"]
            cur["text"] = (cur["text"] + " " + text).strip()
        words = len(cur["text"].split())
        dur = cur["end"] - cur["start"]
        if END_PUNCT.search(cur["text"]) or dur >= args.max_dur or words >= args.max_words:
            flush()

    flush()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=1)

    words = sum(len(g["text"].split()) for g in groups)
    span = sum(g["end"] - g["start"] for g in groups)
    print(f"{len(segs)} segments -> {len(groups)} groups")
    print(f"覆盖 {span/60:.1f} min，共 {words} words，平均 {words/max(1,len(groups)):.1f} words/组")
    long_ones = [g for g in groups if g["end"] - g["start"] > args.max_dur * 1.5]
    if long_ones:
        print(f"提示：{len(long_ones)} 组超过 {args.max_dur*1.5:.0f}s（长句没有标点），出字幕时会按时长切成多条")
        for g in long_ones[:3]:
            print(f"  - {g['start']:.0f}s: {g['text'][:60]}...")


if __name__ == "__main__":
    main()
