#!/bin/sh
# 起こす係の中身: GitHub の投稿処理（post.yml）を今すぐ起こす
# launchd（Mac）か cron（VPS）が、投稿の時刻の1分後に呼ぶ。
# 使い方: sh scripts/trigger_post.sh <gh のパス> <持ち主/リポジトリ>
echo "=== $(date '+%F %T') post-trigger"
exec "$1" workflow run post.yml -R "$2"
