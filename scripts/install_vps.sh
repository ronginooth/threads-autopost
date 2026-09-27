#!/bin/bash
# 【VPS（Linux）】起こす係（投稿の時刻の1分後に post.yml を起こす）と、鍵の自動延長（毎週月曜 9:05）を cron に入れる
# times は日本時間。このサーバーの時刻帯（UTC など）に直して書く。時刻帯を変えたら入れ直す
# 使い方: bash scripts/install_vps.sh [設定ファイル]
#         DRY_RUN=1 を付けると、入れずに書く行だけ表示する
# 外す:   bash scripts/install_vps.sh [設定ファイル] --remove
set -eu
cd "$(dirname "$0")/.."
ROOT=$(pwd)
CONFIG=${1:-configs/my_account.yml}
NAME=$(basename "$CONFIG" .yml)
BEGIN="# threads-autopost:$NAME ここから"
END="# threads-autopost:$NAME ここまで"
current=$(crontab -l 2>/dev/null | sed "/^$BEGIN\$/,/^$END\$/d" || true)
if [ "${2:-}" = "--remove" ]; then
  printf '%s\n' "$current" | crontab -
  echo "remove: cron から $NAME の行を外した"; exit 0
fi
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=$(command -v python3)
GH=$(command -v gh) || { echo "ERROR: gh が無い（README の「VPS で動かす」で GitHub CLI を入れてから gh auth login）" >&2; exit 1; }
"$GH" auth status >/dev/null 2>&1 || { echo "ERROR: gh にログインしていない（gh auth login）" >&2; exit 1; }
[ -f "$ROOT/.env" ] || { echo "ERROR: $ROOT/.env が無い。先に scripts/set_token.py を実行する" >&2; exit 1; }
"$PY" -c "import requests, dotenv, yaml" || { echo "ERROR: $PY に requests / python-dotenv / pyyaml が無い" >&2; exit 1; }
REPO=$("$PY" -c "import sys; sys.path.insert(0, '.'); from lib.token_store import github_repo; print(github_repo())")
mkdir -p "$ROOT/logs"

LINES=$("$PY" - "$CONFIG" "$GH" "$REPO" "$ROOT" "$PY" <<'PY'
import sys, yaml
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
config, gh, repo, root, py = sys.argv[1:]
jst = ZoneInfo("Asia/Tokyo")
here = datetime.now().astimezone().tzinfo          # このサーバーの時刻帯
monday = datetime.now(jst).date()
monday += timedelta(days=(7 - monday.weekday()) % 7)  # 次の月曜（日本時間）
def at(h, m):
    return datetime(monday.year, monday.month, monday.day, h, m, tzinfo=jst).astimezone(here)
for hhmm in yaml.safe_load(open(config, encoding="utf-8"))["times"]:
    h, m = (int(x) for x in hhmm.split(":"))
    t = at(h, m) + timedelta(minutes=1)
    print(f'{t.minute} {t.hour} * * * /bin/sh "{root}/scripts/trigger_post.sh" "{gh}" "{repo}" >> "{root}/logs/post_trigger.log" 2>&1')
t = at(9, 5)
dow = (t.weekday() + 1) % 7                            # cron は日曜=0
print(f'{t.minute} {t.hour} * * {dow} cd "{root}" && "{py}" scripts/refresh_token.py --config {config} >> "{root}/logs/token_refresh.log" 2>&1')
PY
)
block=$(printf '%s\n%s\n%s' "$BEGIN" "$LINES" "$END")
echo "install: $NAME（$REPO の post.yml を起こす / サーバーの時刻帯 $(date +%Z)）"
printf '%s\n' "$block"
if [ "${DRY_RUN:-}" = 1 ]; then echo "install: DRY_RUN（cron には入れていない）"; exit 0; fi
{ [ -n "$current" ] && printf '%s\n' "$current"; printf '%s\n' "$block"; } | crontab -
echo "install: 完了（ログ: $ROOT/logs/post_trigger.log と token_refresh.log）"
