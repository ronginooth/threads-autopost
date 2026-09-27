"""
コメントの置き場と、人が読むレポート（fetch_replies.py / reply.py の共通処理）

置き場（data/<アカウント>/）:
- comments.json      取り込んだコメント（新しい順）。種類・感情・返信案もここに入る
- seen_comments.json 見たコメントの ID（同じコメントを2回取り込まない）
- status.json        中身が変わった最後の回の様子（取り込んだ数・分類と返信案が止まった理由）
- replies.md         人が読むレポート。GitHub で開くと表示が整う（毎回作り直す）

他人のコメントが入るので、非公開のリポジトリでだけ使う（replies.yml と reply.yml の最初の段が確かめる）
"""
import json
import re
from datetime import datetime, timedelta, timezone
from itertools import zip_longest

JST = timezone(timedelta(hours=9))

COMMENTS_FILE = "comments.json"
SEEN_FILE = "seen_comments.json"
STATUS_FILE = "status.json"
REPORT_FILE = "replies.md"

# 種類ごとの返し方（Jev の分類に合わせる。返信案の頼み方と replies.md の両方に使う）
TYPE_GUIDE = {
    "共感・応援": "お礼を伝え、相手の言葉を1つ拾って返す。短く温かく。",
    "質問": "質問にまっすぐ答える。分からないことは分からないと言う。長くしない。",
    "体験の共有": "相手の体験を受け止め、共通点を一言添えて、軽く問い返す。",
    "補足・情報": "お礼を伝え、学びになった点を一言返す。",
    "反論・指摘": "まず受け止める。正しい点は認める。違う点は1つだけ穏やかに。言い負かそうとしない。",
    "強い言い方": "返信しなくてよい。返すなら短く中立に。言い返さない。",
    "宣伝・無関係": "返信しなくてよい。",
    "その他": "角が立たないように、短く返す。",
}
EMOTION_GUIDE = {
    "喜び・楽しさ": "明るく、相手の楽しさに乗る。",
    "感謝・尊敬": "素直にお礼を言う。謙遜しすぎない。",
    "共感・安心": "「同じですね」の気持ちを返す。",
    "不安・悩み": "まず寄り添う。助言は押しつけず、1つだけ。",
    "驚き・関心": "興味に応えて、一言だけ補足する。",
    "不満・苛立ち": "まず受け止める。言い返さない。",
    "落ち着き": "落ち着いた調子で短く返す。",
}
DRAFT_COUNT = {"強い言い方": 1, "宣伝・無関係": 0}  # ここに無い種類は3案
NOT_LABELED = "分類がまだです。角が立たないように、短く返してください。"


def labeled(c: dict) -> bool:
    return bool(c.get("type")) and not c.get("label_error")


def advice_for(c: dict) -> str:
    """返し方の方針（1〜2文）。感情は確かなときだけ足す"""
    if not labeled(c):
        return NOT_LABELED
    t, e = c.get("type"), c.get("emotion")
    parts = [TYPE_GUIDE.get(t, TYPE_GUIDE["その他"])]
    if e in EMOTION_GUIDE and c.get("emotion_sure") and t not in DRAFT_COUNT:
        parts.append(EMOTION_GUIDE[e])
    return "".join(parts)


# ---------- 置き場 ----------

def _read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def load_comments(data_dir) -> list:
    return _read_json(data_dir / COMMENTS_FILE, [])


def save_comments(data_dir, comments: list):
    (data_dir / COMMENTS_FILE).write_text(json.dumps(newest_first(comments), ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")


def load_seen(data_dir) -> set:
    return set(_read_json(data_dir / SEEN_FILE, []))


def save_seen(data_dir, seen: set):
    # 並びを決めておく（毎回の差分を小さくする）
    (data_dir / SEEN_FILE).write_text(json.dumps(sorted(seen)) + "\n", encoding="utf-8")


def load_status(data_dir) -> dict:
    return _read_json(data_dir / STATUS_FILE, {})


def save_status(data_dir, status: dict):
    (data_dir / STATUS_FILE).write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ---------- 時刻 ----------

def parse_ts(value) -> datetime | None:
    """Threads の時刻（例: 2026-09-27T04:58:00+0000）を読む。読めなければ None"""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    for parse in (datetime.fromisoformat, lambda v: datetime.strptime(v, "%Y-%m-%dT%H:%M:%S%z")):
        try:
            dt = parse(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def newest_first(comments: list) -> list:
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(comments, key=lambda c: parse_ts(c.get("comment_ts")) or oldest, reverse=True)


def jst_label(value) -> str:
    dt = parse_ts(value)
    return dt.astimezone(JST).strftime("%m/%d %H:%M") if dt else "時刻不明"


# ---------- Markdown ----------

_ASCII_PUNCT = re.compile(r"([!-/:-@\[-`{-~])")
_SAFE_URL = re.compile(r"^https://[^\s<>]+$")


def md_escape(text) -> str:
    """他人の書いた文を、Markdown の記号として読まれないようにする（リンク・見出し・数式・HTML にならない）"""
    return _ASCII_PUNCT.sub(r"\\\1", str(text or ""))


def one_line(text, n: int) -> str:
    first = next((l.strip() for l in str(text or "").splitlines() if l.strip()), "")
    return first if len(first) <= n else first[:n] + "…"


def quote(text) -> list[str]:
    """引用（>）の行にする。改行はそのまま見せる"""
    lines = [l.strip() for l in str(text or "").strip().splitlines()] or ["（本文なし。画像などのコメント）"]
    out = []
    for i, line in enumerate(lines):
        if not line:
            out.append(">")
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        out.append("> " + md_escape(line) + ("\\" if nxt else ""))
    return out


def _conf(value) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "不明"


def label_line(c: dict) -> str:
    if c.get("label_error"):
        return f"種類 判定できず（{md_escape(one_line(c['label_error'], 80))}。次の回にまた分けます）"
    if not labeled(c):
        return "種類 （分類前）"
    t = c["type"]
    if c.get("type_source") == "claude":
        kind = f"種類 **{t}**（Jev の確信度が {_conf(c.get('type_conf'))} と低いので、Claude が判定）"
    elif t == "強い言い方" and c.get("type_raw") != "強い言い方":
        kind = f"種類 **{t}**（強い言い方の確率 {_conf(c.get('hostile_p'))}）"
    else:
        kind = f"種類 **{t}**（確信度 {_conf(c.get('type_conf'))}）"
    parts = [kind]
    if c.get("emotion"):
        parts.append(f"感情 **{c['emotion']}**（確信度 {_conf(c.get('emotion_conf'))}）")
    if not (c.get("type_sure") and c.get("emotion_sure")):
        parts.append("判定を確かめて")
    return "・".join(parts)


def link_line(c: dict) -> str:
    post = md_escape(one_line(c.get("post_text"), 40)) or "（本文なし）"
    line = f"投稿「{post}」へのコメント"
    for key, label in (("comment_permalink", "コメントを Threads で開く"), ("permalink", "投稿を Threads で開く")):
        url = c.get(key) or ""
        if _SAFE_URL.match(url):
            return f"{line}・[{label}](<{url}>)"
    return line


def comment_block(c: dict) -> list[str]:
    user = f"@{md_escape(c['username'])}" if c.get("username") else "（名前が見えないアカウント）"
    out = [f"### {user}（{jst_label(c.get('comment_ts'))}）", "", label_line(c), ""]
    out += quote(c.get("comment_text"))
    out += ["", link_line(c), ""]
    if c.get("replied"):
        text = c.get("replied_text") or "（Threads のアプリから返信済み）"
        out += [f"**返信**: {md_escape(text)}", ""]
        return out

    guide = c.get("advice") or advice_for(c)
    head = "返信しないのがおすすめ" if c.get("type") == "強い言い方" and labeled(c) else "返し方"
    out += [f"**{head}**: {md_escape(guide)}", ""]
    drafts = [(d, f) for d, f in zip_longest(c.get("drafts") or [], c.get("draft_flags") or [], fillvalue="") if d]
    if drafts:
        out += ["**返信案**", ""]
        for i, (d, flag) in enumerate(drafts, 1):
            note = f"（{md_escape(flag)}）" if flag else ""
            out.append(f"{i}. {md_escape(' '.join(str(d).split()))}{note}")
        out.append("")
    elif c.get("draft_error"):
        out += [f"返信案を作れなかった（次の回にまた作ります）: {md_escape(one_line(c['draft_error'], 120))}", ""]
    elif labeled(c) and DRAFT_COUNT.get(c.get("type")) == 0:
        out += ["返信案: なし（返信しなくてよい種類）", ""]
    out += ["コメントID（返信するときに使う）", "", "```text", str(c.get("comment_id", "")), "```", ""]
    return out


def status_lines(status: dict) -> list[str]:
    out = []
    fetch = status.get("fetch") or {}
    if fetch:
        line = f"- {fetch.get('at', '')} の回: 新しいコメント {fetch.get('new', 0)}件"
        if fetch.get("left"):
            line += f"（ほかに {fetch['left']}件は次の回に取り込みます）"
        out.append(line)
    cl = status.get("classify") or {}
    if cl.get("ok") is False:
        out.append(f"- **分類が止まっている**: {md_escape(cl.get('reason', ''))}")
    elif cl.get("count"):
        via = {"typesafe": "TypeSafe に直接", "gateway": "Vercel AI Gateway 経由"}.get(cl.get("via"), cl.get("via"))
        out.append(f"- 分類: Jev（{via}）で {cl['count']}件・約 {cl.get('tokens') or 0:,}トークン")
    dr = status.get("drafts") or {}
    if dr.get("ok") is False:
        out.append(f"- **返信案を作れなかった**（次の回にまた試します）: {md_escape(dr.get('reason', ''))}")
    elif dr.get("skipped"):
        out.append(f"- 返信案は作っていない: {md_escape(dr['skipped'])}")
    elif dr.get("count"):
        out.append(f"- 返信案: {dr['count']}件のコメントに作った")
    return out


def write_report(ctx, comments: list, status: dict):
    """replies.md を作り直す（新しい順。返信待ち → 返信済み）"""
    open_ = newest_first([c for c in comments if not c.get("replied")])
    done = newest_first([c for c in comments if c.get("replied")])
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    out = [
        f"# コメントと返信案（{md_escape(ctx.account)}）",
        "",
        f"更新 {now}（日本時間）・返信待ち {len(open_)}件・返信済み {len(done)}件",
        "",
        "このファイルは自動で作り直します（手で直しても、次の回で消えます）。"
        "返信は、Actions の「Threads コメントに返信」→「Run workflow」に、コメントIDと返信の文を入れて出します。",
        "",
    ]
    lines = status_lines(status)
    if lines:
        out += lines + [""]
    out += [f"## 返信待ち（{len(open_)}件）", ""]
    if not open_:
        out += ["いまはありません。", ""]
    for c in open_:
        out += comment_block(c)
    out += [f"## 返信済み（直近 {len(done)}件）", ""]
    if not done:
        out += ["まだありません。", ""]
    for c in done:
        out += comment_block(c)
    path = ctx.data_dir / REPORT_FILE
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return path
