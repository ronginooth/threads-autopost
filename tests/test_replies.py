"""
コメントの取得・分類・返信案・replies.md・返信の流れを、ネットにつながずに確かめる。
Threads・Jev・Claude はにせ物に差し替える（鍵は要らない。データは一時フォルダに書く）

使い方: python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from lib import jev  # noqa: E402
from lib import replies as store  # noqa: E402
from scripts import fetch_replies, reply  # noqa: E402

ME = "me_account"


def ts(minutes_ago: int) -> str:
    """Threads の形の時刻（例: 2026-09-27T04:58:00+0000）"""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def make_threads():
    """にせの Threads: 自分の投稿2本と、そのコメント"""
    posts = [
        {"id": "900", "text": "朝の10分でできる片付けのコツを3つ書きました\n1. 置き場所を決める", "permalink": "https://www.threads.com/@me_account/post/AAA"},
        {"id": "901", "text": "週末の作り置き、何を作っていますか？", "permalink": "https://www.threads.com/@me_account/post/BBB"},
    ]
    replies = {
        "900": [
            {"id": "101", "text": "2つ目のコツ、もう少し詳しく教えてください", "timestamp": ts(10), "username": "alice",
             "permalink": "https://www.threads.com/@alice/post/C101", "has_replies": False},
            {"id": "102", "text": "ありがとうございます！", "timestamp": ts(9), "username": ME, "has_replies": False},
            {"id": "103", "text": "昔のコメント", "timestamp": ts(60 * 24 * 40), "username": "old_user", "has_replies": False},
            {"id": "104", "text": "こんなの常識でしょ。ばかじゃないの", "timestamp": ts(30), "username": "bob", "has_replies": False},
        ],
        "901": [
            {"id": "105", "text": "フォローしてくれたら100万円当たる！", "timestamp": ts(50), "username": "spam_bot", "has_replies": False},
            {"id": "106", "text": "作り置き、毎週迷うんですよね", "timestamp": ts(20), "username": "carol", "has_replies": False},
            {"id": "107", "text": "[ここを押す](http://evil.example) <script>alert(1)</script>\n# 見出し\n1. リスト\n$x^2$ と `code`",
             "timestamp": ts(40), "has_replies": False},  # 非公開のアカウント（名前もリンクも返ってこない）
        ],
    }
    return SimpleNamespace(posts=posts, replies=replies, nested={}, sent=[])


def fake_classify(items, concurrency=4):
    out = {}
    for it in items:
        text = it["comment"]
        hostile = 0.02
        if "教えて" in text:
            t, tc, e, ec = "質問", 0.95, "驚き・関心", 0.72
        elif "ばかじゃない" in text:
            t, tc, e, ec, hostile = "反論・指摘", 0.61, "不満・苛立ち", 0.93, 0.85
        elif "フォロー" in text:
            t, tc, e, ec = "宣伝・無関係", 0.97, "落ち着き", 0.8
        elif "迷う" in text:
            t, tc, e, ec = "その他", 0.41, "不安・悩み", 0.66
        else:
            t, tc, e, ec = "共感・応援", 0.93, "感謝・尊敬", 0.95
        out[it["id"]] = {"type": {"choice": t, "confidence": tc, "probabilities": {t: tc}},
                         "emotion": {"choice": e, "confidence": ec, "probabilities": {e: ec}},
                         "hostile_p": hostile, "model": "jev-1.13.0", "tokens": 1500}
    return out


def fake_check(items, concurrency=4):
    return {it["id"]: [0.95 if "20年" in d else 0.5 if "去年" in d else 0.05 for d in it["drafts"]] for it in items}


def fake_claude(prompt, model):
    if "種類の名前だけ" in prompt:
        return "体験の共有"
    n = int(prompt.split("案は")[1][0])
    drafts = ["ありがとうございます。置き場所を決めるところから始めると続きやすいです",
              "私は20年この片付けを続けてきて、毎朝10分で済むようになりました",
              "去年から同じやり方で、気持ちが楽になりました"][:n]
    return "はい。\n" + json.dumps(drafts, ensure_ascii=False) + "\n以上です"


class RepliesFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = SimpleNamespace(
            name="test_account", account=f"@{ME}", token="dummy-token", user_id="1",
            config={"replies": {"max_comments_per_run": 10, "persona": "片付けを発信している会社員"}},
            data_dir=Path(self.tmp.name) / "data" / "test_account",
        )
        self.th = make_threads()
        self.claude_prompts = []

        def get_replies(media_id, token, fields=None):
            return self.th.nested.get(media_id, []) if media_id not in self.th.replies else self.th.replies[media_id]

        def ask(prompt, model):
            self.claude_prompts.append((prompt, model))
            return fake_claude(prompt, model)

        self.jev_classify = mock.Mock(side_effect=fake_classify)
        patches = [
            mock.patch.object(fetch_replies, "get_me", lambda token: {"id": "1", "username": ME}),
            mock.patch.object(fetch_replies, "get_my_posts", lambda token, uid, limit=25: self.th.posts[:limit]),
            mock.patch.object(fetch_replies, "get_replies", get_replies),
            mock.patch.object(fetch_replies, "ask_claude", ask),
            mock.patch.object(fetch_replies.shutil, "which", lambda name: "/usr/local/bin/claude"),
            mock.patch.object(jev, "backend", lambda: "typesafe"),
            mock.patch.object(jev, "classify_comments", self.jev_classify),
            mock.patch.object(jev, "check_drafts", fake_check),
            mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                         if k not in ("GITHUB_ACTIONS", "CLAUDE_CODE_OAUTH_TOKEN")}, clear=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def read(self, name):
        path = self.ctx.data_dir / name
        return json.loads(path.read_text(encoding="utf-8")) if name.endswith(".json") else path.read_text(encoding="utf-8")

    def by_id(self):
        return {c["comment_id"]: c for c in self.read("comments.json")}

    def test_first_run_classifies_drafts_and_writes_report(self):
        self.assertTrue(fetch_replies.run(self.ctx))
        comments = self.by_id()
        self.assertEqual(sorted(comments), ["101", "104", "105", "106", "107"])  # 自分の返信（102）と40日前（103）は取り込まない
        self.assertEqual(set(self.read("seen_comments.json")), {"101", "102", "103", "104", "105", "106", "107"})
        self.assertEqual([c["comment_id"] for c in self.read("comments.json")], ["101", "106", "104", "107", "105"])  # 新しい順

        c = comments["101"]
        self.assertEqual((c["type"], c["emotion"], c["type_sure"], c["emotion_sure"]), ("質問", "驚き・関心", True, False))
        self.assertEqual(len(c["drafts"]), 2)                      # 「20年」の案は外れる
        self.assertNotIn("20年", "".join(c["drafts"]))
        self.assertEqual(c["draft_flags"], ["", "体験や数字を足していないか確認"])  # 「去年」の案には印
        self.assertEqual(c["drafts_v"], 2)
        self.assertEqual((comments["104"]["type"], comments["104"]["type_raw"], len(comments["104"]["drafts"])),
                         ("強い言い方", "反論・指摘", 1))
        self.assertEqual((comments["105"]["type"], comments["105"]["drafts"], comments["105"]["drafts_v"]),
                         ("宣伝・無関係", [], 2))
        self.assertEqual((comments["106"]["type"], comments["106"]["type_source"]), ("体験の共有", "claude"))

        draft_prompt = next(p for p, _ in self.claude_prompts if "コメントへの返信案" in p)
        self.assertIn(f"@{ME}", draft_prompt)
        self.assertIn("この人の紹介: 片付けを発信している会社員", draft_prompt)
        self.assertEqual({m for _, m in self.claude_prompts}, {"opus"})

        status = self.read("status.json")
        self.assertEqual(status["fetch"]["new"], 5)
        self.assertEqual((status["classify"]["ok"], status["classify"]["count"], status["classify"]["tokens"]), (True, 5, 7500))
        self.assertEqual(status["drafts"], {"ok": True, "count": 4})           # 宣伝・無関係には作らない

        md = self.read("replies.md")
        order = [md.index(s) for s in ("@alice", "@carol", "@bob", "（名前が見えないアカウント）", "@spam\\_bot")]
        self.assertEqual(order, sorted(order))                     # 新しい順
        self.assertIn("種類 **質問**（確信度 0.95）・感情 **驚き・関心**（確信度 0.72）・判定を確かめて", md)
        self.assertIn("種類 **強い言い方**（強い言い方の確率 0.85）", md)
        self.assertIn("**返信しないのがおすすめ**", md)
        self.assertIn("Claude が判定", md)
        self.assertIn("返信案: なし（返信しなくてよい種類）", md)
        self.assertIn("[コメントを Threads で開く](<https://www.threads.com/@alice/post/C101>)", md)
        self.assertIn("[投稿を Threads で開く](<https://www.threads.com/@me_account/post/BBB>)", md)
        self.assertIn("```text\n101\n```", md)
        self.assertIn("（体験や数字を足していないか確認）", md)
        # 他人の文は Markdown の記号として読まれない（リンク・HTML・見出し・リスト・数式・コードにならない）
        self.assertIn("> \\[ここを押す\\]\\(http\\:\\/\\/evil\\.example\\) \\<script\\>alert\\(1\\)\\<\\/script\\>\\", md)
        self.assertIn("> \\# 見出し\\", md)
        self.assertIn("> 1\\. リスト\\", md)
        self.assertIn("> \\$x\\^2\\$ と \\`code\\`", md)
        self.assertNotIn("<script>", md)

    def test_second_run_marks_app_replies_and_does_not_reclassify(self):
        fetch_replies.run(self.ctx)
        calls = self.jev_classify.call_count
        self.th.replies["900"][0]["has_replies"] = True                       # 101 の下に返信が付いた
        self.th.nested["101"] = [{"id": "201", "username": ME}]               # 自分が Threads のアプリから返した
        self.assertTrue(fetch_replies.run(self.ctx))
        comments = self.by_id()
        self.assertTrue(comments["101"]["replied"])
        self.assertEqual(self.jev_classify.call_count, calls)                 # 分類済みは聞き直さない
        self.assertEqual(self.read("status.json")["fetch"], {**self.read("status.json")["fetch"], "new": 0, "synced": 1})
        md = self.read("replies.md")
        self.assertIn("## 返信済み（直近 1件）", md)
        self.assertIn("**返信**: （Threads のアプリから返信済み）", md)

    def test_no_rewrite_when_nothing_changed(self):
        fetch_replies.run(self.ctx)
        fetch_replies.run(self.ctx)                                           # 新着 0 件の回（数が変わるので1回は書く）
        before = {n: self.read(n) for n in ("comments.json", "seen_comments.json", "status.json", "replies.md")}
        with mock.patch.object(store, "write_report", wraps=store.write_report) as w:
            self.assertTrue(fetch_replies.run(self.ctx))
        self.assertEqual(w.call_count, 0)                                     # 時刻だけの違いでは書き換えない（コミットが増えない）
        self.assertEqual(before, {n: self.read(n) for n in before})

    def test_cap_leaves_the_rest_for_next_run(self):
        self.ctx.config["replies"]["max_comments_per_run"] = 2
        fetch_replies.run(self.ctx)
        self.assertEqual(sorted(self.by_id()), ["101", "106"])                # 新しい順に2件
        self.assertEqual(self.read("status.json")["fetch"]["left"], 3)
        self.assertNotIn("104", self.read("seen_comments.json"))             # 残りは見ていない扱い
        self.assertIn("ほかに 3件は次の回に取り込みます", self.read("replies.md"))
        fetch_replies.run(self.ctx)
        self.assertEqual(sorted(self.by_id()), ["101", "104", "106", "107"])

    def test_without_claude_only_classifies(self):
        with mock.patch.object(fetch_replies.shutil, "which", lambda name: None):
            self.assertTrue(fetch_replies.run(self.ctx))
        comments = self.by_id()
        self.assertEqual(self.claude_prompts, [])
        self.assertTrue(all(c["drafts"] == [] and c.get("type") for c in comments.values()))
        self.assertEqual(comments["106"]["type"], "その他")                  # Claude が無ければ Jev の答えのまま
        self.assertIn("返信案は作っていない: Claude Code が無い", self.read("replies.md"))

    def test_on_github_without_claude_secret_only_classifies(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):      # ランナーに claude があっても、鍵が無ければ使わない
            self.assertTrue(fetch_replies.run(self.ctx))
        self.assertEqual(self.claude_prompts, [])
        self.assertEqual(self.read("status.json")["drafts"], {"skipped": "Secret の CLAUDE_CODE_OAUTH_TOKEN が無い"})

    def test_claude_failure_stops_drafting_for_this_run(self):
        def broken(prompt, model):
            self.claude_prompts.append((prompt, model))
            raise fetch_replies.ClaudeStopped("claude -p が失敗: Invalid API key · Please run /login")
        with mock.patch.object(fetch_replies, "ask_claude", broken):
            self.assertTrue(fetch_replies.run(self.ctx))
        self.assertEqual(len(self.claude_prompts), 1)                         # 1回失敗したら、この回はもう呼ばない
        status = self.read("status.json")
        self.assertEqual(status["drafts"]["ok"], False)
        self.assertIn("Invalid API key", status["drafts"]["reason"])
        self.assertIn("**返信案を作れなかった**", self.read("replies.md"))
        self.assertTrue(fetch_replies.run(self.ctx))                          # 次の回に作り直す
        self.assertEqual(len(self.by_id()["101"]["drafts"]), 2)

    def test_jev_failure_keeps_comments_and_reports(self):
        self.jev_classify.side_effect = lambda items, concurrency=4: {it["id"]: {"error": "HTTPError: 401 Unauthorized"} for it in items}
        self.assertFalse(fetch_replies.run(self.ctx))                        # 失敗として知らせる（終了コード 1）
        comments = self.by_id()
        self.assertEqual(len(comments), 5)                                    # 取得と保存は続ける
        self.assertTrue(all(c["label_error"].startswith("HTTPError") for c in comments.values()))
        self.assertEqual(comments["101"]["drafts_v"], 1)                      # 分類前の一般的な案
        md = self.read("replies.md")
        self.assertIn("**分類が止まっている**", md)
        self.assertIn("種類 判定できず", md)
        self.jev_classify.side_effect = fake_classify                        # 直ったら分けて、案を作り直す
        self.assertTrue(fetch_replies.run(self.ctx))
        self.assertEqual((self.by_id()["101"]["type"], self.by_id()["101"]["drafts_v"]), ("質問", 2))

    def test_reply_send(self):
        fetch_replies.run(self.ctx)
        sent = []
        with mock.patch.object(reply, "create_reply", lambda text, to, token, uid: sent.append((text, to)) or "c1"), \
             mock.patch.object(reply, "publish_post", lambda cid, token, uid: "t1"):
            self.assertEqual(reply.send(self.ctx, " 101 ", "ありがとうございます。明日詳しく書きます"), "t1")
            self.assertIsNone(reply.send(self.ctx, "101", "ありがとうございます。明日詳しく書きます"))  # 同じ文は2回出さない
            for cid, text, why in (("999", "こんにちは", "comments.json に無い"), ("abc", "こんにちは", "数字だけ"),
                                   ("101", "あ" * 501, "501 文字"), ("101", "  ", "空")):
                with self.assertRaisesRegex(RuntimeError, why):
                    reply.send(self.ctx, cid, text)
        self.assertEqual(sent, [("ありがとうございます。明日詳しく書きます", "101")])
        c = self.by_id()["101"]
        self.assertEqual((c["replied"], c["replied_thread_id"]), (True, "t1"))
        md = self.read("replies.md")
        self.assertIn("**返信**: ありがとうございます。明日詳しく書きます", md)
        self.assertIn("返信待ち 4件・返信済み 1件", md)


class JevAdapterTest(unittest.TestCase):
    """lib/jev.py の2つの道（TypeSafe に直接・Vercel AI Gateway 経由）が、同じ形の答えを返すか"""

    def test_gateway_path(self):
        seen = {}

        def fake_node(cmd, capture_output, text, timeout):
            src, dst = Path(cmd[2]), Path(cmd[3])
            req = json.loads(src.read_text(encoding="utf-8"))
            seen.update(req)
            out = {}
            for r in req["requests"]:
                if "type" in r["questions"]:
                    out[r["id"]] = {"answers": {
                        "type": {"type": "choice", "choice": "質問", "probabilities": {"質問": 0.91}},
                        "emotion": {"type": "choice", "choice": "驚き・関心", "probabilities": {"驚き・関心": 0.7}},
                        "hostile": {"type": "boolean", "probability": 0.03}},
                        "confidence": {"type": 0.91, "emotion": 0.7}, "usage": {"inputTokens": 1400, "outputTokens": 0},
                        "model": "typesafe-ai/jev"}
                else:
                    out[r["id"]] = {"answers": {k: {"type": "boolean", "probability": 0.8 if k == "invented_1" else 0.1}
                                                for k in r["questions"]}, "usage": {}, "model": "typesafe-ai/jev"}
            dst.write_text(json.dumps(out), encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="")

        env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "AI_GATEWAY_API_KEY")}
        env["AI_GATEWAY_API_KEY"] = "dummy"
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(jev.subprocess, "run", fake_node):
            res = jev.classify_comments([{"id": "a", "post": "投稿", "comment": "教えてください"}])["a"]
            self.assertEqual(seen["model"], jev.GATEWAY_MODEL)
            q = seen["requests"][0]["questions"]
            self.assertEqual((q["type"]["type"], q["emotion"]["type"], q["hostile"]["type"]), ("choice", "choice", "boolean"))
            self.assertIn("その他", q["type"]["criteria"])
            self.assertEqual(seen["requests"][0]["state"], {"post": {"text": "投稿"}, "comment": {"text": "教えてください"}})
            self.assertEqual((res["type"]["choice"], res["type"]["confidence"], res["hostile_p"], res["tokens"]),
                             ("質問", 0.91, 0.03, 1400))
            flags = jev.check_drafts([{"id": "d", "post": "p", "comment": "c", "drafts": ["案1", "案2"]}])["d"]
            self.assertEqual(flags, [0.1, 0.8])

    def test_typesafe_path(self):
        answer = lambda c, conf: SimpleNamespace(choice=c, confidence=conf, probabilities={c: conf})

        class FakeClient:
            def __init__(self, model, retry):
                self.model = model

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def system_one(self, state, questions):
                assert set(questions) == {"type", "emotion", "hostile"}
                return SimpleNamespace(
                    choices={"type": answer("共感・応援", 0.97), "emotion": answer("感謝・尊敬", 0.93)},
                    nouls={"hostile": SimpleNamespace(noul=0.012345)},
                    model=self.model, usage=SimpleNamespace(input_tokens=1500, output_tokens=0))

        env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "AI_GATEWAY_API_KEY")}
        env["TYPESAFE_API_KEY"] = "dummy"
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(jev, "AsyncTypeSafeClient", FakeClient):
            res = jev.classify_comments([{"id": "a", "post": "投稿", "comment": "ありがとう"}])["a"]
        self.assertEqual((res["type"]["choice"], res["emotion"]["confidence"], res["hostile_p"], res["model"], res["tokens"]),
                         ("共感・応援", 0.93, 0.0123, jev.MODEL, 1500))


class MarkdownTest(unittest.TestCase):
    def test_quote_keeps_line_breaks(self):
        self.assertEqual(store.quote("一行目\n二行目\n\n三行目."), ["> 一行目\\", "> 二行目", ">", "> 三行目\\."])

    def test_parse_threads_timestamp(self):
        self.assertEqual(store.jst_label("2026-09-27T04:58:00+0000"), "09/27 13:58")
        self.assertIsNone(store.parse_ts("not a time"))


if __name__ == "__main__":
    unittest.main()
