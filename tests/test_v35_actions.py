# -*- coding: utf-8 -*-
"""P2 打通层测试：actions-serve 骨架 / 命令注册表 / timeline / 数字皆门。

覆盖三类风险：

1. **接线必须真打**（H22 先例）：起真实 actionsserve（随机端口）、用真
   HTTP 请求打每个端点，而不是只调函数——"函数对但路由没接上"是这轮最
   可能出的错。
2. **动作面不是万能 shell**（SOP §5.3/§7.2）：非白名单 action_id 必须
   403；参数只认注册表声明过的键（多余键丢弃、类型不对 400）。
3. **命令注册表不许漂移**（P2-2 验收）：`commands.json` 与真实
   `harvester.cli.build_parser()` 逐命令逐参数比对——上游加一个参数而
   注册表没跟上，这个测试就该红。

夹具用真库？**不用**：
- 合成库（走 `indexing.SCHEMA`）用于端点形状与错误路径——快、确定；
- 真库用于"数字对账"（G2 的 pattern count ⟷ pattern-sessions 的 count）
  ——那正是 P2-5 要钉的等式，且只读。
"""
from __future__ import annotations

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import commandgen as CG  # noqa: E402

# 真库（只读用；不存在时对账类测试 skip，不假绿）
REAL_DB = AS.upstream_root() / "harvester.db"
REAL_TOPICS_META = AS.upstream_root() / "topics_meta.db"

# 合成语料：两条会话、两条同模式错误（一条会重试=自愈，一条不会=未解决）
_ERR_A = ("Error: old_string was not found in \"C://Users//x//a.py\". "
          "Read the file first.")
_ERR_B = ("Error: old_string was not found in \"C://Users//x//b.py\". "
          "Read the file first.")


def _build_fixture_db(path: Path) -> None:
    """用**上游真 schema** 建库（`indexing.SCHEMA`），再灌最小真实形态。"""
    err = AS.ensure_harvester_importable()
    if err:
        raise RuntimeError(err)
    from harvester.indexing import SCHEMA
    con = sqlite3.connect(str(path))
    try:
        con.executescript(SCHEMA)
        # 列名以**真库**为准（实测 2026-10-12：sessions 无 message_count/
        # step_count/error_count；messages 无 seq——按 rowid 序）
        con.executemany(
            "INSERT INTO sessions (sid, source, session_id, title, category, "
            "model, created_at, updated_at, file) VALUES (?,?,?,?,?,?,?,?,?)",
            [("s1", "dsh", "s1", "会话甲", None, "m1", "2026-10-01 10:00:00",
              "2026-10-01 10:10:00", "f1"),
             ("s2", "dsh", "s2", "会话乙", None, "m1", "2026-10-02 10:00:00",
              "2026-10-02 10:10:00", "f2")])
        con.executemany(
            "INSERT INTO messages (sid, role, ts, text, raw) VALUES (?,?,?,?,?)",
            [("s1", "user", "2026-10-01 10:00:00", "改文件", "改文件"),
             ("s1", "assistant", "2026-10-01 10:01:00", _ERR_A, _ERR_A),
             ("s2", "user", "2026-10-02 10:00:00", "改文件", "改文件"),
             ("s2", "assistant", "2026-10-02 10:01:00", _ERR_B, _ERR_B)])
        # steps：s1 的错误之后**同工具又有一次调用**（resolved=True，自愈），
        # s2 的错误之后再无同工具调用（resolved=False，未解决）
        con.executemany(
            "INSERT INTO steps (sid, seq, phase, tool, status, ts, error, "
            "detail) VALUES (?,?,?,?,?,?,?,?)",
            [("s1", 1, "call", "edit", "ok", "2026-10-01 10:00:30", None,
              None),
             ("s1", 2, "result", "edit", "error", "2026-10-01 10:01:00",
              _ERR_A, None),
             ("s1", 3, "call", "edit", "ok", "2026-10-01 10:02:00", None,
              None),
             ("s2", 1, "call", "edit", "ok", "2026-10-02 10:00:30", None,
              None),
             ("s2", 2, "result", "edit", "error", "2026-10-02 10:01:00",
              _ERR_B, None)])
        con.commit()
    finally:
        con.close()


class _ServerMixin:
    """起真实服务（随机端口）+ 真 HTTP 请求的公共夹具。"""

    cfg: AS.Config
    _td: tempfile.TemporaryDirectory

    @classmethod
    def _make_cfg(cls, td: Path, db: Path, **kw) -> AS.Config:
        glossary = td / "glossary.json"
        glossary.write_text(json.dumps(
            {"terms": [{"term": "t1", "plain": "p1"}]}, ensure_ascii=False),
            encoding="utf-8")
        cards = td / "cards"
        cards.mkdir(exist_ok=True)
        return AS.Config(db=db, cards_root=cards, glossary=glossary, **kw)

    @classmethod
    def _start(cls, cfg: AS.Config) -> None:
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), AS.make_handler(cfg))
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.socket.getsockname()[1]

    @classmethod
    def _stop(cls) -> None:
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.th.join(timeout=2)
        cls._td.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def _post(self, path, obj):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _wait_job(self, jid, timeout=30.0):
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            _st, job = self._get("/api/actions/job/" + jid)
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(0.05)
        self.fail("作业 %s 超时未完成" % jid)


class ActionsServeTest(_ServerMixin, unittest.TestCase):
    """合成库：端点形状、白名单、错误路径。"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        db = td / "h.db"
        _build_fixture_db(db)
        cls.cfg = cls._make_cfg(td, db)
        cls._start(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    # -- P2-1 骨架与归并 --

    def test_health_reports_version_handshake(self):
        st, d = self._get("/api/health")
        self.assertEqual(st, 200)
        self.assertEqual(d["view_actions_api"], AS.VIEW_ACTIONS_API)
        self.assertEqual(d["port"], 8766)
        # 归并自 P1 viewserver 的字段一个不少（additive 守卫）
        for k in ("cards_root", "cards_root_exists", "glossary",
                  "glossary_exists"):
            self.assertIn(k, d)
        # 版本握手：健康检查必须报出两侧版本，且判据唯一
        self.assertEqual(d["harvester_version"], AS.harvester.__version__)
        self.assertEqual(d["commands_adapter_version"],
                         AS.harvester.__version__)
        self.assertTrue(d["version_ok"])
        self.assertIsNone(d["upstream_error"])

    def test_view_endpoints_still_work_after_merge(self):
        """P1 的三个端点归并后路径/结构不变（既有调用方零破坏）。"""
        st, d = self._get("/view/cards")
        self.assertEqual((st, d["view_api"]), (200, 1))
        self.assertEqual(d["cards"], [])
        st, d = self._get("/view/glossary")
        self.assertEqual((st, d["terms"][0]["term"]), (200, "t1"))
        st, d = self._get("/view/health")
        self.assertTrue(d["ok"])
        st, d = self._get("/")
        self.assertIn("/api/pattern-sessions?pattern=&days=", d["endpoints"])

    def test_action_registry_is_frozen_whitelist(self):
        """P2 时这里返回的只有 A 档（4 个）；P3 起 B 档并入同一端点。

        本用例保持 P2 的原意（**A 档实现全在白名单里**），并补上 P3 的
        双档断言；B 档的逐项校验在 `test_v36_write_actions.py`。
        """
        st, d = self._get("/api/actions")
        self.assertEqual(st, 200)
        ids = [a["action_id"] for a in d["actions"]]
        a_ids = sorted(a["action_id"] for a in d["actions"] if a["tier"] == "A")
        b_ids = sorted(a["action_id"] for a in d["actions"] if a["tier"] == "B")
        self.assertEqual(a_ids, sorted(AS.ACTIONS))
        self.assertEqual(len(ids), len(set(ids)), "A/B 档不许有重复 action_id")
        self.assertTrue(b_ids, "P3 起 B 档必须出现")
        for a in d["actions"]:
            self.assertIn(a["tier"], ("A", "B"))
            self.assertIn("reversible", a)
        self.assertIn("B", d["tiers"])
        # B 档必须带风险等级与目标库（页面据此渲染徽章与"写哪里"）
        for a in d["actions"]:
            if a["tier"] == "B":
                self.assertIn(a["risk"], ("reversible", "destructive"))
                self.assertTrue(a["target"])

    def test_run_report_errors_matches_upstream_numbers(self):
        """A 档动作必须与 /api/reports/errors **同源同数**（同函数）。"""
        st, d = self._post("/api/actions/run",
                           {"action_id": "report_errors",
                            "view_actions_api": AS.VIEW_ACTIONS_API})
        self.assertEqual(st, 202)
        job = self._wait_job(d["job_id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["result"]["meta"]["error_count"], 2)
        con = AS.open_ro(self.cfg.db)
        try:
            direct = AS._hapi.api_reports_errors(con, self.cfg.db, {})
        finally:
            con.close()
        self.assertEqual(job["result"], direct)

    def test_run_topic_list_reads_meta(self):
        st, d = self._post("/api/actions/run", {"action_id": "topic_list"})
        self.assertEqual(st, 202)
        job = self._wait_job(d["job_id"])
        self.assertEqual(job["status"], "done", job)
        # 未配置 topics_meta → 诚实空表 + hint（不是 500）
        self.assertEqual(job["result"]["topics"], [])
        self.assertIn("hint", job["result"])

    def test_unknown_action_403_not_a_shell(self):
        st, d = self._post("/api/actions/run", {"action_id": "rm_rf"})
        self.assertEqual(st, 403)
        self.assertIn("白名单", d["error"])

    def test_extra_and_bad_params(self):
        """多余键丢弃（不会变成自由参数）；days 非数字 400。"""
        st, d = self._post("/api/actions/run",
                           {"action_id": "report_errors",
                            "params": {"days": 7, "--db": "/etc/passwd",
                                       "evil": 1}})
        self.assertEqual(st, 202)
        job = self._wait_job(d["job_id"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(d["params"], {"days": 7.0})
        st, d = self._post("/api/actions/run",
                           {"action_id": "report_errors",
                            "params": {"days": "上周"}})
        self.assertEqual(st, 400)
        self.assertIn("不是数字", d["error"])

    def test_post_only_for_run(self):
        st, d = self._post("/api/health", {})
        self.assertEqual(st, 405)
        self.assertIn("/api/actions/run", d["error"])

    def test_contract_version_mismatch_409(self):
        st, d = self._post("/api/actions/run",
                           {"action_id": "topic_list", "view_actions_api": 99})
        self.assertEqual(st, 409)

    def test_job_unknown_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/actions/job/deadbeef1234")
        self.assertEqual(cm.exception.code, 404)

    # -- P2-3 timeline --

    def test_timeline_bad_level_400_and_missing_topic_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/topic/tp-x/timeline?level=nope")
        self.assertEqual(cm.exception.code, 400)
        # topics_meta 未配置 → 503（诚实：能力缺口 ≠ 参数错）
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/topic/tp-x/timeline?level=title")
        self.assertEqual(cm.exception.code, 503)

    def test_timeline_fine_requires_sid(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/topic/tp-x/timeline?level=fine")
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("sid", json.loads(cm.exception.read().decode("utf-8"))
                      ["error"])

    # -- P2-5 第二跳 --

    def test_pattern_sessions_counts_and_resolved(self):
        # 模式名**现场从报告取**，不写死（归一规则属上游口径，写死=下次
        # 上游调规则就出现"夹具没变但测试红"的假红）
        con = AS.open_ro(self.cfg.db)
        try:
            rep = AS._hapi.api_reports_errors(con, self.cfg.db, {})
        finally:
            con.close()
        self.assertEqual(rep["meta"]["error_count"], 2, rep["by_class"])
        self.assertEqual(len(rep["patterns"]), 1, rep["patterns"])
        pat = rep["patterns"][0]["pattern"]
        st, d = self._get("/api/pattern-sessions?pattern="
                          + urllib.parse.quote(pat))
        self.assertEqual(st, 200)
        # 两条错误同模式 → count=2、两个会话
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["occurrences"], 2)
        self.assertEqual(d["sessions_count"], 2)
        by = {s["sid"]: s for s in d["sessions"]}
        self.assertEqual(by["s1"]["given_up"], 0)   # 之后又调了 edit → 自愈
        self.assertEqual(by["s2"]["given_up"], 1)   # 之后再无调用 → 未解决
        self.assertEqual(d["given_up"], 1)
        # 逐会话次数之和 == 总数（P2-5 逐级对账）
        self.assertEqual(sum(s["occurrences"] for s in d["sessions"]), d["count"])
        # 会话标题是从 sessions 表带出来的（第二跳要给人看名字）
        self.assertEqual(by["s1"]["title"], "会话甲")

    def test_pattern_sessions_missing_pattern_400(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/pattern-sessions")
        self.assertEqual(cm.exception.code, 400)


class RegressActionTest(unittest.TestCase):
    """A 档重活：`regress` 动作必须**真跑**并保留上游的退出码三分。

    这是本阶段"能失败的断言"证据：同一动作跑 `corpus=drift`（上游故意漂移
    的语料）必须变红——如果两种情况都绿，说明这个动作只是个装饰。
    """

    @classmethod
    def setUpClass(cls):
        if AS.ensure_harvester_importable():
            raise unittest.SkipTest("上游不可用")

    def test_default_corpus_passes(self):
        out = AS.ACTIONS["regress"]["handler"]
        r = out(None, {"corpus": "default"})
        self.assertEqual(r["exit_code"], 0, r)
        self.assertEqual(r["failed_assertions"], 0)
        self.assertGreater(r["assertions"], 50, r)
        self.assertEqual(r["steps_failed"], 0)
        # 步骤名必须真的带出来（首版认错了键 → 8 个空名，页面上看不出来）
        names = [s["step"] for s in r["steps"]]
        self.assertEqual(names[0], "precondition")
        self.assertIn("diagnostics", names)
        self.assertNotIn(None, names)

    def test_drift_corpus_turns_red(self):
        out = AS.ACTIONS["regress"]["handler"]
        r = out(None, {"corpus": "drift"})
        self.assertEqual(r["exit_code"], 1,
                         "drift 语料（故意漂移）必须让断言变红；都绿=断言没用")
        self.assertGreater(r["failed_assertions"], 0)
        # 未执行 ≠ 失败：链路后半段要被单独计成 not_run（AGENTS.md §五 16）
        self.assertGreater(r["steps_not_run"], 0)
        self.assertEqual(r["steps_not_run"] + r["steps_failed"] +
                         len([s for s in r["steps"] if s["status"] == "pass"]),
                         r["steps_total"])

    def test_bad_corpus_400(self):
        with self.assertRaises(AS._BadRequest):
            AS.ACTIONS["regress"]["handler"](None, {"corpus": "nope"})


class CommandsRegistryTest(unittest.TestCase):
    """P2-2 验收：注册表覆盖真 CLI 全部子命令与参数，且参数有数据源标注。"""

    def test_no_drift_against_real_parser(self):
        diffs = CG.check()
        self.assertEqual(diffs, [], "commands.json 已漂移：%s" % diffs[:10])

    def test_coverage_and_annotations(self):
        d = json.loads(CG.OUT.read_text(encoding="utf-8"))
        cmds = d["commands"]
        names = [c["cmd"] for c in cmds]
        self.assertEqual(len(names), len(set(names)), "命令重复")
        # 每张卡都至少有一条命令（否则是空卡）
        cards = {c["card_id"] for c in d["cards"]}
        self.assertEqual({c["card_id"] for c in cmds}, cards)
        # 每个参数都有 src，且取值在允许集合内
        allowed = {"none", "server", "chips", "topics_api",
                   "sessions_api", "cards_api", "exports_dir", "auto_out"}
        n = 0
        for c in cmds:
            self.assertIn(c["tier"], "ABC", "%s 档位非法" % c["cmd"])
            for a in c["args"]:
                n += 1
                self.assertIn(a["src"], allowed,
                              "%s %s src=%r" % (c["cmd"], a["name"], a["src"]))
        self.assertGreater(n, 200, "参数总数异常（解析器换了形态？）")

    def test_tier_whitelist_consistency(self):
        d = json.loads(CG.OUT.read_text(encoding="utf-8"))
        by = {c["cmd"]: c["tier"] for c in d["commands"]}
        # SOP §5.4 钦定的几处三档归属（不要被后来的重构悄悄改掉）
        for cmd in ("update", "sync"):
            self.assertEqual(by[cmd], "B", "%s 应写库=B 档" % cmd)
        for cmd in ("mcp-serve", "api-serve", "weblogin check",
                    "weblogin prepare"):
            self.assertEqual(by[cmd], "C", "%s 应只生成命令=C 档" % cmd)
        # 裸 `weblogin` 不是叶子命令（必须选子动作），注册表只收叶子——
        # 这条钉住"不会为了好看凭空造一条没有参数事实的命令"
        self.assertNotIn("weblogin", by)
        for cmd in ("report-tools", "report-errors",
                    "chain-audit", "deadcode-scan", "regress"):
            self.assertEqual(by[cmd], "A", "%s 应只读=A 档" % cmd)
        # `topic` 是聚合命令：读子动作 A、写子动作 B；命令级取**最宽**（B），
        # 逐项归属在 subactions 里（不提这个区分，"topic 能不能在页面跑"
        # 就没人答得上来）
        self.assertEqual(by["topic"], "B")
        subs = {s["name"]: s["tier"]
                for s in next(c for c in d["commands"]
                              if c["cmd"] == "topic")["subactions"]}
        for s in ("list", "show", "turns", "export", "md",
                  "chain", "pack", "dedupe"):
            self.assertEqual(subs[s], "A", "topic %s 应只读=A 档" % s)
        for s in ("register", "add", "remove", "merge", "rename", "delete",
                  "keywords"):
            self.assertEqual(subs[s], "B", "topic %s 应写=B 档" % s)
        # 实测事实：CLI 的 topic 没有 timeline 子动作（五档时间线是库函数，
        # 由本仓 /api/topic/<id>/timeline 端点透出）——别在注册表里造一条
        # 跑不通的命令
        self.assertNotIn("timeline", subs)

    def test_path_params_hidden_by_default(self):
        """路径类参数必须标 server（页面不暴露）——SOP §5 红线。"""
        d = json.loads(CG.OUT.read_text(encoding="utf-8"))
        for c in d["commands"]:
            for a in c["args"]:
                if a["dest"] in ("db", "meta", "topics_meta", "keywords_meta",
                                 "suggestions_meta", "cards_root",
                                 "chain_root"):
                    self.assertEqual(a["src"], "server",
                                     "%s %s 应标 server" % (c["cmd"], a["name"]))

    def test_key_annotations_are_pinned(self):
        """**关键参数的数据源一个都不许悄悄掉回 none**（本轮真实教训）。

        为什么单列一条：我在改这个表时误删了一整段，`src` 全掉成 `none`、
        可计数从 132 掉到 106——而**所有门照样绿**（`none` 本来就是合法
        取值，没有任何断言盯着"哪些 dest 必须是指定 src"）。掉回 none 的
        后果是页面上这些参数从"选择器"退化成"要人背的手输框"，正是 SOP
        §5.5 要消灭的东西，却没有任何信号。
        """
        d = json.loads(CG.OUT.read_text(encoding="utf-8"))
        got: dict[str, set] = {}
        for c in d["commands"]:
            for a in c["args"]:
                got.setdefault(a["dest"], set()).add(a["src"])
        expect = {
            "days": "chips", "since": "chips", "level": "chips",
            "max_chars": "chips", "role": "chips", "corpus": "chips",
            "out": "auto_out", "plan_out": "auto_out",
            "sid": "sessions_api", "sids": "sessions_api",
            "from_dir": "exports_dir", "chain": "cards_api",
            "id_": "topics_api", "craft_topic": "topics_api",
            "db": "server", "meta": "server", "topics_meta": "server",
            "keywords_meta": "server", "suggestions_meta": "server",
            "cards_root": "server", "chain_root": "server",
            "artifacts_meta": "server", "temp_base": "server",
            "sources": "none",
        }
        for dest, want in expect.items():
            self.assertIn(dest, got, "参数 %s 在注册表里消失了？" % dest)
            self.assertEqual(got[dest], {want},
                             "参数 %s 的数据源应为 %r，实际 %r"
                             % (dest, want, sorted(got[dest])))
        # 有数据源标注的参数总量不许大幅缩水（掉回 none 的静默失效信号）
        annotated = sum(1 for c in d["commands"] for a in c["args"]
                        if a["src"] != "none")
        # 145 是量到的真值；阈值留一成余量——**低到能抓"整段掉回 none"
        # （本轮实测 106），又不会因为多一个自由文本参数就假红**
        self.assertGreaterEqual(annotated, 135,
                                "标注数异常缩水（本轮曾因误删一段掉到 106）")

    def test_sources_stays_free_text(self):
        """`--sources` 是 sources.json 的**路径**，但也是自由文本（可换任何
        配置文件名）——故刻意留在 none，不进 SERVER_DESTS（反例守卫：
        谁把它顺手改成 server，这条会红，提醒先想清楚）。"""
        self.assertEqual(CG.SRC["sources"], "none")
        self.assertNotIn("sources", CG.SERVER_DESTS)


class RealDbReconcileTest(unittest.TestCase):
    """P2-5 数字对账（真库，只读）：G2 的 pattern count ⟷ 第二跳的 count。"""

    @classmethod
    def setUpClass(cls):
        if not REAL_DB.is_file():
            raise unittest.SkipTest("真库不存在：%s" % REAL_DB)
        err = AS.ensure_harvester_importable()
        if err:
            raise unittest.SkipTest(err)
        cls._td = tempfile.TemporaryDirectory()
        cls.cfg = _ServerMixin._make_cfg(
            Path(cls._td.name), REAL_DB, topics_meta=REAL_TOPICS_META)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_top_patterns_counts_reconcile(self):
        con = AS.open_ro(REAL_DB)
        try:
            rep = AS._hapi.api_reports_errors(con, REAL_DB, {})
        finally:
            con.close()
        # 取**真库当前**最大的三个模式（不写死阈值：库里模式分布会随语料变，
        # 写死 100 只会在某天变成假红/假绿）
        pats = sorted(rep["patterns"], key=lambda p: -p["count"])[:3]
        self.assertTrue(pats and pats[0]["count"] > 0,
                        "真库没有错误模式？口径变了？")
        for p in pats:
            d = AS.pattern_sessions(self.cfg, p["pattern"])
            self.assertEqual(
                d["count"], p["count"],
                "模式 %r：报告 count=%d ≠ 第二跳 count=%d（数字皆门断了）"
                % (p["pattern"], p["count"], d["count"]))
            self.assertEqual(
                d["occurrences"],
                sum(s["occurrences"] for s in d["sessions"]))
            self.assertEqual(d["sessions_count"], len(d["sessions"]))


class RealTimelineTest(unittest.TestCase):
    """P2-3：真库 + 真 topics_meta 上跑五档里的两档（title/coarse）。"""

    @classmethod
    def setUpClass(cls):
        if not (REAL_DB.is_file() and REAL_TOPICS_META.is_file()):
            raise unittest.SkipTest("真库或 topics_meta 不存在")
        err = AS.ensure_harvester_importable()
        if err:
            raise unittest.SkipTest(err)
        cls._td = tempfile.TemporaryDirectory()
        cls.cfg = _ServerMixin._make_cfg(
            Path(cls._td.name), REAL_DB, topics_meta=REAL_TOPICS_META)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_title_and_coarse(self):
        con = AS.open_ro(REAL_TOPICS_META)
        try:
            rows = [dict(r) for r in con.execute(
                "SELECT id, members FROM topics ORDER BY id")]
        finally:
            con.close()
        target = None
        for r in rows:
            try:
                if len(json.loads(r["members"] or "[]")) > 0:
                    target = r["id"]
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        self.assertIsNotNone(target, "topics_meta 里没有带成员的主题？")
        d = AS.topic_timeline(self.cfg, target, "title", None)
        self.assertEqual(d["level"], "title")
        self.assertEqual(d["topic"]["id"], target)
        self.assertEqual(d["view_actions_api"], AS.VIEW_ACTIONS_API)
        d2 = AS.topic_timeline(self.cfg, target, "coarse", None)
        self.assertEqual(d2["level"], "coarse")
        self.assertTrue(d2.get("rows"), "coarse 档应回会话级行")
        for row in d2["rows"]:
            self.assertIn("sid", row)

    def test_missing_topic_404(self):
        with self.assertRaises(AS._NotFound):
            AS.topic_timeline(self.cfg, "tp-不存在-000", "title", None)

    def test_corpus_watermark_opt_in_only(self):
        """库龄判定**缺省关**（stale=None=未判定）；开了才真判。

        这条钉两件事：① 默认不扫语料盘（timeline 的验收是"能看时间线"，
        不该被库龄拖住）；② 开了之后 `stale` 确实由"当前语料 mtime ⟷ 上次
        索引水位"算出来，而不是照抄库文件 mtime（H104 的假 STALE 教训）。
        """
        up = AS.upstream_root()
        con = AS.open_ro(up / "topics_meta.db")
        try:
            tid = con.execute("SELECT id FROM topics ORDER BY id").fetchone()[0]
        finally:
            con.close()
        d_off = AS.topic_timeline(self.cfg, tid, "title", None)
        self.assertIsNone(d_off["db_fingerprint"].get("stale"),
                          "缺省不该判库龄（未判定必须是 None）")
        cfg_on = AS.Config(db=self.cfg.db, cards_root=self.cfg.cards_root,
                           glossary=self.cfg.glossary,
                           topics_meta=self.cfg.topics_meta,
                           corpus_watermark=True)
        fp = AS.topic_timeline(cfg_on, tid, "title", None)["db_fingerprint"]
        self.assertIsNotNone(fp.get("corpus_mtime"))
        self.assertIsNotNone(fp.get("corpus_indexed_mtime"))
        self.assertIn(fp.get("stale"), (True, False),
                      "开了之后必须是明确判定，不能还是 None")
        # 口径检查：判定必须等于 two 个时间串的比较（同格式可直接比）
        self.assertEqual(fp["stale"],
                         fp["corpus_mtime"] > fp["corpus_indexed_mtime"])


if __name__ == "__main__":
    import urllib.parse  # noqa: F401 - 供 test_pattern_sessions 用
    unittest.main()
