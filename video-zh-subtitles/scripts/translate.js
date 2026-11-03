#!/usr/bin/env node
/**
 * 批量英译中：默认走腾讯交互翻译（Transmart）公开接口，国内直连、免 key。
 *
 * 用法：
 *   node translate.js --in groups.json --out groups_zh.json
 *   node translate.js --in groups.json --out groups_zh.json --src en --tgt zh --batch 15
 *
 * 特性：分批、失败重试、增量落盘（中断后重跑只补缺失条目、不重复请求）。
 * 输出：{"0": "中文", "1": "中文", ...}，键是输入数组下标。
 */
const fs = require("fs");
const https = require("https");

function parseArgs(argv) {
  const a = { src: "en", tgt: "zh", batch: 15, field: "text", retries: 5, sleep: 400 };
  for (let i = 2; i < argv.length; i += 2) {
    const k = argv[i].replace(/^--/, "");
    a[k] = argv[i + 1];
  }
  if (!a.in || !a.out) {
    console.error("用法: node translate.js --in groups.json --out groups_zh.json [--field text] [--batch 15] [--src en] [--tgt zh]");
    process.exit(1);
  }
  a.batch = parseInt(a.batch, 10) || 15;
  a.retries = parseInt(a.retries, 10) || 5;
  return a;
}

const CLIENT_KEY =
  "browser-chrome-110.0.0-Mac OS-df4bd4c5-a65d-44b2-a40f-42f34f3535f2-1677486696487";

function postJSON(url, body) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(body));
    const req = https.request(
      url,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": data.length,
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
          Origin: "https://transmart.qq.com",
          Referer: "https://transmart.qq.com/",
        },
        timeout: 30000,
      },
      (res) => {
        let buf = "";
        res.on("data", (d) => (buf += d));
        res.on("end", () => {
          try {
            resolve({ status: res.statusCode, json: JSON.parse(buf) });
          } catch (e) {
            reject(new Error("响应不是 JSON: " + buf.slice(0, 200)));
          }
        });
      }
    );
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
    req.end(data);
  });
}

async function translateBatch(texts, src, tgt) {
  const body = {
    header: { fn: "auto_translation", client_key: CLIENT_KEY },
    type: "plain",
    model_category: "normal",
    source: { lang: src, text_list: texts },
    target: { lang: tgt },
  };
  const res = await postJSON("https://transmart.qq.com/api/imt", body);
  const out = res.json && res.json.auto_translation;
  if (!Array.isArray(out) || out.length !== texts.length) {
    throw new Error(`翻译返回异常 status=${res.status} body=${JSON.stringify(res.json).slice(0, 200)}`);
  }
  return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const args = parseArgs(process.argv);
  const items = JSON.parse(fs.readFileSync(args.in, "utf8"));
  const out = fs.existsSync(args.out) ? JSON.parse(fs.readFileSync(args.out, "utf8")) : {};

  const todo = [];
  items.forEach((it, i) => {
    const key = String(i);
    const zh = out[key];
    if (typeof zh !== "string" || !zh.trim()) todo.push(i);
  });
  console.log(`共 ${items.length} 条，已完成 ${items.length - todo.length}，待翻译 ${todo.length}`);

  for (let k = 0; k < todo.length; k += args.batch) {
    const idxs = todo.slice(k, k + args.batch);
    const texts = idxs.map((i) => (typeof items[i] === "string" ? items[i] : String(items[i][args.field] || "")));
    let ok = false;
    for (let attempt = 1; attempt <= args.retries && !ok; attempt++) {
      try {
        const res = await translateBatch(texts, args.src, args.tgt);
        idxs.forEach((i, j) => (out[String(i)] = res[j]));
        ok = true;
      } catch (e) {
        console.error(`  批次 ${k / args.batch} 第 ${attempt} 次失败: ${e.message}`);
        await sleep(1500 * attempt);
      }
    }
    if (!ok) {
      console.error(`  批次 ${k / args.batch} 放弃（可重跑本脚本补齐）`);
      continue;
    }
    fs.writeFileSync(args.out, JSON.stringify(out, null, 1), "utf8");
    process.stdout.write(`\r进度 ${Math.min(todo.length, k + args.batch)}/${todo.length}`);
    await sleep(parseInt(args.sleep, 10) || 400);
  }
  console.log(`\n完成 -> ${args.out}`);
  const missing = items.map((_, i) => i).filter((i) => !out[String(i)]);
  if (missing.length) {
    console.log(`仍缺 ${missing.length} 条：${missing.slice(0, 10).join(", ")}${missing.length > 10 ? " ..." : ""}，重跑即可补齐`);
    process.exit(2);
  }
}

main().catch((e) => {
  console.error("失败:", e.message);
  process.exit(1);
});
