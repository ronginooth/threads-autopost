"""
数字の集計（stats.yml が毎晩呼ぶ）
posted_log.json に載った自分の投稿の表示・いいね等を取り、data/<アカウント>/stats.csv に書く。
再投稿（recycle.py）の順位づけはこの数字を使う
"""
import json
import csv
from datetime import datetime
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.threads_api import get_insights, get_user_insights
from lib.account_context import get_context

HEADERS = ["thread_id", "file", "text_preview", "posted_at", "collected_at",
           "views", "likes", "replies", "reposts", "quotes"]


def load_log(log_file) -> list:
    if not log_file.exists():
        print("posted_log.json がありません。まず投稿してください。")
        return []
    return json.loads(log_file.read_text())


def load_previous(stats_file) -> dict:
    """前回の stats.csv（取得に失敗した投稿は前回の行を残すため）"""
    if not stats_file.exists():
        return {}
    with open(stats_file, encoding="utf-8") as f:
        return {row["thread_id"]: row for row in csv.DictReader(f)}


def run(ctx=None) -> bool:
    """全件の取得に失敗したら False を返す"""
    if ctx is None:
        ctx = get_context()

    log = load_log(ctx.log_file)
    if not log:
        return True

    previous = load_previous(ctx.stats_file)
    failed = 0
    rows = []
    for entry in log:
        thread_id = entry["thread_id"]
        print(f"収集中: {thread_id} ({entry['file']})")
        try:
            insights = get_insights(thread_id, ctx.token)
            row = {
                "thread_id": thread_id,
                "file": entry["file"],
                "text_preview": entry["text"][:50].replace("\n", " "),
                "posted_at": entry["posted_at"],
                "collected_at": datetime.now().isoformat(),
                "views": insights.get("views", 0),
                "likes": insights.get("likes", 0),
                "replies": insights.get("replies", 0),
                "reposts": insights.get("reposts", 0),
                "quotes": insights.get("quotes", 0),
            }
            rows.append(row)
            print(f"  views={row['views']} likes={row['likes']} replies={row['replies']}")
        except Exception as e:
            failed += 1
            print(f"  ❌ 取得失敗: {e}")
            # 前回の行を残す（collected_at は前回のまま。消すと再投稿の順位づけが効かなくなる）
            if thread_id in previous:
                rows.append(previous[thread_id])

    with open(ctx.stats_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ stats.csv に保存しました（取得 {len(log) - failed} 件 / 失敗 {failed} 件）")

    # アカウント全体のインサイト（views, clicks, followers等）
    collect_account_insights(ctx)

    if failed == len(log):
        print("❌ 全件の取得に失敗しました。トークン切れの可能性が高い（上のエラー文を確認）")
        return False
    return True


def collect_account_insights(ctx):
    """ユーザーレベルInsightsを取得してaccount_insights.csvに追記"""
    account_stats_file = ctx.data_dir / "account_insights.csv"
    account_headers = ["collected_at", "views", "clicks", "likes", "replies", "reposts", "quotes", "followers_count"]

    print("\nアカウント全体のインサイトを収集中...")
    try:
        insights = get_user_insights(ctx.token, ctx.user_id)
        row = {
            "collected_at": datetime.now().isoformat(),
            "views": insights.get("views", 0),
            "clicks": insights.get("clicks", 0),
            "likes": insights.get("likes", 0),
            "replies": insights.get("replies", 0),
            "reposts": insights.get("reposts", 0),
            "quotes": insights.get("quotes", 0),
            "followers_count": insights.get("followers_count", 0),
        }

        is_new = not account_stats_file.exists()
        with open(account_stats_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=account_headers)
            if is_new:
                writer.writeheader()
            writer.writerow(row)

        print(f"  views={row['views']} clicks={row['clicks']} followers={row['followers_count']}")
        print(f"✅ account_insights.csv に保存しました")
    except Exception as e:
        print(f"  ⚠️ アカウントInsights取得失敗: {e}")
        print(f"  （フォロワー100人未満の場合、一部メトリクスが利用できない場合があります）")


if __name__ == "__main__":
    ctx = get_context()
    if not run(ctx):
        sys.exit(1)
