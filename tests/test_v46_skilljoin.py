# -*- coding: utf-8 -*-
"""P4-5（v0.55）测试：skill 调用点挂轨道（report-skill-join 数据消费）。

TODO P4-5 验收（逐字，规格即测试）：
「pdf-text-extractor 案例在时间线上可见调用点与成败」

本文件钉住（数据面 + 端点；渲染在 tests/render_skilljoin.js 真执行）：
1. **上游确定性层直通**：view 端点 = `harvester.skilljoin.build_cross`
   （T4 权威口径），不重算 join——成员内节点精确归组带 turn、时间窗
   归组、margin 只向后放宽（`_after` 后缀）三通道全部来自上游实现；
2. **成败可见**：同一 fixture 里 ok 与 error 两种调用都在 rows 里带
   status（前端渲染门负责 ✅/❌ 可视）；
3. **skill 过滤与 margin**：skill= 全部/单名；margin=0 时不归后期调用，
   margin=90（叙事节奏 chain 的认可口径）归 temporal_after（after_stage）；
4. **诚实降级**：无 chain 长文 → 404（不猜路径）；margin 非法 → 400；
5. **只读**：请求前后索引库 sha256 不变。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
VIEW_ROOT = _HERE.parent
sys.path.insert(0, str(VIEW_ROOT))

from actions import ensure_harvester_importable  # noqa: E402

_ERR = ensure_harvester_importable()
if _ERR is None:
    from actions import actionsserve as AS  # noqa: E402
    from harvester.indexing import SCHEMA, index_session  # noqa: E402
    from harvester.models import Message, SessionRecord  # noqa: E402
    from harvester.topics import add_members, register_topic  # noqa: E402

CHAIN_MD = """---
topic: 挂轨道测试主题
topic_id: {tid}
members:
  - dsh:s1
anchors:
  - stage: 阶段一·调用可见
    span: 2026-01-01 ~ 2026-01-02
    nodes:
      - sid: dsh:s1
        turn: 1
        note: 调用点所在回合
---

# 正文占位
"""


def _mk_index_db(db: Path) -> None:
    """临时索引库：
    - s1（成员）：两对 Skill 调用（ok + error），turn 1 内；
    - s2（非成员）：span 内一次（时间窗归组）；
    - s3（非成员）：span+margin 外一次（unassigned）；
    - s4（非成员）：span 结束后 3 天（margin=0 unassigned / 90 归 _after）。
    """
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        for sid, day in (("s1", "2026-01-01"), ("s2", "2026-01-02"),
                         ("s3", "2026-03-01"), ("s4", "2026-01-05")):
            msgs = [Message(role="user", text="轮一",
                                    timestamp=day + " 09:00:00")]
            if sid == "s1":
                msgs.append(Message(role="user", text="轮二",
                                    timestamp=day + " 10:00:00"))
            index_session(con, SessionRecord(
                source="dsh", session_id=sid, title="会话" + sid,
                created_at=day + " 09:00:00", updated_at=day + " 09:05:00",
                messages=msgs), category="test")
        def call(seq, ts):
            return ("dsh:s1", seq, ts, "Skill", "call", None, None,
                    json.dumps({"skill": "pdf-text-extractor", "args": "a.pdf"}))
        def res(seq, ts, st):
            return ("dsh:s1", seq, ts, "Skill", "result", st, None, "")
        con.executemany(
            "INSERT INTO steps(sid,seq,ts,tool,phase,status,error,detail) "
            "VALUES (?,?,?,?,?,?,?,?)", [
                call(1, "2026-01-01 09:30:00"),
                res(2, "2026-01-01 09:31:00", "ok"),
                call(3, "2026-01-01 09:40:00"),
                res(4, "2026-01-01 09:41:00", "error"),
            ])
        for sid, seq, ts in (("dsh:s2", 1, "2026-01-02 08:00:00"),
                             ("dsh:s3", 1, "2026-03-01 08:00:00"),
                             ("dsh:s4", 1, "2026-01-05 08:00:00")):
            con.execute(
                "INSERT INTO steps(sid,seq,ts,tool,phase,status,error,detail) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (sid, seq, ts, "Skill", "call", None, None,
                 json.dumps({"skill": "pdf-text-extractor", "args": "b.pdf"})))
        con.commit()
    finally:
        con.close()


def _mk_env(td: Path):
    if AS.ensure_harvester_importable():
        raise unittest.SkipTest("上游 harvester 不可用")
    db = td / "harvester.db"
    _mk_index_db(db)
    meta = td / "topics_meta.db"
    tid = register_topic(meta, "挂轨道测试主题", keywords=["测试"])
    add_members(meta, tid, ["dsh:s1"])
    chain_root = td / "topics"
    chain_root.mkdir()
    (chain_root / ("chain-挂轨道测试主题.md")).write_text(
        CHAIN_MD.format(tid=tid), encoding="utf-8", newline="\n")
    cfg = AS.Config(db=db, cards_root=td, glossary=td / "g.json",
                    topics_meta=meta, chain_root=chain_root)
    return cfg, db, tid


class _Srv:
    def __init__(self, cfg):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                       AS.make_handler(cfg))
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever,
                                       daemon=True)
        self.thread.start()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=60) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestSkillJoinEndpoint(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cfg, self.db, self.tid = _mk_env(Path(self._td.name))
        self.srv = _Srv(self.cfg)

    def tearDown(self):
        self.srv.stop()
        self._td.cleanup()

    def _sha(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def test_joined_rows_carry_anchor_turn_status(self):
        """pdf-text-extractor 案例：调用点可见 + 成败可见（验收主判据）。"""
        code, d = self.srv.get("/api/topic/%s/skilljoin"
                               "?skill=pdf-text-extractor"
                               % urllib.parse.quote(self.tid))
        self.assertEqual(code, 200)
        self.assertEqual(d["skill"], "pdf-text-extractor")
        self.assertEqual(d["total"], 5)    # s1×2 配对 + s2/s3/s4 未闭合（no_result）
        self.assertEqual(d["joined"], 3)   # s1×2（成员）+ s2（时间窗）
        rows = d["stages"][0]["rows"]
        sts = {(r["anchor"], r["status"]) for r in rows}
        self.assertIn(("dsh:s1#1", "ok"), sts)      # 成败都可见
        self.assertIn(("dsh:s1#3", "error"), sts)
        member = [r for r in rows if r["kind"] == "member"]
        self.assertEqual(len(member), 2)
        self.assertEqual(member[0]["turn"], 1)      # 节点精确归组带 turn
        self.assertTrue(any(r["kind"] == "temporal" for r in rows))
        self.assertEqual(len(d["unassigned"]), 2)   # s3 窗外 + s4 margin=0

    def test_margin_only_backwards_with_after_suffix(self):
        code, d = self.srv.get("/api/topic/%s/skilljoin?margin=90"
                               % urllib.parse.quote(self.tid))
        self.assertEqual(code, 200)
        rows = d["stages"][0]["rows"]
        s4 = [r for r in rows if r["sid"] == "dsh:s4"]
        self.assertEqual(len(s4), 1)
        self.assertEqual(s4[0]["kind"], "temporal_after")
        self.assertTrue(s4[0]["after_stage"])
        # margin=0 时 s4 不归组
        code, d0 = self.srv.get("/api/topic/%s/skilljoin"
                                % urllib.parse.quote(self.tid))
        self.assertNotIn("dsh:s4",
                         [r["sid"] for r in d0["stages"][0]["rows"]])

    def test_skill_filter_none(self):
        code, d = self.srv.get("/api/topic/%s/skilljoin?skill=nope"
                               % urllib.parse.quote(self.tid))
        self.assertEqual(code, 200)
        self.assertEqual(d["total"], 0)
        self.assertEqual(d["joined"], 0)

    def test_topic_without_chain_is_404_not_guess(self):
        code, d = self.srv.get("/api/topic/tp-nochain/skilljoin")
        self.assertEqual(code, 404)
        self.assertIn("chain", d.get("error", ""))

    def test_bad_margin_is_400(self):
        code, d = self.srv.get("/api/topic/%s/skilljoin?margin=abc"
                               % urllib.parse.quote(self.tid))
        self.assertEqual(code, 400)

    def test_readonly_db_unchanged(self):
        before = self._sha()
        self.srv.get("/api/topic/%s/skilljoin?margin=90"
                     % urllib.parse.quote(self.tid))
        self.assertEqual(self._sha(), before)


if __name__ == "__main__":
    unittest.main()
