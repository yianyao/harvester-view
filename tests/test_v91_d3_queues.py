# -*- coding: utf-8 -*-
"""《优化方案》D3'（2026-10-26 用户「做吧」）：决策层补全·后半。

两个新 target 接进工作台/处理单：

- `queue:artifact` **待评审产物**：有核心产物（同会话同文件改过 ≥3 版）
  但**没有任何卡片锚点指向这批会话**——「改了很多版却从没沉淀成卡」。
- `queue:distill.nags` **反复叮嘱候选组**：同一句意思的问话在多会话反复
  出现（`analysis/nags.py` 冻结口径）。

钉的东西：① 两条 target 在 KINDS/DETAIL_MAP/QUEUE_TARGETS 三处都登记了
（漏一处就 KeyError 或页面少一张卡）；② 判据②真的生效（有卡的批不再报）；
③ 没配产物库/没库时返回空列表而不是崩。
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
from actions import overview, workitems  # noqa: E402

UP = AS.upstream_root()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT,
    file TEXT, model TEXT);
CREATE TABLE IF NOT EXISTS steps (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT,
    phase TEXT, status TEXT, error TEXT, detail TEXT,
    PRIMARY KEY (sid, seq));
CREATE TABLE IF NOT EXISTS artifacts (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT NOT NULL,
    file_path TEXT, old_text TEXT, new_text TEXT, PRIMARY KEY (sid, seq));
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    keywords TEXT NOT NULL DEFAULT '[]', members TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL);
"""


def _mk(p: Path, sids: list[str], arts: list[tuple], topics: list[tuple]):
    con = sqlite3.connect(str(p))
    con.executescript(_SCHEMA)
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5("
                "sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text, "
                "raw UNINDEXED, tokenize='unicode61')")
    for s in sids:
        con.execute("INSERT INTO sessions (sid, source, session_id) VALUES (?,?,?)",
                    (s, s.split(":", 1)[0], s))
    for a in arts:
        con.execute("INSERT INTO artifacts (sid, seq, ts, tool, file_path, "
                    "old_text, new_text) VALUES (?,?,?,?,?,?,?)", a)
    for tid, name, members in topics:
        con.execute("INSERT INTO topics (id, name, members, created) "
                    "VALUES (?,?,?,?)",
                    (tid, name, json.dumps(members, ensure_ascii=False),
                     "2026-10-01 10:00"))
    con.commit()
    con.close()
    return p


ARTS = [
    ("dsh:s1", 0, "2026-10-01 10:00:00", "", r"w\x.md", None, "v1"),
    ("dsh:s1", 1, "2026-10-01 10:01:00", "", r"w\x.md", "v1", "v2xx"),
    ("dsh:s1", 2, "2026-10-01 10:02:00", "", r"w\x.md", "v2xx", "v3xxxx"),
]
TOPICS = [("tp-1", "有核心产物的主题",
           [{"sid": "dsh:s1", "evidence": "人工确认"}])]


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.db = _mk(self.dir / "h.db", ["dsh:s1"], ARTS, TOPICS)
        self.cfg = AS.Config(db=self.db, cards_root=self.dir / "cards",
                             glossary=ROOT / "static" / "glossary.json",
                             topics_meta=self.db, artifacts_meta=self.db)

    def tearDown(self):
        self.td.cleanup()


class WiringTest(unittest.TestCase):
    def test_kinds_has_both(self):
        self.assertIn("queue:artifact", workitems.KINDS)
        self.assertIn("queue:distill.nags", workitems.KINDS)

    def test_detail_map_has_nags_row(self):
        rows = workitems.DETAIL_MAP["distill"]
        self.assertIn(("反复叮嘱候选组", "queue:distill.nags", "groups"), rows,
                      "队列明细行要在 DETAIL_MAP 里登记，否则页面拿不到明细")

    def test_queue_targets_has_artifact_card(self):
        self.assertIn("queue:artifact", overview.QUEUE_TARGETS,
                      "D3′ 要求接 home：不进 QUEUE_TARGETS 就没有卡片")

    def test_registry_conflict_targets_still_complete(self):
        # 五张卡都必须在 KINDS 里（漏一个 targets() 就 KeyError）
        for tid in overview.QUEUE_TARGETS:
            self.assertIn(tid, workitems.KINDS)


class ArtifactBatchTest(Base):
    def test_no_meta_returns_empty(self):
        cfg = AS.Config(db=self.db, cards_root=self.dir / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        topics_meta=self.db, artifacts_meta=None)
        self.assertEqual(workitems._artifact_batches({"cfg": cfg, "cards": None}), [])

    def test_batch_with_core_and_no_card(self):
        g = {"cfg": self.cfg, "cards": {"cards": []}}
        items = workitems._artifact_batches(g)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["title"], "有核心产物的主题")
        self.assertEqual(it["metrics"]["core"], 1)
        cmds = it.get("commands") or []
        self.assertTrue(cmds, "每条都该带「打主题评审包」的命令")
        self.assertEqual(cmds[0].get("action_id"), "review_pack")
        self.assertEqual((cmds[0].get("params") or {}).get("mode"), "topic")

    def test_batch_covered_by_card_is_dropped(self):
        g = {"cfg": self.cfg,
             "cards": {"cards": [{"anchors": [{"sid": "dsh:s1", "turn": 3}]}]}}
        self.assertEqual(workitems._artifact_batches(g), [],
                         "判据②：已经有卡的锚点指向这批，就不算「从没沉淀」")

    def test_card_with_bare_sid_also_covers(self):
        # 真实卡片有带 dsh: 前缀的，也有不带的（reviewpack 同判据）
        g = {"cfg": self.cfg,
             "cards": {"cards": [{"anchors": [{"sid": "s1"}]}]}}
        self.assertEqual(workitems._artifact_batches(g), [])


class NagsTest(Base):
    def test_no_db_returns_empty(self):
        cfg = AS.Config(db=self.dir / "nope.db",
                        cards_root=self.dir / "no-cards",
                        glossary=ROOT / "static" / "glossary.json")
        self.assertEqual(workitems._nags_items({"cfg": cfg}), [])

    def test_empty_messages_gives_no_groups(self):
        self.assertEqual(workitems._nags_items({"cfg": self.cfg}), [],
                         "空库上应是 0 组，不是崩")

    def test_items_shape(self):
        con = sqlite3.connect(str(self.db))
        try:
            for i in range(3):
                con.execute(
                    "INSERT INTO messages (sid, role, ts, text, raw) "
                    "VALUES (?,?,?,?,?)",
                    ("dsh:s%d" % i, "user", "2026-10-01 10:0%d:00" % i,
                     "记得先跑测试再提交记得先跑测试再提交", None))
            con.commit()
        finally:
            con.close()
        items = workitems._nags_items({"cfg": self.cfg})
        self.assertTrue(items, "三个会话说同一句话，应归成一组")
        it = items[0]
        self.assertGreaterEqual(it["metrics"]["count"], 3)
        self.assertEqual(it["metrics"]["sessions"], 3)
        self.assertTrue(any("rowid" in (f[1] or "") for f in it["facts"]),
                        "每组要给一条可回原文的锚点（messages.rowid）")


@unittest.skipUnless(UP is not None and (Path(UP) / "harvester.db").is_file(),
                     "真库不可用（H9：不可用≠失败）")
class RealTargetsTest(unittest.TestCase):
    def test_targets_derived_counts(self):
        cfg = AS.Config(db=Path(UP) / "harvester.db",
                        cards_root=Path(UP) / "knowledge/cards",
                        glossary=ROOT / "static" / "glossary.json",
                        topics_meta=Path(UP) / "topics_meta.db",
                        artifacts_meta=Path(UP) / "artifacts_meta.db",
                        suggestions_meta=Path(UP) / "suggestions_meta.db",
                        chain_root=Path(UP) / "knowledge/topics")
        tl = workitems.targets(workitems.collect(cfg))
        idx = {x["target_id"]: x for x in tl}
        for tid, key in (("queue:artifact", "batches"),
                         ("queue:distill.nags", "groups")):
            t = idx[tid]
            self.assertEqual(t["count"], len(t["items"]),
                             "%s 的 count 必须由明细派生" % tid)
            self.assertEqual(t["count"], t["counts"][key])


if __name__ == "__main__":
    unittest.main()
