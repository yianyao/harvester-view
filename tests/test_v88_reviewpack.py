# -*- coding: utf-8 -*-
"""P2 #14 · D2' 评审包（`review_pack`）：一个文件为什么被改成这样。

本文件钉的是**拼装纪律**（各块的算法属于各自的模块，不在这里重测）：

1. **候选按会话过滤**：`revisions`/`nags` 是全库跑一遍的实现，评审包只留本
   会话的那几条——不过滤就等于把别人的打磨混进这次的证据里。
2. **时间对位不能声称因果**：候选只按 ts 归到"落在第几版之前"，
   字段名与文案都不许出现"因为"。
3. **卡片命中要能解释**：强命中（锚点）与弱命中（triggers 撞词）必须分开，
   弱命中要写明撞了哪个词——否则就是让人猜这张卡为什么冒出来。
4. **锚点齐全**：五类材料（产物/打磨/反转/错误/卡片）都得指得回原文。

真库跑一条（H9：库不在就 skip）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402  先挂上 harvester 的搜索路径
from actions import reviewpack as RP  # noqa: E402

UP = AS.upstream_root()

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
    title TEXT, category TEXT, created_at TEXT, updated_at TEXT,
    file TEXT, model TEXT);
CREATE TABLE IF NOT EXISTS steps (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT NOT NULL,
    phase TEXT NOT NULL, status TEXT, error TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS artifacts (
    sid TEXT NOT NULL, seq INTEGER NOT NULL, ts TEXT, tool TEXT NOT NULL,
    file_path TEXT, old_text TEXT, new_text TEXT, PRIMARY KEY (sid, seq));
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    keywords TEXT NOT NULL DEFAULT '[]', members TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL);
"""


def _mk_db(p: Path) -> Path:
    con = sqlite3.connect(str(p))
    try:
        con.executescript(_DDL)
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5("
                    "sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text, "
                    "raw UNINDEXED, tokenize='unicode61')")
        con.commit()
    finally:
        con.close()
    return p


class PackFixture(unittest.TestCase):
    SID = "dsh:s1"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        self.db = _mk_db(self.dir / "h.db")
        con = sqlite3.connect(str(self.db))
        try:
            con.execute("INSERT INTO sessions (sid, source, session_id) "
                        "VALUES (?,?,?)", (self.SID, "dsh", "s1"))
            # 同一条错误犯两次（应归一组）+ 另一种错误一次
            for seq, ts, tool, err in [
                    (10, "2026-10-01 10:01:00", "Edit", "FS_STALE_VERSION"),
                    (20, "2026-10-01 10:06:00", "Edit", "FS_STALE_VERSION"),
                    (30, "2026-10-01 10:07:00", "Bash", "错误: 命令不存在")]:
                con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, "
                            "status, error) VALUES (?,?,?,?,?,?,?)",
                            (self.SID, seq, ts, tool, "result", "error", err))
                con.execute("INSERT INTO steps (sid, seq, ts, tool, phase) "
                            "VALUES (?,?,?,?,?)",
                            (self.SID, seq + 1, ts, tool, "call"))
            con.commit()
        finally:
            con.close()
        self.meta = self.dir / "a.db"
        con = sqlite3.connect(str(self.meta))
        try:
            con.executescript(_DDL)
            for seq, ts, old, new in [
                    (70, "2026-10-04 09:03:29", None, "第一版"),
                    (71, "2026-10-04 09:04:00", "第一版", "第二版内容"),
                    (72, "2026-10-04 09:05:00", "第二版内容", "第三版")]:
                con.execute("INSERT INTO artifacts (sid, seq, ts, tool, "
                            "file_path, old_text, new_text) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (self.SID, seq, ts, "", r"docs\稿.md", old, new))
            # 另一个会话的同一文件（台账里是另一行；评审包不许把它混进来）
            con.execute("INSERT INTO artifacts (sid, seq, ts, tool, file_path, "
                        "old_text, new_text) VALUES (?,?,?,?,?,?,?)",
                        ("dsh:s2", 0, "2026-10-05 09:00:00", "",
                         r"docs\稿.md", None, "别人的版本"))
            con.commit()
        finally:
            con.close()
        self.cards = self.dir / "cards"
        self.cards.mkdir()
        (self.cards / "kc-1.md").write_text(
            "---\nid: kc-1\ntitle: 改文件前要重读\ntype: pitfall\n"
            'anchors: [{session_id: "s1", turn: 3}]\n'
            "triggers: [稿.md, 版本不对]\nconfidence: 0.4\n"
            "created: 2026-10-06\n---\n## 现象\n\n一句话结论\n",
            encoding="utf-8", newline="\n")
        self.cfg = AS.Config(db=self.db,
                             cards_root=self.cards,
                             glossary=ROOT / "static" / "glossary.json",
                             artifacts_meta=self.meta)

    def tearDown(self):
        self.td.cleanup()


def _fake_rev(band, hits):
    return {"account": {"user_turns": 9, "n_band": len(band),
                        "n_turn_words": len(hits)},
            "thresholds": {"band": 0.8, "dup": 0.9},
            "band": band, "turn_hits": hits}


class AssembleTest(PackFixture):
    def build(self, **kw):
        return RP.build(self.cfg, sid=self.SID, **kw)

    def test_sid_required(self):
        with self.assertRaises(ValueError):
            RP.build(self.cfg, sid="  ")

    def test_versions_only_this_sid(self):
        d = self.build()
        self.assertEqual([v["seq"] for v in d["versions"]], [70, 71, 72],
                         "别的会话的版本混进来了")
        self.assertEqual(d["target"]["n_versions"], 3)
        self.assertEqual(d["target"]["changed_chars"],
                         len("第一版") + len("第二版内容") + len("第三版"))

    def test_auto_pick_when_no_file_path(self):
        d = RP.build(self.cfg, sid=self.SID)
        self.assertTrue(d["auto_picked"])
        self.assertEqual(d["file_path"], r"docs\稿.md")
        self.assertTrue(d["files"], "该会话登记过的文件清单要一起给（供切换）")

    def test_unknown_file_hint(self):
        d = RP.build(self.cfg, sid=self.SID, file_path=r"no\such.md")
        self.assertEqual(d["versions"], [])
        self.assertIn("逐字一致", d["hint"])

    def test_unconfigured_hint_refuses_to_run(self):
        cfg = AS.Config(db=self.db, cards_root=self.cards,
                        glossary=ROOT / "static" / "glossary.json",
                        artifacts_meta=None)
        d = RP.build(cfg, sid=self.SID)
        self.assertFalse(d["configured"])
        self.assertIn("artifacts extract", d["hint"])
        self.assertIn("绝不代跑", d["hint"])

    def test_band_filtered_to_this_sid(self):
        band = [{"sid": self.SID, "rowid_a": 5, "rowid_b": 6, "jaccard": 0.85},
                {"sid": "dsh:s2", "rowid_a": 7, "rowid_b": 8,
                 "jaccard": 0.86}]
        hits = [{"sid": self.SID, "rowid": 9, "word": "重来", "head": "重来一遍"},
                {"sid": "dsh:s2", "rowid": 10, "word": "算了", "head": "算了吧"}]
        with mock.patch("actions.analysis.revisions.collect",
                        return_value=_fake_rev(band, hits)):
            d = self.build()
        self.assertEqual(len(d["revisions"]["band"]), 1)
        self.assertEqual(d["revisions"]["band"][0]["sid"], self.SID)
        self.assertEqual(len(d["revisions"]["turn_hits"]), 1)
        self.assertEqual(d["revisions"]["turn_hits"][0]["word"], "重来")

    def test_process_metrics_are_ratios_not_scores(self):
        band = [{"sid": self.SID, "rowid_a": 5, "rowid_b": 6, "jaccard": 0.85},
                {"sid": self.SID, "rowid_a": 6, "rowid_b": 7, "jaccard": 0.87}]
        hits = [{"sid": self.SID, "rowid": 9, "word": "重来", "head": "重来"}]
        with mock.patch("actions.analysis.revisions.collect",
                        return_value=_fake_rev(band, hits)):
            d = self.build()
        pr = d["revisions"]["process"]
        self.assertEqual(pr["band_pairs"], 2)
        self.assertEqual(pr["turn_words"], 1)
        self.assertAlmostEqual(pr["band_per_version"], round(2 / 3, 2))
        self.assertTrue(any("不是质量分" in x for x in d["honest"]))

    def test_timeline_assigns_before_version(self):
        band = [{"sid": self.SID, "rowid_a": 5, "rowid_b": 6, "jaccard": 0.85}]
        hits = [{"sid": self.SID, "rowid": 9, "word": "重来", "head": "重来"}]
        with mock.patch("actions.analysis.revisions.collect",
                        return_value=_fake_rev(band, hits)):
            d = self.build()
        # 假数据的 ts 是空的（messages 空表）→ 归不到版本上，必须是 None
        # 而不是瞎指一个版本号：宁可"不知道"，也不许编一个位置
        for x in d["timeline"]:
            self.assertIsNone(x["before_version"])
        self.assertTrue(any("不是因果" in x for x in d["honest"]))

    def test_window_pure_function(self):
        versions = [{"seq": 1, "ts": "2026-01-01 10:00:00"},
                    {"seq": 2, "ts": "2026-01-01 11:00:00"}]
        band = [{"sid": "s", "rowid_a": 1, "rowid_b": 2, "jaccard": 0.81,
                 "ts": "2026-01-01 10:30:00"}]
        hits = [{"sid": "s", "rowid": 3, "word": "重来",
                 "ts": "2026-01-01 09:00:00"}]
        out = RP._window(versions, band, hits)
        got = {x["kind"]: x["before_version"] for x in out}
        self.assertEqual(got["打磨候选"], 1, "10:30 落在第 1 版之后 → 第 1 版")
        self.assertIsNone(got["反转候选"], "09:00 早于所有版本 → 不知道，不是 0")

    def test_err_groups_merge_same_pattern(self):
        d = self.build()
        g = d["errors"]["groups"]
        self.assertEqual(d["errors"]["total"], 3)
        top = g[0]
        self.assertEqual(top["n"], 2, "同一条错误犯两次应并成一组")
        self.assertEqual(top["class"], "tool_interface")
        self.assertEqual(top["pattern"], "FS_STALE_VERSION")
        self.assertTrue(top["first"]["resolved"], "后面还有同工具调用 → 有重试")

    def test_card_strong_match_without_source_prefix(self):
        # 真实卡片里锚点常不带 `dsh:` 前缀——逐字比会把同一会话判成没关系
        d = self.build()
        self.assertEqual(len(d["cards"]), 1)
        c = d["cards"][0]
        self.assertTrue(c["strong"])
        self.assertIn("源前缀", c["why"])

    def test_card_weak_match_names_the_word(self):
        (self.cards / "kc-2.md").write_text(
            "---\nid: kc-2\ntitle: 别的东西\ntype: insight\n"
            "triggers: [稿.md]\nconfidence: 0.5\ncreated: 2026-10-06\n---\n"
            "## 现象\n\nx\n", encoding="utf-8", newline="\n")
        d = self.build()
        weak = [c for c in d["cards"] if not c["strong"]]
        self.assertEqual(len(weak), 1, "triggers 撞上文件名应是弱命中")
        self.assertIn("稿.md", weak[0]["why"], "弱命中必须写明撞了哪个词")

    def test_anchors_cover_all_kinds(self):
        band = [{"sid": self.SID, "rowid_a": 5, "rowid_b": 6, "jaccard": 0.85}]
        hits = [{"sid": self.SID, "rowid": 9, "word": "重来", "head": "重来"}]
        with mock.patch("actions.analysis.revisions.collect",
                        return_value=_fake_rev(band, hits)):
            d = self.build()
        kinds = {a["kind"] for a in d["anchors"]}
        for k in ("产物", "打磨候选", "反转候选", "错误步", "卡片"):
            self.assertIn(k, kinds, "锚点缺一类：%s" % k)
        for a in d["anchors"]:
            self.assertTrue(a["ref"], "锚点没有指向")
            self.assertTrue(a["note"], "锚点没有说明")

    def test_md_has_steps_and_boundaries(self):
        d = self.build()
        md = d["md"]
        for kw in ("评审包", "版本序列", "怎么用（三步）", "cards new",
                   "不是因果", "不是质量分"):
            self.assertIn(kw, md)
        self.assertEqual(len(d["steps"]), 3)

    def test_same_session_rules(self):
        self.assertTrue(RP._same_session("dsh:a/b", "a/b"))
        self.assertTrue(RP._same_session("dsh:a/b", "dsh:a/b"))
        self.assertFalse(RP._same_session("dsh:a/b", "dsh:a/c"))
        # 不越界归一：大小写不同就是不同（猜错比漏配更坏）
        self.assertFalse(RP._same_session("dsh:A/b", "A/B"))
        self.assertFalse(RP._same_session("", "dsh:a"))


@unittest.skipUnless((UP / "artifacts_meta.db").is_file()
                     and (UP / "harvester.db").is_file(),
                     "需要真产物台账与索引库（H9）")
class RealPackTest(unittest.TestCase):
    def test_real_pack(self):
        import sqlite3 as s3
        meta = UP / "artifacts_meta.db"
        con = s3.connect("file:%s?mode=ro" % meta.as_posix(), uri=True)
        try:
            sid, fp = con.execute(
                "SELECT sid, file_path FROM artifacts GROUP BY sid, file_path "
                "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        finally:
            con.close()
        cfg = AS.Config(db=UP / "harvester.db",
                        cards_root=UP / "knowledge" / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        topics_meta=UP / "topics_meta.db",
                        artifacts_meta=meta)
        d = RP.build(cfg, sid=sid, file_path=fp)
        self.assertEqual(d["schema"], "review_pack/1")
        self.assertTrue(d["configured"])
        self.assertTrue(d["versions"], "真库上版本序列不该空")
        self.assertGreater(d["target"]["n_versions"], 0)
        self.assertTrue(d["md"])
        self.assertIn("评审包", d["md"])


if __name__ == "__main__":
    unittest.main()
