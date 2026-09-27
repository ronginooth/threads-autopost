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
| `scripts/fetch_replies.py` | 【コメント】自分の投稿のコメントを集め、Jev で種類と感情に分け、返信案を付けて `replies.md` に書く（非公開のリポジトリでだけ動く・最初は切ってある） |
| `scripts/reply.py` | 【コメント】Actions の入力欄から、コメントに返信する |
| `scripts/set_jev_key.py` `set_claude_token.py` | 【コメント】分類（Jev）と返信案（Claude）の鍵を確かめて、GitHub の Secrets に入れる |
| `tests/test_replies.py` | 【コメント】ネットにつながずに、取り込み・分類・返信案・`replies.md`・返信の流れを確かめる |

## 用意するもの

- Threads のアカウント
- GitHub のアカウント
- 起こす係を置く場所: **いつも電源が入っている Mac** か **VPS**
- （使うなら）コメントの分類と返信案: Jev の鍵と、Claude のサブスク（下の「コメントの分類と返信案（Jev）」）

Mac を持っていない人や、家に置きっぱなしにできない人は、VPS の方が向いています。
置き場の比べ方は、作者のページにまとめています → https://rongi-ai-agent.pages.dev/vps/ （【PR】広告を含むページです）

## 手順

### 1. このひな形から、自分のリポジトリを作る

右上の **Use this template** → **Create a new repository**。
公開範囲は **Private（非公開）** をおすすめします。投稿の予定や数字がリポジトリに入るためです。
コメントの機能（下の「コメントの分類と返信案（Jev）」）は、Private でないと動きません。
非公開だと GitHub Actions の無料枠（月2,000分）を使います。この仕組みは1日3本で月およそ400分です。コメントの機能を使うと、月300〜600分ほど増えます（見込み）。

### 2. Threads の鍵を用意する

1. [Meta for Developers](https://developers.facebook.com/) でアプリを作り、ユースケースに「Threads API にアクセス」を選ぶ
2. 権限は `threads_basic` と `threads_content_publish`（数字も集めるなら `threads_manage_insights` も。コメントの機能も使うなら `threads_read_replies` と `threads_manage_replies` も）
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

## コメントの分類と返信案（Jev）

自分の投稿に付いたコメントを、日本時間 7時〜23時の2時間おきに集めて、1件ずつ種類と感情に分け、返信の案を付けます。
結果は `data/my_account/replies.md` に新しい順で並びます。返信は自分で案を選び、Actions の入力欄から出します（勝手には返信しません）。
最初は切ってあります。Jev の鍵を入れると、スイッチ（リポジトリの変数 `COMMENTS_ENABLED`）が `true` になって動きます。切ってあるあいだは2時間おきの起動がスキップになり、Actions の分数を使いません。投稿はこの機能と関係なく動きます。

- **種類**（8つ）: 共感・応援／質問／体験の共有／補足・情報／反論・指摘／強い言い方／宣伝・無関係／その他
- **感情**（7つ）: 喜び・楽しさ／感謝・尊敬／共感・安心／不安・悩み／驚き・関心／不満・苛立ち／落ち着き
- 分けるのは Jev（TypeSafe の、判定のためのモデル）。どれくらい確かか（確信度 0〜1）も付きます。0.9 未満は「判定を確かめて」と出ます。種類の確信度が 0.5 未満のときは、Claude が判定し直します（Claude のトークンを入れたとき）
- 返信案は Claude Code が、種類と感情に合った返し方で作ります（ふつうは3案。強い言い方は1案で「返信しないのがおすすめ」、宣伝・無関係は0案）
- 案に、投稿にもコメントにも無い体験や数字が入っていないかを Jev で確かめ、入っていそうな案は外します。少し疑わしい案には「体験や数字を足していないか確認」と付きます

### 非公開のリポジトリでだけ動きます

コメントは他人が書いたものです。公開のリポジトリに入れると、名前と本文、それに付けた分類や返信案まで、誰でも読めるようになります。
そのため、リポジトリが **Private（非公開）** でないと、この機能は何も取らずに終わります（Actions の記録の最初の段に理由が出ます）。

- これから作るなら、手順1の **Use this template** で **Private** を選ぶ
- 公開で作ってしまったなら、Settings → General のいちばん下の「Change visibility」で Private にする
- あとで公開に切り替えると、それまでに入ったコメントも履歴ごと読めるようになります。公開にしたいときは、コメントの入っていない新しいリポジトリを作り直してください

### 入れる鍵（GitHub の Secrets）と、かかるお金

| Secret の名前 | 使うところ | かかるお金 |
|---|---|---|
| `TYPESAFE_API_KEY` | Jev でコメントを分ける（TypeSafe に直接） | TypeSafe の使った分 |
| `AI_GATEWAY_API_KEY` | 上と同じ（Vercel AI Gateway 経由）。TypeSafe の鍵が無いときに使う | Vercel AI Gateway の使った分 |
| `CLAUDE_CODE_OAUTH_TOKEN` | 返信案を作る。無くてもよい（無ければ取得と分類だけ） | 自分の Claude のサブスク（Pro / Max など）の枠 |

- Jev の鍵は、どちらか1つで動きます（両方あれば TypeSafe に直接）。どちらも無いあいだ、この機能は切れたままです
- Jev の値段は、どちらの道も入力100万トークンあたり0.042ドル（出力は無料。2026年9月時点）で、それぞれのサービスから請求されます。コメント1件の分類は約1,500トークンです。作者の最初の本番では、コメント92件の分類で約0.006ドルでした（作者の実測）。返信案を作ったコメントは、案の確かめで少し多く使います
- 返信案の Claude は、API キーではなく自分のサブスクの枠で動きます（従量課金にはなりません。サブスクの使える量は減ります）。案を書くモデルは設定の `replies.model`（はじめは `opus`。枠を節約するなら `sonnet`）
- GitHub Actions の分数も使います。鍵を入れて動かすと、1回1〜2分（返信案が多い回はもっと）×1日9回で、月300〜600分ほどの見込みです（非公開の無料枠は月2,000分）

### 鍵を入れる

Threads の鍵には、コメントを読む権限 `threads_read_replies` と、返信する権限 `threads_manage_replies` が要ります。
手順2のときに入れていなければ、Meta のアプリの権限に足してトークンを作り直し、手順5をもう一度行います。

ここからは、手順3で持ってきたリポジトリで実行します（`gh auth login` 済みの Mac か VPS）。先に、コメントの機能の部品を足します（投稿の部品はそのまま）。

```bash
.venv/bin/pip install -r requirements-comments.txt
```

どのスクリプトも、鍵を1回試して、通ったものだけを GitHub の Secrets に入れ、コメントの取得を1回走らせます。鍵は画面に出ません。

**Jev の鍵（TypeSafe）** — https://console.typesafe.ai/keys で API key を作って、次を実行して貼る

```bash
.venv/bin/python scripts/set_jev_key.py
```

**Jev の鍵（Vercel AI Gateway）** — TypeSafe の登録ができないときはこちら。Vercel の AI Gateway の画面で API key を作って、次を実行して貼る（Node.js 22 以上が要ります）

```bash
.venv/bin/python scripts/set_jev_key.py --gateway
```

**返信案の Claude のトークン** — Claude Code が入っている手元で `claude setup-token` を実行し、出てきたトークン（`sk-ant-oat` で始まる・1年もの）をコピーしてから、同じ手元のこのリポジトリで次を実行して貼る

```bash
.venv/bin/python scripts/set_claude_token.py
```

GitHub の画面から入れるときは、Settings → Secrets and variables → Actions → New repository secret に、上の表の名前で入れます（その場合、試しの確認はありません）。あわせて同じ画面の **Variables** タブで、`COMMENTS_ENABLED` という変数を `true` で作ります（これがスイッチです）。

### replies.md の読み方

GitHub で `data/my_account/replies.md` を開くと、整った形で読めます（非公開なので、見えるのは自分と招いた人だけです）。

- いちばん上: 更新の時刻、返信待ちと返信済みの数、止まっていることがあればその理由
- 返信待ち（新しい順）: 名前と時刻 → 種類と感情（確信度） → コメントの本文 → どの投稿へのコメントか（Threads で開くリンク） → 返し方 → 返信案 → コメントID
- 返信済み: 直近20件。Threads のアプリから返信したものも、次の回で返信済みに移ります
- 返信待ちのまま30日（`replies.fresh_days`）を過ぎたコメントは、表から外れます

### 返信を出す

1. GitHub の **Actions** タブ →「Threads コメントに返信」→ **Run workflow**
2. 「返信先のコメントID」に replies.md のコメントIDを、「返信の文」に案を（直してよい）貼って、**Run workflow**

取り込んだコメントにだけ返信します（IDの打ち間違いで、よその投稿に返信しないため）。同じコメントに同じ文を2回は出しません。出したら replies.md の返信済みに移ります。

### 設定

`configs/my_account.yml` の `replies:` で変えられます（見に行く投稿の数・1回に取り込む数・返信案を作るか・モデル・自分の紹介）。

### 止める

- **全部止める**: Settings → Secrets and variables → Actions → **Variables** の `COMMENTS_ENABLED` を `false` にする（再開は `true`）。止めているあいだは Actions の分数を使いません
- **返信案だけ止める**: `replies.draft_replies` を `false` にして送る

## うまく動かないとき

- **投稿されない** — GitHub の Actions タブで「Threads 自動投稿」の記録を開く。止めるスイッチ・時刻前・1日の上限・最低の間隔のどれかが出ています
- **鍵が切れた**（60日を過ぎた）— 手順5をもう一度
- **起こす係が動いたか** — `logs/post_trigger.log`（Mac・VPS とも）
- **コメントが replies.md に出てこない** — Actions の「Threads コメントの分類と返信案」の記録を開く。最初の段に、公開のリポジトリか、足りない鍵の名前が出ています。記録が「スキップ」（灰色）なら、スイッチの変数 `COMMENTS_ENABLED` が `true` になっていません。分類や返信案が止まったときの理由は replies.md のいちばん上に出ます
- **アカウントの名前を変えたい** — `configs/` のファイル名・`data/` のフォルダ名・`.github/workflows/*.yml` の `config: [my_account]`（`reply.yml` は入力欄の既定値 `default: "my_account"`）をそろえる

## 作った人

ロンギ（Threads [@ronginooth_ai](https://www.threads.com/@ronginooth_ai)）が、AI（Claude Code）と一緒に作りました。
自由に使って、直して構いません（MIT ライセンス・無保証）。
