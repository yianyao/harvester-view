# -*- coding: utf-8 -*-
"""P2 #14 补：产物台账**分批视角**（用户 2026-10-26）。

原话：「**产物台账不应该是按主题，或者按最终产物分批汇总么？一个主题千百
个文件，打成个评审包能看出啥东西来？**」

本文件钉的是「分批」这件事不能悄悄退化：

1. **默认按主题**——不是按文件。默认值退回去，页面就又是一屏文件行。
2. **核心产物按文件合并**——同一文件在两个会话里各改一遍时是**一条**，
   不是两行（`会话交接.md` 实测在两个会话里各 63 版，pair 级列出来像 bug）。
3. **空主题要折起来**，不能被静默丢掉（丢掉了「N 个主题没产物」这个信号
   就没了，而页面照样显示正常）。
4. **分批不是质量分级**——这句必须留在 `honest` 里，别被"优化"掉。

夹具：s1、s2 都注册在 tp-1 里（s1 把稿.md 改 3 版；s2 改 1 版 + 把 a.py
改 2 版）；s3 没注册进任何主题（改 b.md 2 版）；另有空主题 tp-2。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import artledger, reviewpack  # noqa: E402

UP = AS.upstream_root()

_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT,
    file TEXT, model TEXT);
"""
_STEPS = """
CREATE TABLE IF NOT EXISTS steps (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT,
    phase TEXT, status TEXT, error TEXT, detail TEXT,
    PRIMARY KEY (sid, seq));
"""
_ARTIFACTS = """
CREATE TABLE IF NOT EXISTS artifacts (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT NOT NULL,
    file_path TEXT, old_text TEXT, new_text TEXT, PRIMARY KEY (sid, seq));
"""
_TOPICS = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    keywords TEXT NOT NULL DEFAULT '[]', members TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL);
"""


def _mk_db(p: Path, sids: list[str]) -> Path:
    con = sqlite3.connect(str(p))
    try:
        con.executescript(_SESSIONS)
        con.executescript(_STEPS)   # errstats 会查它（空表也要在）
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5("
                    "sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text, "
                    "raw UNINDEXED, tokenize='unicode61')")
        for s in sids:
            con.execute("INSERT INTO sessions (sid, source, session_id) "
                        "VALUES (?,?,?)", (s, s.split(":", 1)[0], s))
        con.commit()
    finally:
        con.close()
    return p


def _mk_artifacts(p: Path, rows: list[tuple]) -> Path:
    con = sqlite3.connect(str(p))
    try:
        con.executescript(_ARTIFACTS)
        for r in rows:
            con.execute("INSERT INTO artifacts (sid, seq, ts, tool, file_path, "
                        "old_text, new_text) VALUES (?,?,?,?,?,?,?)", r)
        con.commit()
    finally:
        con.close()
    return p


def _mk_topics(p: Path, topics: list[tuple]) -> Path:
    con = sqlite3.connect(str(p))
    try:
        con.executescript(_TOPICS)
        for tid, name, members in topics:
            con.execute("INSERT INTO topics (id, name, members, created) "
                        "VALUES (?,?,?,?)",
                        (tid, name, json.dumps(members, ensure_ascii=False),
                         "2026-10-01 10:00"))
        con.commit()
    finally:
        con.close()
    return p


class Fixture(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.db = _mk_db(self.dir / "h.db", ["dsh:s1", "dsh:s2", "dsh:s3"])
        self.meta = _mk_artifacts(self.dir / "a.db", [
            ("dsh:s1", 0, "2026-10-01 10:00:00", "", r"work\docs\稿.md", None, "v1"),
            ("dsh:s1", 1, "2026-10-01 10:05:00", "", r"work\docs\稿.md", "v1", "v2xx"),
            ("dsh:s1", 2, "2026-10-01 10:09:00", "", r"work\docs\稿.md", "v2xx",
             "v3xxxx"),
            ("dsh:s2", 0, "2026-10-02 09:00:00", "", r"work\docs\稿.md", None, "w1"),
            ("dsh:s2", 1, "2026-10-02 09:10:00", "", r"work\src\a.py", None, "p1"),
            ("dsh:s2", 2, "2026-10-02 09:20:00", "", r"work\src\a.py", "p1", "p2"),
            ("dsh:s3", 0, "2026-10-03 09:00:00", "", r"other\b.md", None, "q1"),
            ("dsh:s3", 1, "2026-10-03 09:10:00", "", r"other\b.md", "q1", "q2"),
        ])
        self.topics = _mk_topics(self.dir / "t.db", [
            ("tp-1", "稿子打磨", [{"sid": "dsh:s1", "evidence": "人工确认"},
                                  {"sid": "dsh:s2", "evidence": "人工确认"}]),
            ("tp-2", "空主题", []),
        ])
        self.cfg = AS.Config(db=self.db, cards_root=self.dir / "cards",
                             glossary=ROOT / "static" / "glossary.json",
                             topics_meta=self.topics,
                             artifacts_meta=self.meta)

    def tearDown(self):
        self.td.cleanup()

    def build(self, **kw):
        return artledger.build(self.cfg, **kw)


class TopicGroupTest(Fixture):
    def test_default_group_is_topic(self):
        d = self.build()
        self.assertEqual(d["group"], "topic",
                         "默认必须按主题——退回按文件等于用户那条反馈没修")

    def test_topic_row_numbers(self):
        d = self.build()
        by = {r.get("topic_id") or "unfiled": r for r in d["groups"]}
        r = by["tp-1"]
        self.assertEqual(r["sessions"], 2)
        self.assertEqual(r["files"], 2)
        self.assertEqual(r["versions"], 6)
        self.assertEqual(r["buckets"]["core"]["pairs"], 1, "只有 s1 的稿.md 够 3 版")
        self.assertEqual(r["buckets"]["once"]["pairs"], 1, "s2 的稿.md 只 1 版")
        self.assertEqual(r["buckets"]["few"]["pairs"], 1, "s2 的 a.py 2 版")

    def test_unfiled_row_collects_sessions_without_topic(self):
        d = self.build()
        by = {r.get("topic_id") or "unfiled": r for r in d["groups"]}
        self.assertIn("unfiled", by, "没注册进主题的会话不能凭空消失")
        r = by["unfiled"]
        self.assertEqual(r["files"], 1)
        self.assertEqual(r["versions"], 2)
        self.assertEqual(r["buckets"]["core"]["pairs"], 0)
        self.assertEqual(r["buckets"]["few"]["pairs"], 1)
        self.assertTrue(r.get("note"), "未归主题要给一句为什么/怎么办")

    def test_empty_topic_is_folded_not_dropped(self):
        d = self.build()
        names = [x["name"] for x in d["empty_groups"]]
        self.assertEqual(names, ["空主题"],
                         "0 产物的主题要折进 empty_groups（不是消失，不是占一行空表）")
        self.assertNotIn("空主题", [r.get("topic_name") for r in d["groups"]])

    def test_core_files_merged_by_path(self):
        d = self.build()
        r = [x for x in d["groups"] if x.get("topic_id") == "tp-1"][0]
        core = r["core_files"]
        self.assertEqual(len(core), 1,
                         "只有稿.md 够核心；且两个会话改的要合并成一条：%s" % core)
        c = core[0]
        self.assertEqual(c["n_versions"], 4, "s1 的 3 版 + s2 的 1 版 = 4 版")
        self.assertEqual(len(c["sessions"]), 2, "合并后要保留「哪几个会话改过」")
        self.assertEqual(r["buckets"]["core"]["pairs"], 1,
                         "pair 级仍是 1 条（只有 s1 那条够 3 版）——两层口径都在")

    def test_top_dirs_use_last_two_segments(self):
        d = self.build()
        r = [x for x in d["groups"] if x.get("topic_id") == "tp-1"][0]
        self.assertEqual(sorted(x["dir"] for x in r["top_dirs"]),
                         ["work/docs/", "work/src/"],
                         "目录聚类取路径最后两段（绝对路径前两段是盘符噪声）")

    def test_honest_keeps_batching_is_not_quality(self):
        d = self.build()
        blob = "".join(d["honest"])
        self.assertIn("分批不是质量分级", blob)
        self.assertIn("主题行相加", blob)

    def test_definitions_and_sources_cover_totals(self):
        d = self.build()
        defs, srcs = d["definitions"], d["sources"]
        for k in d["totals"]:
            self.assertIn(k, defs, "totals.%s 没有口径" % k)
            self.assertIn(k, srcs, "totals.%s 没有来源" % k)

    def test_batch_labels_three(self):
        d = self.build()
        self.assertEqual(sorted(d["batches"]), ["core", "few", "once"])


class SessionGroupTest(Fixture):
    def test_session_rows_have_short_name(self):
        d = self.build(group="session")
        self.assertEqual(len(d["groups"]), 3)
        shorts = [r["short"] for r in d["groups"]]
        self.assertEqual(len(set(shorts)), 3, "会话短名必须能区分：%s" % shorts)

    def test_file_group_keeps_pairs_and_adds_bucket(self):
        d = self.build(group="file")
        self.assertEqual(d["group"], "file")
        self.assertEqual(len(d["rows"]), 4, "文件视角是「会话×文件」4 行")
        self.assertEqual({r["bucket"] for r in d["rows"]}, {"core", "few", "once"})

    def test_bad_group_falls_back_to_topic(self):
        d = self.build(group="whatever")
        self.assertEqual(d["group"], "topic")


class RenderTest(Fixture):
    def test_topic_md_has_batches_and_core(self):
        md = artledger.render_md(self.build())
        self.assertIn("按主题分批", md)
        self.assertIn("核心产物", md)
        self.assertIn("另有 **1 个主题**", md, "空主题要折成一句")

    def test_file_md_has_batch_column(self):
        md = artledger.render_md(self.build(group="file"))
        self.assertIn("| 版数 | 批次 | 文件 |", md)


class TopicPackTest(Fixture):
    def test_topic_pack_batches_and_bulk(self):
        d = reviewpack.build_topic(self.cfg, topic_id="tp-1")
        self.assertEqual(d["schema"], reviewpack.SCHEMA)
        self.assertEqual(d["kind"], "topic")
        self.assertEqual(d["topic"]["id"], "tp-1")
        self.assertEqual(len(d["batches"]), 2, "一次会话 = 一批")
        self.assertEqual(d["core_files"][0]["n_versions"], 4)
        self.assertEqual(d["core_files"][0]["n_sessions"], 2)
        self.assertEqual(d["bulk"]["files"], 1, "a.py 只 2 版，归「其余」")
        self.assertIn("为什么只摊开核心产物", d["md"])

    def test_unfiled_pack(self):
        d = reviewpack.build_topic(self.cfg, topic_id="unfiled")
        self.assertEqual(d["topic"]["name"], "（未归主题）")
        self.assertEqual(d["target"]["files"], 1)
        self.assertEqual(d["core_files"], [], "2 版不算核心产物")

    def test_unknown_topic_raises(self):
        with self.assertRaises(ValueError):
            reviewpack.build_topic(self.cfg, topic_id="tp-nope")

    def test_empty_topic_gives_hint(self):
        d = reviewpack.build_topic(self.cfg, topic_id="tp-2")
        self.assertTrue(d["configured"])
        self.assertEqual(d["core_files"], [])
        self.assertIn("一条产物都没有", d["hint"])

    def test_bulk_sample_present(self):
        d = reviewpack.build_topic(self.cfg, topic_id="tp-1")
        self.assertTrue(d["bulk"]["sample"], "只计数也要给样例文件名")


@unittest.skipUnless(UP is not None and (Path(UP) / "harvester.db").is_file(),
                     "真库不可用（H9：不可用≠失败）")
class RealLedgerTest(unittest.TestCase):
    def test_real_topic_groups_are_all_nonempty(self):
        cfg = AS.Config(db=Path(UP) / "harvester.db",
                        cards_root=Path(UP) / "knowledge/cards",
                        glossary=ROOT / "static" / "glossary.json",
                        topics_meta=Path(UP) / "topics_meta.db",
                        artifacts_meta=Path(UP) / "artifacts_meta.db")
        d = artledger.build(cfg, group="topic")
        self.assertTrue(d["groups"], "真库应至少有一批有产物")
        for r in d["groups"]:
            self.assertGreater(r["versions"], 0,
                               "groups 里不该出现 0 版的行：%s" % r.get("topic_name"))


if __name__ == "__main__":
    unittest.main()
