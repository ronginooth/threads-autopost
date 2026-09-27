"""
Threads のアクセストークンを登録する（切れたとき・作り直したときに、人が1回だけ実行する）

1. 貼り付けたトークンを Threads API で確かめる（アカウント・権限・有効期限）
2. 短期トークン（1時間）なら、アプリシークレットを聞いて長期トークン（60日）に交換する
3. ローカルの .env と GitHub Secrets の両方に入れる
4. data/<アカウント>/KILL_SWITCH があれば GitHub 上から消して、投稿を再開する
5. 統計収集を1回走らせる（再投稿の順位づけを新しい数字にする）

トークンは画面に出さない（先頭4文字と長さだけ）。以後の延長は refresh_token.py が毎週やる。
使い方: python3 scripts/set_token.py --config configs/my_account.yml
"""
import getpass
import re
import subprocess
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.account_context import get_context
from lib.threads_api import ThreadsApiError, get_me, debug_token, exchange_long_lived
from lib import token_store

NEED_SCOPES = {"threads_basic", "threads_content_publish"}


def check(token: str, expected_username: str) -> dict:
    me = get_me(token)
    if expected_username and me.get("username") != expected_username:
        raise RuntimeError(f"別のアカウント（@{me.get('username')}）のトークン。@{expected_username} で作り直す")
    try:
        info = debug_token(token)
    except ThreadsApiError as e:
        print(f"⚠️ 有効期限を確かめられなかった（アカウントは確認済みなので登録は続ける）: {token_store.mask(e)}")
        info = {}
    if info and not info.get("is_valid"):
        raise RuntimeError("トークンが無効（is_valid=false）")
    scopes = set(info.get("scopes") or [])
    if scopes and NEED_SCOPES - scopes:
        raise RuntimeError(f"権限が足りない: {', '.join(sorted(NEED_SCOPES - scopes))}")
    info["username"] = me.get("username")
    info["user_id"] = me.get("id")
    return info


def remove_kill_switch(repo: str, account: str):
    path = f"repos/{repo}/contents/data/{account}/KILL_SWITCH"
    found = subprocess.run(["gh", "api", path, "-q", ".sha"], capture_output=True, text=True)
    if found.returncode != 0:
        print("KILL_SWITCH: なし（投稿は止まっていない）")
        return
    subprocess.run(["gh", "api", "-X", "DELETE", path,
                    "-f", "message=chore: トークン更新で投稿を再開（KILL_SWITCH を外す）",
                    "-f", f"sha={found.stdout.strip()}"],
                   capture_output=True, text=True, check=True)
    print("KILL_SWITCH: GitHub 上から消した（15分以内の自動実行から投稿が再開する）")


def run(ctx):
    env = token_store.read_env()
    raw = getpass.getpass("Threads のアクセストークンを貼り付けて Enter（画面には出ません）: ")
    # 貼り付けの目印（ESC[200~ / ESC[201~）や改行・空白が混ざっても外す。トークンに空白は無い
    token = re.sub(r"\s+", "", re.sub(r"\x1b\[20[01]~", "", raw))
    if not token:
        raise RuntimeError("何も貼られていない")
    print(f"受け取った文字列: {token_store.preview(token)}（Threads のトークンは TH で始まり200文字前後）")
    if "set_token.py" in token or token.startswith("cd/"):
        # よくある間違い: コマンドをコピーしたあとに貼ったので、トークンの代わりにコマンドが入った
        raise RuntimeError("貼られたのはコマンドの文字（クリップボードがコマンドのまま）。"
                           "先にこのコマンドを動かし、待っている間に Meta でトークンをコピーして貼る")

    username = ctx.account.lstrip("@")
    info = check(token, username)
    if info.get("expires_at") and info["expires_at"] - time.time() < 2 * 86400:
        print("これは短期トークン（1時間）。長期トークン（60日）に交換する。")
        secret = getpass.getpass("Threads のアプリシークレットを貼り付けて Enter（画面には出ません・保存しません）: ").strip()
        token = exchange_long_lived(token, secret)["access_token"]
        info = check(token, username)

    expires = token_store.fmt_ts(info["expires_at"]) if info.get("expires_at") else "不明"
    print(f"確認OK: @{info['username']} / {token_store.preview(token)} / 期限 {expires}")

    # アプリを作り直すとユーザーIDの番号が変わることがある。変わっていれば一緒に入れ替える
    old_user_id = env.get("THREADS_USER_ID") or ctx.user_id
    user_id_changed = bool(info["user_id"]) and info["user_id"] != old_user_id
    updates = {
        "THREADS_ACCESS_TOKEN": token,
        "THREADS_TOKEN_EXPIRES_AT": token_store.iso_ts(info["expires_at"]) if info.get("expires_at") else "",
    }
    if user_id_changed:
        updates["THREADS_USER_ID"] = info["user_id"]
    token_store.write_env(updates)
    print(f".env: 更新した（{token_store.ENV_FILE}）")

    repo = token_store.github_repo()
    name = token_store.secret_name(repo, "THREADS_ACCESS_TOKEN", ctx.name)
    token_store.set_github_secret(repo, name, token)
    print(f"GitHub Secrets: {repo} の {name} を更新した")
    if user_id_changed:
        id_name = token_store.secret_name(repo, "THREADS_USER_ID", ctx.name)
        token_store.set_github_secret(repo, id_name, info["user_id"])
        print(f"GitHub Secrets: ユーザーIDが前と違ったので {id_name} も更新した（アプリが違うと番号が変わる）")

    remove_kill_switch(repo, ctx.name)
    subprocess.run(["gh", "workflow", "run", "stats.yml", "-R", repo], capture_output=True, text=True, check=True)
    print("統計収集: 1回走らせた（数分で stats.csv が新しくなる）")


if __name__ == "__main__":
    ctx = get_context()
    try:
        run(ctx)
    except subprocess.CalledProcessError as e:
        print(f"❌ 失敗: {' '.join(e.cmd[:3])} … {token_store.mask(e.stderr or '')}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 失敗: {token_store.mask(e)}")
        if "Session has expired" in str(e):
            print("   このトークンは期限切れ。Meta の画面で作り直した新しいトークンを貼る")
        elif "code=190" in str(e):
            print("   トークンとして読めない（途中で切れた・別の文字列が入った）。「アクセストークンを生成」で出た文字列をコピーし直して貼る")
        sys.exit(1)
