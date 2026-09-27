# Threads 定時投稿のひな形（threads-autopost）

時刻を書いた文をフォルダに置いておくと、その時刻に Threads へ出す仕組みです。
投稿するのは GitHub Actions、時刻どおりに起こすのは手元の Mac か、借りたサーバー（VPS）です。

## しくみ

1. **文を置く** — `data/my_account/queue/` に、先頭に予約の時刻を書いた `.md` を置いて GitHub に送る
2. **起こす** — 起こす係（Mac の launchd か VPS の cron）が、投稿の時刻の1分後に GitHub へ「投稿して」と合図する
3. **投稿する** — GitHub Actions が、時刻が来た文を Threads に出し、`posted/` へ移す

GitHub の定期実行だけに任せると、数時間おきにしか動かないことがあり、時刻から外れます。
そこで時刻を守る役だけを手元に置いています。GitHub の定期実行は予備（3時間ごと）として残しています。

## 入っているもの

| 部品 | 中身 |
|---|---|
| `scripts/post.py` | 時刻が来た文を1本出す。止めるスイッチ・1日の上限・最低の間隔・二重投稿の防止つき |
| `scripts/set_token.py` | Threads の鍵を確かめて、手元の `.env` と GitHub の Secrets に入れる（初回と、作り直したとき） |
| `scripts/refresh_token.py` | 鍵（60日で切れる）を毎週延ばす |
| `scripts/install_post_trigger.sh` `install_token_refresh.sh` | 【Mac】起こす係と鍵の延長を launchd に入れる |
| `scripts/install_vps.sh` | 【VPS】起こす係と鍵の延長を cron に入れる |
| `scripts/stats.py` | 自分の投稿の表示・いいねを毎晩集める |
| `scripts/recycle.py` | 空いた時刻に、過去に伸びた投稿を入れる（最初は切ってある） |

## 用意するもの

- Threads のアカウント
- GitHub のアカウント
- 起こす係を置く場所: **いつも電源が入っている Mac** か **VPS**

Mac を持っていない人や、家に置きっぱなしにできない人は、VPS の方が向いています。
置き場の比べ方は、作者のページにまとめています → https://rongi-ai-agent.pages.dev/vps/ （【PR】広告を含むページです）

## 手順

### 1. このひな形から、自分のリポジトリを作る

右上の **Use this template** → **Create a new repository**。
公開範囲は **Private（非公開）** をおすすめします。投稿の予定や数字がリポジトリに入るためです。
非公開だと GitHub Actions の無料枠（月2,000分）を使います。この仕組みは1日3本で月およそ400分です。

### 2. Threads の鍵を用意する

1. [Meta for Developers](https://developers.facebook.com/) でアプリを作り、ユースケースに「Threads API にアクセス」を選ぶ
2. 権限は `threads_basic` と `threads_content_publish`（数字も集めるなら `threads_manage_insights` も）
3. アプリの画面の「カスタマイズ」→「設定」→「ユーザートークン生成ツール」で、自分のアカウントのアクセストークンを出す
   - 生成ツールに自分が出ないときは、アプリの「役割」で自分を Threads のテスターに足し、Threads アプリの「設定 → アカウント → ウェブサイトのアクセス許可 → 招待」で受ける
4. 出てきたトークンは、この次の手順5で貼ります。ファイルやメモには書かないでください

### 3. 起こす係を置く場所に、自分のリポジトリを持ってくる

**Mac のとき**（[Homebrew](https://brew.sh/) が入っている前提）

```bash
brew install gh git python
gh auth login
gh repo clone <自分のアカウント>/<リポジトリ名>
cd <リポジトリ名>
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

**VPS のとき**（Ubuntu の例）

```bash
sudo apt update && sudo apt install -y git python3-venv
```

GitHub CLI（gh）は、公式の手順（https://github.com/cli/cli/blob/trunk/docs/install_linux.md の「Debian, Ubuntu」）で入れます。

```bash
gh auth login   # 「Login with a web browser」を選び、出てきたコードを手元のブラウザで入れる
gh repo clone <自分のアカウント>/<リポジトリ名>
cd <リポジトリ名>
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### 4. 設定を書く

`configs/my_account.yml` を開き、`account` を自分の Threads のユーザー名に、`times` を出したい時刻（日本時間）に書き換えて、GitHub に送ります。

```bash
git add -A && git commit -m "設定" && git push
```

### 5. 鍵を登録する

```bash
.venv/bin/python scripts/set_token.py --config configs/my_account.yml
```

手順2のトークンを貼って Enter（画面には出ません）。持ち主と権限を確かめ、短期のトークンなら60日の長期トークンに交換してから、手元の `.env` と GitHub の Secrets に入れます。

### 6. 起こす係を入れる

**Mac のとき**

```bash
bash scripts/install_post_trigger.sh configs/my_account.yml
bash scripts/install_token_refresh.sh configs/my_account.yml
```

Mac がスリープしている間は起こせません。「システム設定」の省エネの項目で、スリープしないようにしておきます。

**VPS のとき**

```bash
bash scripts/install_vps.sh configs/my_account.yml
```

`times` は日本時間のまま書けば、サーバーの時刻帯（UTC など）に直して cron に入れます。

### 7. 投稿の文を置く

```text
data/my_account/queue/2026-10-01_0700_朝の投稿.md
```

```markdown
---
scheduled: 2026-10-01 07:00
---
ここに投稿の本文を書く（500文字まで）
```

GitHub に送れば、その時刻に出ます。ファイル名の先頭を日時にしておくと、並びが予約の順になります。
見本の `2099-01-01_0700_サンプル.md` は、消すか書き換えて使ってください。

## 止める・再開する

- **止める**: `data/my_account/KILL_SWITCH` という空のファイルを置いて GitHub に送る
- **再開する**: そのファイルを消して送る

## うまく動かないとき

- **投稿されない** — GitHub の Actions タブで「Threads 自動投稿」の記録を開く。止めるスイッチ・時刻前・1日の上限・最低の間隔のどれかが出ています
- **鍵が切れた**（60日を過ぎた）— 手順5をもう一度
- **起こす係が動いたか** — `logs/post_trigger.log`（Mac・VPS とも）
- **アカウントの名前を変えたい** — `configs/` のファイル名・`data/` のフォルダ名・`.github/workflows/*.yml` の `config: [my_account]` をそろえる

## 作った人

ロンギ（Threads [@ronginooth_ai](https://www.threads.com/@ronginooth_ai)）が、AI（Claude Code）と一緒に作りました。
自由に使って、直して構いません（MIT ライセンス・無保証）。
