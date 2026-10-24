# -*- coding: utf-8 -*-
"""v0.81 P1-3 skill 治理档案聚合（actions/skillmatrix.py）测试。

验收口径（与模块 docstring 一一对应）：
1. 每 skill 一行：G4 画像（调用/会话/成功/失败）+ 服务的主题 +
   关联错误 top1 + 消歧状态；
2. 「服务的主题」= 有 chain 的主题经 skilljoin 交叉后按 skill 聚合——
   **无 chain 的主题不计入**（明示，不猜）；
3. 失败调用的错误文本 top1 进档案（G4 只数次数，这里补"败在什么上"）；
4. 解析不出 skill 名的调用点归「（未识别）」并标消歧状态，不静默丢弃；
5. 跨 harness 同名 → 消歧状态标出（两套工具名词表可能同名不同物）；
6. 只读：不写任何库与文件（测试用只读挂载校验不了写入，至少断言
   聚合过程不因缺 topics_meta / 坏 chain 而失败——诚实的空）。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from actions import skillmatrix

DB_SESSIONS = """CREATE TABLE sessions (
    sid TEXT PRIMARY KEY, source TEXT, session_id TEXT, title TEXT,
    category TEXT, created_at TEXT, updated_at TEXT, file TEXT, model TEXT)"""
DB_MESSAGES = ("CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT,"
               " text TEXT, raw TEXT)")
DB_STEPS = ("CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,"
            " phase TEXT, status TEXT, error TEXT, detail TEXT)")
META_TOPICS = ("CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT NOT NULL"
               " UNIQUE, keywords TEXT NOT NULL DEFAULT '[]',"
               " members TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL)")


def make_db(path: Path, sessions: dict[str, dict]) -> None:
    """sessions: sid -> {"source", "ts", "steps": [(tool, phase, status,
    error, detail), ...]}"""
    con = sqlite3.connect(str(path))
    con.executescript(DB_SESSIONS)
    con.executescript(DB_MESSAGES)
    con.executescript(DB_STEPS)
    for sid, spec in sessions.items():
        con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, spec.get("source", "dsh"), sid.split(":", 1)[-1],
                     "t", "cat", spec.get("ts", "2026-10-01 10:00:00"),
                     spec.get("ts", "2026-10-01 10:00:00"), "f", None))
        for i, st in enumerate(spec.get("steps", []), 1):
            con.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)",
                        (sid, i, spec.get("ts", "2026-10-01 10:00:00"),
                         st[0], st[1], st[2], st[3], st[4]))
    con.commit()
    con.close()


def skill_call(skill: str) -> tuple:
    return ("Skill", "call", None, None, json.dumps({"skill": skill}))


def skill_result(status: str, error: str = "") -> tuple:
    return ("Skill", "result", status, error, "")


CHAIN_TMPL = """---
topic_id: {tid}
topic: {tname}
members: []
anchors:
  - stage: 阶段一·起手
    span: "2026-10-01 ~ 2026-10-03"
    nodes: []
---

# chain {tname}
正文（测试用，内容不影响 join）。
"""


class TestSkillMatrix(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _fixture(self):
        """一个库 + 一个有 chain 的主题 + 一个无 chain 的主题。"""
        db = self.dir / "t.db"
        make_db(db, {
            "dsh:s1": {"source": "dsh", "steps": [
                skill_call("pdf-text-extractor"),
                skill_result("success"),
                skill_call("pdf-text-extractor"),
                skill_result("error", "ENOENT: no such file"),
                skill_call("markitdown-skill"),
                skill_result("success"),
            ]},
            "wb:s1": {"source": "workbuddy", "steps": [
                skill_call("pdf-text-extractor"),   # 跨 harness 同名
                skill_result("success"),
                skill_call(""),                      # 名解析不出
                skill_result("no_result"),
            ]},
        })
        meta = self.dir / "topics_meta.db"
        con = sqlite3.connect(str(meta))
        con.executescript(META_TOPICS)
        con.execute("INSERT INTO topics VALUES (?,?,?,?,?)",
                    ("tp-1", "主题甲", "[]", "[]", "2026-10-01"))
        con.execute("INSERT INTO topics VALUES (?,?,?,?,?)",
                    ("tp-2", "主题乙（无 chain）", "[]", "[]", "2026-10-01"))
        con.commit()
        con.close()
        chains = self.dir / "chains"
        chains.mkdir()
        (chains / "chain-主题甲.md").write_text(
            CHAIN_TMPL.format(tid="tp-1", tname="主题甲"), encoding="utf-8")
        # 坏 chain 也要落在 chain-*.md 通配里，才能验「坏文档不静默跳过」
        (chains / "chain-坏档.md").write_text("不是 chain", newline="\n",
                                             encoding="utf-8")
        return db, meta, chains

    def test_matrix_rows_and_join(self):
        db, meta, chains = self._fixture()
        d = skillmatrix.build_matrix(db, meta, chains)
        by = {s["skill"]: s for s in d["skills"]}
        # 三个 skill 行（pdf ×2 harness 调用、markitdown ×1、未识别 ×1）
        self.assertEqual(set(by), {"pdf-text-extractor", "markitdown-skill",
                                   "（未识别）"})
        pdf = by["pdf-text-extractor"]
        self.assertEqual(pdf["calls"], 3)
        self.assertEqual(pdf["sids"], 2)
        self.assertEqual(pdf["ok"], 2)
        self.assertEqual(pdf["err"], 1)
        self.assertAlmostEqual(pdf["fail_rate"], round(1 / 3, 4), places=4)
        self.assertEqual(pdf["sources"], ["dsh", "workbuddy"])
        # 服务主题：chain 时间窗 2026-10-01~03 覆盖全部调用 → 主题甲
        self.assertEqual(pdf["topics_served"], ["主题甲"])
        self.assertEqual(pdf["topics_count"], 1)
        # 关联错误 top1（败在什么上）
        self.assertEqual(pdf["top_error"]["text"], "ENOENT: no such file")
        self.assertEqual(pdf["top_error"]["count"], 1)
        # 消歧状态：跨 2 个 harness
        self.assertTrue(any("跨 2 个 harness" in f for f in pdf["disambig"]))
        # 未识别：名解析不出 + 无服务主题（no_result 调用也进 cross）
        unk = by["（未识别）"]
        self.assertTrue(any("名解析不出" in f for f in unk["disambig"]))
        self.assertEqual(unk["calls"], 1)
        self.assertEqual(unk["no_result"], 1)
        # 总量与账目
        self.assertEqual(d["totals"]["skills"], 3)
        self.assertEqual(d["totals"]["calls"], 5)
        self.assertEqual(d["totals"]["failed"], 1)
        self.assertEqual(d["totals"]["unknown_named"], 1)
        self.assertEqual(d["totals"]["chains_scanned"], 1)   # broken.md 跳过
        self.assertEqual(d["totals"]["chains_total"], 2)
        self.assertEqual(d["totals"]["topics_registered"], 2)
        # 坏文档不静默：errors 列表里留下痕迹
        self.assertEqual(len(d["chain_errors"]), 1)

    def test_no_chain_root_honest_empty(self):
        """没有 chain_root：服务的主题全 0，但不报错、不猜。"""
        db, meta, _chains = self._fixture()
        d = skillmatrix.build_matrix(db, meta, None)
        self.assertTrue(all(s["topics_count"] == 0 for s in d["skills"]))
        self.assertEqual(d["totals"]["chains_total"], 0)
        self.assertIn("无 chain 的主题不计入", d["note"])

    def test_no_topics_meta_still_works(self):
        """缺 topics_meta：主题名从 chain frontmatter 兜底，不失败。"""
        db, _meta, chains = self._fixture()
        d = skillmatrix.build_matrix(db, None, chains)
        pdf = [s for s in d["skills"]
               if s["skill"] == "pdf-text-extractor"][0]
        self.assertEqual(pdf["topics_served"], ["主题甲"])

    def test_empty_db(self):
        db = self.dir / "e.db"
        make_db(db, {})
        d = skillmatrix.build_matrix(db, None, None)
        self.assertEqual(d["skills"], [])
        self.assertEqual(d["totals"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
