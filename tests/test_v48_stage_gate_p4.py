# -*- coding: utf-8 -*-
"""P4-6 阶段门测试：两条新管线首份报告归档 + 确认流可用（会红的那种钉子）。

阶段门原文（SOP §3 P4 表）：**两条新管线各出首份候选报告；确认流可用**。
两条新管线 = 反复叮嘱之两层（用户裁决语义，会话 8 定）：

- 层一 nags（P4-1/P4-2）：指令遵循叮嘱 → 建议池归宿裁决；
- 层二 revisions（P4-3/P4-4）：产出打磨推敲 → 双轨时间线候选带。

首份报告归档 `docs/reports/p4-*-2026-10-13.*`；确认流 = 候选 → 复制就绪
`suggest-status` 命令（**本视图绝不代跑**，红线）→ suggestions_meta 台账
（pending/adopted/rejected）→ 人工裁决留痕。

走查做成测试（P3-6 先例），判据（都能说出什么时候会红）：

1. **归档门**：三份首份报告存在且结构齐（账目/锚点/非结论声明/裁决列）；
   nags pool md↔json 对账（json 条目数 == md 唯一 key 数 == md 裁决命令数，
   且 key 集合恒等）。报告缺失/被改名 → 红。
2. **占位符泄漏门**：归档报告不得含未插值的 `%s`/`%d`——P4-6 修的真 bug
   （revisions.render_markdown 的 Top1 行漏写 `%` 运算符，占位符原样进了
   归档报告）钉住防复发。
3. **确认流端到端门**：fixture 库跑 nags CLI 产出 pool → 取条目 status_cmd
   的 key → **真跑上游 `harvester suggest-status`**（子进程，同 CLI 路径）
   → 回查 suggestion_status 表 pending → adopted（adopted_at 只增不减）。
   命令文案与上游 CLI 脱节（参数改名/choices 收窄/建表变更）→ 红。
4. **候选语义门**：revisions 候选带锚点（sid + rowid 对，可回翻原文）；
   渲染必含「非结论」声明——确认 = 人看原文后升级，模块不代下结论、
   不自动写库（dualtrack 侧的 prev/cur 自带与 honest 声明由 test_v44 钉）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UPSTREAM = ROOT.parent / "session-harvester"
REPORTS = ROOT / "docs" / "reports"

#: 首份归档报告（两条管线 × 各自形态）
NAGS_CAND = REPORTS / "p4-nags-candidates-2026-10-13.md"
NAGS_POOL_MD = REPORTS / "p4-nags-pool-2026-10-13.md"
NAGS_POOL_JSON = REPORTS / "p4-nags-pool-2026-10-13.json"
REV_CAND = REPORTS / "p4-revisions-candidates-2026-10-13.md"

MSG_DDL = """CREATE TABLE messages(
    rowid_ INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT, role TEXT, text TEXT DEFAULT '', raw TEXT DEFAULT '',
    ts TEXT)"""

NAG_TEXT = "做完了，注意要更新文档（仓库结构说明/操作手册都要对上）"


def _mkdb(rows):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    con = sqlite3.connect(f.name)
    con.execute(MSG_DDL)
    for sid, raw in rows:
        con.execute("INSERT INTO messages(sid, role, text, raw) "
                    "VALUES(?, 'user', '', ?)", (sid, raw))
    con.commit()
    return con, Path(f.name)


class TestArchiveGate(unittest.TestCase):
    """① 归档门 + ② 占位符泄漏门。"""

    def test_first_reports_exist_and_structured(self):
        for p in (NAGS_CAND, NAGS_POOL_MD, NAGS_POOL_JSON, REV_CAND):
            self.assertTrue(p.is_file(),
                            "首份报告缺失：%s（归档被改名/删除即红）" % p.name)
        rev = REV_CAND.read_text(encoding="utf-8")
        self.assertIn("## 账目", rev)                    # 账目表
        self.assertIn("rowid 对", rev)                  # 锚点可回翻
        self.assertIn("由人甄别", rev)                  # 非结论声明
        self.assertIn("Jaccard", rev)                   # 口径可追溯
        cand = NAGS_CAND.read_text(encoding="utf-8")
        self.assertIn("锚点", cand)                     # 候选带锚点
        pool = NAGS_POOL_MD.read_text(encoding="utf-8")
        self.assertIn("裁决命令（复制执行；本视图不代跑）", pool)
        self.assertIn("人工裁决", pool)                 # 留痕列

    def test_no_placeholder_leak_in_reports(self):
        """P4-6 修的真 bug：render_markdown Top1 行漏 % 运算符 →
        %s/%d 原样进归档报告。任何归档报告再出现即红。"""
        for p in (NAGS_CAND, NAGS_POOL_MD, REV_CAND):
            t = p.read_text(encoding="utf-8")
            for bad in ("%s", "%d"):
                self.assertNotIn(bad, t,
                                 "%s 含未插值占位符 %r（渲染漏 %% 运算符）"
                                 % (p.name, bad))

    def test_pool_md_json_reconcile(self):
        pool = json.loads(NAGS_POOL_JSON.read_text(encoding="utf-8"))
        entries = pool["rule_entries"] + pool["issue_entries"]
        md = NAGS_POOL_MD.read_text(encoding="utf-8")
        md_keys = set(re.findall(r"nags:[0-9a-f]{12}", md))
        json_keys = {e["key"] for e in entries}
        n_cmd = len(re.findall(
            r"- 裁决命令（复制执行；本视图不代跑）：.*suggest-status", md))
        self.assertEqual(len(entries), len(json_keys), "json key 重复")
        self.assertEqual(json_keys, md_keys, "md↔json key 集合不一致")
        self.assertEqual(len(entries), n_cmd, "md 裁决命令数与条目数不一致")
        for e in entries:
            self.assertIn(e["dest"], ("rule", "issue"))
            self.assertIn(e["owner"], ("harness", "tool", "workflow"))  # 三值冻结
            self.assertIn('--key "%s"' % e["key"], e["status_cmd"])
            self.assertIn("--status pending", e["status_cmd"])
            self.assertIn("suggest-status", e["status_cmd"])


class TestConfirmFlowE2E(unittest.TestCase):
    """③ 确认流端到端：pool 条目 → 上游 suggest-status 真跑 → 台账可查。"""

    def test_pool_entry_to_ledger_pending_then_adopted(self):
        con, db = _mkdb([("s1", NAG_TEXT), ("s2", NAG_TEXT)])
        con.close()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "pool"
            p = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "actions.analysis.nags",
                 "--db", str(db), "--pool-out", str(out),
                 "--meta", str(Path(td) / "suggestions_meta.db")],
                capture_output=True, text=True, cwd=str(ROOT), timeout=120,
                env={**os.environ,
                     "PYTHONPATH": str(ROOT) + os.pathsep
                                  + os.environ.get("PYTHONPATH", "")})
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            pool = json.loads(Path(str(out) + ".json")
                              .read_text(encoding="utf-8"))
            entries = pool["rule_entries"] + pool["issue_entries"]
            self.assertTrue(entries, "fixture 应产出 ≥1 条候选")
            key = entries[0]["key"]

            meta = str(Path(td) / "suggestions_meta.db")
            env = {**os.environ,
                   "PYTHONPATH": str(UPSTREAM) + os.pathsep
                                + os.environ.get("PYTHONPATH", "")}

            def run_status(status, *extra):
                return subprocess.run(
                    [sys.executable, "-X", "utf8", "-m", "harvester",
                     "suggest-status", "--meta", meta, "--key", key,
                     "--status", status, *extra],
                    capture_output=True, text=True, cwd=str(UPSTREAM),
                    timeout=120, env=env)

            # ① pending：裁决入口 = 台账先记"待定"
            r1 = run_status("pending")
            self.assertEqual(r1.returncode, 0, r1.stderr[-800:])
            # ② adopted：人工采纳（带 adopted_at 分界 T）
            r2 = run_status("adopted", "--adopted-at", "2026-10-13 20:00:00",
                            "--note", "P4-6 阶段门 E2E")
            self.assertEqual(r2.returncode, 0, r2.stderr[-800:])
            # ③ 台账回查：闭环可用 = 状态与 adopted_at 真落了库
            ledger = sqlite3.connect("file:%s?mode=ro" % Path(meta).as_posix(),
                                     uri=True)
            try:
                row = ledger.execute(
                    "SELECT status, adopted_at, note FROM suggestion_status "
                    "WHERE key=?", (key,)).fetchone()
            finally:
                ledger.close()
            self.assertIsNotNone(row, "key 未落台账")
            self.assertEqual(row[0], "adopted")
            self.assertEqual(row[1], "2026-10-13 20:00:00")
            self.assertIn("P4-6 阶段门 E2E", row[2] or "")


class TestCandidateSemantics(unittest.TestCase):
    """④ 候选语义门：锚点可回翻 + 非结论声明（确认=人升级，不自动写库）。"""

    def test_revisions_candidates_carry_anchors(self):
        from actions.analysis import revisions as RV
        con, db = _mkdb([
            ("s1", "第一段论述围绕双轨时间线的数据面设计与阈值口径展开讨论"),
            ("s1", "第一段论述围绕双轨时间线的数据面设计与阈值口径展开斟酌"),
        ])
        try:
            res = RV.collect(con)
        finally:
            con.close()
        self.assertGreaterEqual(len(res["band"]), 1, "相似对应进边界带")
        for p_ in res["band"]:
            self.assertTrue(p_["sid"] and p_["rowid_a"] and p_["rowid_b"],
                            "候选必须带锚点（sid+rowid 对）才能回翻原文")
        md = RV.render_markdown(res)
        self.assertIn("由人甄别", md)
        self.assertNotIn("%s", md)      # 语义门顺带钉占位符
        self.assertNotIn("%d", md)


if __name__ == "__main__":
    unittest.main(verbosity=1)
