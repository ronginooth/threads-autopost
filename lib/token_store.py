"""
トークンの置き場（set_token.py / refresh_token.py の共通処理）

- ローカル: リポジトリ直下の .env（.gitignore 済み・600権限）
- GitHub: Actions の Secret（gh CLI で入れる。値は標準入力で渡す）

トークンの値は画面・ログに出さない。見せてよいのは先頭4文字と長さだけ。
"""
import os
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ENV_FILE = BASE / ".env"
JST = timezone(timedelta(hours=9))

_SECRET_PARAMS = re.compile(r"((?:access_token|input_token|client_secret)=)[^&\s'\"]+")


def mask(text: str) -> str:
    """エラー文に混ざったトークン（URLの引数）を伏せる"""
    return _SECRET_PARAMS.sub(r"\1***", str(text))


def preview(token: str) -> str:
    return f"{token[:4]}…（{len(token)}文字）"


def read_env() -> dict:
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def write_env(updates: dict):
    """.env の該当キーだけ書き換える（無ければ足す）。ほかの行はそのまま"""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)


def github_repo() -> str:
    """origin の URL から owner/repo を取る"""
    url = subprocess.run(["git", "-C", str(BASE), "remote", "get-url", "origin"],
                         capture_output=True, text=True, check=True).stdout.strip()
    match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not match:
        raise RuntimeError(f"origin が GitHub ではない: {mask(url)}")
    return match.group(1)


def secret_name(repo: str, base: str, account: str) -> str:
    """ワークフローが読む Secret 名。アカウント固有の名前があればそちら（lib/account_context.py と同じ順）"""
    specific = f"{base}_{account.upper()}"
    names = subprocess.run(["gh", "secret", "list", "-R", repo],
                           capture_output=True, text=True, check=True).stdout
    return specific if re.search(rf"^{specific}\s", names, re.MULTILINE) else base


def set_github_secret(repo: str, name: str, value: str):
    subprocess.run(["gh", "secret", "set", name, "-R", repo],
                   input=value, text=True, capture_output=True, check=True)


def fmt_ts(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, JST).strftime("%Y-%m-%d %H:%M")


def iso_ts(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, JST).isoformat(timespec="minutes")
