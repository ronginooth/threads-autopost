"""
Jev（TypeSafe の判定用のモデル）に、コメントの種類・感情と、返信案の中身を聞く部品。

問いの立て方は公式の手引き（docs.typesafe.ai）に沿っている:
- 状態（state）は名前つきの JSON にする（`post.text` と `comment.text` のように指せる）
- 1つの問いには1つの判定。判定は instructions、選べる答えの意味は criteria に書く
- どれにも当たらないときの答え（その他）を置く
- 確率と確信度はそのまま残し、しきい値を変えても聞き直さずに済むようにする
- 同じ状態への独立した問いは1回のリクエストにまとめる
- 版を固定する（別名 jev-latest は新版が出ると答えが変わりうる）

呼び方は2つ。鍵のある方を使う（両方あれば TypeSafe）:
- TypeSafe に直接: 公式 Python SDK（typesafe-sdk）。鍵 TYPESAFE_API_KEY（https://console.typesafe.ai/keys）。版は MODEL に固定
- Vercel AI Gateway 経由: AI SDK（scripts/jev_gateway.mjs・Node.js 22 以上）。鍵 AI_GATEWAY_API_KEY。版は選べない（GATEWAY_MODEL）
料金はどちらも入力100万トークンあたり0.042ドル（出力は無料）。コメント1件の分類は約1,500トークン。
"""
import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy

MODEL = "jev-1.13.0"             # 版を上げるときは、自分のコメントで答えが変わらないかを確かめてから
GATEWAY_MODEL = "typesafe-ai/jev"
BASE = Path(__file__).resolve().parent.parent

COMMENT_QUESTIONS = {
    "type": Choice(
        instructions="`comment.text` は、Threads の投稿 `post.text` に付いたコメントです。このコメントはどの種類ですか。",
        criteria={
            "共感・応援": "投稿を好意的に受け止めている（共感・感想・お礼・応援・称賛）",
            "質問": "投稿者に何かを尋ねている",
            "体験の共有": "自分の体験や状況を話している（投稿を否定していない）",
            "補足・情報": "関連する知識や情報を足している（投稿を否定していない）",
            "反論・指摘": "投稿の内容を否定・訂正する、または別の見方を示している（言い方は丁寧）",
            "強い言い方": "皮肉・からかい・説教・見下し・攻撃的な言い方をしている",
            "宣伝・無関係": "宣伝、または投稿と関係のない内容",
            "その他": "上のどれにも当てはまらない",
        },
    ),
    "emotion": Choice(
        instructions="`comment.text` を書いた人の、いちばん強い感情はどれですか。",
        criteria={
            "喜び・楽しさ": "楽しい・うれしい・わくわくしている",
            "感謝・尊敬": "ありがたい・すごいと思っている",
            "共感・安心": "わかる・自分も同じだと感じている",
            "不安・悩み": "不安・つらさ・悩みを抱えている",
            "驚き・関心": "驚いている・興味を持っている",
            "不満・苛立ち": "不満・苛立ち・呆れを感じている",
            "落ち着き": "感情があまり表に出ていない",
        },
    ),
    "hostile": Noul(
        instructions="`comment.text` は、投稿者を下に置く言い方を含みますか。",
        criteria={
            "true": "見下す・からかう・皮肉を言う・説教する・呆れを示すなど、相手を下に置く言い方をしている",
            "false": "丁寧な反論や質問、好意的な感想など、相手を下に置く言い方はしていない",
        },
    ),
}


def draft_questions(n: int) -> dict:
    """返信案の検査（公式の LLM ガードレールの型）: 案ごとに「作った体験・数字」を見る"""
    return {
        f"invented_{i}": Noul(
            instructions=(f"`reply_drafts[{i}]` は、`post.text` と `comment.text` のどちらにも書かれていない、"
                          "書き手自身の体験・出来事・経歴・数字を、事実として書いていますか。"),
            criteria={
                "true": "書かれていない体験・出来事・経歴・数字を足している（例: 「20年やってきて」「私も昨年」）",
                "false": "投稿とコメントに書かれている範囲で返しているか、気持ちや考えを述べているだけ",
            },
        )
        for i in range(n)
    }


def backend() -> str | None:
    if os.getenv("TYPESAFE_API_KEY"):
        return "typesafe"
    if os.getenv("AI_GATEWAY_API_KEY"):
        return "gateway"
    return None


def _choice(choice: str, confidence, probabilities) -> dict:
    return {"choice": choice, "confidence": confidence,
            "probabilities": {k: round(v, 4) for k, v in (probabilities or {}).items()}}


async def _run_typesafe(requests: list, concurrency: int) -> dict:
    out = {}
    sem = asyncio.Semaphore(concurrency)
    async with AsyncTypeSafeClient(model=MODEL, retry=RetryPolicy(max_retries=2)) as client:
        async def one(rid, state, questions):
            async with sem:
                try:
                    r = await client.system_one(state=state, questions=questions)
                    out[rid] = {
                        "choices": {k: _choice(a.choice, a.confidence, a.probabilities) for k, a in r.choices.items()},
                        "nouls": {k: a.noul for k, a in r.nouls.items()},
                        "model": r.model,
                        "tokens": (r.usage.input_tokens or 0) + (r.usage.output_tokens or 0),
                    }
                except Exception as e:  # 1件の失敗は、その件の error にして続ける（呼び出し側が数える）
                    out[rid] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        await asyncio.gather(*(one(*r) for r in requests))
    return out


def _run_gateway(requests: list, concurrency: int) -> dict:
    def as_ai_sdk(q) -> dict:
        d = q.model_dump()
        return {"type": "boolean" if isinstance(q, Noul) else d["type"], "instructions": d["instructions"],
                "criteria": d["criteria"]}

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.json", Path(tmp) / "out.json"
        src.write_text(json.dumps({"model": GATEWAY_MODEL, "concurrency": concurrency, "requests": [
            {"id": rid, "state": state, "questions": {k: as_ai_sdk(q) for k, q in questions.items()}}
            for rid, state, questions in requests]}, ensure_ascii=False), encoding="utf-8")
        res = subprocess.run(["node", str(BASE / "scripts" / "jev_gateway.mjs"), str(src), str(dst)],
                             capture_output=True, text=True, timeout=900)
        if not dst.exists():
            raise RuntimeError(f"jev_gateway.mjs が結果を出さなかった（exit {res.returncode}）: {res.stderr.strip()[-200:]}")
        raw = json.loads(dst.read_text(encoding="utf-8"))
    out = {}
    for rid, r in raw.items():
        if "error" in r:
            out[rid] = r
            continue
        answers, conf, usage = r["answers"], r.get("confidence") or {}, r.get("usage") or {}
        out[rid] = {
            "choices": {k: _choice(a["choice"], conf.get(k), a.get("probabilities"))
                        for k, a in answers.items() if a["type"] == "choice"},
            "nouls": {k: a["probability"] for k, a in answers.items() if a["type"] == "boolean"},
            "model": r.get("model"),
            "tokens": (usage.get("inputTokens") or 0) + (usage.get("outputTokens") or 0),
        }
    return out


def _run(requests: list, concurrency: int) -> dict:
    """requests: [(id, state, questions)] → {id: {"choices", "nouls", "model", "tokens"} | {"error"}}"""
    via = backend()
    if via == "typesafe":
        return asyncio.run(_run_typesafe(requests, concurrency))
    if via == "gateway":
        return _run_gateway(requests, concurrency)
    return {rid: {"error": "Jev の鍵（TYPESAFE_API_KEY か AI_GATEWAY_API_KEY）が無い"} for rid, _, _ in requests}


def classify_comments(items: list, concurrency: int = 4) -> dict:
    """items: [{"id", "post", "comment"}] → {id: {"type": {...}, "emotion": {...}, "hostile_p", "model", "tokens"} | {"error"}}"""
    reqs = [(it["id"], {"post": {"text": it["post"][:600]}, "comment": {"text": it["comment"]}}, COMMENT_QUESTIONS)
            for it in items]
    res = {}
    for rid, r in _run(reqs, concurrency).items():
        res[rid] = r if "error" in r else {
            "type": r["choices"]["type"], "emotion": r["choices"]["emotion"],
            "hostile_p": round(r["nouls"]["hostile"], 4), "model": r["model"], "tokens": r["tokens"]}
    return res


def check_drafts(items: list, concurrency: int = 4) -> dict:
    """items: [{"id", "post", "comment", "drafts": [...]}] → {id: [案ごとの「作った体験」の確率] | {"error"}}"""
    reqs = [(it["id"], {"post": {"text": it["post"][:600]}, "comment": {"text": it["comment"]},
                        "reply_drafts": it["drafts"]}, draft_questions(len(it["drafts"])))
            for it in items if it["drafts"]]
    return {rid: (r if "error" in r else [round(r["nouls"][f"invented_{i}"], 4) for i in range(len(r["nouls"]))])
            for rid, r in _run(reqs, concurrency).items()}
