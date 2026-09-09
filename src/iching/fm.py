"""FinMind client（P2 回補專用）。

設計原則（借自 taiwan-flows/src/finmind.py 的 lazy token、postmkt/src/fmclient.py 的 402/429 等 65 秒、
taiwan-stock-news build_news.py fetch_news_one 的「失敗必須回報、不可與空資料混同」）：

- token 只從環境變數 `FINMIND_TOKEN` 或 repo 根目錄 `.env` 讀，**lazy**（建構時不讀），
  **絕不 print／log／寫進例外訊息**（CANON 第 1 條）。
- 回應分類（`classify_response`，純函式、可離線測）：
    ok          HTTP 200 且 body.status==200（或無 status）且 data 非空
    empty       同上但 data 為空——非交易日／該區間無資料（2026-09-09 實測：帶 data_id 查週六回 200 空陣列）
    permission  HTTP 400 且 msg 含「level」（2026-09-09 實測：免 token 打 Sponsor 資料集回
                `Your level is free. Please update your user level`）→ 需 Sponsor，**不重試**
    quota       HTTP 402/429（2026-09-09 實測：`Requests reach the upper limit`）→ 等 65 秒重試
    error       其他（5xx／非 JSON／400 非權限）→ 短退避重試，耗盡後由呼叫端記成 failure
- 所有分類都**不看 HTTP code 單獨判定**：P0-A §4.5 指出 FinMind 會回 HTTP 200 但 body status 400。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
QUOTA_WAIT_SEC = 65          # postmkt fmclient.RATE_WAIT：略大於 FinMind 一分鐘額度窗
QUOTA_MAX_WAITS = 8          # 8 × 65s ≈ 8.7 分鐘仍 402 → 放棄（多半是小時額度用盡，讓 run 中止、稍後續跑）
TRANSIENT_BACKOFF = (5.0, 15.0, 45.0)
_TOKEN_RE = re.compile(r"(token=)[^&\s'\"]+", re.IGNORECASE)


def redact(text: str) -> str:
    """任何可能含 token 的字串（requests 例外訊息會帶完整 URL）在進入 log／例外前先遮。"""
    return _TOKEN_RE.sub(r"\1<redacted>", str(text))


class FinMindError(Exception):
    """基底；訊息不含 token。"""


class PermissionRequired(FinMindError):
    """需 Sponsor 級（或 token 缺失）。"""


class QuotaExceeded(FinMindError):
    """額度用盡且等待後仍失敗。"""


class TransientError(FinMindError):
    """連線／5xx／非 JSON／其他 400，重試耗盡。"""


def load_token(env_file: Path | None = None, required: bool = True) -> str | None:
    """環境變數優先，其次 .env（每行 KEY=VALUE，忽略 # 註解與引號）。回傳值不得被 log。"""
    t = os.environ.get("FINMIND_TOKEN", "").strip()
    if t:
        return t
    if env_file and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("FINMIND_TOKEN="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    if required:
        raise PermissionRequired("找不到 FINMIND_TOKEN（環境變數或 .env 都沒有）")
    return None


def classify_response(status_code: int, body: Any, text: str = "") -> tuple[str, str]:
    """純函式：回 (kind, msg)。kind ∈ ok/empty/permission/quota/error。"""
    if status_code in (402, 429):
        msg = ""
        if isinstance(body, dict):
            msg = str(body.get("msg", ""))
        return "quota", f"HTTP {status_code} {msg}".strip()
    if not isinstance(body, dict):
        return "error", f"HTTP {status_code} 非 JSON 回應：{text[:80]!r}"
    msg = str(body.get("msg", ""))
    bstatus = body.get("status")
    if status_code == 400 or bstatus == 400:
        if "level" in msg.lower() or "sponsor" in msg.lower():
            return "permission", msg
        return "error", f"HTTP {status_code} status={bstatus} msg={msg}"
    if status_code != 200:
        return "error", f"HTTP {status_code} status={bstatus} msg={msg}"
    if bstatus not in (200, None):
        if "limit" in msg.lower():
            return "quota", msg
        if "level" in msg.lower():
            return "permission", msg
        return "error", f"status={bstatus} msg={msg}"
    data = body.get("data")
    if not data:
        return "empty", msg or "empty"
    if not isinstance(data, list):
        return "error", f"data 非 list：{type(data).__name__}"
    return "ok", msg or "success"


class FinMind:
    """單一 session、全域最小間隔、分類重試。

    `get()` 回 list[dict]（可為空＝empty）。失敗一律以例外表達：
    PermissionRequired／QuotaExceeded／TransientError——**呼叫端絕不可把例外當成空資料**。
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        env_file: Path | None = None,
        min_interval: float = 0.7,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 60.0,
        allow_no_token: bool = False,
    ) -> None:
        self._token = token
        self._env_file = env_file
        self._token_loaded = token is not None
        self._allow_no_token = allow_no_token
        self.min_interval = min_interval
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self.timeout = timeout
        self._last_call = 0.0
        self.n_requests = 0
        self.n_quota_waits = 0

    # -- token（lazy） -----------------------------------------------------
    def _get_token(self) -> str | None:
        if not self._token_loaded:
            self._token = load_token(self._env_file, required=not self._allow_no_token)
            self._token_loaded = True
        return self._token

    def has_token(self) -> bool:
        return bool(self._get_token())

    # -- 節流 ---------------------------------------------------------------
    def _throttle(self) -> None:
        now = self._clock()
        wait = self.min_interval - (now - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = self._clock()

    def _raw_get(self, params: dict[str, Any]) -> tuple[int, Any, str]:
        q = dict(params)
        tok = self._get_token()
        # token 走 Authorization header 而非 ?token=（P0-A §2 實測兩種皆可）：
        # 這樣 requests 例外訊息裡的 URL 不會帶 token。
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        self._throttle()
        self.n_requests += 1
        r = self.session.get(FINMIND_DATA_URL, params=q, headers=headers, timeout=self.timeout)
        text = r.text
        try:
            body = r.json()
        except (ValueError, json.JSONDecodeError):
            body = None
        return r.status_code, body, text

    # -- 對外 ----------------------------------------------------------------
    def get(self, dataset: str, **params: Any) -> list[dict]:
        """查詢一個 dataset。永遠不回 None：空＝[]，失敗＝例外。"""
        q = {"dataset": dataset, **{k: v for k, v in params.items() if v is not None}}
        label = f"{dataset} {params.get('data_id', '')} {params.get('start_date', '')}~{params.get('end_date', '')}".strip()
        transient = 0
        quota = 0
        while True:
            try:
                code, body, text = self._raw_get(q)
            except requests.RequestException as e:  # 連線層
                kind, msg = "error", redact(f"{type(e).__name__}: {str(e)[:160]}")
            else:
                kind, msg = classify_response(code, body, text)

            if kind == "ok":
                return list(body["data"])
            if kind == "empty":
                return []
            if kind == "permission":
                raise PermissionRequired(f"{label}: {msg}")
            if kind == "quota":
                quota += 1
                self.n_quota_waits += 1
                if quota > QUOTA_MAX_WAITS:
                    raise QuotaExceeded(f"{label}: 等待 {QUOTA_MAX_WAITS}×{QUOTA_WAIT_SEC}s 後仍 {msg}")
                self._sleep(QUOTA_WAIT_SEC)
                continue
            # error
            if transient >= len(TRANSIENT_BACKOFF):
                raise TransientError(f"{label}: {msg}")
            self._sleep(TRANSIENT_BACKOFF[transient])
            transient += 1
