"""
返信案づくり（replies.yml）が使う Claude のサブスク用トークンを確かめて、GitHub の Secrets に入れる（人が1回だけ実行する）

- トークンは `claude setup-token` で作る1年ものの CLAUDE_CODE_OAUTH_TOKEN（sk-ant-oat で始まる。API キーではないので従量課金にならない）
- 貼り付けを待つ（画面には出ない）。--from <ファイル> なら、そのファイルに書いてある CLAUDE_CODE_OAUTH_TOKEN を使う
- 入れる前に、この手元の claude（Claude Code）で1回だけ試し、通ったものだけ登録する。モデルは設定の replies.model
- 登録したら replies.yml を1回走らせる

使い方: python3 scripts/set_claude_token.py [--config configs/my_account.yml] [--from <ファイル>]
"""
import argparse
import getpass
import json
import os
import re
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from lib import token_store

SECRET_NAME = "CLAUDE_CODE_OAUTH_TOKEN"


def read_token(source: str | None) -> str:
    if source:
        text = Path(source).expanduser().read_text(encoding="utf-8")
        match = re.search(r"CLAUDE_CODE_OAUTH_TOKEN=[\"']?([^\"'\s]+)", text)
        if not match:
            raise RuntimeError(f"{source} に CLAUDE_CODE_OAUTH_TOKEN が無い")
        return match.group(1)
    raw = getpass.getpass("Claude のトークン（claude setup-token で出たもの）を貼り付けて Enter（画面には出ません）: ")
    return re.sub(r"\s+", "", re.sub(r"\x1b\[20[01]~", "", raw))


def model_of(config: str) -> str:
    path = Path(config)
    if not path.exists():
        return "opus"
    settings = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("replies") or {}
    return str(settings.get("model") or "opus")


def try_token(token: str, model: str):
    """このトークンだけで claude -p が通るかを1回試す（API キーは外す）"""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    try:
        res = subprocess.run(
            ["claude", "-p", "OKとだけ返して", "--output-format", "json", "--model", model,
             "--tools", "", "--permission-mode", "dontAsk", "--no-session-persistence"],
            capture_output=True, text=True, timeout=180, env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("claude（Claude Code）が無い。claude setup-token を実行した場所でもう一度動かす")
    try:
        out = json.loads(res.stdout)
    except ValueError:
        raise RuntimeError(f"claude -p の出力が読めない（exit {res.returncode}）: {(res.stderr or res.stdout)[:200]}")
    if out.get("is_error"):
        raise RuntimeError(f"この鍵では Claude（{model}）が使えない: {str(out.get('result'))[:200]}")


def run(source: str | None, config: str):
    token = read_token(source)
    if not token:
        raise RuntimeError("何も貼られていない")
    print(f"受け取った文字列: {token_store.preview(token)}（sk-ant-oat で始まる）")
    if not token.startswith("sk-ant-oat"):
        raise RuntimeError("サブスク用のトークン（sk-ant-oat…）ではない。API キー（sk-ant-api…）は使わない")

    model = model_of(config)
    try_token(token, model)
    print(f"確認OK: このトークンで Claude（{model}）が1回答えた")

    repo = token_store.github_repo()
    if token_store.repo_is_private(repo) is False:
        print(f"⚠️ {repo} は公開のリポジトリ。コメントの機能は非公開のリポジトリでだけ動く（鍵は入れておくが、動かない）。"
              "Settings → General のいちばん下の Change visibility で Private にする")
    token_store.set_github_secret(repo, SECRET_NAME, token)
    print(f"GitHub Secrets: {repo} の {SECRET_NAME} を登録した")
    subprocess.run(["gh", "workflow", "run", "replies.yml", "-R", repo], capture_output=True, text=True, check=True)
    print("返信案づくり: GitHub で1回走らせた（数分後に Actions の「Threads コメントの分類と返信案」で様子が見える。"
          "Jev の鍵がまだなら、鍵が足りないと出て終わる）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", help="CLAUDE_CODE_OAUTH_TOKEN が書いてあるファイル")
    parser.add_argument("--config", default="configs/my_account.yml", help="設定ファイル（replies.model を読む）")
    args = parser.parse_args()
    try:
        run(args.source, args.config)
    except subprocess.CalledProcessError as e:
        print(f"❌ 失敗: {' '.join(e.cmd[:3])} … {token_store.mask(e.stderr or '')}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 失敗: {token_store.mask(e)}")
        sys.exit(1)
