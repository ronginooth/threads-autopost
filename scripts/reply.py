"""
コメントに返信する（reply.yml が呼ぶ。Actions の「Threads コメントに返信」→「Run workflow」の入力から）

- 返信先は comments.json にあるコメントだけ（ID の打ち間違いで、よその投稿に返信しないため）
- 同じコメントに同じ文は2回出さない（ボタンの押し直しで二重にならないため）
- 出したら、そのコメントを返信済みにして replies.md を作り直す

使い方: python3 scripts/reply.py --config configs/my_account.yml --comment-id 1234567890 --text "返信の文"
"""
import argparse
import re
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.account_context import AccountContext
from lib.threads_api import create_reply, publish_post
from lib.token_store import mask
from lib import replies as store

MAX_CHARS = 500  # Threads の1投稿の上限


def send(ctx, comment_id: str, text: str) -> str | None:
    """返信して thread_id を返す（同じ文をもう出していれば何もせず None）"""
    comment_id, text = comment_id.strip(), text.strip()
    if not ctx.token or not ctx.user_id:
        raise RuntimeError("THREADS_ACCESS_TOKEN / THREADS_USER_ID が設定されていません。")
    if not re.fullmatch(r"\d+", comment_id):
        raise RuntimeError(f"コメントIDは数字だけ（入力: {comment_id[:40]}）。replies.md の「コメントID」をそのまま貼る")
    if not text:
        raise RuntimeError("返信の文が空")
    if len(text) > MAX_CHARS:
        raise RuntimeError(f"返信の文が {len(text)} 文字。{MAX_CHARS} 文字までにする")

    comments = store.load_comments(ctx.data_dir)
    target = next((c for c in comments if str(c.get("comment_id")) == comment_id), None)
    if target is None:
        raise RuntimeError(f"comments.json に無いコメントID（{comment_id}）。replies.md の「コメントID」をそのまま貼る"
                           "（よその投稿に返信しないよう、取り込んだコメントにだけ返信する）")
    if (target.get("replied_text") or "").strip() == text:
        print("同じコメントに同じ文をもう出している。二重には出さない")
        return None

    print(f"返信先: @{target.get('username') or '?'}「{store.one_line(target.get('comment_text'), 40)}」")
    print(f"返信の文: {text}")
    creation_id = create_reply(text, comment_id, ctx.token, ctx.user_id)
    thread_id = publish_post(creation_id, ctx.token, ctx.user_id)
    print(f"✅ 返信した thread_id: {thread_id}")

    target.update({
        "replied": True,
        "replied_text": text,
        "replied_thread_id": thread_id,
        "replied_at": datetime.now(store.JST).isoformat(timespec="minutes"),
    })
    store.save_comments(ctx.data_dir, comments)
    store.write_report(ctx, comments, store.load_status(ctx.data_dir))
    print(f"comments.json と replies.md を更新した（data/{ctx.data_dir.name}/）")
    return thread_id


def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="設定ファイル（例: configs/my_account.yml）")
    parser.add_argument("--comment-id", required=True, help="返信先のコメントID（replies.md に出ている番号）")
    parser.add_argument("--text", required=True, help=f"返信の文（{MAX_CHARS}文字まで）")
    args = parser.parse_args(argv)
    return send(AccountContext(args.config), args.comment_id, args.text)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"❌ 返信できなかった: {mask(e)}")
        if "code=10 " in f"{e} " or "permission" in str(e).lower():
            print("   トークンに threads_manage_replies の権限が無い可能性。Meta のアプリの権限に足してトークンを作り直し、"
                  "set_token.py をもう一度")
        sys.exit(1)
