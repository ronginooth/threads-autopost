#!/bin/bash
# 【Mac】投稿の時刻どおりに GitHub の post.yml を起こす係（launchd）を、この Mac に入れる
# GitHub の定期実行（cron）は数時間おきにしか動かないことがあり、予約の時刻に出せない。
# 設定ファイルの times の各時刻の1分後に `gh workflow run post.yml` を送る（手動の起動はすぐ走る）。GitHub 側の定期実行は予備に残る
# 使い方: bash scripts/install_post_trigger.sh [設定ファイル]
#         DRY_RUN=1 を付けると、入れずに中身（plist）だけ logs/ に書いて確かめる
# 外す:   launchctl bootout gui/$(id -u)/com.threads-autopost.post-trigger.<名前>
set -eu
cd "$(dirname "$0")/.."
ROOT=$(pwd)
CONFIG=${1:-configs/my_account.yml}
NAME=$(basename "$CONFIG" .yml)
LABEL="com.threads-autopost.post-trigger.$NAME"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
[ "${DRY_RUN:-}" = 1 ] && PLIST="$ROOT/logs/$LABEL.plist"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=$(command -v python3)
GH=$(command -v gh) || { echo "ERROR: gh が無い（brew install gh のあと gh auth login）" >&2; exit 1; }
"$PY" -c "import yaml" || { echo "ERROR: $PY に pyyaml が無い（README の「部品を入れる」）" >&2; exit 1; }
REPO=$("$PY" -c "import sys; sys.path.insert(0, '.'); from lib.token_store import github_repo; print(github_repo())")
# times（HH:MM・日本時間）の1分後を launchd の StartCalendarInterval に並べる（Mac の時計が日本時間である前提）
INTERVALS=$("$PY" - "$CONFIG" <<'PY'
import sys, yaml
times = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["times"]
for t in times:
    h, m = (int(x) for x in t.split(":"))
    m += 1
    h, m = (h + m // 60) % 24, m % 60
    print(f"        <dict><key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>")
PY
)
mkdir -p "$ROOT/logs"
echo "install: $LABEL（$REPO の post.yml を起こす / gh=$GH）"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/sh</string>
        <string>$ROOT/scripts/trigger_post.sh</string>
        <string>$GH</string>
        <string>$REPO</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
$INTERVALS
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$(dirname "$GH"):/usr/bin:/bin</string>
    </dict>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/post_trigger.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/post_trigger.log</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST"
if [ "${DRY_RUN:-}" = 1 ]; then echo "install: DRY_RUN（入れていない。中身: $PLIST）"; exit 0; fi
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true   # 入れ直し（未登録なら何もしない）
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl print "gui/$(id -u)/$LABEL" | grep -E "^\s*(state|path) =" | head -2
echo "install: 完了（起こす時刻: $("$PY" -c "import yaml; print(' / '.join(yaml.safe_load(open('$CONFIG'))['times']))") の各1分後。ログ: $ROOT/logs/post_trigger.log）"
