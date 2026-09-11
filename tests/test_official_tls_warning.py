"""`--tpex-no-verify` 時壓掉 urllib3 逐請求的 InsecureRequestWarning（2026-09-11 Hetzner 實測噪音）。

守門重點：壓制**只能**在關閉驗證時發生。預設（驗證開啟）若也裝上 ignore 過濾器，
等於把真正該看見的警告永久吞掉——第二支測試就是為此而寫。
兩支都在 `warnings.catch_warnings()` 內跑，離開時還原全域 filters，不污染其他測試。
"""
from __future__ import annotations

import warnings

import pytest

from iching import twse as T

InsecureRequestWarning = pytest.importorskip("urllib3.exceptions").InsecureRequestWarning


def _has_ignore_filter() -> bool:
    return any(f[0] == "ignore" and f[2] is InsecureRequestWarning for f in warnings.filters)


def test_no_verify_silences_per_request_warning() -> None:
    with warnings.catch_warnings():
        warnings.resetwarnings()
        assert not _has_ignore_filter()
        T.OfficialClient(tpex_verify=False)
        assert _has_ignore_filter(), "tpex_verify=False 應壓掉 InsecureRequestWarning"


def test_default_verify_leaves_warning_visible() -> None:
    with warnings.catch_warnings():
        warnings.resetwarnings()
        T.OfficialClient()          # 預設 tpex_verify=True
        assert not _has_ignore_filter(), "驗證開啟時不得動到 InsecureRequestWarning 的過濾器"


def test_helper_is_idempotent() -> None:
    with warnings.catch_warnings():
        warnings.resetwarnings()
        T.silence_insecure_warnings()
        T.silence_insecure_warnings()
        assert _has_ignore_filter()
