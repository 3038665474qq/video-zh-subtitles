---
name: video-zh-subtitles
description: 把任意视频（网页/课程/直播回放/本地 mp4-mkv-mov/直链）做成中文或中英双语字幕与带软字幕的成品视频：下载视频→ffmpeg 抽音轨→本地 faster-whisper 识别→按句合并→腾讯交互翻译→批量润色→生成 SRT/VTT→封装 MP4。凡是用户提到视频字幕、视频翻译、中文字幕、字幕文件、听写/转写、语音识别、ASR、Whisper、把视频转成文字、给视频配中文、双语字幕、字幕轨、课程/访谈/播客/直播回放翻译，甚至只说「这个视频怎么翻译」「帮我把这个视频转成中文」「这段视频讲了什么，出个字幕」，都应使用本技能。即使用户已经有一个半成品字幕、只需要修时间轴或润色译文，也先读本技能。
---

# 视频转中文字幕流水线

## 先判断：要不要真的跑本地流水线

用户常常只是「想看懂这个视频」。先分清两种诉求，别把 30 分钟的计算浪费在只需要 3 分钟的场景上：

| 诉求 | 推荐做法 |
| --- | --- |
| 只想自己看懂 / 快速看 | 直接把视频下载下来，告诉用户用剪映专业版：导入 →「文本 → 智能字幕 → 识别字幕」→ 全选右键「翻译字幕」；或上传到通义听悟 / 讯飞听见 / 飞书妙记出中英双语字幕。 |
| 要交付成品（字幕文件、带软字幕的视频、中英对照文稿），或要批量、自动化、不外传视频 | 跑本流水线。 |

用户明确要「字幕文件」「成品」「以后都这么处理」时，直接跑流水线。

## 流水线总览

```
source.mp4
  └─ ffmpeg 抽 16k 单声道 wav
       └─ faster-whisper (small.en, int8, CPU) 识别 → segments.json（带时间戳）
            └─ 按句末标点/时长/词数合并 → groups.json（整句，避免逐句机翻割裂）
                 └─ 腾讯交互翻译 Transmart 英→中 → groups_zh.json
                      └─ 批量润色（术语保留英文、统一人名、修机翻硬伤）
                           └─ build_subs.py 按字数比例回填时间轴 + 折行 → SRT/VTT
                                └─ ffmpeg -c copy 封装软字幕 MP4
```

设计上有两个不能省的细节，都是踩过坑换来的：

1. **必须先在句子层面合并再翻译**。Whisper 的分段是按语音停顿切的，一句话常被切成 2–3 段。逐段翻译会让中文断在词中间（「我是罗伯 / 特科万」）。合并成整句后翻译，再按字数比例把译文回填到各组时间区间，中文才通顺。
2. **字幕行宽要按显示宽度算，不是字符数**。中文 1 字 ≈ 2 个西文宽度，`RenderGraph Viewer` 这种混排必须整体换行，否则播放器会自动折成三行并压到画面中间。

## 目录约定

每处理一个视频建一个工作目录（例如 `<cwd>/<slug>/`），所有中间产物都放进去，便于复现和二次生成：

```
<workdir>/
  source.mp4        # 原始视频
  audio16k.wav      # 16k 单声道
  segments.json     # ASR 原始分段 [{"start","end","text"}]
  groups.json       # 合并后的整句
  groups_zh.json    # 译文 {"0": "...", ...}
  chunk_*.json / polish_*.json   # 润色中间文件
  subs/             # 最终字幕
  <name>.zh.mp4     # 成品
```

脚本默认在技能目录 `scripts/` 下，用绝对路径调用；工作目录用参数传。

## 0. 环境自检

```bash
python <skill>/scripts/env_check.py
```

它检查 python/node/ffmpeg/ffprobe 版本、ffmpeg 是否带 `subtitles`/`mov_text`、以及 hf-mirror、阿里云 PyPI、腾讯 Transmart 是否可达。任何一项不通先解决，再往下走（替换方案见 `references/network-cn.md`）。

## 1. 拿到视频文件

按顺序尝试，命中即停：

1. **页面里嵌的直链**：抓页面 HTML/`__NEXT_DATA__`/`__NUXT__`，找 `.mp4`、`mediagw`、`storage.googleapis.com` 等；很多教学站的视频块还带 `subtitles` 字段——先看它！如果 `src` 非空，直接下现成字幕，省掉整条 ASR 链路。
2. **yt-dlp**：`yt-dlp -f "bv*+ba/b" -o source.mp4 <url>`（YouTube 若报 “Sign in to confirm you're not a bot”，换页面直链或换站点）。
3. **已有本地文件**：直接进第 2 步。

下载后记下时长：`ffprobe -v error -show_entries format=duration -of csv=p=0 source.mp4`。

## 2. 抽音轨

```bash
ffmpeg -y -i source.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le audio16k.wav
```

16k 单声道是 Whisper 的输入规格；先转成 wav 再读，比让解码库直接吃 mp4 稳（见 troubleshooting 里的 `av` 版本坑）。

## 3. 语音识别（faster-whisper，CPU int8）

**依赖没装时**（常见：pip 被沙箱/ACL 挡住，或根本不该联网装包）：

```bash
python <skill>/scripts/getwheels.py --dest ./pylibs --packages faster-whisper
python <skill>/scripts/getmodel.py --repo Systran/faster-whisper-small.en --dest ./models/faster-whisper-small.en
PYTHONPATH=./pylibs python <skill>/scripts/transcribe.py --wav audio16k.wav --model ./models/faster-whisper-small.en --out segments.json --parts 4 --threads 4
```

- `--parts N`：把音频按时间等分给 N 个进程并行（时间戳已自动加偏移并合并去重）。**这一步很值得做**：单进程多线程在普通笔记本上只能吃满 2–3 核，4 路并行能把 57 分钟音频从 ~30 分钟压到 ~22 分钟。N 取 `CPU 物理核数 / threads`。
- `--threads`：每个 worker 的 ctranslate2 线程数。
- 模型选择：`small.en` 速度快、够用；术语密集或口音重时换 `Systran/faster-whisper-large-v3`（CPU 上慢 4–6 倍，按需取舍）。
- 如果视频不是英语，去掉 `.en` 并传 `--language <code>`（脚本支持）。

识别完先抽查：`segments.json` 首尾各几条、总条数、最后一条 end 是否≈视频时长。时长差 >30 秒说明有段落被 VAD 吞掉，检查音频或调 `--vad` 参数。

## 4. 合并成整句

```bash
python <skill>/scripts/group_segments.py --segments segments.json --out groups.json --max-dur 14 --max-words 45
```

规则：累加分段直到句末标点（`.` `?` `!`）或超时/超词数。这步决定了中文的可读性，不要跳过。

## 5. 翻译

```bash
node <skill>/scripts/translate.js --in groups.json --out groups_zh.json --field text --src en --tgt zh --batch 15
```

- 默认引擎是腾讯交互翻译的公开接口（国内直连、免 key、质量对技术内容不错），协议见 `references/network-cn.md`。脚本自带重试、增量落盘（中断后重跑只补缺的条目）。
- 若该接口不可用：改用任意可用翻译服务，或让 LLM 分批翻译（一个 subagent 处理 150–200 条，输出 `{"序号": "中文"}` 的 JSON），产物格式保持一致即可。
- 翻译前把专有名词表定下来（产品名、API 名、人名），后面润色会用到。

## 6. 润色（这一步决定成品是否像人做的）

机翻会把 `RenderGraph` 译成「渲染图」、`render pass` 译成「通行证」、`chat` 译成「查特」。用 subagent 分批润色，成本低、收益极大：

1. 把 `groups_zh.json` 按 150–200 条切成 `chunk_0..k.json`，每条 `{"i","en","zh"}`。
2. 每个 chunk 派一个 subagent，brief 用 `references/polish-brief.md`（把术语表、人名表替换成当前视频的）。
3. 每个 subagent 输出 `polish_<k>.json`（`{"序号": "润色后中文"}`），并自检条目数、键完整、无空值。
4. `python <skill>/scripts/merge_polish.py --base groups_zh.json --polish 'polish_*.json'` 合并。

想省事也可以整段交给一个 subagent，但分片能并行、失败可控。润色是可选的：纯机翻也能用，只是可读性差一档。

## 7. 生成字幕

```bash
python <skill>/scripts/build_subs.py --groups groups.json --translations groups_zh.json \
  --out-dir subs --name <VideoName> --cps 5 --width 34
```

产出 `<name>.zh.srt`（UTF-8 with BOM，Windows 播放器/剪辑软件兼容）、`<name>.zh-en.srt`（中英双语）、`<name>.zh.vtt`。

排版规则（脚本已实现，改参数时别破坏）：每行显示宽度 ≤ `--width`（中文按 2 计），最多 2 行；优先在标点处断行；一条字幕的字数上限按时长算（`--cps` 字/秒），超了就在标点处切成多条并按比例分配时间——这样长句不会在屏幕上堆成三行。时间轴相邻条之间留 50ms 间隙，避免闪叠。

## 8. 封装 + 验收

```bash
python <skill>/scripts/mux.py --video source.mp4 --zh subs/<name>.zh.srt \
  --bilingual subs/<name>.zh-en.srt --out <name>.zh.mp4 --preview-at 600
```

- 用 `-c copy -c:s mov_text` 加软字幕轨，**不重新编码**，几秒完成、画质无损。
- 脚本会自动 `ffprobe` 校验轨数/时长，并可选在 `--preview-at` 秒处用 `subtitles` 滤镜烧一帧 PNG，肉眼确认中文能正常渲染、没压到画面内容。手动做同理：`ffmpeg -ss <t> -copyts -i source.mp4 -vf "subtitles=<srt>" -frames:v 1 -update 1 preview.png`（`-copyts` 不能省，否则输入 seek 会把时间戳归零，字幕不显示）。

**验收清单**（交付前逐项确认）：字幕轨存在且语言标记 `zho`；总时长与源一致；抽 2–3 个时间点烧帧看排版；`zh-en.srt` 用文本编辑器打开确认无乱码、无 BOM 重复。最后把 README（三条路线 + 文件说明 + 局限）和成品一起交给用户，别只丢一堆中间文件。

## 产出

- `<name>.zh.mp4`：原画质 + 两条软字幕轨（纯中文 / 中英双语），播放器可切轨、可关闭
- `<name>.zh.srt` / `.zh-en.srt` / `.zh.vtt`
- 中英对照文稿（`transcript.zh-en.md`，用 `--transcript` 让 `build_subs.py` 一并生成）
- 一份 README：方法、文件清单、效果与局限（诚实说明 ASR 偶有错词、译文是机翻 + 润色水平）

## 参考

- `references/network-cn.md`：无 pip、curl 坏掉、境外站点不通时的替代方案；Transmart 接口协议；hf-mirror 模型下载。
- `references/polish-brief.md`：subagent 润色 brief 模板（含术语/人名规则）。
- `references/troubleshooting.md`：`av` 与 faster-whisper 的 `metadata_errors` 冲突、pip ACL 报错、字幕不显示、性能估算。

## 性能参考（普通 Windows 笔记本，无 GPU）

| 阶段 | 57 分钟视频实测 |
| --- | --- |
| 下载 | 视带宽，370 MB 约 1 分钟 |
| 抽音轨 | <2 s |
| ASR（small.en，4 进程 × 4 线程） | ~22 min（单进程 14 线程约 27 min） |
| 翻译（522 句） | ~1.5 min |
| 润色（3 个 subagent 并行） | ~2 min |
| 出字幕 + 封装 | <30 s |

短视频（<5 分钟）全流程 1–2 分钟，直接跑完再问用户要不要调风格。
