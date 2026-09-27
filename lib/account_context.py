"""
アカウントの設定（configs/<名前>.yml）を読み、置き場・鍵・設定を1か所にまとめる

使い方:
  from lib.account_context import get_context
  ctx = get_context()  # --config 引数を読む
  # ctx.queue_dir, ctx.token, ctx.times などで使う
"""
import os
import argparse
from pathlib import Path
import yaml
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).parent.parent


class AccountContext:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        self.name = self.config_path.stem  # 例: "my_account"

        # データの置き場（アカウントごとに分ける）
        self.data_dir = BASE / "data" / self.name
        self.queue_dir = self.data_dir / "queue"
        self.posted_dir = self.data_dir / "posted"
        self.log_file = self.data_dir / "posted_log.json"
        self.stats_file = self.data_dir / "stats.csv"
        self.kill_switch = self.data_dir / "KILL_SWITCH"

        # 鍵（アカウント専用の名前 → 共通の名前の順に探す）
        suffix = self.name.upper()
        self.token = (
            os.getenv(f"THREADS_ACCESS_TOKEN_{suffix}")
            or os.getenv("THREADS_ACCESS_TOKEN")
        )
        self.user_id = (
            os.getenv(f"THREADS_USER_ID_{suffix}")
            or os.getenv("THREADS_USER_ID")
        )

        # 設定の近道
        self.account = self.config.get("account", "")
        self.times = self.config.get("times", ["07:00", "10:00", "13:00", "17:00", "21:00"])
        self.max_daily_posts = int(self.config.get("max_daily_posts") or len(self.times))
        self.min_interval_minutes = int(self.config.get("min_interval_minutes") or 60)

    def ensure_dirs(self):
        """置き場のフォルダを作る"""
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.posted_dir.mkdir(parents=True, exist_ok=True)


def get_context() -> AccountContext:
    """--config 引数を読んで AccountContext を返す"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="設定ファイル（例: configs/my_account.yml）")
    args, _ = parser.parse_known_args()
    ctx = AccountContext(args.config)
    ctx.ensure_dirs()
    return ctx
