#!/usr/bin/env node
// jev_gateway.mjs — Jev を Vercel AI Gateway 経由で呼ぶ（AI SDK の experimental_evaluate。ai-sdk.dev/docs/ai-sdk-core/evaluation）
// lib/jev.py が、TypeSafe の鍵が無く AI_GATEWAY_API_KEY があるときに呼ぶ。問いの中身は lib/jev.py が決める。
//
// 使い方: node scripts/jev_gateway.mjs <依頼.json> <結果.json>
//   依頼: {"model", "concurrency", "requests": [{"id", "state", "questions"}]}（questions は AI SDK の形: choice / boolean）
//   結果: {"<id>": {"answers", "confidence", "usage", "model"} | {"error"}}
// 鍵: 環境変数 AI_GATEWAY_API_KEY（値は表示しない）。無ければ exit 2。全件失敗は exit 1

import { readFileSync, writeFileSync } from 'node:fs';
import { experimental_evaluate as evaluate } from 'ai';

const [inPath, outPath] = process.argv.slice(2);
const log = (m) => console.error(`[jev_gateway] ${m}`);
if (!inPath || !outPath) { log('使い方: node scripts/jev_gateway.mjs <依頼.json> <結果.json>'); process.exit(2); }
if (!process.env.AI_GATEWAY_API_KEY) { log('AI_GATEWAY_API_KEY が無い'); process.exit(2); }

const { model, concurrency = 4, requests } = JSON.parse(readFileSync(inPath, 'utf8'));
const out = {};
let failed = 0;

async function one(req) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const r = await evaluate({ model, state: req.state, questions: req.questions });
      const meta = r.providerMetadata?.typesafe ?? {};
      return { answers: r.answers, confidence: meta.confidence ?? {}, usage: r.usage ?? {}, model: meta.model ?? model };
    } catch (e) {
      if (attempt === 3) throw e;
      await new Promise((ok) => setTimeout(ok, attempt * 2000));
    }
  }
}

log(`${requests.length} 件を聞く（${model}・並列 ${concurrency}）`);
let cursor = 0;
await Promise.all(Array.from({ length: Math.min(concurrency, requests.length) }, async () => {
  while (cursor < requests.length) {
    const req = requests[cursor++];
    try { out[req.id] = await one(req); }
    catch (e) { failed++; out[req.id] = { error: String(e?.message ?? e).slice(0, 200) }; log(`${req.id} 失敗: ${out[req.id].error}`); }
  }
}));
writeFileSync(outPath, JSON.stringify(out), 'utf8');
log(`終わり（${requests.length - failed} 件 / 失敗 ${failed} 件）`);
if (requests.length && failed === requests.length) process.exit(1);
