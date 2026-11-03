#!/usr/bin/env python3
"""把整句译文 + 时间轴渲染成 SRT / VTT / 中英对照文稿。

排版规则（都是踩坑换来的）：
- 行宽按“显示宽度”算：中文=2，西文=1；默认每行 34，最多 2 行。
- 一条字幕的字数上限按时长算（--cps 字/秒），超长句在标点处切成多条并按字数比例分配时间，
  否则长句会在屏幕上叠三行、盖住画面。
- 相邻字幕之间留 50ms 间隙，避免闪叠。

用法：
  python build_subs.py --groups groups.json --translations groups_zh.json --out-dir subs --name Video
  python build_subs.py ... --chunks-dir chunks --chunk-size 174   # 同时切出润色用的分片
  python build_subs.py ... --transcript                           # 额外生成中英对照 md
"""
import argparse
import json
import os
import re

END_PUNCT_CN = "，。！？、；：,;"


def ts(t, sep=","):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d%s%03d" % (h, m, s, sep, ms)


def _dw(s):
    """显示宽度：CJK 记 2，其他记 1。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def _split_long(s, maxlen):
    """把超长从句优先在逗号/分号处切开，否则硬切。"""
    out = []
    while len(s) > maxlen:
        window = s[: maxlen + 1]
        cut = -1
        for p in "，、；：,;":
            i = window.rfind(p)
            if i > maxlen * 0.45:
                cut = max(cut, i)
        if cut > 0:
            out.append(s[: cut + 1])
            s = s[cut + 1:]
        else:
            out.append(s[:maxlen])
            s = s[maxlen:]
    if s:
        out.append(s)
    return out


def split_cn(t, maxlen):
    """按句号/逗号把中文切成 <= maxlen 的若干块。"""
    t = re.sub(r"\s+", " ", t).strip()
    sents = [x for x in re.findall(r"[^。！？]*[。！？]?", t) if x]
    out, cur = [], ""
    for s in sents:
        if len(cur) + len(s) <= maxlen:
            cur += s
            continue
        if cur:
            out.append(cur)
            cur = ""
        if len(s) <= maxlen:
            cur = s
        else:
            pieces = _split_long(s, maxlen)
            out.extend(pieces[:-1])
            cur = pieces[-1]
    if cur:
        out.append(cur)
    return out or [t]


def wrap_cn(t, width=34):
    """中文/中英混排折行：整行不超过 width 显示宽度，尽量在标点处断，双行均衡。"""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/+#-]*|[ \t]+|.", t)

    def wt(x):
        return _dw(x)

    total = sum(wt(x) for x in tokens)
    if total <= width:
        return "".join(tokens).strip()

    if total <= 2 * width:
        cand, acc = [], 0
        for k, x in enumerate(tokens[:-1]):
            acc += wt(x)
            if x in END_PUNCT_CN and acc <= width and total - acc <= width:
                cand.append((abs(acc - total / 2), k + 1))
        if cand:
            best = min(cand)[1]
            head, rest = tokens[:best], tokens[best:]
        else:
            acc, best, score = 0, 0, 1e9
            for k, x in enumerate(tokens[:-1]):
                acc += wt(x)
                if acc > width:
                    break
                sc = abs(acc - total / 2) - (3 if x in END_PUNCT_CN else 0)
                if sc < score:
                    score, best = sc, k + 1
            head, rest = tokens[:best], tokens[best:]
        return "".join(head).strip() + "\n" + "".join(rest).strip()

    # 超长：贪心，每行尽量在标点后断
    lines, cur = [], []
    for tok in tokens:
        if cur and sum(wt(x) for x in cur) + wt(tok) > width:
            acc, best = 0, -1
            for k, x in enumerate(cur):
                acc += wt(x)
                if x in END_PUNCT_CN and acc >= width * 0.4:
                    best = k
            if best >= 0:
                lines.append("".join(cur[: best + 1]).strip())
                cur = cur[best + 1:]
            else:
                lines.append("".join(cur).strip())
                cur = []
        cur.append(tok)
    if "".join(cur).strip():
        lines.append("".join(cur).strip())
    return "\n".join(lines)


def wrap_en(t, width=50):
    words, lines, cur = t.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def build_rows(groups, tr, cps, width=34):
    rows = []
    for i, g in enumerate(groups):
        zh = (tr.get(str(i)) or "").strip()
        if not zh:
            continue
        dur = max(0.5, g["end"] - g["start"])
        maxchars = max(12, min(28, int(round(dur * cps))))
        norm = re.sub(r"\s+", " ", zh).strip()
        chunks = split_cn(norm, maxchars) if len(norm) > maxchars else [norm]
        if len(chunks) == 1:
            rows.append([g["start"], g["end"], wrap_cn(chunks[0], width), wrap_en(g["text"])])
            continue
        total = sum(len(c) for c in chunks) or 1
        t = g["start"]
        for k, c in enumerate(chunks):
            span = dur * len(c) / total
            rows.append([t, t + span, wrap_cn(c, width), wrap_en(g["text"]) if k == 0 else ""])
            t += span

    for i in range(len(rows) - 1):
        a, b = rows[i][0], rows[i][1]
        if b > rows[i + 1][0] - 0.05:
            rows[i][1] = max(a + 0.4, rows[i + 1][0] - 0.05)
    return rows


def write_srt(path, rows, fn):
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        for n, (a, b, zh, en) in enumerate(rows, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (n, ts(a), ts(b), fn(zh, en)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", required=True)
    ap.add_argument("--translations", required=True)
    ap.add_argument("--out-dir", default="subs")
    ap.add_argument("--name", default="video")
    ap.add_argument("--width", type=int, default=34, help="每行显示宽度（中文算 2）")
    ap.add_argument("--cps", type=float, default=5.0, help="每秒最多多少字")
    ap.add_argument("--chunks-dir", default=None, help="输出润色分片目录")
    ap.add_argument("--chunk-size", type=int, default=174)
    ap.add_argument("--transcript", action="store_true", help="同时生成中英对照 Markdown")
    args = ap.parse_args()

    groups = json.load(open(args.groups, encoding="utf-8"))
    tr = json.load(open(args.translations, encoding="utf-8"))
    os.makedirs(args.out_dir, exist_ok=True)

    rows = build_rows(groups, tr, args.cps, args.width)
    zh_path = os.path.join(args.out_dir, f"{args.name}.zh.srt")
    bi_path = os.path.join(args.out_dir, f"{args.name}.zh-en.srt")
    vtt_path = os.path.join(args.out_dir, f"{args.name}.zh.vtt")

    write_srt(zh_path, rows, lambda zh, en: zh)
    write_srt(bi_path, rows, lambda zh, en: zh + ("\n" + en if en else ""))
    with open(vtt_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("WEBVTT\n\n")
        for a, b, zh, _en in rows:
            f.write("%s --> %s\n%s\n\n" % (ts(a, "."), ts(b, "."), zh))

    print(f"{len(groups)} groups -> {len(rows)} subtitles")
    print(" ", zh_path, os.path.getsize(zh_path), "bytes")
    print(" ", bi_path, os.path.getsize(bi_path), "bytes")
    print(" ", vtt_path, os.path.getsize(vtt_path), "bytes")

    if args.transcript:
        md = os.path.join(args.out_dir, f"{args.name}.zh-en.md")
        with open(md, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# {args.name} — 中英对照文稿\n\n")
            for i, g in enumerate(groups):
                zh = (tr.get(str(i)) or "").strip()
                if not zh:
                    continue
                f.write(f"**[{ts(g['start'])[:8]}]** {zh}\n\n{g['text']}\n\n")
        print(" ", md, os.path.getsize(md), "bytes")

    if args.chunks_dir:
        os.makedirs(args.chunks_dir, exist_ok=True)
        items = [{"i": i, "en": g["text"], "zh": (tr.get(str(i)) or "").strip()} for i, g in enumerate(groups)]
        n = max(1, (len(items) + args.chunk_size - 1) // args.chunk_size)
        for k in range(n):
            part = items[k * args.chunk_size : (k + 1) * args.chunk_size]
            p = os.path.join(args.chunks_dir, f"chunk_{k}.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(part, f, ensure_ascii=False, indent=1)
            print(" ", p, len(part), "条")

    print("\n提示：交付前抽 1-2 个时间点烧帧检查排版（见 mux.py --preview-at）")


if __name__ == "__main__":
    main()
