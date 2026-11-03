# 常见坑与排障

## 1. `faster_whisper` 抛 `TypeError: open() got an unexpected keyword argument 'metadata_errors'`

原因：`av`（PyAV）版本比 faster-whisper 期望的新/旧，`WhisperModel.transcribe(path)` 内部
用 `av.open` 读音频时参数对不上。**不要**去降级 `av`（在受限环境里很难装对），直接绕开：

用 `wave` + `numpy` 自己读 16-bit wav，把 float32 数组喂给 `transcribe()`：

```python
import wave, numpy as np
with wave.open("audio16k.wav", "rb") as w:
    raw = w.readframes(w.getnframes())
x = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
segs, info = model.transcribe(x, language="en", vad_filter=True, ...)
```

`transcribe.py` 已经这样做，所以只需 wav 输入，不要传 mp4。

## 2. 字幕烧帧不显示（`subtitles` 滤镜没反应）

`ffmpeg -ss 8 -i in.mp4 -vf subtitles=captions.srt -frames:v 1 out.png` → 图里有字才怪：
输入 seek 会把 PTS 归零，滤镜按 0 秒以后找字幕，8 秒前的字幕早过去了。

正确写法：`-ss 8 -copyts -i in.mp4 -vf "subtitles=captions.srt" -frames:v 1 -update 1 out.png`

`-update 1` 是给单帧输出用的，缺了会报 “does not contain an image sequence pattern”。

## 3. 播放器里中文不显示 / 显示成方块

- 软字幕（mov_text）由播放器渲染，Windows 上用 PotPlayer/VLC 默认没问题；系统缺中文字体时
  才会方块（极少见）。
- 某些老播放器只认 `.ass`/`.srt` 外挂文件，不认 mp4 内嵌 mov_text：把 `.zh.srt` 与视频同名放同目录即可。
- SRT 用 UTF-8 **with BOM** 保存（脚本默认如此），否则 Windows 记事本/部分播放器会乱码。

## 4. 字幕叠成三行 / 挡住画面

两个原因叠在一起：行宽按字符数算（英文术语实际更宽）、长句没切。修法见 `build_subs.py`：
按显示宽度（中文 2、西文 1）折行，单条字数按时长（`--cps`）限制，超长在标点处切多条。
默认 `--width 34 --cps 5` 在 1080p 上一般是 2 行以内。

## 5. ASR 结尾少了 30 秒以上

- VAD 把轻声/音乐段吞了：`--no-vad` 重跑该段。
- 分片并行时最后一片偏移算错：检查 `segments.json` 最后一条 `end` 是否≈视频时长。
- 音频本身就是静音结尾：正常。

## 6. 并行转写没有变快

- 笔记本上单进程多线程只能吃 2–3 核；要真正并行必须**多进程**（`--parts`）。
- 每个 worker 的 `--threads` 别开太大（4 左右），否则线程争抢反而更慢。
- 机械硬盘上多个进程同时读同一个 100MB+ wav 会有 IO 抖动，影响不大。

## 7. 性能估算（CPU-only，普通笔记本）

| 阶段 | 57 min 音频 |
| --- | --- |
| small.en int8，4 进程×4 线程 | ~22 min |
| large-v3 int8 | ~2.5–3 小时（不建议在 CPU 上跑） |
| 翻译 522 句（Transmart，15 条/批） | ~1.5 min |
| 出字幕 | <5 s |
| 封装（-c copy） | 3–10 s |

规则：音频越长越要分片；短视频（<5 分钟）单进程即可。

## 8. 硬字幕 vs 软字幕

- 软字幕（本技能默认）：`-c copy -c:s mov_text`，几秒完成、画质无损、可关可切。
- 硬字幕（烧进画面）：`-vf subtitles=... -c:v libx264 -crf 18`，57 分钟 4K 视频在 CPU 上要
  几小时，除非用户明确要「任何设备都能看」，否则别做。
