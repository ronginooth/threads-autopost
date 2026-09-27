"""
コメントの分類（Jev）の鍵を確かめて、GitHub の Secrets に入れる（人が1回だけ実行する）

道は2つ。両方の鍵があれば TypeSafe に直接つなぐ（lib/jev.py）
- TypeSafe に直接: 鍵 TYPESAFE_API_KEY（https://console.typesafe.ai/keys で作る）
- Vercel AI Gateway 経由（--gateway）: 鍵 AI_GATEWAY_API_KEY（Vercel の AI Gateway の画面で作る。Node.js 22 以上が要る）

1. 鍵を貼り付けてもらう（画面には出ない）。--from <ファイル> なら、そのファイル（.env の形）から読む。
   あわせて、手元の Threads の鍵にコメントを読む・返信する権限（threads_read_replies・threads_manage_replies）があるかを見る
2. その鍵で、見本のコメント1件を実際に分けてみる（通らなければ登録しない）
3. 手元の .env（--from のときは書かない）と GitHub の Secrets に入れる
4. replies.yml を1回走らせる（非公開のリポジトリなら、コメントの取得と分類が始まる）

Jev の料金は入力100万トークンあたり0.042ドル（出力は無料）。コメント1件の分類は約1,500トークン。
使い方:
  python3 scripts/set_jev_key.py              （TypeSafe の鍵を貼る）
  python3 scripts/set_jev_key.py --gateway    （Vercel AI Gateway の鍵を貼る）
"""
import argparse
import getpass
import os
import re
import subprocess
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import token_store
from lib.threads_api import debug_token

BASE = Path(__file__).resolve().parent.parent
SAMPLE = {"id": "test", "post": "朝の10分でできる片付けのコツを3つ書きました",
          "comment": "2つ目、今日からやってみます。ありがとうございます"}


def read_key(name: str, source: str | None) -> str:
    if source:
        text = Path(source).expanduser().read_text(encoding="utf-8")
        match = re.search(rf"^\s*(?:export\s+)?{name}\s*=\s*[\"']?([^\"'\s]+)", text, re.MULTILINE)
        if not match:
            raise RuntimeError(f"{source} に {name} が無い")
        return match.group(1)
    label = "Vercel AI Gateway" if name == "AI_GATEWAY_API_KEY" else "TypeSafe"
    raw = getpass.getpass(f"{label} の API key を貼り付けて Enter（画面には出ません）: ")
    # 貼り付けの目印（ESC[200~ / ESC[201~）や改行・空白が混ざっても外す
    return re.sub(r"\s+", "", re.sub(r"\x1b\[20[01]~", "", raw))


def try_key(name: str, key: str) -> dict:
    """その鍵だけで、見本のコメント1件を分けてみる（もう一方の鍵は外す）"""
    for k in ("TYPESAFE_API_KEY", "AI_GATEWAY_API_KEY"):
        os.environ.pop(k, None)
    os.environ[name] = key
    if name == "AI_GATEWAY_API_KEY" and not (BASE / "node_modules" / "ai").exists():
        print("Gateway 経由の部品を入れる（npm ci）…")
        try:
            subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=BASE, capture_output=True, text=True, check=True)
        except FileNotFoundError:
            raise RuntimeError("npm が無い。Node.js 22 以上を入れてからもう一度")
    try:
        from lib.jev import classify_comments
    except ImportError:
        raise RuntimeError("部品が入っていない。先に pip install -r requirements.txt を実行する")
    out = classify_comments([SAMPLE])["test"]
    if "error" in out:
        raise RuntimeError(f"この鍵では分類できない: {out['error']}")
    return out


def check_threads_scopes():
    """コメントを読む・返信する権限が、手元の Threads の鍵にあるかを確かめる（無ければ知らせるだけ）"""
    token = token_store.read_env().get("THREADS_ACCESS_TOKEN")
    if not token:
        print("Threads の鍵が手元の .env に無いので、権限は確かめない（先に scripts/set_token.py）")
        return
    try:
        scopes = set(debug_token(token).get("scopes") or [])
    except Exception as e:
        print(f"Threads の鍵の権限を確かめられなかった（登録は続ける）: {token_store.mask(e)}")
        return
    missing = sorted({"threads_read_replies", "threads_manage_replies"} - scopes)
    if scopes and missing:
        print(f"⚠️ Threads の鍵に {' と '.join(missing)} の権限が無い（コメントを読む・返信するのに要る）。"
              "Meta のアプリの権限に足してトークンを作り直し、scripts/set_token.py をもう一度")


def check_gh():
    """登録に使う gh が GitHub にログインできているかを、試しの分類より先に確かめる"""
    if subprocess.run(["gh", "auth", "status"], capture_output=True, text=True).returncode != 0:
        raise RuntimeError("gh が GitHub にログインしていない。gh auth login のあとでもう一度")


def run(name: str, key: str, source: str | None):
    print(f"受け取った文字列: {token_store.preview(key)}")
    check_gh()
    check_threads_scopes()
    repo = token_store.github_repo()
    private = token_store.repo_is_private(repo)
    if private is False:
        print(f"⚠️ {repo} は公開のリポジトリ。コメントの機能は非公開のリポジトリでだけ動く（鍵は入れておくが、動かない）。"
              "Settings → General のいちばん下の Change visibility で Private にする")
    out = try_key(name, key)
    t, e = out["type"], out["emotion"]
    conf = t["confidence"]
    print(f"確認OK: 見本のコメントは「{t['choice']}」（確信度 {conf:.2f}）「{e['choice']}」と分かれた"
          if isinstance(conf, (int, float)) else f"確認OK: 見本のコメントは「{t['choice']}」「{e['choice']}」と分かれた")
    print(f"  （{out['tokens']} トークン・{out['model']}）")
    if not source:
        token_store.write_env({name: key})
        print(f".env: {token_store.ENV_FILE} に {name} を書いた")
    token_store.set_github_secret(repo, name, key)
    print(f"GitHub Secrets: {repo} の {name} を登録した")
    subprocess.run(["gh", "workflow", "run", "replies.yml", "-R", repo], capture_output=True, text=True, check=True)
    print("コメントの取得: GitHub で1回走らせた（数分で data/<アカウント>/replies.md ができる。"
          "Actions の「Threads コメントの分類と返信案」で様子が見える）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", action="store_true", help="Vercel AI Gateway の鍵（AI_GATEWAY_API_KEY）を登録する")
    parser.add_argument("--from", dest="source", help="鍵が書いてあるファイル（.env の形）")
    args = parser.parse_args()
    name = "AI_GATEWAY_API_KEY" if args.gateway else "TYPESAFE_API_KEY"
    key = ""
    try:
        key = read_key(name, args.source)
        if not key:
            raise RuntimeError("何も貼られていない")
        if "set_jev_key" in key or key.startswith(("cd/", "python")):
            raise RuntimeError("コマンドの文字列が貼られている。鍵の文字列を貼る")
        run(name, key, args.source)
    except subprocess.CalledProcessError as e:
        print(f"❌ 失敗: {' '.join(e.cmd[:3])} … {token_store.mask(e.stderr or '')}")
        sys.exit(1)
    except Exception as e:
        msg = token_store.mask(e)
        print(f"❌ 失敗: {msg.replace(key, '***') if key else msg}")
        sys.exit(1)
