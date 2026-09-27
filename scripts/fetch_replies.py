"""
コメントの取得・分類・返信案（replies.yml が2時間おきに呼ぶ）

1. 自分の最近の投稿（replies.recent_posts 本）に付いたコメントを取る。1回に取り込むのは新しい順に
   replies.max_comments_per_run 件まで（残りは次の回）。replies.fresh_days 日より古いコメントは取り込まない
2. Threads のアプリから返信したコメントは「返信済み」に移す（自分の返信が付いているかを見る）
3. 返信待ちのコメントを Jev で、種類・感情・強い言い方の確率に分ける（lib/jev.py。TYPESAFE_API_KEY か AI_GATEWAY_API_KEY）
   - 強い言い方の確率が HOSTILE_P 以上なら、種類に関係なく「強い言い方」
   - 確信度が SURE 以上なら「確か」。それ未満は replies.md に「判定を確かめて」と出す
   - 種類の確信度が ESCALATE 未満なら、Claude が判定し直す（Claude が使えるときだけ）
4. 種類と感情に合った返し方で、Claude Code（自分のサブスク。CLAUDE_CODE_OAUTH_TOKEN）が返信案を作る
   案は Jev で確かめ、投稿にもコメントにも無い体験・数字を書いていそうな案は外す（少し疑わしい案には印）
5. comments.json・seen_comments.json・status.json・replies.md に書く（lib/replies.py）

分類が止まっても、取得と保存は続ける（理由は replies.md の上に出し、終了コード 1 で知らせる）。
返信案が作れなくても、分類までは残す（理由は replies.md の上に出す。次の回にまた試す）。

使い方: python3 scripts/fetch_replies.py --config configs/my_account.yml
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.account_context import get_context
from lib.threads_api import ThreadsApiError, get_me, get_my_posts, get_replies
from lib.token_store import mask
from lib import replies as store
from lib.replies import DRAFT_COUNT, JST, advice_for, labeled, one_line

# configs/<アカウント>.yml の replies: に書かなかった項目
DEFAULTS = {
    "recent_posts": 25,
    "max_comments_per_run": 20,
    "fresh_days": 30,
    "draft_replies": True,
    "model": "opus",
    "persona": "",
}
KEEP_REPLIED = 20       # 返信済みは直近この件数だけ残す
HOSTILE_P = 0.70        # 強い言い方の確率がこれ以上なら「強い言い方」（作者のコメント40件で、丁寧な反論を巻き込まない線に合わせた）
SURE = 0.90             # 確信度がこれ以上なら「確か」（公式 docs.typesafe.ai の確信度の目安）
ESCALATE = 0.50         # 種類の確信度がこれ未満なら Claude が判定し直す（公式: 低いものは人か考える AI へ回す）
INVENTED_DROP = 0.70    # 案が「作った体験・数字」を含む確率がこれ以上なら外す（公式の LLM ガードレールの目安）
INVENTED_REVIEW = 0.35  # これ以上は「確認して」の印を付ける

TYPE_CRITERIA = {
    "共感・応援": "投稿を好意的に受け止めている",
    "質問": "投稿者に何かを尋ねている",
    "体験の共有": "自分の体験や状況を話している",
    "補足・情報": "関連する知識や情報を足している",
    "反論・指摘": "投稿を否定・訂正する、別の見方を示す（言い方は丁寧）",
    "強い言い方": "皮肉・からかい・説教・見下し・攻撃的",
    "宣伝・無関係": "宣伝や、投稿と関係のない内容",
    "その他": "どれにも当てはまらない",
}


def load_settings(ctx) -> dict:
    settings = dict(DEFAULTS)
    settings.update(ctx.config.get("replies") or {})
    return settings


def now_jst_label() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


# ---------- Claude（サブスクの枠。API キーは使わない） ----------

class ClaudeStopped(Exception):
    """claude -p そのものが動かない（入っていない・鍵切れ・枠の上限・時間切れ）。この回はもう呼ばない"""


def ask_claude(prompt: str, model: str) -> str:
    """Claude Code を1回だけ呼んで答えの文を返す"""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # あると API キー（従量課金）が優先されるので外す
    try:
        res = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", model,
             "--tools", "", "--permission-mode", "dontAsk", "--no-session-persistence"],
            capture_output=True, text=True, timeout=180, env=env,
        )
    except FileNotFoundError:
        raise ClaudeStopped("claude（Claude Code）が入っていない")
    except subprocess.TimeoutExpired:
        raise ClaudeStopped("claude -p が180秒で終わらなかった")
    try:
        out = json.loads(res.stdout)
    except ValueError:
        raise ClaudeStopped(f"claude -p の出力が読めない（exit {res.returncode}）: {(res.stderr or res.stdout).strip()[:200]}")
    if res.returncode != 0 or out.get("is_error"):
        raise ClaudeStopped(f"claude -p が失敗: {str(out.get('result'))[:200]}")
    return out.get("result", "")


class Claude:
    """返信案と判定し直しに使う。off は初めから使わない理由、failed はこの回の途中で止まった理由"""

    def __init__(self, model: str, off: str = ""):
        self.model = model
        self.off = off
        self.failed = ""

    @property
    def usable(self) -> bool:
        return not (self.off or self.failed)

    def ask(self, prompt: str) -> str:
        if not self.usable:
            raise ClaudeStopped(self.off or self.failed)
        try:
            return ask_claude(prompt, self.model)
        except ClaudeStopped as e:
            self.failed = str(e)
            raise


# ---------- 1. 取得 ----------

def fetch_new(ctx, settings: dict, my_username: str, known: set, seen: set) -> tuple[list, int, dict]:
    """新しいコメントを取る → (取り込むコメント, 次の回に回した数, {コメントID: has_replies})。seen は書き足す"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(settings["fresh_days"]))
    posts = get_my_posts(ctx.token, ctx.user_id, limit=int(settings["recent_posts"]))
    print(f"自分の最近の投稿 {len(posts)} 本のコメントを見る")
    found, has_replies, failed = [], {}, 0
    for post in posts:
        try:
            replies = get_replies(post["id"], ctx.token)
        except (ThreadsApiError, requests.RequestException) as e:
            failed += 1
            print(f"  コメントの取得に失敗（この投稿は次の回に）{post['id']}: {mask(e)}")
            continue
        for r in replies:
            rid = r.get("id")
            if not rid:
                continue
            has_replies[rid] = r.get("has_replies")
            if rid in seen or rid in known:
                continue
            if my_username and r.get("username") == my_username:
                seen.add(rid)  # 自分の返信は取り込まない
                continue
            ts = store.parse_ts(r.get("timestamp"))
            if ts and ts < cutoff:
                seen.add(rid)  # fresh_days より古いコメントは取り込まない
                continue
            found.append((ts or now, post, r))
    if posts and failed == len(posts):
        raise RuntimeError("どの投稿でもコメントを取れなかった（トークン切れか、トークンに threads_read_replies の権限が無い。"
                           "上のエラー文を確認）")

    found.sort(key=lambda x: x[0], reverse=True)
    take = found[:int(settings["max_comments_per_run"])]
    new = []
    for _, post, r in take:
        new.append({
            "comment_id": r["id"],
            "post_id": post["id"],
            "post_text": post.get("text", ""),
            "permalink": post.get("permalink", ""),
            "comment_permalink": r.get("permalink", ""),
            "username": r.get("username", ""),
            "comment_text": r.get("text", ""),
            "comment_ts": r.get("timestamp", ""),
            "drafts": [],
            "replied": False,
        })
        seen.add(r["id"])
        print(f"新着コメント: @{r.get('username') or '?'} → {one_line(r.get('text'), 40)}")
    return new, len(found) - len(take), has_replies


# ---------- 2. 返信済みの同期 ----------

def sync_replied(comments: list, has_replies: dict, token: str, my_username: str) -> int:
    """Threads のアプリから返信したコメントを「返信済み」にする。
    自分の返信があるかを聞くのは、下に返信が付いている（has_replies が false でない）コメントだけ"""
    if not my_username:
        return 0
    synced = 0
    for c in comments:
        if c.get("replied") or has_replies.get(c.get("comment_id"), False) is False:
            continue
        try:
            nested = get_replies(c["comment_id"], token, fields="id,username")
        except (ThreadsApiError, requests.RequestException) as e:
            print(f"  返信済みかの確認に失敗（次の回に）{c['comment_id']}: {mask(e)}")
            continue
        if any(r.get("username") == my_username for r in nested):
            c["replied"] = True
            synced += 1
    return synced


def keep(comments: list, fresh_days: int) -> list:
    """返信待ちは fresh_days 日以内のものだけ、返信済みは直近 KEEP_REPLIED 件だけ残す"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(fresh_days))
    open_ = [c for c in comments if not c.get("replied") and (store.parse_ts(c.get("comment_ts")) or now) >= cutoff]
    done = store.newest_first([c for c in comments if c.get("replied")])[:KEEP_REPLIED]
    return open_ + done


# ---------- 3. 分類（Jev） ----------

def claude_type(c: dict, claude: Claude) -> str | None:
    """Jev が迷った種類を Claude に判定し直してもらう"""
    menu = "／".join(f"{k}（{v}）" for k, v in TYPE_CRITERIA.items())
    prompt = f"""Threads の投稿に付いたコメントを、次の種類のどれか1つに分けてください。種類の名前だけを返してください。
種類: {menu}

【投稿】
{c.get("post_text", "")[:400]}

【コメント】
{c.get("comment_text", "")}"""
    text = claude.ask(prompt)
    return next((k for k in TYPE_CRITERIA if k in text), None)


def _num(value) -> float | None:
    return value if isinstance(value, (int, float)) else None


def classify(comments: list, claude: Claude) -> dict:
    """種類・感情が付いていない返信待ちのコメントを Jev で分ける → status の classify"""
    from lib import jev
    todo = [c for c in comments if not c.get("replied") and not labeled(c) and c.get("comment_id")]
    via = jev.backend()
    if not todo:
        return {"ok": True, "count": 0, "via": via}
    if not via:
        raise RuntimeError("Jev の鍵（TYPESAFE_API_KEY か AI_GATEWAY_API_KEY）が無い")
    print(f"Jev で {len(todo)} 件を分ける（{via}）")
    labels = jev.classify_comments([{"id": c["comment_id"], "post": c.get("post_text", ""),
                                     "comment": c.get("comment_text", "")} for c in todo])
    at = datetime.now(JST).isoformat(timespec="minutes")
    tokens, errors = 0, []
    for c in todo:
        lab = labels.get(c["comment_id"]) or {"error": "結果が無い"}
        if "error" in lab:
            c["label_error"] = lab["error"]
            errors.append(lab["error"])
            continue
        tokens += lab.get("tokens") or 0
        t, e = lab["type"], lab["emotion"]
        t_conf, e_conf = _num(t.get("confidence")), _num(e.get("confidence"))
        hostile = lab["hostile_p"] >= HOSTILE_P
        c.update({
            "type": "強い言い方" if hostile else t["choice"],
            "type_raw": t["choice"], "type_conf": t_conf, "type_probs": t.get("probabilities"),
            "type_source": "jev",
            "type_sure": hostile or (t_conf is not None and t_conf >= SURE),
            "emotion": e["choice"], "emotion_conf": e_conf, "emotion_probs": e.get("probabilities"),
            "emotion_sure": e_conf is not None and e_conf >= SURE,
            "hostile_p": lab["hostile_p"],
            "jev_model": lab.get("model"),
            "labeled_at": at,
        })
        c.pop("label_error", None)
        if not hostile and t_conf is not None and t_conf < ESCALATE and claude.usable:
            try:
                picked = claude_type(c, claude)
                if picked:
                    c.update({"type": picked, "type_source": "claude"})
                    print(f"  Jev が迷った（確信度 {t_conf:.2f}）→ Claude が「{picked}」と判定: {one_line(c.get('comment_text'), 30)}")
            except ClaudeStopped as ex:
                print(f"  Claude が使えないので、Jev の答えのまま: {ex}")
    if len(errors) == len(todo):
        raise RuntimeError(f"Jev の分類が全件失敗（{errors[0][:120]}）")
    print(f"  Jev: {len(todo) - len(errors)} 件を分けた（失敗 {len(errors)} 件・約 {tokens} トークン）")
    return {"ok": True, "count": len(todo) - len(errors), "failed": len(errors), "via": via, "tokens": tokens}


# ---------- 4. 返信案（Claude）と、その確かめ（Jev） ----------

def check_drafts(c: dict):
    """案を Jev で確かめ、作った体験・数字の確率が高い案を外して、外した数を返す（確かめられないときは None）"""
    from lib import jev
    c.pop("draft_flags", None)
    if not c.get("drafts") or not jev.backend():
        return None
    res = jev.check_drafts([{"id": "x", "post": c.get("post_text", ""), "comment": c.get("comment_text", ""),
                             "drafts": c["drafts"]}]).get("x")
    if not isinstance(res, list):
        print(f"  案の確かめに失敗（確かめなしで残す）: {res}")
        c["draft_flags"] = ["確かめられなかった"] * len(c["drafts"])
        return None
    kept = [(d, p) for d, p in zip(c["drafts"], res) if p < INVENTED_DROP]
    dropped = len(c["drafts"]) - len(kept)
    c["drafts"] = [d for d, _ in kept]
    c["draft_flags"] = ["体験や数字を足していないか確認" if p >= INVENTED_REVIEW else "" for _, p in kept]
    return dropped


def generate_reply_drafts(c: dict, claude: Claude, settings: dict, my_username: str) -> list[str]:
    """種類と感情に合わせて、Claude に返信案を作ってもらう"""
    n = DRAFT_COUNT.get(c.get("type"), 3) if labeled(c) else 3
    if n == 0:
        return []
    sure = lambda ok: "確か" if ok else "確かではない"
    view = c if labeled(c) else dict(c, type="（分類前）", emotion="（分類前）", type_sure=False, emotion_sure=False)
    who = f"@{my_username}" if my_username else "投稿の書き手"
    persona = str(settings.get("persona") or "").strip()
    prompt = f"""あなたは Threads で発信している人（{who}）の代わりに、コメントへの返信案を書きます。{f"この人の紹介: {persona}" if persona else ""}

【自分の投稿】
{c.get("post_text", "")[:300]}

【相手のコメント】
{c.get("comment_text", "")}

【このコメントの見立て】種類: {view.get("type")}（判定は{sure(view.get("type_sure"))}）／感情: {view.get("emotion")}（判定は{sure(view.get("emotion_sure"))}）
【返し方】{advice_for(c)}{"" if view.get("type_sure") else "判定が確かではないので、どの種類でも角が立たない返し方にする。"}

決まり:
- 自分の体験談・数字・出来事を作らない（投稿に書いてあること以外の経験を足さない）
- 相手を言い負かさない。上から教えない
- 各案 40〜80 文字。自然な口語。絵文字は使わない
- 案は{n}つ。それぞれ違う方向で
JSON 配列だけを返してください: ["案1", ...]"""
    for attempt in (1, 2):
        text = claude.ask(prompt).strip()
        start = text.find("[")
        try:
            drafts, _ = json.JSONDecoder().raw_decode(text[start:]) if start >= 0 else ([], 0)
            drafts = [str(d) for d in drafts if d][:n] if isinstance(drafts, list) else []
            if drafts:
                return drafts
        except ValueError:
            pass
        print(f"  返信案の形が読めない（{attempt}回目）: {text[:60]}")
    raise RuntimeError("返信案の形が2回とも読めない")


def draft_comments(comments: list, claude: Claude, settings: dict, my_username: str) -> int:
    """返信待ちで fresh_days 日以内のコメントに案を作る（1回に max_comments_per_run 件まで。新しい順）。
    分類済みなら返し方に合わせた案（drafts_v=2）。分類できていないものは一般的な返し方の案（drafts_v=1）で、
    あとで分類できたら作り直す。案を作れたコメントの数を返す"""
    now = datetime.now(timezone.utc)
    fresh_after = now - timedelta(days=int(settings["fresh_days"]))
    todo = [c for c in comments if not c.get("replied")
            and (store.parse_ts(c.get("comment_ts")) or now) >= fresh_after
            and ((labeled(c) and c.get("drafts_v") != 2) or (not labeled(c) and not c.get("drafts")))]
    todo = store.newest_first(todo)[:int(settings["max_comments_per_run"])]
    done = 0
    for c in todo:
        c["advice"] = advice_for(c)
        try:
            c["drafts"] = generate_reply_drafts(c, claude, settings, my_username)
            dropped = check_drafts(c)
            if dropped and not c["drafts"]:
                print(f"  案が全部「作った体験」と判定されて外れた。1回だけ作り直す @{c.get('username')}")
                c["drafts"] = generate_reply_drafts(c, claude, settings, my_username)
                check_drafts(c)
            elif dropped:
                print(f"  案を {dropped} 件外した（作った体験・数字の確率が高い）@{c.get('username')}")
            c["drafts_v"] = 2 if labeled(c) else 1
            c.pop("draft_error", None)
            done += 1 if c["drafts"] else 0
            print(f"  返信案: @{c.get('username')} [{c.get('type')}/{c.get('emotion')}] {len(c['drafts'])}件")
        except ClaudeStopped as e:
            print(f"  返信案づくりは、この回はここでやめる（次の回にまた試す）: {e}")
            break
        except Exception as e:
            c["draft_error"] = str(e)[:200]
            print(f"  返信案の生成エラー @{c.get('username')}: {e}")
    return done


# ---------- まとめ ----------

def _without_time(status: dict) -> dict:
    return {k: ({kk: vv for kk, vv in v.items() if kk != "at"} if isinstance(v, dict) else v) for k, v in status.items()}


def changed(ctx, comments: list, seen: set, status: dict) -> bool:
    """前の回から中身が変わったか（時刻だけの違いは数えない）。変わっていなければ書き換えない＝コミットが増えない"""
    return (store.newest_first(comments) != store.load_comments(ctx.data_dir)
            or seen != store.load_seen(ctx.data_dir)
            or _without_time(status) != _without_time(store.load_status(ctx.data_dir))
            or not (ctx.data_dir / store.REPORT_FILE).exists())


def run(ctx=None) -> bool:
    """分類が全部止まったら False を返す（取得と保存は済ませてある）"""
    if ctx is None:
        ctx = get_context()
    settings = load_settings(ctx)
    if not ctx.token or not ctx.user_id:
        raise RuntimeError("THREADS_ACCESS_TOKEN / THREADS_USER_ID が設定されていません。")
    ctx.data_dir.mkdir(parents=True, exist_ok=True)

    my_username = get_me(ctx.token).get("username", "")
    print(f"自分のユーザー名: @{my_username}")
    comments = store.load_comments(ctx.data_dir)
    seen = store.load_seen(ctx.data_dir)
    known = {c.get("comment_id") for c in comments}

    new, left, has_replies = fetch_new(ctx, settings, my_username, known, seen)
    comments += new
    synced = sync_replied(comments, has_replies, ctx.token, my_username)
    if synced:
        print(f"Threads のアプリから返信済みのコメント: {synced} 件を返信済みにした")
    comments = keep(comments, settings["fresh_days"])
    status = {"fetch": {"at": now_jst_label(), "new": len(new), "left": left, "synced": synced}}

    if os.getenv("GITHUB_ACTIONS") and not os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        off = "Secret の CLAUDE_CODE_OAUTH_TOKEN が無い"
    elif not shutil.which("claude"):
        off = "Claude Code が無い（GitHub では Secret の CLAUDE_CODE_OAUTH_TOKEN を入れると使う）"
    else:
        off = ""
    claude = Claude(str(settings["model"] or "opus"), off=off)
    try:
        status["classify"] = classify(comments, claude)
    except Exception as e:
        status["classify"] = {"ok": False, "reason": mask(e)[:200]}
        print(f"⚠️ コメントの分類が止まった（取得と保存は続ける）: {mask(e)}")

    if not settings["draft_replies"]:
        status["drafts"] = {"skipped": "設定の replies.draft_replies が false"}
    elif claude.off:
        status["drafts"] = {"skipped": claude.off}
    else:
        drafted = draft_comments(comments, claude, settings, my_username)
        status["drafts"] = {"ok": False, "reason": claude.failed} if claude.failed else {"ok": True, "count": drafted}
    if status["drafts"].get("skipped"):
        print(f"返信案は作らない: {status['drafts']['skipped']}")

    waiting = sum(1 for c in comments if not c.get("replied"))
    if not changed(ctx, comments, seen, status):
        print(f"変わりなし（返信待ち {waiting} 件・返信済み {len(comments) - waiting} 件。ファイルは書き換えない）")
        return status["classify"].get("ok") is not False
    store.save_comments(ctx.data_dir, comments)
    store.save_seen(ctx.data_dir, seen)
    store.save_status(ctx.data_dir, status)
    report = store.write_report(ctx, comments, status)
    print(f"✅ 保存: 返信待ち {waiting} 件・返信済み {len(comments) - waiting} 件（data/{ctx.data_dir.name}/{report.name}）")
    return status["classify"].get("ok") is not False


if __name__ == "__main__":
    ctx = get_context()
    try:
        ok = run(ctx)
    except Exception as e:
        print(f"❌ 失敗: {mask(e)}")
        sys.exit(1)
    if not ok:
        print("❌ 分類が止まっている（取得と保存はした。理由は replies.md のいちばん上）")
        sys.exit(1)
