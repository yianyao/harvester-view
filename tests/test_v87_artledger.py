# -*- coding: utf-8 -*-
"""P2 #14 · D1' 产物台账（`artifact_ledger`）：全库「会话 × 文件」台账。

本文件钉四类东西（都是"静默错"的高发地）：

1. **分组键是 (sid, file_path)**——同一文件在两个会话里必须是两行，
   否则「哪个会话改的」这个信息就丢了，而丢失时页面照样有数字。
2. **口径与来源必须全覆盖**：`totals` 里每个数字都要在 `definitions`
   与 `sources` 里说清怎么算、取自哪本账（同 growth 的纪律）。
3. **覆盖度永远在**：`coverage` 缺了就等于"部分覆盖冒充全库"。
4. **空与未配置要区分**：未配置给终端命令（视图绝不代跑提取），
   配了但空给另一句——两种情况修法不同。

真库跑一条（H9：库不在就 skip，不可用≠失败）。
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

from actions import actionsserve as AS  # noqa: E402  先让它把 harvester 挂上 sys.path
from actions import artledger  # noqa: E402

UP = AS.upstream_root()

_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT,
    file TEXT, model TEXT);
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


def _mk_harvester_db(p: Path, sids: list[str]) -> Path:
    con = sqlite3.connect(str(p))
    try:
        con.executescript(_SESSIONS)
        # revisions/nags 要读 messages（user 轮）——建空 FTS 就行，
        # 台账页的候选账目在空库上应为 0，不是崩。
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


class LedgerFixture(unittest.TestCase):
    """两块会话：s1 里同一文件 3 版、s2 里同一文件 1 版 + 另一个文件 2 版。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.db = _mk_harvester_db(self.dir / "h.db", ["dsh:s1", "dsh:s2"])
        self.meta = _mk_artifacts(self.dir / "a.db", [
            ("dsh:s1", 0, "2026-10-01 10:00:00", "", r"docs\稿.md", None, "v1"),
            ("dsh:s1", 1, "2026-10-01 10:05:00", "", r"docs\稿.md", "v1", "v2xx"),
            ("dsh:s1", 2, "2026-10-01 10:09:00", "", r"docs\稿.md", "v2xx",
             "v3xxxx"),
            ("dsh:s2", 0, "2026-10-02 09:00:00", "", r"docs\稿.md", None, "w1"),
            ("dsh:s2", 1, "2026-10-02 09:10:00", "", r"src\a.py", None, "p1"),
            ("dsh:s2", 2, "2026-10-02 09:20:00", "", r"src\a.py", "p1", "p2"),
        ])
        # s1 在一个主题里（members 用**对象列表**形态）；s2 不在任何主题里
        self.topics = _mk_topics(self.dir / "t.db", [
            ("tp-1", "稿子打磨", [{"sid": "dsh:s1", "evidence": "人工确认"}]),
        ])
        self.cfg = AS.Config(db=self.db,
                             cards_root=self.dir / "cards",
                             glossary=ROOT / "static" / "glossary.json",
                             topics_meta=self.topics,
                             artifacts_meta=self.meta)

    def tearDown(self):
        self.td.cleanup()

    def build(self, **kw):
        return artledger.build(self.cfg, **kw)


class GroupingTest(LedgerFixture):
    def test_same_file_in_two_sessions_is_two_rows(self):
        d = self.build()
        rows = d["rows"]
        self.assertEqual(d["rows_total"], 3,
                         "应是 3 个「会话×文件」组合：%s"
                         % [(r["sid"], r["file_path"]) for r in rows])
        keyed = {(r["sid"], r["file_path"]): r for r in rows}
        self.assertEqual(keyed[("dsh:s1", r"docs\稿.md")]["n_versions"], 3)
        self.assertEqual(keyed[("dsh:s2", r"docs\稿.md")]["n_versions"], 1)
        self.assertEqual(keyed[("dsh:s2", r"src\a.py")]["n_versions"], 2)

    def test_files_count_is_distinct_paths(self):
        d = self.build()
        self.assertEqual(d["totals"]["files"], 2, "只有两个不同路径")
        self.assertEqual(d["totals"]["pairs"], 3)
        self.assertEqual(d["totals"]["versions"], 6)
        self.assertEqual(d["totals"]["changed_chars"],
                         sum(len(x) for x in
                             ["v1", "v2xx", "v3xxxx", "w1", "p1", "p2"]))

    def test_topic_reverse_lookup(self):
        d = self.build()
        keyed = {(r["sid"], r["file_path"]): r for r in d["rows"]}
        a = keyed[("dsh:s1", r"docs\稿.md")]
        self.assertEqual([t["id"] for t in a["topics"]], ["tp-1"])
        self.assertEqual(a["topic_names"], "稿子打磨")
        b = keyed[("dsh:s2", r"src\a.py")]
        self.assertEqual(b["topics"], [])
        self.assertEqual(b["topic_names"], "")
        self.assertEqual(d["totals"]["pairs_in_topics"], 1)

    def test_members_plain_string_list_also_works(self):
        # 真实库是对象列表；早期版本按纯字符串写会 TypeError（growth 踩过）。
        # 两种形态都得认，否则换个写法就整页崩。
        self.topics = _mk_topics(self.dir / "t2.db", [("tp-2", "旧写法",
                                                       ["dsh:s2"])])
        cfg = AS.Config(db=self.db, cards_root=self.dir / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        topics_meta=self.topics, artifacts_meta=self.meta)
        d = artledger.build(cfg)
        keyed = {(r["sid"], r["file_path"]): r for r in d["rows"]}
        self.assertEqual(keyed[("dsh:s2", r"src\a.py")]["topic_names"], "旧写法")

    def test_tool_blank_stays_blank(self):
        # 真实库 2109 行 tool 全空；空就空，不许拿 Write/Edit 去补（那是猜）
        d = self.build()
        for r in d["rows"]:
            self.assertEqual(r["tools"], [], "tool 空时不许编造工具名")
        self.assertTrue(any("工具" in x for x in d["honest"]),
                        "「工具一列实测为空」这件事要写进 honest")


class SortingPagingTest(LedgerFixture):
    def test_sort_versions(self):
        d = self.build(sort="versions")
        self.assertEqual(d["rows"][0]["n_versions"], 3)

    def test_sort_recent(self):
        d = self.build(sort="recent")
        self.assertEqual(d["rows"][0]["sid"], "dsh:s2",
                         "recent 应按最后改动时间倒序")

    def test_sort_path(self):
        d = self.build(sort="path")
        paths = [r["file_path"] for r in d["rows"]]
        self.assertEqual(paths, sorted(paths))

    def test_limit_and_truncated_flag(self):
        d = self.build(limit=1)
        self.assertEqual(len(d["rows"]), 1)
        self.assertTrue(d["truncated"])
        self.assertEqual(d["rows_total"], 3, "行数截断，总数不许跟着截")

    def test_limit_clamped(self):
        d = self.build(limit=99999)
        self.assertLessEqual(d["limit"], artledger.MAX_ROWS)

    def test_sid_filter(self):
        d = self.build(sid="dsh:s2")
        self.assertEqual(d["rows_total"], 2)
        self.assertTrue(all(r["sid"] == "dsh:s2" for r in d["rows"]))


class HonestyTest(LedgerFixture):
    def test_every_total_has_definition_and_source(self):
        d = self.build()
        for k in d["totals"]:
            self.assertIn(k, d["definitions"], "数字 %s 缺口径" % k)
            self.assertIn(k, d["sources"], "数字 %s 缺来源" % k)

    def test_coverage_always_present(self):
        d = self.build()
        cv = d["coverage"]
        self.assertEqual(cv["sources"], ["dsh"])
        self.assertIn("不代表全库", cv["line"])
        self.assertIn("不代表全库", cv["line_text"])
        self.assertNotIn("**", cv["line_text"], "页面用纯文本版，不该带星号")

    def test_coverage_present_even_when_unconfigured(self):
        cfg = AS.Config(db=self.db, cards_root=self.dir / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        artifacts_meta=None)
        d = artledger.build(cfg)
        self.assertFalse(d["configured"])
        self.assertIn("coverage", d, "未配置也必须给覆盖度（否则读者以为 0 是事实）")

    def test_unconfigured_hint_gives_cli_and_refuses_to_run(self):
        cfg = AS.Config(db=self.db, cards_root=self.dir / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        artifacts_meta=None)
        d = artledger.build(cfg)
        self.assertIn("artifacts extract", d["hint"])
        self.assertIn("绝不代跑", d["hint"])
        self.assertEqual(d["rows"], [])

    def test_empty_ledger_has_its_own_hint(self):
        empty = _mk_artifacts(self.dir / "empty.db", [])
        cfg = AS.Config(db=self.db, cards_root=self.dir / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        artifacts_meta=empty)
        d = artledger.build(cfg)
        self.assertTrue(d["configured"])
        self.assertEqual(d["rows_total"], 0)
        self.assertIn("artifacts extract", d["hint"])
        self.assertNotEqual(d["hint"], artledger.HINT_UNCONFIGURED,
                            "「没配」与「配了但空」是两种修法，不能同一句")

    def test_honest_covers_the_four_boundaries(self):
        d = self.build()
        blob = "".join(d["honest"])
        for kw in ("不代表全库", "不是文件系统快照", "路径归一", "工具"):
            self.assertIn(kw, blob, "honest 少了边界：%s" % kw)

    def test_candidates_zero_on_empty_messages(self):
        d = self.build()
        cand = d["candidates"]
        self.assertEqual(cand["revisions"]["account"]["n_band"], 0)
        self.assertEqual(cand["nags"]["account"]["n_groups"], 0)

    def test_render_md(self):
        md = artledger.render_md(self.build())
        for kw in ("产物台账", "覆盖度", "账目", "别把这面读过头", "口径与来源"):
            self.assertIn(kw, md)


@unittest.skipUnless((UP / "artifacts_meta.db").is_file(),
                     "需要真产物台账（H9）")
class RealLedgerTest(unittest.TestCase):
    def test_real_ledger_builds(self):
        cfg = AS.Config(db=UP / "harvester.db",
                        cards_root=UP / "knowledge" / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        topics_meta=UP / "topics_meta.db",
                        artifacts_meta=UP / "artifacts_meta.db",
                        suggestions_meta=UP / "suggestions_meta.db")
        d = artledger.build(cfg, limit=5)
        self.assertEqual(d["schema"], "artifact_ledger/1")
        self.assertTrue(d["configured"])
        self.assertGreater(d["rows_total"], 0, "真台账一条都没有？")
        self.assertGreater(d["totals"]["versions"], 0)
        self.assertEqual(len(d["rows"]), 5)
        self.assertTrue(d["truncated"])
        self.assertIn("coverage", d)
        self.assertGreater(d["coverage"]["registered_sessions"], 0)
        # 候选账目在真库上跑得通（口径来自 revisions/nags 的同一实现）
        self.assertIn("revisions", d["candidates"])


if __name__ == "__main__":
    unittest.main()
