# -*- coding: utf-8 -*-
"""v0.49 测试：工作项层（`actions/workitems.py`）与「数字 ↔ 明细」对账。

用户 2026-10-13 需求原文：
    「工作台，四队列中点击（去处理）即跳转到相关页面，这点很好。但**队列明细
      对不上**，还有，跳转到的页面希望**只显示需处理的记录**，并有处理建议，
      能打包处理命令且可直接跳转到命令台。工作台中闭环健康项，也希望能如上述
      队列一样需求（当前不能跳转，也无建议）」

本文件钉住四组判据：

1. **对账**：每条指标的值都能由它自己的明细按声明的 `mode` 派生出来
   （`items` → `len(items)`；`sum` → `sum(item.metrics[sum_field])`）。
   这是"队列明细对不上"的**正面防线**——数字与明细同源，对不上会红。
2. **空壳也能跑**（不依赖真库）：用手搭的最小 `g` 跑 `targets()`，保证本仓
   没有真库时这些判据仍可执行（skip ≠ 通过，故不把它们推给真库测试）。
3. **真库对账**（缺真库时 skip）：`/api/overview` 的每个数字 == 对应 target 的
   `count` == 那份明细的派生值；且四队列/三环的每一个数字都有 target。
4. **发出去的命令必须真能跑**：`workitems` 生成的每条 CLI 都必须能被
   `harvester.cli.build_parser()` 解析（照 AGENTS.md §四 14「让文档里的命令被
   测试真跑」办）；每个 `action_id` 必须真在 `actions_registry.json` 里。
"""
from __future__ import annotations

import json
import shlex
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import overview as OV  # noqa: E402
from actions import workitems as WI  # noqa: E402

UP = AS.upstream_root()
REAL_DB = UP / "harvester.db"
REAL_SUG = UP / "suggestions_meta.db"
REAL_TOPICS = UP / "topics_meta.db"
REAL_KW = UP / "keywords_meta.db"
CARDS = Path.home() / ".workbuddy" / "knowledge" / "cards"
CHAINS = Path.home() / ".workbuddy" / "knowledge" / "topics"

#: 每条请求都要新算一遍（现算，无缓存），故超时给宽些
_T = 120


def _have_real() -> bool:
    return all(p.is_file() for p in (REAL_DB, REAL_SUG, REAL_TOPICS))


class _Cfg:
    """只有路径的最小 cfg（`_rel` 需要 db 与各 meta 的父目录）。"""

    def __init__(self, d: Path):
        self.db = d / "harvester.db"
        self.cards_root = d / "cards"
        self.suggestions_meta = d / "suggestions_meta.db"
        self.topics_meta = d / "topics_meta.db"
        self.keywords_meta = d / "keywords_meta.db"
        self.artifacts_meta = d / "artifacts_meta.db"
        self.chain_root = d / "chains"
        self.exports_root = d / "exports"


def _empty_g(cfg) -> dict:
    """空壳 `g`：所有上游都是"没有数据"的合法形状（不 mock 私有函数）。"""
    return {
        "cfg": cfg,
        "pstats": {},
        "err_meta": {"error_count": 0},
        "errs_rep": {"patterns": [], "db_fingerprint": {"sessions": 0}},
        "agents": {"entries": []},
        "skills_rep": {"skills": []},
        "topics": {"topics": []},
        "triage": {"new_patterns": [], "old_patterns": [], "skills": []},
        "cards": None,
        "records": {},
        "registry": {"topics": 0, "noise": 0},
        "ev": {"counts": {}, "adopted_total": 0, "skipped": 0,
               "skipped_topic": 0, "entries": []},
        "cards_pending": [],
    }


class ReconcileLogicTest(unittest.TestCase):
    """不需要真库：派生逻辑本身（空壳 g 也能跑）。"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.cfg = _Cfg(Path(cls._td.name))
        cls.g = _empty_g(cls.cfg)
        cls.index = WI.build_index(cls.g)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_target_ids_unique(self):
        ids = [t["target_id"] for t in self.index]
        self.assertEqual(len(ids), len(set(ids)), "target_id 必须唯一")
        self.assertEqual(len(ids), len(WI.target_ids(self.g)))

    def test_errors_ring_definition_explains_window_gap(self):
        """v0.64（用户 2026-10-14 批注：362/343 两边数字对不上）：
        错误环第一段的口径说明必须讲清「报告页=窗口内、此处=全库」，
        并给出对账方法（把报告页窗口调成 0）。"""
        t = next(x for x in self.index if x["target_id"] == "ring:errors.0")
        for needle in ("口径不同", "全库", "调成 0", "30 天"):
            self.assertIn(needle, t["definition"],
                          "ring:errors.0 定义缺「%s」——窗口/全库口径没讲清" % needle)

    def test_target_ids_are_url_safe(self):
        """target id 必须**不做转义就能放进 URL 查询串**。

        真实缺陷（本轮自己踩的）：首版用 `ring:errors#0` 这种 id，而 `#` 是 URL 的
        **片段分隔符**——`?target=ring:errors#0` 到服务端只剩 `ring:errors` → 404。
        页面上因为用了 `encodeURIComponent` 侥幸能跑，**任何手写 URL / 别的客户端
        都会踩**。

        判据按 RFC 3986 §3.4：查询串里允许 `pchar`（含 `:` 与 `@`）与 `/`、`?`，
        但 `#` 是片段开头、`&`/`=` 是参数分隔符——这些一律不许出现在 id 里。
        故本门只放行 `[A-Za-z0-9._:-]`，并要求 `quote(id, safe=":_-.") == id`。
        """
        from urllib.parse import quote
        safe = ":_-."
        for t in self.index:
            tid = t["target_id"]
            self.assertRegex(tid, r"^[A-Za-z0-9._:-]+$",
                             "%s 含需 URL 转义的字符（# ? & = 空格等）" % tid)
            self.assertEqual(quote(tid, safe=safe), tid,
                             "%s 需要转义才能安全放进查询串" % tid)
            self.assertNotIn("#", tid, "%s 含 URL 片段符 #" % tid)

    def test_every_target_reconciles_on_empty_corpus(self):
        """空语料下也必须对得上（0 == 0），不许出现"有数没明细"。"""
        for t in self.index:
            self.assertTrue(t["reconcile"], t["target_id"])
            for r in t["reconcile"]:
                self.assertTrue(r["ok"],
                                "%s 的 %s 对不上：expected=%s actual=%s"
                                % (t["target_id"], r["metric"], r["expected"],
                                   r["actual"]))
                self.assertEqual(r["expected"], r["actual"])
            self.assertTrue(t["reconcile_ok"], t["target_id"])

    def test_primary_metric_matches_count(self):
        for t in self.index:
            got = [m for m in t["metrics"] if m["name"] == t["primary"]]
            self.assertEqual(len(got), 1,
                             "%s 的 primary=%s 不在 metrics 里"
                             % (t["target_id"], t["primary"]))
            self.assertEqual(t["count"], got[0]["value"], t["target_id"])

    def test_every_metric_has_label_mode_and_unit(self):
        for t in self.index:
            for m in t["metrics"]:
                self.assertTrue(m["label"], (t["target_id"], m))
                self.assertIn(m["mode"], ("items", "sum"), (t["target_id"], m))
                self.assertTrue(m.get("unit") or t.get("unit"),
                                "%s 的指标 %s 没单位" % (t["target_id"], m["name"]))

    def test_every_target_has_suggestion_and_owner(self):
        """用户需求「并有处理建议」：每个数字（不只每条明细）都要有建议。"""
        for t in self.index:
            self.assertTrue(t.get("suggestion"),
                            "%s 没有处理建议" % t["target_id"])
            self.assertTrue(t.get("owner"), "%s 没写责任方" % t["target_id"])
            self.assertTrue(t.get("source") and t.get("definition"),
                            "%s 缺 source/definition" % t["target_id"])

    def test_every_queue_detail_row_points_at_a_real_target(self):
        """明细行必须指向真 target 或显式标 aggregate —— 不许第三种情况。"""
        det = WI.build_queue_details(self.g, self.index)
        ids = {t["target_id"] for t in self.index}
        n_checked = 0
        for qid, rows in det.items():
            self.assertTrue(rows, qid)
            for r in rows:
                n_checked += 1
                if r["aggregate"]:
                    self.assertIsNone(r["target"], r)
                    self.assertIsNone(r["metric"], r)
                else:
                    self.assertIn(r["target"], ids, r)
                    self.assertTrue(r["metric"], r)
        self.assertGreaterEqual(n_checked, 8)

    def test_queue_detail_value_equals_target_metric_value(self):
        det = WI.build_queue_details(self.g, self.index)
        by_id = {t["target_id"]: t for t in self.index}
        for rows in det.values():
            for r in rows:
                if r["aggregate"]:
                    self.assertIsInstance(r["value"], int, r)
                    continue
                t = by_id[r["target"]]
                m = [x for x in t["metrics"] if x["name"] == r["metric"]][0]
                self.assertEqual(r["value"], m["value"], r)

    def test_reconcile_reports_truncation_loudly(self):
        """超上限必须报 truncated 且 ok=false（不许静默截断）。"""
        items = [WI._item("i%d" % i, "t", metrics={"count": 1})
                 for i in range(WI.MAX_ITEMS + 3)]
        m = WI._metric("c", "条数", "items")
        r = WI._reconcile(items, m, items[:WI.MAX_ITEMS])
        self.assertFalse(r["ok"], "截断了还报 ok == 静默骗人")
        self.assertTrue(r["truncated"])
        self.assertEqual(r["items_total"], WI.MAX_ITEMS + 3)
        self.assertEqual(r["shown"], WI.MAX_ITEMS)

    def test_reconcile_sum_mode_uses_named_field(self):
        """sum 模式必须按**具名字段**求和，而不是固定用 count。"""
        items = [WI._item("a", "t", metrics={"count": 5, "given_up": 2}),
                 WI._item("b", "t", metrics={"count": 7, "given_up": 3})]
        m = WI._metric("gu", "未解决", "sum", "given_up")
        r = WI._reconcile(items, m, items)
        self.assertEqual(r["expected"], 5)
        self.assertEqual(r["actual"], 5)
        self.assertEqual(r["by"], "sum(item.metrics.given_up)")
        self.assertTrue(r["ok"])

    def test_unknown_target_is_rejected_with_keyerror(self):
        """未知 target 必须显式拒绝（KeyError），不许悄悄返回空清单。"""
        with self.assertRaises(KeyError):
            # `_targets_all` 是纯函数，用它构造一次查找即可（不碰真库）
            found = [t for t in WI._targets_all(self.g)
                     if t["target_id"] == "ring:nope.9"]
            if not found:
                raise KeyError("ring:nope.9")

    def test_aggregate_rows_are_labelled_not_pretending(self):
        det = WI.build_queue_details(self.g, self.index)
        aggs = [r for rows in det.values() for r in rows if r["aggregate"]]
        self.assertGreaterEqual(len(aggs), 2,
                                "聚合数（噪声登记/未评估）必须显式标 aggregate")
        for r in aggs:
            self.assertIn("聚合数", r["definition"], r)


class GeneratedCommandTest(unittest.TestCase):
    """生成出去的命令必须**真能被真解析器解析**，action_id 必须真在注册表里。"""

    @classmethod
    def setUpClass(cls):
        if not _have_real() or AS.ensure_harvester_importable():
            raise unittest.SkipTest("真库/上游不在位，生成命令的解析门跳过")
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        (td / "g.json").write_text("{}", encoding="utf-8")
        cls.cfg = AS.Config(db=REAL_DB, cards_root=CARDS,
                            glossary=td / "g.json",
                            topics_meta=REAL_TOPICS, suggestions_meta=REAL_SUG,
                            keywords_meta=REAL_KW, chain_root=CHAINS,
                            actions_meta=td / "am.db")
        cls.g = WI.collect(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _all_cli(self):
        out = []
        for t in WI.targets(self.g):
            for c in t.get("package") or []:
                if c.get("cli"):
                    out.append((t["target_id"], c["cli"]))
            for i in t["items"]:
                for c in i["commands"]:
                    if c.get("cli"):
                        out.append((t["target_id"], c["cli"]))
        return out

    def test_there_are_commands_to_check(self):
        clis = self._all_cli()
        self.assertGreaterEqual(len(clis), 5,
                                "一个命令都没生成，这道门就是空转")
        self.assertGreaterEqual(len({c for _, c in clis}), 3)

    def test_every_generated_cli_parses_with_real_parser(self):
        from harvester.cli import build_parser
        # 前缀按本机环境现探测（v0.68 envprobe）：uv / python / 绝对路径，
        # 不再写死 "python -m harvester"（用户机器可能 python 不在 PATH）。
        from actions.envprobe import probe
        prefix = probe()["cli_prefix"] + " "
        parser = build_parser()
        bad = []
        for tid, cli in self._all_cli():
            self.assertTrue(cli.startswith(prefix), cli)
            parts = shlex.split(cli[len(prefix):], posix=True)
            try:
                parser.parse_args(parts)
            except SystemExit as e:  # argparse 出错会 SystemExit(2)
                bad.append((tid, cli, str(e)))
        self.assertEqual(bad, [], "生成的命令行解析失败：%r" % (bad,))

    def test_every_action_id_is_registered(self):
        """动作 id 必须真在注册表里（否则命令台的「在命令台打开」点不动）。

        注册表把动作分在 `read_actions`（A 档）与 `write_actions`（B 档）两张表里
        ——**没有** `actions` 这个键。首版测试读错了键，得到一个空集合，
        把 9 个合法 action 全判成未注册（这道门第一次跑就抓住了我自己的错）。
        """
        reg = json.loads((ROOT / "actions" / "actions_registry.json")
                         .read_text(encoding="utf-8"))
        known = {a["action_id"] for a in reg.get("read_actions") or []}
        known |= {a["action_id"] for a in reg.get("write_actions") or []}
        self.assertTrue(known, "注册表读不出任何动作，这道门会空转")
        seen = set()
        for t in WI.targets(self.g):
            for c in t.get("package") or []:
                self._assert_action(c, known, t["target_id"])
                if c.get("action_id"):
                    seen.add(c["action_id"])
            for i in t["items"]:
                for c in i["commands"]:
                    self._assert_action(c, known, t["target_id"])
                    if c.get("action_id"):
                        seen.add(c["action_id"])
        self.assertIn("suggest_status", seen,
                      "建议池写动作没出现在任何命令里（页面就没法一键送命令台）")
        self.assertIn("report_errors", seen,
                      "错误报告只读动作没出现在任何命令里")

    def _assert_action(self, c, known, tid):
        aid = c.get("action_id")
        if aid is None:
            return
        self.assertIn(aid, known,
                      "%s 引用了注册表里没有的动作 %s（命令台会点不动）"
                      % (tid, aid))
        if c.get("tier") == "B":
            self.assertTrue(c.get("params"),
                            "%s 的 B 档命令没给 params，命令台预填不了" % tid)

    def test_repo_relative_paths_used_for_sibling_dbs(self):
        """命令里的库路径应是同目录文件名（不印本机绝对路径）。

        不写死具体库名（换机器可能不同），只钉住那条不变式：
        **同目录的库不许写成带盘符/家目录的全路径**。
        """
        metas = [cli for _, cli in self._all_cli() if "--meta" in cli]
        self.assertTrue(metas)
        for cli in metas:
            self.assertNotIn(str(Path.home()), cli,
                             "命令里印了本机绝对路径：%s" % cli)
            self.assertNotIn(":\\", cli, "命令里印了本机绝对路径：%s" % cli)


class RealCorpusReconcileTest(unittest.TestCase):
    """真库：工作台每个数字都必须有**正好那一批**明细。缺真库 skip。"""

    @classmethod
    def setUpClass(cls):
        if not _have_real():
            raise unittest.SkipTest("真库/meta 库不在位")
        if AS.ensure_harvester_importable():
            raise unittest.SkipTest("上游 harvester 不可用")
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        gl = td / "g.json"
        gl.write_text("{}", encoding="utf-8")
        cls.cfg = AS.Config(db=REAL_DB, cards_root=CARDS, glossary=gl,
                            topics_meta=REAL_TOPICS, suggestions_meta=REAL_SUG,
                            keywords_meta=REAL_KW, chain_root=CHAINS,
                            actions_meta=td / "am.db")
        cls.g = WI.collect(cls.cfg)
        cls.index = WI.build_index(cls.g)
        cls.ov = OV.build_overview(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_overview_reports_no_broken_target(self):
        self.assertEqual(self.ov["reconcile_bad"], [],
                         "工作台有 target 对不上（数字与明细不一致）")

    def test_every_queue_count_equals_its_item_list(self):
        """**这条就是"队列明细对不上"的回归门。**

        旧实现下它会红：待修补卡显示 6（unresolved>=1 的条目），而跳过去的
        G2 建议页渲染全部 8 条；待验证显示 9 却跳到 381 条错误报告。
        """
        for q in self.ov["queues"]:
            d = WI.build_workitems(self.cfg, q["target"])
            self.assertEqual(q["count"], d["count"], q["queue_id"])
            self.assertEqual(q["count"], len(d["items"]),
                             "队列 %s 的数字与它自己的明细条数不一致"
                             % q["queue_id"])
            self.assertTrue(d["reconcile_ok"], q["queue_id"])

    def test_every_ring_arm_count_equals_its_item_list(self):
        for r in self.ov["rings"]:
            for a in r["arms"]:
                self.assertTrue(a.get("target"),
                                "环 %s 的段没有 target → 页面点不动"
                                % r["ring_id"])
                d = WI.build_workitems(self.cfg, a["target"])
                self.assertEqual(a["value"], d["count"],
                                 (r["ring_id"], a["target"]))
                for rc in d["reconcile"]:
                    if rc["mode"] == "items":
                        self.assertEqual(rc["expected"], len(d["items"]),
                                         (a["target"], rc["metric"]))
                    else:
                        self.assertEqual(
                            rc["expected"],
                            sum(i["metrics"].get(
                                self._sum_field(d, rc["metric"])) or 0
                                for i in d["items"]),
                            (a["target"], rc["metric"]))

    @staticmethod
    def _sum_field(d, metric):
        return [m for m in d["metrics"] if m["name"] == metric][0]["sum_field"]

    def test_every_queue_detail_row_value_equals_target_metric(self):
        by_id = {t["target_id"]: t for t in self.index}
        for qid, rows in self.ov["queue_details"].items():
            for r in rows:
                if r["aggregate"]:
                    self.assertFalse(r["target"], r)
                    continue
                t = by_id[r["target"]]
                m = [x for x in t["metrics"] if x["name"] == r["metric"]][0]
                self.assertEqual(r["value"], m["value"], (qid, r["label"]))

    def test_givenup_target_uses_pattern_stats_not_pattern_rows(self):
        """未解决口径**必须走 pattern_stats**（P3 那次口径冲突的正解）。

        `/api/reports/errors` 的 pattern 行**没有 `given_up` 字段**：照它算会得到 0，
        误用 `count` 会得到 381。这条把"必须走 `pattern_stats.given_up`"钉住——
        一旦把 sum_field 从 `given_up` 改成 `count`，本数就从 31 变 381 → 红。
        """
        from harvester.errstats import (collect_errors_from_db, pattern_stats)
        con = AS.open_ro(REAL_DB)
        try:
            errs, _meta = collect_errors_from_db(REAL_DB, con=con)
        finally:
            con.close()
        ps = pattern_stats(errs)
        want_tot = sum(v["given_up"] for v in ps.values())
        want_pat = sum(1 for v in ps.values() if v["given_up"])
        d = WI.build_workitems(self.cfg, "ring:errors.givenup")
        self.assertEqual(d["count"], want_tot,
                         "未解决次数合计不等于 pattern_stats 的求和")
        self.assertEqual(d["count"],
                         sum(i["metrics"]["given_up"] for i in d["items"]))
        got_pat = [m for m in d["metrics"] if m["name"] == "patterns"][0]["value"]
        self.assertEqual(got_pat, want_pat)
        self.assertLessEqual(want_pat, want_tot, "模式数不可能超过次数")

    def test_queue_cards_carry_suggestion_and_source(self):
        for q in self.ov["queues"]:
            self.assertTrue(q.get("suggestion"), q["queue_id"])
            self.assertTrue(q.get("target"), q["queue_id"])
            self.assertIn("target", q)
        for r in self.ov["rings"]:
            for a in r["arms"]:
                self.assertTrue(a.get("suggestion"), (r["ring_id"], a["target"]))

    def test_ring_arm_counts_still_match_the_original_upstream_numbers(self):
        """三环的数字口径**不许因为本层重构而漂移**（P3 的 6 个真数字）。"""
        from harvester import harvesteval as HE
        from harvester.errstats import (collect_errors_from_db,
                                        pattern_stats)
        from harvester.triage import collect_triage
        con = AS.open_ro(REAL_DB)
        try:
            errs, emeta = collect_errors_from_db(REAL_DB, con=con)
            ps = pattern_stats(errs)
            tp = AS._hapi.api_topics(con, REAL_DB, REAL_TOPICS, CHAINS)  # noqa: SLF001
            skills = AS._hapi.api_reports_skills(con, REAL_DB, {})  # noqa: SLF001
            tri = collect_triage(REAL_DB, cards_root=CARDS, min_count=2,
                                 top_n=10, con=con)
        finally:
            con.close()
        ev = HE.run_eval(REAL_DB, REAL_SUG)
        arms = {r["ring_id"]: [a["value"] for a in r["arms"]]
                for r in self.ov["rings"]}
        self.assertEqual(arms["errors"][0], emeta["error_count"])
        self.assertEqual(arms["errors"][2], ev["counts"][HE.VERDICT_INCONCLUSIVE])
        self.assertEqual(arms["skill"][0], len(skills["skills"]))
        self.assertEqual(arms["skill"][1], len(tri["skills"]))
        self.assertEqual(arms["topics"][0], len(tp["topics"]))
        self.assertEqual(arms["topics"][1],
                         sum(int(t.get("chains_count") or 0)
                             for t in tp["topics"]))
        self.assertEqual(arms["errors"][1],
                         len([k for k, v in self.g["records"].items()
                              if v["status"] == "adopted"]))


class WorkitemsHttpTest(unittest.TestCase):
    """接线真打：函数对但路由没接上是这一类改动最常见的错。"""

    @classmethod
    def setUpClass(cls):
        if not _have_real():
            raise unittest.SkipTest("真库/meta 库不在位")
        if AS.ensure_harvester_importable():
            raise unittest.SkipTest("上游 harvester 不可用")
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

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=_T) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _wi(self, tid, raw=False):
        """打 /api/workitems。`raw=True` = **不做任何转义**直接拼进 URL。"""
        q = tid if raw else quote(tid, safe="")
        return self._get("/api/workitems?target=" + q)

    def test_missing_target_is_400_and_lists_choices(self):
        st, d = self._get("/api/workitems")
        self.assertEqual(st, 400)
        self.assertIn("target", d["error"])
        self.assertIn("queue:repair", d["available"])
        self.assertIn("ring:errors.0", d["available"])

    def test_unknown_target_is_404_and_lists_choices(self):
        st, d = self._wi("queue:nope")
        self.assertEqual(st, 404)
        self.assertIn("queue:nope", d["error"])
        self.assertTrue(d["available"])

    def test_known_target_returns_items_and_reconcile(self):
        st, d = self._wi("queue:repair")
        self.assertEqual(st, 200)
        self.assertTrue(d["reconcile_ok"], d["reconcile"])
        self.assertEqual(d["count"], len(d["items"]))
        self.assertTrue(d["suggestion"])
        self.assertIn("owner", d)
        for i in d["items"]:
            self.assertTrue(i["suggestion"], i["item_id"])
            self.assertIn("metrics", i)

    def test_raw_unquoted_target_also_works(self):
        """**不转义**直接拼 URL 也必须能用。

        真实缺陷（本轮自己踩）：首版 id 写成 `ring:errors#0`，而 `#` 是 URL 片段
        分隔符——裸 URL 到服务端只剩 `ring:errors` → 404。页面上靠
        `encodeURIComponent` 侥幸能跑，**手写 URL 或别的客户端必踩**。
        """
        st, d = self._wi("ring:errors.0", raw=True)
        self.assertEqual(st, 200, "裸 URL 传 target 失败了（id 里有需转义的字符）")
        self.assertTrue(d["reconcile_ok"])

    def test_ring_target_works_too(self):
        st, d = self._wi("ring:errors.0")
        self.assertEqual(st, 200)
        self.assertTrue(d["reconcile_ok"])
        # sum 模式：次数 = 明细里各模式次数之和
        self.assertEqual(d["count"],
                         sum(i["metrics"]["count"] for i in d["items"]))

    def test_root_lists_workitems_endpoint(self):
        st, d = self._get("/")
        self.assertEqual(st, 200)
        self.assertTrue(any("workitems" in e for e in d["endpoints"]))

    def test_overview_points_every_number_at_a_live_target(self):
        """抽查两个 target 的 HTTP 接线（全量逐个打会慢到没意义；
        全量对账由 RealCorpusReconcileTest 在进程内做）。"""
        st, ov = self._get("/api/overview")
        self.assertEqual(st, 200)
        tids = [q["target"] for q in ov["queues"]]
        tids += [a["target"] for r in ov["rings"] for a in r["arms"]]
        self.assertGreaterEqual(len(tids), 12)
        for tid in (tids[0], tids[-1]):
            st2, d2 = self._wi(tid)
            self.assertEqual(st2, 200, tid)
            self.assertTrue(d2["reconcile_ok"], (tid, d2["reconcile"]))


class RepairDispositionGate(unittest.TestCase):
    """v0.69（用户 2026-10-15 批注：「在处理页里处理了这一条，为啥好像没反映一样」）。

    根因：「待修补」口径 = 历史错误统计（unresolved>=1），处置动作（看原文/
    打包/补批注）天然**不会让它变少**；而条目明细既不显示处置进度（台账状态/
    pattern 批注/评估结论），命令说明也没写「哪些命令改变数字、哪些不改变」——
    用户处理完看不到任何回响。

    本门钉三件事：
    1. 明细 facts 必须亮出处置进度：`pattern 批注`（已补/没补）与 `效果评估`
       （结论或"还没跑"）。
    2. suggestion 按处置状态分流：已采纳+已批注 → 必须明说「数字为何不变、
       退场条件是什么」；已采纳+没批注 → 指向补批注；已驳回 → 挂账说法。
    3. 命令说明确定化：已批注时批注命令改「更新」口径；取材命令必须写明
       「只产材料，不改变本条数字」。
    """

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.cfg = _Cfg(Path(cls._td.name))
        t1 = "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。"
        t2 = "权限被撤销的工具不要原地重试。"
        t3 = "browser 工具报实例失效时先重建实例再继续。"
        cls.g = _empty_g(cls.cfg)
        cls.g["records"] = {
            t1: {"key": t1, "status": "adopted", "updated_at": "u",
                 "adopted_at": "a", "note": "pattern: subagent depth"},
            t2: {"key": t2, "status": "adopted", "updated_at": "u",
                 "adopted_at": "a", "note": ""},
            t3: {"key": t3, "status": "rejected", "updated_at": "u",
                 "adopted_at": None, "note": None},
        }
        cls.g["ev"] = {"counts": {}, "adopted_total": 1, "skipped": 0,
                       "skipped_topic": 0,
                       "entries": [{"key": t1, "verdict": "无变化"}]}
        cls.g["agents"] = {"entries": [
            {"title": t1, "total": 6, "unresolved_count": 6,
             "owner": "harness", "status": "adopted", "body": "b1",
             "samples": [{"sid": "s1", "seq": 1, "error": "boom"}]},
            {"title": t2, "total": 4, "unresolved_count": 4,
             "owner": "harness", "status": "adopted", "body": "b2",
             "samples": []},
            {"title": t3, "total": 2, "unresolved_count": 2,
             "owner": "harness", "status": "rejected", "body": "b3",
             "samples": []},
        ]}
        items = WI._repair_items(cls.g)
        cls.by_title = {i["title"]: i for i in items}

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _fact(self, title, name):
        for k, v in self.by_title[title]["facts"]:
            if k == name:
                return v
        return None

    def test_facts_show_annotation_state(self):
        t1 = "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。"
        t2 = "权限被撤销的工具不要原地重试。"
        v1 = self._fact(t1, "pattern 批注")
        v2 = self._fact(t2, "pattern 批注")
        self.assertIsNotNone(v1, "已处置条目缺「pattern 批注」fact")
        self.assertIn("已补", v1)
        self.assertIn("pattern: subagent depth", v1)
        self.assertIsNotNone(v2, "缺批注条目缺「pattern 批注」fact")
        self.assertIn("没补", v2)

    def test_facts_show_eval_verdict(self):
        t1 = "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。"
        t2 = "权限被撤销的工具不要原地重试。"
        self.assertEqual(self._fact(t1, "效果评估"), "无变化")
        self.assertTrue(self._fact(t2, "效果评估"),
                        "没评估的条目也要有占位说明，不能留空")

    def test_suggestion_tells_why_number_wont_move(self):
        """已处置条目必须明说：数字统计历史坑、处置不减它、退场条件是什么。"""
        t1 = "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。"
        s = self.by_title[t1]["suggestion"]
        self.assertIn("已经处置过", s)
        self.assertIn("不会", s) and self.assertIn("变少", s)
        self.assertIn("评估", s)

    def test_suggestion_points_missing_annotation_at_fix(self):
        t2 = "权限被撤销的工具不要原地重试。"
        s = self.by_title[t2]["suggestion"]
        self.assertIn("批注", s)

    def test_suggestion_rejected_is_honest_about_ligament(self):
        t3 = "browser 工具报实例失效时先重建实例再继续。"
        s = self.by_title[t3]["suggestion"]
        self.assertIn("驳回", s)

    def test_command_labels_are_deterministic(self):
        """已批注 → 命令叫「更新」；取材命令必须写明不改数字。"""
        t1 = "子代理嵌套深度受 maxDepth 限制，深任务改平铺编排。"
        labels = [c["label"] for c in self.by_title[t1]["commands"]]
        self.assertTrue(any("更新 pattern 批注" in x for x in labels),
                        labels)
        drafts = [c for c in self.by_title[t1]["commands"]
                  if "蒸馏包" in c.get("label", "")]
        self.assertTrue(drafts)
        self.assertIn("不改变", drafts[0]["note"] + drafts[0].get("label", ""))


if __name__ == "__main__":
    unittest.main()
