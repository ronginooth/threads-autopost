"""
投稿スクリプト
data/<アカウント>/queue/ にある .md のうち、予約の時刻（scheduled:）を過ぎた一番古い1件を Threads に出す

安全装置:
- KILL_SWITCH: data/<アカウント>/KILL_SWITCH というファイルがあれば、全部止める（消せば再開）
- 1日の上限: 設定の max_daily_posts 本（無ければ times の数）を超えたら、その日は出さない
- 最低の間隔: 設定の min_interval_minutes 分（無ければ60分）たっていなければ出さない
- 二重投稿の防止: 同じ本文が直近6時間に出ていれば、出さずに記録だけ直す
"""
import os
import re
import shutil
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

JST = timezone(timedelta(hours=9))
from lib.threads_api import create_post, publish_post, get_my_posts
from lib.account_context import get_context



def load_log(log_file) -> list:
    if log_file.exists():
        return json.loads(log_file.read_text())
    return []


def save_log(log: list, log_file):
    log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2))


def parse_scheduled_time(path: Path):
    """frontmatterのscheduled:フィールドをJSTのdatetimeとして返す。なければNone"""
    content = path.read_text(encoding="utf-8")
    match = re.search(r'^scheduled:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', content, re.MULTILINE)
    if match:
        try:
            return datetime.strptime(match.group(1).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=JST)
        except ValueError:
            return None
    return None


def get_next_post(queue_dir) -> Path | None:
    """スケジュール時刻を過ぎた最も古いファイルを返す"""
    now = datetime.now(JST)
    files = sorted(queue_dir.glob("*.md"))
    for f in files:
        scheduled = parse_scheduled_time(f)
        if scheduled is None or scheduled <= now:
            return f
    print(f"投稿時刻未到達のためスキップ（現在 {now.strftime('%H:%M JST')}）")
    return None


def parse_post_file(path: Path) -> str:
    """マークダウンファイルから投稿テキストを抽出（frontmatterを除く）"""
    content = path.read_text(encoding="utf-8")
    # frontmatter（---...---）を除去
    content = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL)
    return content.strip()


def already_posted(text: str, token: str, user_id: str, within_hours: int = 6) -> str | None:
    """同じ本文が直近 within_hours 時間に Threads へ出ていれば、その thread_id を返す。
    前の実行が投稿した後に記録の書き戻しに失敗すると、キューに同じファイルが残る。二重に出さないための確認"""
    norm = lambda t: re.sub(r"\s+", "", t or "")
    since = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    for p in get_my_posts(token, user_id, limit=5):
        ts = datetime.strptime(p.get("timestamp", "")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc) if p.get("timestamp") else None
        if ts and ts >= since and norm(p.get("text")) == norm(text):
            return p["id"]
    return None


def check_kill_switch(kill_switch) -> bool:
    """KILL_SWITCHファイルが存在したらTrueを返す"""
    if kill_switch.exists():
        print("🛑 KILL_SWITCH が有効です。全投稿を停止中。")
        print(f"   解除するには {kill_switch} を削除してください。")
        return True
    return False


def check_daily_limit(log_file, limit: int) -> bool:
    """今日の投稿数が上限を超えていたらTrueを返す"""
    log = load_log(log_file)
    today = datetime.now(JST).strftime("%Y-%m-%d")
    today_count = sum(1 for e in log if e["posted_at"][:10] == today)
    if today_count >= limit:
        print(f"⚠️ 本日の投稿上限に達しました（{today_count}/{limit}件）")
        return True
    return False


def check_min_interval(log_file, minutes: int) -> bool:
    """前回投稿からの間隔が短すぎたらTrueを返す"""
    log = load_log(log_file)
    if not log:
        return False
    last_posted = log[-1].get("posted_at", "")
    if not last_posted:
        return False
    try:
        last_time = datetime.fromisoformat(last_posted)
        now = datetime.now()
        diff_minutes = (now - last_time).total_seconds() / 60
        if diff_minutes < minutes:
            print(f"⏳ 前回投稿から{diff_minutes:.0f}分。最低{minutes}分の間隔が必要です。")
            return True
    except Exception:
        pass
    return False


def run(ctx=None):
    if ctx is None:
        ctx = get_context()

    if not ctx.token or not ctx.user_id:
        raise RuntimeError("THREADS_ACCESS_TOKEN / THREADS_USER_ID が設定されていません。")

    # 安全チェック
    if check_kill_switch(ctx.kill_switch):
        return
    if check_daily_limit(ctx.log_file, ctx.max_daily_posts):
        return
    if check_min_interval(ctx.log_file, ctx.min_interval_minutes):
        return

    post_file = get_next_post(ctx.queue_dir)
    if not post_file:
        print("キューに投稿がありません。")
        return

    text = parse_post_file(post_file)
    if not text:
        print(f"テキストが空です: {post_file.name}")
        return

    print(f"投稿中: {post_file.name}")
    print(f"本文:\n{text}\n")

    try:
        thread_id = already_posted(text, ctx.token, ctx.user_id)
        if thread_id:
            print(f"⚠️ 同じ本文が直近6時間に出ている（前の実行の記録が残っていなかった）。二重には出さず、記録だけ直す thread_id: {thread_id}")
        else:
            creation_id = create_post(text, ctx.token, ctx.user_id)
            thread_id = publish_post(creation_id, ctx.token, ctx.user_id)
            print(f"✅ 投稿完了 thread_id: {thread_id}")

        # ログに記録
        log = load_log(ctx.log_file)
        log.append({
            "thread_id": thread_id,
            "file": post_file.name,
            "text": text,
            "posted_at": datetime.now().isoformat(),
        })
        save_log(log, ctx.log_file)

        # ファイルをpostedに移動
        dest = ctx.posted_dir / post_file.name
        shutil.move(str(post_file), str(dest))
        print(f"📁 {post_file.name} → posted/")

    except Exception as e:
        print(f"❌ 投稿失敗: {e}")
        raise


if __name__ == "__main__":
    ctx = get_context()
    try:
        run(ctx)
    except Exception:
        sys.exit(1)
