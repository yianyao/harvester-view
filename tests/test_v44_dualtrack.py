# -*- coding: utf-8 -*-
"""P4-4（v0.54）测试：主题工作台双轨时间线（数据面 + 端点）。

TODO P4-4 验收（逐字，规格即测试）：
「任选有 artifacts 的主题上/下轨同屏对齐；打磨带与 P4-3 候选一致；
  反转点标记可点击看两轮原文对比」

本文件钉住：
1. **与 P4-3 候选一致是构造性的**：双轨数据 = revisions.collect 全库跑一遍
   后按主题成员 sid 过滤——成员内的对必须与 P4-3 候选逐字段一致
   （jaccard / rowid），非成员会话的候选不得混入；
2. **两轮原文对比自带**：band 每对带 a/b 两侧预览（读 raw），turn_hits 带
   cur + prev（同一会话内上一个 user 轮）——点击即看，不二次请求；
3. **下轨产物流**：artifacts 按成员 sid 过滤、按 (sid, file_path) 分组、
   版本按 seq 有序、old/new 预览截断；未配置 artifacts_meta 时诚实降级
   （configured=false，不报错不猜）；
4. **只读**：模块源码零写库语句；请求前后索引库 sha256 不变；
5. **参数纪律**：level 只认 coarse/mid（fine/artifact 是按需档，不在双轨
   范围）→ 400；未知主题 → 404；缺 topics_meta → 503。
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
import urllib.parse
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
    from actions import dualtrack  # noqa: E402
    from harvester.artifacts import ensure_artifacts_db  # noqa: E402
    from harvester.indexing import SCHEMA, index_session  # noqa: E402
    from harvester.models import Message, SessionRecord  # noqa: E402
    from harvester.topics import add_members, register_topic  # noqa: E402

# 与 test_v42 同款互异字串：T1 vs T2 恰差末字 → J = 10/12 ≈ 0.833（边界带）
T1 = "甲乙丙丁戊己庚辛壬癸子丑"
T2 = "甲乙丙丁戊己庚辛壬癸子寅"


def _mk_index_db(db: Path) -> None:
    """临时索引库：s1（成员，含打磨带对 + 反转点）、s2（非成员，对照）。"""
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        for sid, turns in (
            ("s1", [T1, T2, "不对，把开头整段换成另一版试试看"]),
            ("s2", [T1, T2]),
        ):
            msgs = [Message(role="user", text=t) for t in turns]
            index_session(con, SessionRecord(
                source="dsh", session_id=sid, title="会话" + sid,
                created_at="2026-10-01 10:00:0" + sid[-1],
                updated_at="2026-10-01 10:02:0" + sid[-1],
                messages=msgs), category="test")
        con.commit()
    finally:
        con.close()


def _mk_env(td: Path):
    """临时索引库 + topics_meta（一个成员主题）+ artifacts_meta（2 版本）。"""
    if AS.ensure_harvester_importable():
        raise unittest.SkipTest("上游 harvester 不可用")
    db = td / "harvester.db"
    _mk_index_db(db)

    meta = td / "topics_meta.db"
    tid = register_topic(meta, "双轨测试主题", keywords=["测试"])
    add_members(meta, tid, ["dsh:s1"])

    adb = ensure_artifacts_db(td / "artifacts_meta.db")
    con = sqlite3.connect(str(adb))
    try:
        con.executemany(
            "INSERT INTO artifacts(sid,seq,ts,tool,file_path,old_text,new_text) "
            "VALUES (?,?,?,?,?,?,?)",
            [("dsh:s1", 0, "2026-10-01 10:01:00", "Write", "稿件.md",
              None, "第一版全文"),
             ("dsh:s1", 1, "2026-10-01 10:03:00", "Edit", "稿件.md",
              "第一版全文", "第二版全文")])
        con.commit()
    finally:
        con.close()

    cfg = AS.Config(db=db, cards_root=td, glossary=td / "g.json",
                    topics_meta=meta, artifacts_meta=adb)
    return cfg, db, tid


def _mk_index_db_nags(db: Path) -> None:
    """nags 夹具：s1（成员）与 s2（非成员）各有一次同一句叮嘱（含「记得」）。

    N1 归一后 ≥10 字、<600 字（过过滤闸），逐字同串跨 2 会话 → 1 个候选组
    （n=2 < 模板闸 8，不丢）。其余轮次不含 NAG_WORDS，不产生额外组。
    """
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        nag = "记得把工作区的收尾文件写完再退出环境"
        for sid, turns in (
            ("s1", [T1, T2, nag]),
            ("s2", [T1, nag]),
        ):
            # ts 必须有值：真实库 messages.ts 逐条都有（adapter 落库），
            # nags 样例的 ts 是一跳原文的精确锚点（v0.78）
            msgs = [Message(role="user", text=t,
                            timestamp="2026-10-01 10:%02d:00" % i)
                    for i, t in enumerate(turns)]
            index_session(con, SessionRecord(
                source="dsh", session_id=sid, title="会话" + sid,
                created_at="2026-10-01 10:00:0" + sid[-1],
                updated_at="2026-10-01 10:02:0" + sid[-1],
                messages=msgs), category="test")
        con.commit()
    finally:
        con.close()


def _mk_env_nags(td: Path):
    if AS.ensure_harvester_importable():
        raise unittest.SkipTest("上游 harvester 不可用")
    db = td / "harvester.db"
    _mk_index_db_nags(db)
    meta = td / "topics_meta.db"
    tid = register_topic(meta, "nags 双轨主题", keywords=["测试"])
    add_members(meta, tid, ["dsh:s1"])
    cfg = AS.Config(db=db, cards_root=td, glossary=td / "g.json",
                    topics_meta=meta, artifacts_meta=None)
    return cfg, db, tid


class _Srv:
    """随机端口起停 actionsserve（与 test_v41 同款）。"""

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
class TestDualTrackBuild(unittest.TestCase):
    """数据面：build() 的口径与形状。"""

    def test_band_consistent_with_p43_and_scoped_to_members(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db, tid = _mk_env(Path(td))
            from actions.analysis import revisions
            con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
            try:
                con.row_factory = sqlite3.Row
                rev_all = revisions.collect(con)
            finally:
                con.close()
            tl = {"level": "coarse", "rows": [], "members": 1}
            d = dualtrack.build(cfg, tid, "coarse", tl)
            # 成员内候选 = 全库候选按成员过滤（逐字段一致）
            all_pairs = {(p["sid"], p["rowid_a"], p["rowid_b"]): p["jaccard"]
                         for p in rev_all["band"]}
            in_topic = {(p["sid"], p["rowid_a"], p["rowid_b"]): p["jaccard"]
                        for p in d["revisions"]["band"]}
            self.assertEqual(len(in_topic), 1)
            self.assertEqual(d["account"]["band_all"], len(all_pairs))
            self.assertEqual(d["account"]["band"], len(in_topic))
            for k, j in in_topic.items():
                self.assertIn(k, all_pairs)
                self.assertEqual(j, all_pairs[k])   # 与 P4-3 恒等
                self.assertGreaterEqual(j, 0.80)
                self.assertLess(j, 0.90)
            # 非成员 s2 的对不得混入
            self.assertNotIn("dsh:s2", {p["sid"] for p in d["revisions"]["band"]})

    def test_band_pair_carries_two_side_preview_from_raw(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _db, tid = _mk_env(Path(td))
            d = dualtrack.build(cfg, tid, "coarse", {"rows": []})
            p = d["revisions"]["band"][0]
            self.assertIn(T1, p["a"]["preview"])
            self.assertIn(T2, p["b"]["preview"])
            self.assertTrue(p["a"]["rowid"] > 0 and p["b"]["rowid"] > 0)

    def test_turn_hit_carries_cur_and_prev(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _db, tid = _mk_env(Path(td))
            d = dualtrack.build(cfg, tid, "coarse", {"rows": []})
            hits = [h for h in d["revisions"]["turn_hits"]
                    if h["sid"] == "dsh:s1"]
            self.assertEqual(len(hits), 1)
            h = hits[0]
            self.assertEqual(h["word"], "不对")
            self.assertTrue(h["cur"]["preview"].startswith("不对"))
            # 同会话内上一个 user 轮 = T2（反转的是它）
            self.assertIn(T2, h["prev"]["preview"])

    def test_artifacts_grouped_and_capped(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _db, tid = _mk_env(Path(td))
            d = dualtrack.build(cfg, tid, "coarse", {"rows": []})
            art = d["artifacts"]
            self.assertTrue(art["configured"])
            self.assertEqual(len(art["files"]), 1)
            f = art["files"][0]
            self.assertEqual(f["sid"], "dsh:s1")
            self.assertEqual(f["n_versions"], 2)
            self.assertEqual([v["seq"] for v in f["versions"]], [0, 1])
            self.assertIn("第二版全文", f["versions"][1]["new_preview"])
            self.assertEqual(d["account"]["artifact_versions"], 2)

    def test_artifacts_missing_configured_is_honest_false(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, _db, tid = _mk_env(Path(td))
            cfg.artifacts_meta = None
            d = dualtrack.build(cfg, tid, "coarse", {"rows": []})
            self.assertFalse(d["artifacts"]["configured"])
            self.assertEqual(d["artifacts"]["files"], [])
            self.assertIn("artifacts", d["artifacts"]["hint"])


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestDualTrackNags(unittest.TestCase):
    """P5-③ 反复叮嘱候选接入双轨（用户 2026-10-16 批准）。

    口径红线：与 P4-1 候选一致是**构造性的**（调同一 nags.collect 全库跑
    再按成员过滤）；候选**只列不裁决**——「登记零散」只是处置手段之一，
    不等于裁决；非成员的命中保留在 sids 里（页面对账），hit_sids 只列成员。
    """

    def test_nags_group_scoped_and_carries_hit_sids(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db, tid = _mk_env_nags(Path(td))
            d = dualtrack.build(cfg, tid, "coarse", {"rows": []})
            nag = d["nags"]
            self.assertIn("只列不裁决", nag["honest"])
            self.assertEqual(len(nag["groups"]), 1)
            g = nag["groups"][0]
            self.assertEqual(g["sids"], ["dsh:s1", "dsh:s2"],
                             "全库候选组按主题过滤但不丢组外成员（对账用）")
            self.assertEqual(g["hit_sids"], ["dsh:s1"],
                             "hit_sids 只列主题成员")
            self.assertIn("记得", g["sample"]["preview"])
            self.assertTrue(g["sample"].get("ts"),
                            "样例自带 ts（双轨候选一跳原文的精确锚点）")
            self.assertEqual(d["account"]["nags_groups"], 1)
            self.assertEqual(d["account"]["nags_groups_all"], 1)

    def test_nags_consistent_with_collect(self):
        """与 P4-1 候选一致 = 全库 collect 按成员过滤（构造性）。"""
        with tempfile.TemporaryDirectory() as td:
            cfg, db, tid = _mk_env_nags(Path(td))
            from actions.analysis import nags
            con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
            try:
                all_res = nags.collect(con)
            finally:
                con.close()
            d = dualtrack.build(cfg, tid, "coarse", {"rows": []})
            self.assertEqual(d["account"]["nags_groups_all"],
                             all_res["account"]["n_groups"])
            in_topic = {(g["sample"]["sid"], g["sample"]["rowid"])
                        for g in d["nags"]["groups"]}
            mset = {"dsh:s1"}
            expect = {(g["sample"]["sid"], g["sample"]["rowid"])
                      for g in all_res["groups"] if mset & set(g["sids"])}
            self.assertEqual(in_topic, expect)

    def test_nags_honest_empty_without_hits(self):
        """原夹具（T1/T2/反转句不含 NAG_WORDS）→ 诚实空态。"""
        with tempfile.TemporaryDirectory() as td:
            cfg, _db, tid = _mk_env(Path(td))
            d = dualtrack.build(cfg, tid, "coarse", {"rows": []})
            self.assertEqual(d["nags"]["groups"], [])
            self.assertIn("只列不裁决", d["nags"]["honest"])

    def test_nags_track_adds_no_write_sql(self):
        src = Path(dualtrack.__file__).read_text(encoding="utf-8")
        for bad in ("INSERT ", "UPDATE ", "DELETE ", "CREATE "):
            self.assertNotIn(bad, src, "nags 轨道含写语句 %r" % bad)


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestDualTrackReadOnly(unittest.TestCase):
    """验收项：零写库——模块源码无写语句 + 真调用前后库字节不变。"""

    def test_module_has_no_write_sql(self):
        src = Path(dualtrack.__file__).read_text(encoding="utf-8")
        for bad in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ",
                    "DROP ", "ATTACH ", "PRAGMA writable"):
            self.assertNotIn(bad, src, "dualtrack 含写库语句 %r" % bad)

    def test_db_bytes_unchanged_after_build(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, db, tid = _mk_env(Path(td))
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            dualtrack.build(cfg, tid, "coarse", {"rows": []})
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestDualTrackEndpoint(unittest.TestCase):
    """HTTP 面：200 形状 / 400 / 404 / 503。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.cfg, self.db, self.tid = _mk_env(Path(self._td.name))
        self.srv = _Srv(self.cfg)

    def tearDown(self):
        self.srv.stop()
        self._td.cleanup()

    def test_200_shape(self):
        st, d = self.srv.get("/api/topic/%s/dualtrack?level=coarse"
                             % urllib.parse.quote(self.tid))
        self.assertEqual(st, 200)
        self.assertEqual(d["view_actions_api"], 1)
        self.assertEqual(d["topic_id"], self.tid)
        self.assertIn("timeline", d)
        self.assertIn("revisions", d)
        self.assertIn("artifacts", d)
        self.assertEqual(d["level"], "coarse")

    def test_400_bad_level(self):
        st, d = self.srv.get("/api/topic/%s/dualtrack?level=fine"
                             % urllib.parse.quote(self.tid))
        self.assertEqual(st, 400)
        self.assertIn("level", d["error"])

    def test_404_unknown_topic(self):
        st, d = self.srv.get("/api/topic/tp-19700101-999/dualtrack")
        self.assertEqual(st, 404)

    def test_503_without_topics_meta(self):
        self.cfg.topics_meta = None
        srv = _Srv(self.cfg)
        try:
            st, d = srv.get("/api/topic/tp-x/dualtrack")
            self.assertEqual(st, 503)
        finally:
            srv.stop()

if __name__ == "__main__":
    unittest.main(verbosity=2)
