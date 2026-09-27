"""
Threads のアクセストークンを延長する（Mac の launchd か VPS の cron が毎週呼ぶ。install_token_refresh.sh / install_vps.sh で入れる）

長期トークンは60日で切れ、切れたら延長できない（人が作り直すしかない）。
切れる前に refresh_access_token で60日延ばし、.env と GitHub Secrets の両方を差し替える。

失敗したら exit 1。環境変数 THREADS_ALERT_FILE（例: 自分の作業メモの .md）があれば、
そこへ「- [ ]」の行を1つ書く（同じ行は重ねない）。
使い方: python3 scripts/refresh_token.py --config configs/my_account.yml
"""
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.account_context import get_context
from lib.threads_api import ThreadsApiError, debug_token, refresh_long_lived
from lib import token_store

ALERT_MARK = "Threads のトークン"  # 未完了の行にこれがあれば重ねて起票しない


def alert(reason: str, config_path: str):
    path = os.getenv("THREADS_ALERT_FILE")
    if not path:
        return
    alert_file = Path(path)
    text = alert_file.read_text(encoding="utf-8") if alert_file.exists() else ""
    if any(l.startswith("- [ ]") and ALERT_MARK in l for l in text.splitlines()):
        print(f"通知: {alert_file} に未完了の行が既にある（重ねて起票しない）")
        return
    line = (f"- [ ] {ALERT_MARK}の自動延長が失敗（初回 {datetime.now(token_store.JST):%Y-%m-%d}）: {reason[:80]}。"
            f"切れる前なら再実行、切れていたら `cd {token_store.BASE} && python3 scripts/set_token.py --config {config_path}` "
            "で作り直す")
    lines = text.splitlines()
    at = next((i for i, l in enumerate(lines) if l.startswith("- [ ]")), len(lines))
    lines.insert(at, line)
    alert_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"通知: {alert_file} に起票した")


def run(ctx):
    token = token_store.read_env().get("THREADS_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(f"{token_store.ENV_FILE} に THREADS_ACCESS_TOKEN が無い")

    try:
        info = debug_token(token)
    except ThreadsApiError as e:
        if "code=190" in str(e):
            print(f"  {token_store.mask(e)}")
            raise RuntimeError("トークンが切れている（延長できないので作り直しが要る）")
        print(f"⚠️ debug_token で状態を確かめられなかった（延長は試す）: {token_store.mask(e)}")
        info = {}
    if info and not info.get("is_valid"):
        raise RuntimeError("トークンが無効（is_valid=false）。延長できないので作り直しが要る")
    if info.get("issued_at") and time.time() - info["issued_at"] < 24 * 3600:
        print("発行から24時間たっていないので延長は次回")
        return

    refreshed = refresh_long_lived(token)
    new_token = refreshed["access_token"]
    expires_in = int(refreshed.get("expires_in") or 0)
    if expires_in < 50 * 86400:
        raise RuntimeError(f"延長の返事の有効期間が50日未満（expires_in={expires_in}）")
    expires_at = int(time.time()) + expires_in

    token_store.write_env({
        "THREADS_ACCESS_TOKEN": new_token,
        "THREADS_TOKEN_EXPIRES_AT": token_store.iso_ts(expires_at),
    })
    repo = token_store.github_repo()
    name = token_store.secret_name(repo, "THREADS_ACCESS_TOKEN", ctx.name)
    token_store.set_github_secret(repo, name, new_token)
    print(f"延長OK: {token_store.preview(new_token)} / 期限 {token_store.fmt_ts(expires_at)}"
          f" / .env と {repo} の {name} を更新")


if __name__ == "__main__":
    ctx = get_context()
    print(f"=== {datetime.now(token_store.JST):%Y-%m-%d %H:%M} refresh_token ({ctx.name})")
    try:
        run(ctx)
    except subprocess.CalledProcessError as e:
        reason = f"{' '.join(e.cmd[:3])} が失敗: {token_store.mask(e.stderr or '').strip()[:200]}"
        print(f"❌ {reason}")
        alert(reason, str(ctx.config_path))
        sys.exit(1)
    except Exception as e:
        reason = token_store.mask(e)[:300]
        print(f"❌ {reason}")
        alert(reason, str(ctx.config_path))
        sys.exit(1)
