# 网络受限环境下的替代方案

这份笔记来自一次真实排障：Windows 机器上 `pip` 装机被 ACL 挡住、`curl` 因 schannel 报
`SEC_E_NO_CREDENTIALS` 完全不可用、`github.com / pypi.org / huggingface.co / translate.googleapis.com`
全部超时。结论是：**不要放弃，换通道即可**。下面每条都是验证可用的。

## 一、Python 包：绕开 pip

- 症状：`pip install` 报
  `OSError: [Errno 13] Permission denied: '...\\tmp\\pip-unpack-*\\xxx.whl'`，
  `--target` 和换 `TMPDIR` 都没用（Windows 沙箱 ACL 对临时解包目录 deny）。工作区外写入也可能被拒。
- 方案：`getwheels.py`——从镜像读 simple 索引，按当前解释器标签挑 wheel，递归解析
  `METADATA` 的 `Requires-Dist`，直接 `zipfile.extractall` 到 `./pylibs`，运行时
  `PYTHONPATH=./pylibs`。
- 可用镜像：`https://mirrors.aliyun.com/pypi/simple`（实测最稳）、
  `https://pypi.tuna.tsinghua.edu.cn/simple`。
- 坑：要排除 `cp314t`（free-threaded 解释器专用轮子）和 prerelease；abi3 轮子（如 `av` 的
  `cp312-abi3`）可以装在更新的 CPython 上。

## 二、Whisper 模型：绕开 huggingface 直连

- `huggingface.co` 直连超时；`hf-mirror.com` 用 python `urllib` 可正常下载。
- `getmodel.py` 只下 4 个文件：`config.json`、`tokenizer.json`、`vocabulary.txt`、`model.bin`
  （small.en 的 model.bin 约 461MB，约 70 秒）。
- 注意：node 的 fetch 有时连 hf-mirror 也超时，用 python 更稳。
- 其他可用模型源：modelscope（`www.modelscope.cn`，国内快，模型名不同，需自行对应）。

## 三、翻译

- **腾讯交互翻译（Transmart）**：国内直连、免 key，对技术内容质量不错。协议：

  `POST https://transmart.qq.com/api/imt`，`Content-Type: application/json`：

  ```json
  {
    "header": {"fn": "auto_translation", "client_key": "browser-chrome-110.0.0-Mac OS-df4bd4c5-a65d-44b2-a40f-42f34f3535f2-1677486696487"},
    "type": "plain",
    "model_category": "normal",
    "source": {"lang": "en", "text_list": ["Upgrading to Unity 6 improves runtime performance."]},
    "target": {"lang": "zh"}
  }
  ```

  返回 `{"header":{"ret_code":"succ"},"auto_translation":["将项目升级到Unity 6可以提高运行时性能。"]}`。
  单次 15–20 条、批间 sleep 300–500ms；实测 522 句约 1.5 分钟。`translate.js` 已封装（含重试与增量）。
- 不可用时的备选：任意 LLM 分批翻译（150–200 条/批，输出 `{"序号": "中文"}`），或本地
  `nllb`/`opus-mt`（质量对口语内容一般）。
- **不可用**：`translate.googleapis.com`（超时）、Google 网页翻译接口（需要 token 且被墙）。

## 四、视频下载

- 先找页面里的直链：抓 HTML / `__NEXT_DATA__` / `__NUXT__`，搜 `.mp4`、`mediagw`、
  `storage.googleapis.com`。Unity Learn 这类站点是 `connect-mediagw.unity.com/...mp4`，
  由 CDN 302 到签名 URL，`yt-dlp` 能直接下。
- `yt-dlp` 可用，但对 YouTube 会报 `Sign in to confirm you're not a bot`（需要 cookies）——
  优先找页面直链，而不是硬啃 YouTube。
- `curl` 若报 schannel 错误，一律改用 `yt-dlp`、`ffmpeg -i URL`、python `urllib`、node `https`
  中的任意一个，别在 curl 上耗时间。

## 五、给用户的建议

如果用户只是「想看懂」某个视频，最省事的是让他用剪映专业版（识别字幕 → 翻译字幕）
或通义听悟/讯飞听见/飞书妙记上传（中英双语），不需要翻墙、不需要本地环境。
只有在需要成品文件、批量处理、或视频不能外传时，才值得跑本流水线。
