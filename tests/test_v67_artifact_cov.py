# -*- coding: utf-8 -*-
"""P6-8 view 侧门：下轨产物流必须自带**覆盖度自陈**。

为什么在 view 侧再钉一次：后端 `artifacts.coverage()` 正确，不代表页面拿得到
——`_artifacts_track` 少传一个键，页面就会静默退化成"只有文件/版本数字"，
而那正是"部分覆盖冒充全库"。这文件只测**这一层接线**（不重复后端的算术测试）。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
VIEW_ROOT = _HERE.parent
sys.path.insert(0, str(VIEW_ROOT))

from actions import ensure_harvester_importable  # noqa: E402

_ERR = ensure_harvester_importable()
if _ERR is None:
    from actions import dualtrack  # noqa: E402
    from harvester.artifacts import ensure_artifacts_db  # noqa: E402


def _make(tmp: Path, n_sessions: int, sids: list[str]) -> tuple[Path, Path]:
    db = tmp / "idx.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO sessions (sid) VALUES (?)",
                        [(f"s{i}",) for i in range(n_sessions)])
        con.commit()
    finally:
        con.close()
    meta = ensure_artifacts_db(tmp / "art.db")
    con = sqlite3.connect(str(meta))
    try:
        con.executemany(
            "INSERT INTO artifacts (sid, seq, ts, tool, file_path, old_text, "
            "new_text) VALUES (?,?,?,?,?,?,?)",
            [(sid, 0, "2026-10-01T00:00:00", "Write", "a.md", None, "x")
             for sid in sids])
        con.commit()
    finally:
        con.close()
    return db, meta


class TestTrackCarriesCoverage(unittest.TestCase):
    def test_configured_track_has_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make(Path(td), 10, ["s0", "s1"])
            cfg = SimpleNamespace(db=str(db), artifacts_meta=str(meta))
            art = dualtrack._artifacts_track(cfg, ["s0"])
        self.assertTrue(art["configured"])
        cov = art.get("coverage")
        self.assertIsInstance(cov, dict, "下轨载荷缺 coverage（页面会退化成裸数字）")
        self.assertEqual(cov["registered_sessions"], 2)
        self.assertEqual(cov["total_sessions"], 10)
        self.assertEqual(cov["pct"], 20.0)
        self.assertIn("仅 dsh 源", cov["line"])
        self.assertIn("本面不代表全库", cov["line"])

    def test_unconfigured_track_still_has_coverage(self):
        """没配 artifacts_meta 是最容易被写成"没有数字"的分支——仍要给分母。"""
        with tempfile.TemporaryDirectory() as td:
            db, _ = _make(Path(td), 6, ["s0"])
            cfg = SimpleNamespace(db=str(db), artifacts_meta=None)
            art = dualtrack._artifacts_track(cfg, [])
        self.assertFalse(art["configured"])
        cov = art.get("coverage")
        self.assertIsInstance(cov, dict, "未配置分支漏了 coverage")
        self.assertEqual(cov["registered_sessions"], 0)
        self.assertEqual(cov["total_sessions"], 6)
        self.assertIn("仅 dsh 源", cov["line"])

    def test_coverage_matches_backend_function(self):
        """view 侧不许自己算：逐字段等于 `artifacts.coverage()`。"""
        from harvester.artifacts import coverage
        with tempfile.TemporaryDirectory() as td:
            db, meta = _make(Path(td), 4, ["s0"])
            cfg = SimpleNamespace(db=str(db), artifacts_meta=str(meta))
            art = dualtrack._artifacts_track(cfg, [])
            want = coverage(db, meta)
        for k in ("registered_sessions", "registered_versions",
                  "total_sessions", "pct", "line", "sources"):
            self.assertEqual(art["coverage"][k], want[k], "字段 %s 不同源" % k)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()