# -*- coding: utf-8 -*-
"""P4-2 归宿产物（nags → 建议池第二输入源）· test_v43。

验收口径（TODO-harvester-view-v2.md P4-2）：
- md/JSON 产出 + 两类归宿标注（班规条目 / harness 问题清单，owner 三值
  harness/tool/workflow，沿 agent_suggest v0.19 冻结定义）；
- 产出文件可被 suggest-status 流程消费（每条带稳定 key + 复制就绪命令；
  本视图绝不执行命令——红线）；
- 样例含「注意上下文空间」「DSH 跨会话就忘」类条目的归宿判定示例；
- 机器判定仅建议，采纳靠人（人工裁决留痕列）；
- 纪律：key 必须文本派生跨运行稳定；只读采集库，一字节不写。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.analysis import nags as N          # noqa: E402
from actions.analysis.nags import (             # noqa: E402
    DEST_ISSUE, DEST_RULE, build_pool, classify_destination, render_pool_md)

MSG_DDL = """CREATE TABLE messages(
    rowid_ INTEGER PRIMARY KEY AUTOINCREMENT,
    sid TEXT, role TEXT, text TEXT DEFAULT '', raw TEXT DEFAULT '',
    ts TEXT)"""


def _mkdb(rows):
    """rows: (sid, user_raw) 元组序列。"""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    con = sqlite3.connect(f.name)
    con.row_factory = sqlite3.Row
    con.execute(MSG_DDL)
    for sid, raw in rows:
        con.execute("INSERT INTO messages(sid, role, text, raw) "
                    "VALUES(?, 'user', '', ?)", (sid, raw))
    con.commit()
    return con, Path(f.name)


def _u(sid, raw):
    return (sid, raw)


T1 = "甲乙丙丁戊己庚辛壬癸" * 3   # 边界带用不在此文件；占位避免误用


class TestClassifyDestination(unittest.TestCase):
    """归宿判定：curated 判据 + 保守默认。"""

    def test_canonical_context_space_is_rule(self):
        """TODO 点名示例①：「注意上下文空间」→ 班规条目（workflow）。"""
        dest, owner, reason = classify_destination(
            "上下文快满了要告诉我，注意上下文空间检查")
        self.assertEqual(dest, DEST_RULE)
        self.assertEqual(owner, "workflow")
        self.assertTrue(reason.strip())

    def test_canonical_cross_session_forget_is_issue(self):
        """TODO 点名示例②：「DSH 跨会话就忘」→ harness 问题清单。"""
        dest, owner, reason = classify_destination(
            "DSH 跨会话就忘，之前说过的又问一遍")
        self.assertEqual(dest, DEST_ISSUE)
        self.assertEqual(owner, "harness")

    def test_capability_ask_is_tool_issue(self):
        """「能不能加 X」类功能诉求 → 问题清单（owner=tool）。"""
        dest, owner, _ = classify_destination(
            "能不能在view里再加一个tab按主题分类")
        self.assertEqual(dest, DEST_ISSUE)
        self.assertEqual(owner, "tool")

    def test_default_is_conservative_rule(self):
        dest, owner, reason = classify_destination(
            "做完了，注意要更新文档")
        self.assertEqual(dest, DEST_RULE)
        self.assertEqual(owner, "workflow")
        self.assertIn("保守", reason)   # 明示这是机器保守判定

    def test_owner_values_frozen(self):
        """owner 三值必须落在 agent_suggest 冻结定义内。"""
        for s in ("注意上下文空间", "DSH 跨会话就忘",
                  "能不能加个tab", "随便一句叮嘱"):
            self.assertIn(classify_destination(s)[1],
                          ("harness", "tool", "workflow"))


class TestBuildPool(unittest.TestCase):
    def test_pool_split_and_keys(self):
        con, _ = _mkdb([
            # 同归一键跨 ≥2 会话才成组（collect 口径），夹具对齐
            _u("s1", "注意上下文空间，快满了要先说"),
            _u("s2", "注意上下文空间，快满了要先说"),
            _u("s3", "做完了，注意要更新文档"),
            _u("s4", "做完了，注意要更新文档"),
        ])
        try:
            r = build_pool(N.collect(con))
        finally:
            con.close()
        rules, issues = r["rule_entries"], r["issue_entries"]
        self.assertEqual(len(rules), 2)      # 更新文档 + 上下文空间（示例①）
        self.assertEqual(len(issues), 0)
        e = [x for x in rules if "更新文档" in x["title"]][0]
        self.assertEqual(e["dest"], DEST_RULE)
        # 示例①确实进了班规桶且 owner=workflow
        c = [x for x in rules if "上下文空间" in x["title"]][0]
        self.assertEqual((c["dest"], c["owner"]), (DEST_RULE, "workflow"))
        # key 文本派生 + 前缀，跨运行稳定
        self.assertTrue(e["key"].startswith("nags:"))
        self.assertEqual(len(e["key"]), len("nags:") + 12)
        # 复制就绪 suggest-status 命令（含 key），绝不自动执行
        self.assertIn("suggest-status", e["status_cmd"])
        self.assertIn(e["key"], e["status_cmd"])
        self.assertIn("--status pending", e["status_cmd"])
        # 证据带锚点（sid + rowid）
        self.assertTrue(all(m["rowid"] > 0 for m in e["evidence"]))
        self.assertEqual(e["evidence"][0]["sid"], "s3")

    def test_key_stable_across_runs_and_text_derived(self):
        con, _ = _mkdb([_u("s1", "做完了，注意要更新文档"),
                        _u("s2", "做完了，注意要更新文档")])
        try:
            k1 = build_pool(N.collect(con))["rule_entries"][0]["key"]
        finally:
            con.close()
        con, _ = _mkdb([_u("sX", "做完了，注意要更新文档"),
                        _u("sY", "做完了，注意要更新文档"),
                        _u("sZ", "做完了，注意要更新文档")])
        try:
            k2 = build_pool(N.collect(con))["rule_entries"][0]["key"]
        finally:
            con.close()
        self.assertEqual(k1, k2)   # 与 sid/次数无关，仅由样本文本决定

    def test_json_serializable_and_curated_documented(self):
        con, _ = _mkdb([_u("s1", "做完了，注意要更新文档"),
                        _u("s2", "做完了，注意要更新文档")])
        try:
            r = build_pool(N.collect(con))
        finally:
            con.close()
        s = json.dumps(r, ensure_ascii=False)   # 不抛即通过
        self.assertIn("nags:", s)
        # curated 判据必须在产物里可查（判定示例可追溯）
        self.assertTrue(any("上下文空间" in c["match"] for c in r["curated"]))
        self.assertTrue(any("跨会话" in c["match"] for c in r["curated"]))

    def test_readonly_no_writes(self):
        """只读纪律：build_pool/render 全程不落库。"""
        con, path = _mkdb([_u("s1", "做完了，注意要更新文档")])
        try:
            before = path.read_bytes()
            build_pool(N.collect(con))
            self.assertEqual(path.read_bytes(), before)
        finally:
            con.close()


class TestRenderMd(unittest.TestCase):
    def test_md_contains_sections_and_verdict_column(self):
        con, _ = _mkdb([
            _u("s1", "做完了，注意要更新文档"),
            _u("s2", "做完了，注意要更新文档"),
            _u("s3", "DSH 跨会话就忘，说过的又问一遍"),
            _u("s4", "DSH 跨会话就忘，说过的又问一遍"),
        ])
        try:
            md = render_pool_md(build_pool(N.collect(con)))
        finally:
            con.close()
        self.assertIn("班规条目", md)
        self.assertIn("问题清单", md)
        self.assertIn("人工裁决", md)          # 留痕列
        self.assertIn("suggest-status", md)    # 复制就绪命令
        self.assertIn("nags:", md)
        # 机器判定仅建议的声明必须在导语里
        self.assertIn("仅建议", md)


class TestCliPoolOut(unittest.TestCase):
    def test_cli_writes_md_and_json(self):
        con, path = _mkdb([_u("s1", "做完了，注意要更新文档"),
                           _u("s2", "做完了，注意要更新文档")])
        con.close()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "pool"
            p = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", "actions.analysis.nags",
                 "--db", str(path), "--pool-out", str(out),
                 "--meta", "suggestions_meta.db"],
                capture_output=True, text=True, cwd=str(
                    Path(__file__).resolve().parent.parent), timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr[-800:])
            self.assertTrue(Path(str(out) + ".md").is_file())
            self.assertTrue(Path(str(out) + ".json").is_file())
            d = json.loads(Path(str(out) + ".json").read_text(encoding="utf-8"))
            self.assertIn("rule_entries", d)


if __name__ == "__main__":
    unittest.main(verbosity=1)
