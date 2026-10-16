# -*- coding: utf-8 -*-
"""v0.76 主题研读面（V3）后端测试：topicbrief.build 聚合层。

钉住（用户裁决 2026-10-15 四原则——知识直观可见/机器易消费）：
1. 构成/活跃：成员来源分布与月度分布来自 sessions（fixture 三个来源）；
2. 高频问题：kwstats.extract_grams 同函数、数字=出现次数；
3. 错误模式：errstats.pattern_stats 同函数，全库跑→成员过滤（非成员不计）；
4. skill 行为：behstats 口径，成员过滤（fixture 无 skill 调用 → 空列表）；
5. 重复会话：topicprep.dedupe_topic（H40）；
6. chain 结论层：configured=False（未配置）与 configured=True+空（确实没有）
   两种空态诚实分开；
7. 健康：不在索引库的成员如实列出；
8. 只读：build 前后库文件字节不变。
"""
from __future__ import annotations

import json
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
    from actions import topicbrief  # noqa: E402
    from harvester.indexing import SCHEMA  # noqa: E402
else:  # pragma: no cover
    raise RuntimeError("上游 harvester 不可用：" + str(_ERR))


def _mk_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    rows = [
        ("workbuddy-transcript:w1", "workbuddy-transcript", "w1",
         "2026-09-05 10:00:00", "钩子怎么写"),
        ("yuanbao-raw:y1", "yuanbao-raw", "y1",
         "2026-09-20 11:00:00", "节奏与钩子讨论"),
        ("dsh:a1", "dsh", "a1", "2026-10-03 09:00:00", "文件不在了"),
    ]
    for sid, src, sessid, ts, title in rows:
        con.execute("INSERT INTO sessions (sid, source, session_id, "
                    "created_at, title) VALUES (?,?,?,?,?)",
                    (sid, src, sessid, ts, title))
    msgs = [
        ("workbuddy-transcript:w1", "user", "钩子怎么写才抓人？前三行定生死"),
        ("yuanbao-raw:y1", "user", "节奏与钩子的关系是什么"),
        ("dsh:a1", "user", "文件不在了怎么办"),
    ]
    for sid, role, text in msgs:
        con.execute("INSERT INTO messages (sid, role, ts, text, raw) "
                    "VALUES (?,?,?,?,?)",
                    (sid, role, "2026-10-01 10:00:00", text, text))
    con.execute("INSERT INTO steps (sid, seq, ts, tool, phase, status, error) "
                "VALUES ('dsh:a1', 1, '2026-10-03 09:01:00', 'Write', "
                "'result', 'error', "
                "'Error: cannot write \"D:\\\\x.md\": file no longer exists')")
    con.commit()
    con.close()


def _mk_meta(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT, "
                "keywords TEXT, members TEXT, created TEXT)")
    members = json.dumps([
        {"sid": "workbuddy-transcript:w1"},
        {"sid": "yuanbao-raw:y1"},
        {"sid": "dsh:a1"},
        {"sid": "ghost:missing"}], ensure_ascii=False)
    con.execute("INSERT INTO topics (id, name, keywords, members, created) "
                "VALUES (?,?,?,?,?)",
                ("tp-1", "叙事节奏",
                 json.dumps(["节奏", "钩子"], ensure_ascii=False),
                 members, "2026-10-08 10:00"))
    con.commit()
    con.close()


class TestTopicBrief(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        self.meta = root / "topics_meta.db"
        _mk_db(self.db)
        _mk_meta(self.meta)
        self.cfg = SimpleNamespace(
            db=self.db, topics_meta=self.meta, chain_root=None,
            cards_root=root / "cards")

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self):
        return topicbrief.build(self.cfg, "tp-1", "coarse",
                                {"level": "coarse", "rows": []})

    def test_members_and_sources(self):
        d = self._build()
        self.assertEqual(d["members"], 4)
        self.assertEqual(d["composition"]["by_source"],
                         {"workbuddy-transcript": 1, "yuanbao-raw": 1,
                          "dsh": 1})
        self.assertTrue(d["composition"]["months"])

    def test_questions_use_member_filter(self):
        d = self._build()
        grams = " ".join(x["gram"] for x in d["questions"]["top"])
        self.assertIn("钩子", grams)
        self.assertNotIn("不属于", grams)

    def test_errors_member_filtered_and_sourced(self):
        d = self._build()
        pats = d["errors"]["patterns"]
        self.assertEqual(d["errors"]["total"], 1)
        self.assertEqual(pats[0]["class"], "context")
        self.assertTrue(d["errors"]["source"])

    def test_skills_member_filtered(self):
        d = self._build()
        self.assertEqual(d["skills"]["skills"], [],
                         "fixture 里没有 skill 调用——空列表，不是错误")

    def test_dupes_counts(self):
        d = self._build()
        self.assertEqual(d["dupes"]["reps"], 4)
        self.assertEqual(d["dupes"]["duplicates"], 0)

    def test_chains_unconfigured_honest(self):
        d = self._build()
        self.assertFalse(d["chains"]["configured"])
        self.assertIn("未传 --chain-root", d["chains"]["honest"])

    def test_chains_configured_but_empty_honest(self):
        self.cfg.chain_root = Path(self.tmp.name) / "topics"
        self.cfg.chain_root.mkdir()
        d = self._build()
        self.assertTrue(d["chains"]["configured"])
        self.assertIn("确实没有", d["chains"]["honest"])

    def test_health_lists_missing_member(self):
        d = self._build()
        self.assertIn("ghost:missing", d["health"]["members_not_in_index"])


class TestTopicBriefReadOnly(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "h.db"
        self.meta = root / "topics_meta.db"
        _mk_db(self.db)
        _mk_meta(self.meta)
        self.cfg = SimpleNamespace(
            db=self.db, topics_meta=self.meta, chain_root=None,
            cards_root=root / "cards")

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_is_readonly(self):
        before = (self.db.read_bytes(), self.meta.read_bytes())
        topicbrief.build(self.cfg, "tp-1", "coarse",
                         {"level": "coarse", "rows": []})
        self.assertEqual((self.db.read_bytes(), self.meta.read_bytes()),
                         before, "研读面必须只读（库字节不变）")


if __name__ == "__main__":
    unittest.main()
