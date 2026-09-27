"""
再投稿スクリプト（伸びた投稿のくり返し）
キューの空いている投稿時刻に、過去の投稿から伸びたものを入れる。
post.yml が投稿の前に毎回呼ぶ。設定は configs/<アカウント>.yml の recycle: 節（enabled: true で動く）。

選び方:
- 書き出し（空白を除いた先頭 family_prefix 文字）が同じ投稿は1つの「系統」にまとめる
- 系統のスコア = 投稿から mature_days 日以上たった回の表示数の幾何平均
  （再投稿して伸びなければ、スコアが下がって上位から外れる）
- スコア上位 top_n 系統を、最後に出してから長い順に回す。同じ系統は min_gap_days 日あける
- explore_rate の割合で、上位以外（統計がまだ無いものも含む）を混ぜる
- exclude_file / exclude_text に当たる投稿は使わない

キューの扱い:
- 手動で入れた投稿がある時刻（前後 slot_window_minutes 分）は埋めない
- 予定時刻を stale_minutes 分過ぎても出ていない再投稿は捨てる（次の補充で選び直す）

使い方: python3 scripts/recycle.py --config configs/my_account.yml [--dry-run]
"""
import csv
import json
import math
import random
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.account_context import get_context

JST = timezone(timedelta(hours=9))

DEFAULTS = {
    "enabled": False,
    "top_n": 30,
    "min_gap_days": 7,
    "explore_rate": 0.2,
    "days_ahead": 3,
    "family_prefix": 20,
    "mature_days": 2,
    "stale_minutes": 120,
    "slot_window_minutes": 60,
    "exclude_file": "",
    "exclude_text": "",
}


def load_settings(ctx) -> dict:
    settings = dict(DEFAULTS)
    settings.update(ctx.config.get("recycle") or {})
    settings.setdefault("times", ctx.times)
    return settings


def family_key(text: str, prefix: int) -> str:
    return re.sub(r"\s+", "", text)[:prefix]


def parse_posted_at(value: str) -> datetime:
    """posted_log の posted_at。タイムゾーンなしは UTC（GitHub Actions の記録）として読む"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_stats(stats_file) -> dict:
    """thread_id → {"views", "likes"}（同じ thread_id が複数あれば collected_at が新しい方）"""
    if not stats_file.exists():
        return {}
    latest = {}
    with open(stats_file, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row["thread_id"]
            if tid not in latest or row["collected_at"] > latest[tid]["collected_at"]:
                latest[tid] = row
    return {tid: {"views": int(row["views"] or 0), "likes": int(row["likes"] or 0)}
            for tid, row in latest.items()}


def read_queue_file(path: Path) -> tuple[dict, str]:
    content = path.read_text(encoding="utf-8")
    fm = {}
    match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if match:
        for line in match.group(1).split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                fm[key.strip()] = val.strip()
    body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL).strip()
    return fm, body


def parse_scheduled(value: str):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        return None


def build_families(log: list, stats: dict, settings: dict, now: datetime) -> dict:
    exclude_file = re.compile(settings["exclude_file"]) if settings["exclude_file"] else None
    exclude_text = re.compile(settings["exclude_text"]) if settings["exclude_text"] else None
    mature_before = now - timedelta(days=settings["mature_days"])

    families = {}
    for entry in log:
        if exclude_file and exclude_file.search(entry.get("file", "")):
            continue
        if exclude_text and exclude_text.search(entry["text"]):
            continue
        key = family_key(entry["text"], settings["family_prefix"])
        fam = families.setdefault(key, {"key": key, "postings": [], "last": None})
        posted_at = parse_posted_at(entry["posted_at"])
        fam["postings"].append({
            "thread_id": entry["thread_id"],
            "file": entry.get("file", ""),
            "text": entry["text"],
            "posted_at": posted_at,
            "views": stats.get(entry["thread_id"], {}).get("views"),
            "likes": stats.get(entry["thread_id"], {}).get("likes"),
        })
        if fam["last"] is None or posted_at > fam["last"]:
            fam["last"] = posted_at

    for fam in families.values():
        matured = [p["views"] for p in fam["postings"]
                   if p["views"] is not None and p["posted_at"] <= mature_before]
        fam["score"] = (math.exp(sum(math.log1p(v) for v in matured) / len(matured)) - 1
                        if matured else None)
        seen = [p for p in fam["postings"] if p["views"]]
        fam["like_rate"] = (sum(p["likes"] or 0 for p in seen) / sum(p["views"] for p in seen)
                            if seen else None)
        with_views = [p for p in fam["postings"] if p["views"] is not None]
        best = (max(with_views, key=lambda p: p["views"]) if with_views
                else min(fam["postings"], key=lambda p: p["posted_at"]))
        fam["text"] = best["text"]
        fam["origin"] = best["file"]
    return families


def pick(pool: list, slot: datetime, min_gap: timedelta):
    """間隔の条件を満たす系統のうち、最後に出してから一番長いもの（同じならスコアが高い方）"""
    eligible = [f for f in pool if f["last"] is None or f["last"] <= slot - min_gap]
    if not eligible:
        return None
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return min(eligible, key=lambda f: (f["last"] or oldest, -(f["score"] or 0)))


def run(ctx=None, dry_run=False):
    if ctx is None:
        ctx = get_context()
    settings = load_settings(ctx)
    if not settings["enabled"]:
        print("再投稿: recycle.enabled が false のため何もしない")
        return

    now = datetime.now(JST)
    stale_before = now - timedelta(minutes=settings["stale_minutes"])
    window = timedelta(minutes=settings["slot_window_minutes"])
    min_gap = timedelta(days=settings["min_gap_days"])

    # キューを読む（出しそびれた古い再投稿は捨てる）
    queued = []
    removed = []
    for path in sorted(ctx.queue_dir.glob("*.md")):
        fm, body = read_queue_file(path)
        scheduled = parse_scheduled(fm.get("scheduled", ""))
        is_recycle = fm.get("source") == "recycle"
        stale = is_recycle and scheduled and scheduled < stale_before
        if stale:
            removed.append(path.name)
            if not dry_run:
                path.unlink()
            continue
        queued.append({"file": path.name, "scheduled": scheduled, "body": body})

    log = json.loads(ctx.log_file.read_text()) if ctx.log_file.exists() else []
    families = build_families(log, load_stats(ctx.stats_file), settings, now)

    # キューに入っている分も「出した」扱いにして、間隔の計算に入れる
    for q in queued:
        fam = families.get(family_key(q["body"], settings["family_prefix"]))
        if fam and q["scheduled"] and (fam["last"] is None or q["scheduled"] > fam["last"]):
            fam["last"] = q["scheduled"]

    scored = sorted((f for f in families.values() if f["score"] is not None),
                    key=lambda f: f["score"], reverse=True)
    top = scored[:settings["top_n"]]
    top_keys = {f["key"] for f in top}
    rest = [f for f in families.values() if f["key"] not in top_keys]
    print(f"再投稿: 使える系統 {len(families)}（上位 {len(top)} / それ以外 {len(rest)}）")

    added = []
    for day in range(settings["days_ahead"]):
        date = (now + timedelta(days=day)).date()
        for hhmm in settings["times"]:
            hour, minute = (int(x) for x in hhmm.split(":"))
            slot = datetime.combine(date, time(hour, minute), tzinfo=JST)
            if slot <= now:
                continue
            if any(q["scheduled"] and abs(q["scheduled"] - slot) < window for q in queued):
                continue

            rng = random.Random(f"{ctx.name}:{slot:%Y-%m-%d %H:%M}")
            explore = rng.random() < settings["explore_rate"]
            first, second = (rest, top) if explore else (top, rest)
            fam = pick(first, slot, min_gap)
            label = "おためし" if explore else "上位"
            if fam is None:
                fam = pick(second, slot, min_gap)
                label = "上位" if explore else "おためし"
            if fam is None:
                print(f"  ⚠️ {slot:%m/%d %H:%M} に出せる投稿がない（全部 {settings['min_gap_days']} 日以内に出している）")
                continue

            fam["last"] = slot
            filename = f"{slot:%Y-%m-%d_%H%M}_再投稿.md"
            content = (
                "---\n"
                f"scheduled: {slot:%Y-%m-%d %H:%M}\n"
                f"type: 再投稿（{label}）\n"
                "source: recycle\n"
                f"origin: {fam['origin']}\n"
                "---\n\n"
                f"{fam['text']}\n"
            )
            if not dry_run:
                (ctx.queue_dir / filename).write_text(content, encoding="utf-8")
            queued.append({"file": filename, "scheduled": slot, "body": fam["text"]})
            score = "統計なし" if fam["score"] is None else f"{fam['score']:,.0f}"
            first_line = fam["text"].splitlines()[0][:30]
            added.append(filename)
            print(f"  + {slot:%m/%d %H:%M} {label} スコア {score} | {first_line}")

    mode = "（dry-run: 書き込みなし）" if dry_run else ""
    print(f"再投稿: 追加 {len(added)} 件 / 捨てた古い枠 {len(removed)} 件{mode}")
    for name in removed:
        print(f"  - {name}")


if __name__ == "__main__":
    ctx = get_context()
    run(ctx, dry_run="--dry-run" in sys.argv)
