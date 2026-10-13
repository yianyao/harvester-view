# -*- coding: utf-8 -*-
"""P3-3 测试：今日工作台数据面（`/api/overview`）四队列 + 三环。

三条纪律，每条一个断言簇：

1. **每个数字都带口径与真源**（P3-3 验收原文「三环积压量口径在页面可见」）：
   `value/unit/source/definition` 四件套齐全，缺一个都算没交付；
2. **互相对得上**：同一实体的两次计数必须相等——例如「待修补」队列数
   = 报告里 unresolved>=1 的条目数、`given_up` 模式数与 `pattern-sessions`
   口径一致。**口径冲突要当场红，不能等页面上两个数打架**；
3. **未执行 ≠ 0**：`skipped`/`skipped_topic`（未评估）与 `不下结论`
   （评估了但样本不够）分开报——AGENTS.md §五 16 条的纪律。
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import overview as OV  # noqa: E402

UP = AS.upstream_root()
REAL_DB = UP / "harvester.db"
REAL_SUG = UP / "suggestions_meta.db"
REAL_TOPICS = UP / "topics_meta.db"
REAL_KW = UP / "keywords_meta.db"
CARDS = Path.home() / ".workbuddy" / "knowledge" / "cards"
CHAINS = Path.home() / ".workbuddy" / "knowledge" / "topics"


def _have_real() -> bool:
    return all(p.is_file() for p in (REAL_DB, REAL_SUG, REAL_TOPICS))


class OverviewTest(unittest.TestCase):
    """真库现算（只读）。缺真库时 skip，不假绿。"""

    @classmethod
    def setUpClass(cls):
        if not _have_real():
            raise unittest.SkipTest("真库/meta 库不在位")
        if AS.ensure_harvester_importable():
            raise unittest.SkipTest("上游不可用")
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        gl = td / "g.json"
        gl.write_text("{}", encoding="utf-8")
        cls.cfg = AS.Config(db=REAL_DB, cards_root=CARDS, glossary=gl,
                            topics_meta=REAL_TOPICS,
                            suggestions_meta=REAL_SUG,
                            keywords_meta=REAL_KW,
                            chain_root=CHAINS,
                            actions_meta=td / "am.db")
        cls.ov = OV.build_overview(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    # -- ① 四件套齐全 --

    def test_queues_have_four_required_fields(self):
        qs = self.ov["queues"]
        self.assertEqual([q["queue_id"] for q in qs],
                         ["decide", "repair", "distill", "verify"])
        for q in qs:
            self.assertIsInstance(q["count"], int)
            self.assertGreaterEqual(q["count"], 0)
            for field in ("label", "question", "source", "definition",
                          "where"):
                self.assertTrue(q.get(field),
                                "队列 %s 缺 %s（口径必须随数字一起给）"
                                % (q["queue_id"], field))

    def test_every_arm_has_source_and_definition(self):
        n = 0
        for r in self.ov["rings"]:
            self.assertTrue(r.get("definition"), r["ring_id"])
            self.assertEqual(len(r["arms"]), 2 if r["ring_id"] == "skill" else 3,
                             "环 %s 段数变了，必须显式复核" % r["ring_id"])
            for a in r["arms"]:
                n += 1
                self.assertIsInstance(a["value"], int)
                self.assertTrue(a.get("unit"), a)
                self.assertTrue(a.get("source"), "臂缺真源：%r" % a)
                self.assertTrue(a.get("definition"), "臂缺口径：%r" % a)
        self.assertGreaterEqual(n, 8)

    def test_all_numbers_non_negative(self):
        for r in self.ov["rings"]:
            for a in r["arms"]:
                self.assertGreaterEqual(a["value"], 0, a)
        for arms in self.ov["queue_details"].values():
            for a in arms:
                self.assertGreaterEqual(a["value"], 0, a)

    # -- ② 互相对账（同一实体两处计数必须相等） --

    def test_repair_queue_reconciles_with_report(self):
        con = AS.open_ro(REAL_DB)
        try:
            agents = AS._hapi.api_reports_agents(  # noqa: SLF001
                con, REAL_DB, {}, meta_db=REAL_SUG)
        finally:
            con.close()
        want = len([e for e in agents["entries"] if e["unresolved_count"] >= 1])
        got = [q for q in self.ov["queues"] if q["queue_id"] == "repair"][0]
        self.assertEqual(got["count"], want,
                         "待修补队列数 ≠ 报告里 unresolved>=1 的条目数")

    def test_given_up_arms_reconcile_with_pattern_stats(self):
        """错误环的 given_up 两臂：模式数 ≤ 次数，且次数 = pattern_stats 求和。

        这条同时钉住一个**已修的坑**：`/api/reports/errors` 的 pattern 行
        **没有透出 given_up**，谁照它算就会得到 0（本会话实测 0 vs 真实 31）。
        """
        from harvester.errstats import collect_errors_from_db, pattern_stats
        con = AS.open_ro(REAL_DB)
        try:
            errs, _meta = collect_errors_from_db(REAL_DB, con=con)
        finally:
            con.close()
        ps = pattern_stats(errs)
        want_pat = sum(1 for v in ps.values() if v["given_up"])
        want_tot = sum(v["given_up"] for v in ps.values())
        con = AS.open_ro(REAL_DB)
        try:
            rep = AS._hapi.api_reports_errors(con, REAL_DB, {})  # noqa: SLF001
        finally:
            con.close()
        api_total = sum(p.get("given_up", 0) for p in rep["patterns"])
        self.assertEqual(api_total, 0,
                         "如果 api 的 pattern 行开始带 given_up 了，"
                         "本条注释与工作台口径都要跟着更新")
        arms = self.ov["queue_details"]["repair"]
        vals = {a["source"]: a["value"] for a in arms}
        got_tot = vals["errstats.pattern_stats.given_up"]
        self.assertIn(want_tot, [a["value"] for a in arms],
                      "错误环未解决次数合计与 pattern_stats 对不上")
        self.assertTrue(any(a["value"] == want_pat for a in arms),
                        "错误模式数（given_up>0）与 pattern_stats 对不上")
        self.assertLessEqual(want_pat, got_tot, "模式数不可能超过次数")

    def test_verify_queue_counts_inconclusive_only(self):
        """待验证 = 不下结论；它不该等于 adopted_total，也不该是 0 假绿。"""
        from harvester import harvesteval as HE
        ev = HE.run_eval(REAL_DB, REAL_SUG)
        got = [q for q in self.ov["queues"] if q["queue_id"] == "verify"][0]
        self.assertEqual(got["count"], ev["counts"][HE.VERDICT_INCONCLUSIVE])
        arms = {a["source"]: a["value"]
                for a in self.ov["queue_details"]["verify"]}
        self.assertEqual(arms["harvesteval.run_eval.adopted_total"],
                         ev["adopted_total"])
        # 未执行 ≠ 0：skipped 与不下结论是两个数
        self.assertEqual(arms["harvesteval.run_eval.skipped(_topic)"],
                         ev["skipped"] + ev["skipped_topic"])

    def test_topics_ring_matches_api_topics_and_cards(self):
        con = AS.open_ro(REAL_DB)
        try:
            tp = AS._hapi.api_topics(con, REAL_DB, REAL_TOPICS, CHAINS)  # noqa: SLF001
            cards = AS._hapi.api_cards(con, REAL_DB, CARDS, {})  # noqa: SLF001
        finally:
            con.close()
        ring3 = [r for r in self.ov["rings"] if r["ring_id"] == "topics"][0]
        vals = [a["value"] for a in ring3["arms"]]
        self.assertEqual(vals[0], len(tp["topics"]))
        self.assertEqual(vals[1],
                         sum(int(t.get("chains_count") or 0)
                             for t in tp["topics"]))
        s = cards["summary"]
        self.assertEqual(vals[2], int(s.get("error", 0)) + int(s.get("warn", 0)))

    def test_distill_queue_matches_triage(self):
        from harvester.triage import collect_triage
        con = AS.open_ro(REAL_DB)
        try:
            tri = collect_triage(REAL_DB, cards_root=CARDS, min_count=2,
                                 top_n=10, con=con)
        finally:
            con.close()
        got = [q for q in self.ov["queues"] if q["queue_id"] == "distill"][0]
        self.assertEqual(got["count"], len(tri["new_patterns"]))

    def test_decide_queue_counts_pending_status(self):
        from harvester import suggestmeta as SM
        recs = SM.load_records(REAL_SUG)
        want = len([r for r in recs.values() if r["status"] == "pending"])
        got = [q for q in self.ov["queues"] if q["queue_id"] == "decide"][0]
        self.assertEqual(got["count"], want)


class OverviewHttpTest(unittest.TestCase):
    """接线真打：函数对但路由没接上是这轮最可能的错。"""

    @classmethod
    def setUpClass(cls):
        if not _have_real():
            raise unittest.SkipTest("真库/meta 库不在位")
        if AS.ensure_harvester_importable():
            raise unittest.SkipTest("上游不可用")
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        gl = td / "g.json"
        gl.write_text("{}", encoding="utf-8")
        cfg = AS.Config(db=REAL_DB, cards_root=CARDS, glossary=gl,
                        topics_meta=REAL_TOPICS, suggestions_meta=REAL_SUG,
                        keywords_meta=REAL_KW, chain_root=CHAINS,
                        actions_meta=td / "am.db")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), AS.make_handler(cfg))
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.socket.getsockname()[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.th.join(timeout=2)
        cls._td.cleanup()

    def test_overview_endpoint(self):
        with urllib.request.urlopen(self.base + "/api/overview",
                                    timeout=60) as r:
            self.assertEqual(r.status, 200)
            import json
            d = json.loads(r.read().decode("utf-8"))
        self.assertEqual(len(d["queues"]), 4)
        self.assertEqual(len(d["rings"]), 3)
        self.assertIn("view_actions_api", d)
        self.assertIn("hint", d)
        self.assertTrue(d["db_fingerprint"]["sessions"] > 0)

    def test_root_lists_overview_endpoint(self):
        with urllib.request.urlopen(self.base + "/", timeout=15) as r:
            import json
            d = json.loads(r.read().decode("utf-8"))
        self.assertIn("/api/overview", d["endpoints"])


if __name__ == "__main__":
    unittest.main()
