"""Threads API の共通処理（投稿・数字・鍵）"""
import time

import requests

BASE_URL = "https://graph.threads.net/v1.0"


class ThreadsApiError(Exception):
    """Threads API error with a safe, actionable message."""


def _raise_for_threads_error(res: requests.Response):
    if res.ok:
        return

    try:
        payload = res.json()
    except ValueError:
        payload = {}

    error = payload.get("error", {})
    if error:
        parts = [
            f"Threads API error {res.status_code}",
            f"code={error.get('code')}",
            f"type={error.get('type')}",
            f"subcode={error.get('error_subcode')}",
            f"message={error.get('message')}",
            f"fbtrace_id={error.get('fbtrace_id')}",
        ]
        raise ThreadsApiError(" | ".join(str(p) for p in parts if p is not None))

    raise ThreadsApiError(f"Threads API error {res.status_code}: {res.text[:500]}")


def create_post(text: str, token: str, user_id: str) -> str:
    """投稿コンテナを作成してpost_idを返す"""
    res = requests.post(
        f"{BASE_URL}/{user_id}/threads",
        params={
            "media_type": "TEXT",
            "text": text,
            "access_token": token,
        },
    )
    _raise_for_threads_error(res)
    return res.json()["id"]


def wait_container(creation_id: str, token: str, timeout: int = 60):
    """コンテナの処理が終わる（FINISHED）まで待つ。
    作った直後は IN_PROGRESS で、そのまま公開すると code=24 / subcode=4279009
    「The requested resource does not exist」で断られる（2026-09-24 実測: 0秒 IN_PROGRESS → 5秒 FINISHED）"""
    deadline = time.time() + timeout
    while True:
        res = requests.get(
            f"{BASE_URL}/{creation_id}",
            params={"fields": "status,error_message", "access_token": token},
        )
        _raise_for_threads_error(res)
        data = res.json()
        status = data.get("status")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise ThreadsApiError(f"コンテナの処理に失敗: status={status} message={data.get('error_message')}")
        if time.time() > deadline:
            raise ThreadsApiError(f"コンテナの処理が {timeout} 秒で終わらない（status={status}）")
        time.sleep(3)


def publish_post(creation_id: str, token: str, user_id: str) -> str:
    """コンテナの処理が終わるのを待ってから公開し、thread_idを返す"""
    wait_container(creation_id, token)
    res = requests.post(
        f"{BASE_URL}/{user_id}/threads_publish",
        params={
            "creation_id": creation_id,
            "access_token": token,
        },
    )
    _raise_for_threads_error(res)
    return res.json()["id"]


def get_insights(thread_id: str, token: str) -> dict:
    """投稿のインサイト（いいね・インプ等）を取得"""
    res = requests.get(
        f"{BASE_URL}/{thread_id}/insights",
        params={
            "metric": "views,likes,replies,reposts,quotes",
            "access_token": token,
        },
    )
    _raise_for_threads_error(res)
    data = res.json().get("data", [])
    return {item["name"]: item["values"][0]["value"] for item in data}


def get_my_posts(token: str, user_id: str, limit: int = 25) -> list:
    """自分の最近の投稿一覧を取得"""
    res = requests.get(
        f"{BASE_URL}/{user_id}/threads",
        params={
            "fields": "id,text,timestamp,permalink",
            "limit": limit,
            "access_token": token,
        },
    )
    _raise_for_threads_error(res)
    return res.json().get("data", [])




def get_user_insights(token: str, user_id: str) -> dict:
    """ユーザーレベルのインサイト（プロフィールviews、リンクclicks等）を取得"""
    res = requests.get(
        f"{BASE_URL}/{user_id}/threads_insights",
        params={
            "metric": "views,likes,replies,reposts,quotes,followers_count,clicks",
            "access_token": token,
        },
    )
    _raise_for_threads_error(res)
    data = res.json().get("data", [])
    result = {}
    for item in data:
        name = item["name"]
        # total_value がある場合（followers_count等）
        if "total_value" in item:
            result[name] = item["total_value"].get("value", 0)
        # values 配列がある場合
        elif "values" in item and item["values"]:
            result[name] = item["values"][0].get("value", 0)
    return result




# ========================================
# トークン（set_token.py / refresh_token.py）
# ========================================

TOKEN_URL = "https://graph.threads.net"


def get_me(token: str) -> dict:
    """トークンの持ち主（id, username）"""
    res = requests.get(
        f"{BASE_URL}/me",
        params={"fields": "id,username", "access_token": token},
    )
    _raise_for_threads_error(res)
    return res.json()


def debug_token(token: str) -> dict:
    """トークンの状態（is_valid, issued_at, expires_at, scopes）"""
    res = requests.get(
        f"{TOKEN_URL}/debug_token",
        params={"input_token": token, "access_token": token},
    )
    _raise_for_threads_error(res)
    payload = res.json()
    return payload.get("data", payload)


def exchange_long_lived(short_token: str, app_secret: str) -> dict:
    """短期トークン（1時間）を長期トークン（60日）に交換する"""
    res = requests.get(
        f"{TOKEN_URL}/access_token",
        params={
            "grant_type": "th_exchange_token",
            "client_secret": app_secret,
            "access_token": short_token,
        },
    )
    _raise_for_threads_error(res)
    return res.json()


def refresh_long_lived(token: str) -> dict:
    """長期トークンを60日延長する（発行から24時間以上・期限切れ前のときだけ通る）"""
    res = requests.get(
        f"{TOKEN_URL}/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": token},
    )
    _raise_for_threads_error(res)
    return res.json()
