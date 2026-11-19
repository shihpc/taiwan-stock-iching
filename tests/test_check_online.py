"""P4 驗收線上破快取實查（scripts/check_online.py；docs/P4-PREVIEW.md §34）。離線：以假 fetch／git 驗判準。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_online as CO  # noqa: E402


@pytest.mark.parametrize("case,rc", [("ok", 0), ("cond200", 0), ("stale_cdn", 1), ("stale_origin", 1), ("cond_bad", 1), ("err", 1)])
def test_check_online_rules(monkeypatch, capsys, case, rc):
    want = b"NEW"
    monkeypatch.setattr(CO, "git_bytes", lambda ref, p: want)

    def fetch(url, headers=None):
        if headers:                                    # 條件式請求
            return {"cond200": (200, want, {}), "cond_bad": (200, b"OLD", {})}.get(case, (304, b"", {}))
        if "?cb=" in url:
            return (500, b"", {}) if case == "err" else (200, b"OLD" if case == "stale_origin" else want, {})
        return 200, (b"OLD" if case == "stale_cdn" else want), {"etag": '"x"', "cache-control": "max-age=600"}
    monkeypatch.setattr(CO, "fetch", fetch)
    assert CO.main(["--files", "index.html"]) == rc
    out = capsys.readouterr().out
    assert ("全部與 ref 相同" in out) == (rc == 0)
    assert CO.main(["--base", "https://x"]) == 2
