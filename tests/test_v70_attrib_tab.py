# -*- coding: utf-8 -*-
"""P6-9 归因台（view 侧门）：候选取数面 + 判定槽的落账通道。

渲染在 `tests/render_attrib.js` 里真跑；本文件钉**接线**这三件事：
① `/view/act` 的 `attrib` 动作必须带回**机读载荷**（`data`），否则页面只能
   拿人读 brief 硬凑表格；
② `attrib_verdict` 必须**注册表可见**（B 档）且在 `writeactions` 里**有实现**
   （注册表声明了却没人实现 → 服务端 500，用户白点一次"确认"）；
③ 预演零副作用、执行只写 attrib_meta.db（事实库 harvester.db 一字节不动），
   且同一 (pattern, rule) 再判一次是**改判覆盖**而不是新增一行。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_HERE = Path(__file__).resolve().parent
VIEW_ROOT = _HERE.parent
sys.path.insert(0, str(VIEW_ROOT))

from actions import ensure_harvester_importable  # noqa: E402

_ERR = ensure_harvester_importable()
if _ERR is None:
    from actions import actionsserve, writeactions  # noqa: E402
    from harvester.attrib import list_verdicts  # noqa: E402

REG = VIEW_ROOT / "actions" / "actions_registry.json"


def _cfg(tmp: Path, **over):
    """最小 cfg：只填本文件用到的字段（其余 None）。"""
    base = dict(db=tmp / "harvester.db", attrib_meta=tmp / "attrib_meta.db",
                reviews_meta=None, topics_meta=None, cards_root=None,
                chain_root=None, keywords_meta=None, suggestions_meta=None,
                artifacts_meta=None, exports_root=None)
    base.update(over)
    return SimpleNamespace(**base)


def _make_db(tmp: Path) -> Path:
    """事实库（只读侧）：只要有 sessions 表即可（本文件不跑真 attrib）。"""
    db = tmp / "harvester.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY)")
        con.execute("INSERT INTO sessions VALUES ('s0')")
        con.commit()
    finally:
        con.close()
    return db


class TestRegistryDeclaresIt(unittest.TestCase):
    def setUp(self):
        self.reg = json.loads(REG.read_text(encoding="utf-8"))

    def test_write_action_is_declared_b_tier(self):
        hit = [a for a in self.reg["write_actions"]
               if a["action_id"] == "attrib_verdict"]
        self.assertEqual(len(hit), 1, "attrib_verdict 没进注册表")
        a = hit[0]
        self.assertEqual(a["tier"], "B", "写台账必须是 B 档（两段提交）")
        self.assertEqual(a["cmd"], "attrib-review")
        self.assertEqual(a["target"], "attrib_meta")
        self.assertEqual(a["risk"], "reversible")
        names = {p["name"] for p in a["params"]}
        self.assertTrue({"pattern", "rule", "verdict"} <= names,
                        "缺必填参数：%s" % sorted(names))

    def test_target_is_known_so_page_can_print_the_path(self):
        """确认页要显式印出目标库路径：target 不在 write_targets 里就没得印。"""
        self.assertIn("attrib_meta", self.reg["write_targets"])

    def test_write_action_has_implementation(self):
        self.assertIn("attrib_verdict", writeactions.WRITE_ACTIONS,
                      "注册表声明了 B 档动作，writeactions 里却没有实现")


class TestPlanIsReadOnly(unittest.TestCase):
    def _run(self, md, fn, params):
        before = md.read_bytes() if md.exists() else b""
        try:
            return fn(params)
        finally:
            after = md.read_bytes() if md.exists() else b""
            self.assertEqual(before, after, "预演改了 meta 库字节（必须零副作用）")

    def test_plan_creates_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_db(tmp)
            cfg = _cfg(tmp)
            plan = self._run(tmp / "attrib_meta.db",
                             lambda p: writeactions._plan_attrib_verdict(p, cfg),
                             {"pattern": "ABORTED", "rule": "AR-003",
                              "verdict": "rejected", "note": "不是中断"})
        self.assertEqual(plan["target_path"], str(tmp / "attrib_meta.db"))
        text = "\n".join(plan["human"])
        self.assertIn("ABORTED", text)
        self.assertIn("AR-003", text)
        self.assertIn("rejected", text)
        self.assertIn("不碰 harvester.db", text)
        self.assertIn("新增", text)          # 台账空 → 新增
        self.assertFalse(plan["noop"])

    def test_plan_says_change_when_row_exists(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_db(tmp)
            cfg = _cfg(tmp)
            writeactions._exec_attrib_verdict(
                {"pattern": "ABORTED", "rule": "AR-003", "verdict": "accepted"},
                cfg)
            plan = writeactions._plan_attrib_verdict(
                {"pattern": "ABORTED", "rule": "AR-003", "verdict": "rejected"},
                cfg)
        self.assertIn("改判", "\n".join(plan["human"]))
        self.assertIsNotNone(plan["before"])
        self.assertEqual(plan["before"]["verdict"], "accepted")

    def test_plan_rejects_bad_params(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_db(tmp)
            cfg = _cfg(tmp)
            for bad in ({"rule": "AR-1", "verdict": "accepted"},   # 缺 pattern
                        {"pattern": "X", "verdict": "accepted"},   # 缺 rule
                        {"pattern": "X", "rule": "AR-1", "verdict": "meh"}):
                with self.assertRaises(writeactions.WriteError):
                    writeactions._plan_attrib_verdict(bad, cfg)

    def test_unconfigured_meta_fails_loud(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(Path(td), attrib_meta=None)
            with self.assertRaises(writeactions.WriteError) as cm:
                writeactions._plan_attrib_verdict(
                    {"pattern": "X", "rule": "AR-1", "verdict": "accepted"}, cfg)
        self.assertIn("attrib-meta", str(cm.exception))


class TestExecuteWritesOnlyLedger(unittest.TestCase):
    def test_execute_writes_ledger_and_leaves_fact_db_alone(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            db = _make_db(tmp)
            fact_before = db.read_bytes()
            cfg = _cfg(tmp)
            out = writeactions._exec_attrib_verdict(
                {"pattern": "ABORTED", "rule": "AR-003", "cls": "unclassified",
                 "verdict": "rejected", "note": "只是录制中断", "by": "tester"},
                cfg)
            self.assertEqual(out["recorded"], 1)
            self.assertEqual(db.read_bytes(), fact_before,
                             "归因裁决动了事实库（只许写台账）")
            rows = list_verdicts(tmp / "attrib_meta.db")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pattern_key"], "ABORTED")
        self.assertEqual(rows[0]["rule_id"], "AR-003")
        self.assertEqual(rows[0]["verdict"], "rejected")
        self.assertEqual(rows[0]["note"], "只是录制中断")

    def test_second_verdict_overwrites_instead_of_duplicating(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _make_db(tmp)
            cfg = _cfg(tmp)
            writeactions._exec_attrib_verdict(
                {"pattern": "ABORTED", "rule": "AR-003", "verdict": "accepted"},
                cfg)
            writeactions._exec_attrib_verdict(
                {"pattern": "ABORTED", "rule": "AR-003", "verdict": "revise",
                 "note": "why 要改"}, cfg)
            rows = list_verdicts(tmp / "attrib_meta.db")
        self.assertEqual(len(rows), 1, "改判应覆盖同一行，不应新增")
        self.assertEqual(rows[0]["verdict"], "revise")
        self.assertEqual(rows[0]["note"], "why 要改")


class TestActionCarriesMachinePayload(unittest.TestCase):
    """`attrib` 动作必须把 `--json` 的解析结果带回页面（命令台仍看 brief）。"""

    def _call(self, replies):
        cfg = SimpleNamespace(db=Path("h.db"), attrib_meta=Path("m.db"))
        with mock.patch.object(actionsserve, "_cli_report",
                               side_effect=replies):
            return actionsserve._action_attrib(cfg, {}), cfg

    def test_data_is_parsed_json(self):
        payload = {"schema": "attrib/1", "patterns": [], "summary": {},
                   "coverage": {"line_text": "x"}, "reviewable": {"level": "R3"}}
        rep, _ = self._call([
            {"exit_code": 0, "stdout": "brief 文本"},
            {"exit_code": 0, "stdout": json.dumps(payload, ensure_ascii=False)},
        ])
        self.assertEqual(rep["stdout"], "brief 文本", "命令台的人读出口不许被改")
        self.assertEqual(rep["data"]["schema"], "attrib/1")
        self.assertEqual(rep["data"]["reviewable"]["level"], "R3")

    def test_broken_json_is_reported_not_silent(self):
        rep, _ = self._call([
            {"exit_code": 0, "stdout": "brief"},
            {"exit_code": 0, "stdout": "not json"},
        ])
        self.assertNotIn("data", rep)
        self.assertIn("data_error", rep, "机读面坏了必须显式报出来")

    def test_json_command_failure_is_reported(self):
        rep, _ = self._call([
            {"exit_code": 0, "stdout": "brief"},
            {"exit_code": 2, "stdout": "", "stderr": "库不存在"},
        ])
        self.assertIn("data_error", rep)
        self.assertIn("库不存在", rep["data_error"])

    def test_meta_path_is_forwarded_when_configured(self):
        seen = []
        cfg = SimpleNamespace(db=Path("h.db"), attrib_meta=Path("m.db"))
        with mock.patch.object(actionsserve, "_cli_report",
                               side_effect=lambda argv: seen.append(argv) or
                               {"exit_code": 0, "stdout": "{}"}):
            actionsserve._action_attrib(cfg, {})
        self.assertEqual(len(seen), 2, "brief 与 json 各一次")
        for argv in seen:
            self.assertIn("--meta", argv)
            self.assertEqual(argv[argv.index("--meta") + 1], "m.db")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()