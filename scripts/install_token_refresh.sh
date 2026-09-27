#!/bin/bash
# 【Mac】Threads の鍵の自動延長を、この Mac の launchd に入れる（毎週月曜 9:05 に refresh_token.py）
# 使い方: bash scripts/install_token_refresh.sh [設定ファイル] [失敗を書き込むファイル]
#   例:   bash scripts/install_token_refresh.sh configs/my_account.yml ~/Documents/やること.md
#         DRY_RUN=1 を付けると、入れずに中身（plist）だけ logs/ に書いて確かめる
# 外す:   launchctl bootout gui/$(id -u)/com.threads-autopost.token-refresh.<名前>
set -eu
cd "$(dirname "$0")/.."
ROOT=$(pwd)
CONFIG=${1:-configs/my_account.yml}
ALERT=${2:-}
NAME=$(basename "$CONFIG" .yml)
LABEL="com.threads-autopost.token-refresh.$NAME"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
[ "${DRY_RUN:-}" = 1 ] && PLIST="$ROOT/logs/$LABEL.plist"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=$(command -v python3)
GH=$(command -v gh) || { echo "ERROR: gh が無い（brew install gh のあと gh auth login）" >&2; exit 1; }
[ -f "$ROOT/.env" ] || { echo "ERROR: $ROOT/.env が無い。先に scripts/set_token.py を実行する" >&2; exit 1; }
"$PY" -c "import requests, dotenv, yaml" || { echo "ERROR: $PY に requests / python-dotenv / pyyaml が無い" >&2; exit 1; }
mkdir -p "$ROOT/logs"
echo "install: $LABEL（python=$PY / gh=$GH / 失敗の書き込み先=${ALERT:-なし}）"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$ROOT/scripts/refresh_token.py</string>
        <string>--config</string>
        <string>$CONFIG</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$ROOT</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>5</integer>
    </dict>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$(dirname "$GH"):/usr/bin:/bin</string>
        <key>THREADS_ALERT_FILE</key>
        <string>$ALERT</string>
    </dict>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/token_refresh.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/token_refresh.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST"
if [ "${DRY_RUN:-}" = 1 ]; then echo "install: DRY_RUN（入れていない。中身: $PLIST）"; exit 0; fi
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true   # 入れ直し（未登録なら何もしない）
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl print "gui/$(id -u)/$LABEL" | grep -E "^\s*(state|path) =" | head -2
echo "install: 完了（次回は月曜 9:05。ログ: $ROOT/logs/token_refresh.log）"
