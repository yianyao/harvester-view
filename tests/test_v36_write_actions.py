# -*- coding: utf-8 -*-
"""P3-1/P3-2 测试：动作注册表冻结 + B 档两段提交 + 台账留痕。

覆盖四类风险，每条都能说出"它在什么情况下会红"：

1. **注册表与实现漂移**（P3-1 验收）：注册表说能做而代码里没有、或代码里有而
   注册表没声明 → 起服自检与套件都要报；
2. **预演必须无副作用**：`plan` 前后 meta 库**字节不变**（这是"预览说 3 个成员、
   写下去就是 3 个"的前提，也是用户敢点"确认"的前提）；
3. **两段提交真的挡得住**：无 token 不写；token 一次性；参数被改、库在两段之间
   被改、token 过期 —— 四种情况都必须拒绝而不是"照写"；
4. **台账是全量留痕**：预演也记一行（有人点过什么是有用的事实）、执行记
   done/failed、真写记 write_records（含 before/after；destructive 必须带快照）。

夹具：**真 `topics_meta.db` 的临时副本**（绝不动真的）；suggestions/keywords
用最小可写库自建（这两类的预演只读计数，不需要全量真库）。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import registrygen as RG  # noqa: E402
from actions import runlib  # noqa: E402
from actions import writeactions as WA  # noqa: E402

UP = AS.upstream_root()
REAL_TOPICS = UP / "topics_meta.db"


def _make_cfg(td: Path, **kw) -> AS.Config:
    glossary = td / "glossary.json"
    glossary.write_text("{}", encoding="utf-8")
    cards = td / "cards"
    cards.mkdir(exist_ok=True)
    return AS.Config(db=UP / "harvester.db", cards_root=cards,
                     glossary=glossary,
                     topics_meta=td / "topics_meta.db",
                     suggestions_meta=td / "suggestions_meta.db",
                     keywords_meta=td / "keywords_meta.db",
                     actions_meta=td / "actions_meta.db",
                     registry_json=AS.DEFAULT_REGISTRY_JSON, **kw)


def _fingerprint(path: Path) -> tuple:
    b = Path(path).read_bytes()
    return (len(b), __import__("hashlib").sha256(b).hexdigest())


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        if REAL_TOPICS.is_file():
            shutil.copy2(REAL_TOPICS, self.td / "topics_meta.db")
        else:                                   # pragma: no cover
            self.skipTest("真 topics_meta.db 不存在")
        # 建议台账：建表 + 一条待裁决建议（预演要读它算 before）
        con = sqlite3.connect(str(self.td / "suggestions_meta.db"))
        con.executescript(
            "CREATE TABLE suggestion_status (key TEXT PRIMARY KEY, status TEXT "
            "NOT NULL, adopted_at TEXT, note TEXT, updated_at TEXT NOT NULL);")
        con.execute("INSERT INTO suggestion_status VALUES (?,?,?,?,?)",
                    ("测试建议句。", "pending", None, None,
                     "2026-10-01 00:00:00"))
        con.commit()
        con.close()
        # 词频账本：两个 run（GC 要删掉旧的）
        con = sqlite3.connect(str(self.td / "keywords_meta.db"))
        con.executescript(
            "CREATE TABLE keyword_runs(id INTEGER PRIMARY KEY, ts TEXT);"
            "CREATE TABLE keyword_stats(run_id INTEGER, gram TEXT, n INTEGER);")
        con.executemany("INSERT INTO keyword_runs VALUES (?,?)",
                        [(1, "2026-09-01 00:00:00"), (2, "2026-10-01 00:00:00")])
        con.executemany("INSERT INTO keyword_stats VALUES (?,?,?)",
                        [(1, "aa", 5), (1, "bb", 3), (2, "cc", 9)])
        con.commit()
        con.close()
        self.cfg = _make_cfg(self.td)
        self.specs = AS.registry_specs(self.cfg)

    def tearDown(self):
        self._td.cleanup()

    def spec(self, aid: str) -> dict:
        return self.specs[aid]

    def plan(self, aid: str, params: dict) -> dict:
        return WA.plan(aid, self.spec(aid), params, self.cfg)


class RegistryTest(unittest.TestCase):
    """P3-1 验收：注册表覆盖真 CLI、无遗漏无重复，且与实现一致。"""

    def test_no_drift_against_facts(self):
        self.assertEqual(RG.check(), [], "actions_registry.json 已漂移")

    def test_registry_matches_implementations(self):
        reg = RG.build()
        self.assertEqual(AS.check_registry_consistency(reg), [])

    def test_every_write_action_has_implementation(self):
        reg = RG.build()
        for a in reg["write_actions"]:
            self.assertIn(a["action_id"], WA.WRITE_ACTIONS,
                          "%s 声明了但没实现" % a["action_id"])
            self.assertEqual(a["tier"], "B")
            self.assertIn(a["risk"], ("reversible", "destructive"))

    def test_three_tier_ownership_matches_proposal(self):
        """三档归属钉住提案 §5.4/§5.6 的钦定处。"""
        by_cmd = {}
        for a in RG.build()["write_actions"]:
            by_cmd.setdefault(a["cmd"], []).append(a)
        self.assertTrue(by_cmd["suggest-status"], "suggest-status 必须可写")
        self.assertTrue(by_cmd["keywords-gc"], "keywords-gc 必须可写")
        self.assertTrue(by_cmd["topic"], "topic 的写子动作必须可写")
        # C 档命令**绝不**出现在写动作里
        c_cmds = {"mcp-serve", "api-serve", "weblogin"}
        self.assertFalse(c_cmds & set(by_cmd),
                         "C 档命令不许出现在可执行动作里（红线 3）")

    def test_destructive_actions_are_marked(self):
        """破坏性清单**钉死**；一变就必须回来显式复核。

        2026-10-13（v0.49）复核结论：新增 `session_suppress`——它真删库内
        sessions/messages/steps 行与 exports 副本，且**没有一键恢复**
        （恢复靠下次 sync 重导出，前提是语料还在），故必须标 destructive。
        """
        reg = RG.build()
        destr = {a["action_id"] for a in reg["write_actions"]
                 if a["risk"] == "destructive"}
        self.assertEqual(destr, {"topic_merge", "topic_delete",
                                 "session_suppress"},
                         "破坏性动作清单变了：必须显式复核（它们会丢信息）")

    def test_registrygen_rejects_undeclared_param(self):
        """反向检查会红：把一个动作声明成它**不消费**的参数 → 必须报错。

        这是最危险的缺陷类别（用户以为填了会生效），故单独钉一条。
        **注意怎么构造**：必须只往 `params` 加、**不往 `allow` 加**——
        两边都加就自洽了，门当然不响（首版就是这么写的，红不了：
        "不能失败的断言等于没断言"）。
        """
        import copy
        spec = copy.deepcopy(RG._load(RG.WRITES, "write_actions.json"))  # noqa: SLF001
        for a in spec["actions"]:
            if a["action_id"] == "topic_register":
                a["params"]["sid"] = {"map": "sid"}   # register 不消费 --sid
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "w.json"
            p.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            orig = RG.WRITES
            RG.WRITES = p
            try:
                with self.assertRaises(SystemExit) as cm:
                    RG.build()
                self.assertIn("不消费", str(cm.exception))
            finally:
                RG.WRITES = orig


class PlanIsReadOnlyTest(_Fixture):
    """预演必须无副作用——这是"确认"这个词有意义的前提。"""

    def test_plan_does_not_touch_meta_dbs(self):
        cases = [
            ("topic_register", {"name": "预演不该建的主题"}),
            ("topic_add", {"topic_id": None, "sids": "x"}),
            ("suggest_status", {"key": "测试建议句。", "status": "adopted"}),
            ("keywords_gc", {"keep_runs": 1}),
        ]
        # topic_add 需要真主题 id
        con = sqlite3.connect(str(self.td / "topics_meta.db"))
        tid = con.execute("SELECT id FROM topics ORDER BY id").fetchone()[0]
        con.close()
        cases[1][1]["topic_id"] = tid
        for aid, params in cases:
            for p in (self.td / "topics_meta.db", self.td /
                      "suggestions_meta.db", self.td / "keywords_meta.db"):
                before = _fingerprint(p)
                self.plan(aid, params)
                self.assertEqual(
                    before, _fingerprint(p),
                    "%s 的预演改动了 %s —— 预演必须有副作用为零"
                    % (aid, p.name))

    def test_plan_reports_real_deltas(self):
        con = sqlite3.connect(str(self.td / "topics_meta.db"))
        row = con.execute("SELECT id, name, members FROM topics ORDER BY id"
                          ).fetchone()
        con.close()
        tid, name, members = row[0], row[1], json.loads(row[2] or "[]")
        # 注册同名 → 幂等命中，不该说"将新建"
        p = self.plan("topic_register", {"name": name})
        self.assertTrue(p["idempotent_hit"], p)
        self.assertEqual(p["before"]["topics"], p["after"]["topics"])
        # 加已存在的成员 → noop
        sid = (members[0]["sid"] if members and isinstance(members[0], dict)
               else (members[0] if members else None))
        if sid:
            p2 = self.plan("topic_add", {"topic_id": tid, "sids": sid})
            self.assertTrue(p2["noop"], p2)
            self.assertEqual(p2["before"]["members"], p2["after"]["members"])
        # destructive：delete 必须带快照
        p3 = self.plan("topic_delete", {"topic_id": tid})
        self.assertIsNotNone(p3["snapshot"], "delete 的预演必须给快照")
        self.assertEqual(p3["snapshot"]["id"], tid)
        self.assertEqual(p3["after"]["topics"], p3["before"]["topics"] - 1)

    def test_unknown_topic_404_and_bad_params_400(self):
        with self.assertRaises(WA.WriteError):
            self.plan("topic_delete", {"topic_id": "tp-不存在"})
        with self.assertRaises(WA.WriteError):
            self.plan("topic_register", {"name": ""})
        with self.assertRaises(WA.WriteError):
            self.plan("suggest_status", {"key": "k", "status": "乱写"})
        with self.assertRaises(WA.WriteError):
            self.plan("keywords_gc", {"keep_runs": 0})   # 清空历史必须显式删库
        with self.assertRaises(WA.WriteError):
            self.plan("suggest_status", {"key": "k", "status": "adopted",
                                         "adopted_at": "2026/10/05"})  # 非零填充


class TwoPhaseTest(_Fixture):
    """两段提交的四条拒绝路径 + 一条成功路径。"""

    def test_token_is_single_use(self):
        p = self.plan("suggest_status", {"key": "测试建议句。",
                                         "status": "adopted"})
        c = runlib.issue_confirm(self.cfg.actions_meta, "suggest_status",
                                 p["params"], p, "t")
        runlib.consume_confirm(self.cfg.actions_meta, c["token"],
                               "suggest_status", p["params"], p)
        with self.assertRaises(runlib.ConfirmError) as cm:
            runlib.consume_confirm(self.cfg.actions_meta, c["token"],
                                   "suggest_status", p["params"], p)
        self.assertIn("已用过", str(cm.exception))

    def test_params_tampering_rejected(self):
        p = self.plan("suggest_status", {"key": "测试建议句。",
                                         "status": "adopted"})
        c = runlib.issue_confirm(self.cfg.actions_meta, "suggest_status",
                                 p["params"], p, "t")
        tampered = dict(p["params"], key="另一条建议。")
        with self.assertRaises(runlib.ConfirmError) as cm:
            runlib.consume_confirm(self.cfg.actions_meta, c["token"],
                                   "suggest_status", tampered, p)
        self.assertIn("参数与预览时不一致", str(cm.exception))

    def test_stale_preview_rejected(self):
        """库在两段之间被改过 → 预览指纹不匹配 → 拒绝并要求重新预览。"""
        p = self.plan("suggest_status", {"key": "测试建议句。",
                                         "status": "adopted"})
        c = runlib.issue_confirm(self.cfg.actions_meta, "suggest_status",
                                 p["params"], p, "t")
        # 模拟"别人在这期间也改了这个库"：状态已被改成 rejected
        con = sqlite3.connect(str(self.td / "suggestions_meta.db"))
        con.execute("UPDATE suggestion_status SET status='rejected', "
                    "updated_at='2026-10-12 00:00:00' WHERE key=?",
                    ("测试建议句。",))
        con.commit()
        con.close()
        p2 = self.plan("suggest_status", {"key": "测试建议句。",
                                         "status": "adopted"})
        with self.assertRaises(runlib.ConfirmError) as cm:
            runlib.consume_confirm(self.cfg.actions_meta, c["token"],
                                   "suggest_status", p["params"], p2)
        self.assertIn("库在两段之间变了", str(cm.exception))

    def test_expired_token_rejected(self):
        p = self.plan("keywords_gc", {"keep_runs": 1})
        c = runlib.issue_confirm(self.cfg.actions_meta, "keywords_gc",
                                 p["params"], p, "t", ttl=-1)
        with self.assertRaises(runlib.ConfirmError) as cm:
            runlib.consume_confirm(self.cfg.actions_meta, c["token"],
                                   "keywords_gc", p["params"], p)
        self.assertIn("已过期", str(cm.exception))

    def test_action_mismatch_rejected(self):
        p = self.plan("keywords_gc", {"keep_runs": 1})
        c = runlib.issue_confirm(self.cfg.actions_meta, "keywords_gc",
                                 p["params"], p, "t")
        with self.assertRaises(runlib.ConfirmError) as cm:
            runlib.consume_confirm(self.cfg.actions_meta, c["token"],
                                   "topic_delete", p["params"], p)
        self.assertIn("属于动作", str(cm.exception))


class WriteExecutorTest(_Fixture):
    """真执行：写进去了 + 台账留痕（含 destructive 快照）。"""

    def test_suggest_status_writes_and_records(self):
        p = self.plan("suggest_status", {"key": "测试建议句。",
                                         "status": "adopted"})
        r = WA.execute("suggest_status", self.spec("suggest_status"),
                       p["params"], self.cfg)
        self.assertIn("已记录", r["summary"])
        row = WA._status_row(self.td / "suggestions_meta.db",  # noqa: SLF001
                             "测试建议句。")
        self.assertEqual(row["status"], "adopted")
        self.assertIsNotNone(row["adopted_at"], "adopted 必须落分界日期")

    def test_keywords_gc_deletes_old_runs(self):
        p = self.plan("keywords_gc", {"keep_runs": 1})
        self.assertEqual(p["before"]["runs"], 2)
        r = WA.execute("keywords_gc", self.spec("keywords_gc"),
                       p["params"], self.cfg)
        self.assertEqual(r["deleted_runs"], 1)
        self.assertEqual(r["deleted_stats"], 2)
        con = sqlite3.connect(str(self.td / "keywords_meta.db"))
        left = con.execute("SELECT COUNT(*) FROM keyword_stats").fetchone()[0]
        orphans = con.execute(
            "SELECT COUNT(*) FROM keyword_stats s LEFT JOIN keyword_runs r "
            "ON s.run_id=r.id WHERE r.id IS NULL").fetchone()[0]
        con.close()
        self.assertEqual(left, 1)
        self.assertEqual(orphans, 0, "孤儿统计行必须为 0（比不删更坏）")

    def test_topic_delete_records_snapshot(self):
        con = sqlite3.connect(str(self.td / "topics_meta.db"))
        tid = con.execute("SELECT id FROM topics ORDER BY id").fetchone()[0]
        con.close()
        p = self.plan("topic_delete", {"topic_id": tid})
        spec = self.spec("topic_delete")
        r = WA.execute("topic_delete", spec, p["params"], self.cfg)
        runlib.record_run(self.cfg.actions_meta, run_id="r1",
                          action_id="topic_delete", tier="B", client="t",
                          params=p["params"], elapsed_ms=1, status="done",
                          result_summary=r["summary"])
        runlib.record_write(self.cfg.actions_meta, run_id="r1",
                            action_id="topic_delete",
                            target=p["target"], target_path=p["target_path"],
                            risk=p["risk"], cmd=p["cmd"], summary=r["summary"],
                            before=p["before"], after=p["after"],
                            snapshot=p["snapshot"], client="t")
        writes = runlib.recent_writes(self.cfg.actions_meta)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["risk"], "destructive")
        self.assertIsNotNone(writes[0]["snapshot"],
                             "destructive 动作的台账必须带删除前快照")
        self.assertEqual(writes[0]["snapshot"]["id"], tid)
        con = sqlite3.connect(str(self.td / "topics_meta.db"))
        gone = con.execute("SELECT COUNT(*) FROM topics WHERE id=?",
                           (tid,)).fetchone()[0]
        con.close()
        self.assertEqual(gone, 0, "主题行应已删除")


class WriteHttpTest(_Fixture):
    """真起服真打 HTTP：两段提交的**接线**（函数对但路由没接上是最可能的错）。

    注意：本类**故意不用** `_Fixture.setUp`（它每个用例建一份 tmp 夹具，
    而这里要一份跨用例共享的库）；但仍继承它以便复用 `_make_cfg` 等工具，
    故必须把 `setUp` 显式置空——否则基类的 setUp 会每例重造一份库，
    assert 读到的就不是 setUpClass 那份（首版踩过：读到 None）。
    """

    def setUp(self):        # noqa: D102 - 覆盖基类，见类 docstring
        pass

    def tearDown(self):     # noqa: D102
        pass

    @classmethod
    def setUpClass(cls):
        cls._td0 = tempfile.TemporaryDirectory()
        td = Path(cls._td0.name)
        if REAL_TOPICS.is_file():
            shutil.copy2(REAL_TOPICS, td / "topics_meta.db")
        con = sqlite3.connect(str(td / "suggestions_meta.db"))
        con.executescript(
            "CREATE TABLE suggestion_status (key TEXT PRIMARY KEY, status TEXT "
            "NOT NULL, adopted_at TEXT, note TEXT, updated_at TEXT NOT NULL);")
        con.execute("INSERT INTO suggestion_status VALUES (?,?,?,?,?)",
                    ("接线测试建议。", "pending", None, None,
                     "2026-10-01 00:00:00"))
        con.commit()
        con.close()
        cls.td = td
        cls.cfg = _make_cfg(td)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                      AS.make_handler(cls.cfg))
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.socket.getsockname()[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.th.join(timeout=2)
        cls._td0.cleanup()

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

    def test_registry_endpoint_has_both_tiers(self):
        st, d = self._get("/api/actions")
        self.assertEqual(st, 200)
        tiers = {a["tier"] for a in d["actions"]}
        self.assertEqual(tiers, {"A", "B"})
        b = [a for a in d["actions"] if a["tier"] == "B"]
        # 9 → 11（v0.49 会话抑制加两个）→ 13（v0.62 写工作文件加两个：
        # suggest_agents + draft_pack，用户令 2026-10-14 命令可就地执行）
        # → 14（v0.75 卡片复核落账 card_review：上游 v0.74 复核台账的 view 直给）
        self.assertEqual(len(b), 14)
        for a in b:
            self.assertIn(a["risk"], ("reversible", "destructive"))

    def test_two_phase_over_http(self):
        # 前置：夹具里这条应是 pending
        self.assertEqual(
            WA._status_row(self.td / "suggestions_meta.db",  # noqa: SLF001
                           "接线测试建议。")["status"], "pending")
        # 第一段：只预演，不许写
        st, d = self._post("/api/actions/run",
                           {"action_id": "suggest_status",
                            "params": {"key": "接线测试建议。",
                                       "status": "adopted"}})
        self.assertEqual(st, 200)
        self.assertEqual(d["status"], "needs_confirm")
        self.assertTrue(d["confirm_token"])
        self.assertEqual(d["risk"], "reversible")
        self.assertTrue(any("状态" in h for h in d["human"]))
        # 第一段（预演）不许写库 —— 用**状态**判，不用字节比：
        # 字节比在"改动幂等"时会假绿（本测试首版就在这里红/绿不分）
        self.assertEqual(
            WA._status_row(self.td / "suggestions_meta.db",  # noqa: SLF001
                           "接线测试建议。")["status"], "pending",
            "第一段（预演）不许改状态")
        # 第二段：带 token → 真写
        st2, d2 = self._post("/api/actions/run",
                             {"action_id": "suggest_status",
                              "params": {"key": "接线测试建议。",
                                         "status": "adopted"},
                              "confirm_token": d["confirm_token"],
                              "client": "pytest"})
        self.assertEqual(st2, 200, d2)
        self.assertEqual(d2["status"], "done")
        self.assertIn("已记录", d2["summary"])
        row = WA._status_row(self.td / "suggestions_meta.db",  # noqa: SLF001
                             "接线测试建议。")
        self.assertEqual(row["status"], "adopted", "第二段必须真写进去")
        self.assertIsNotNone(row["adopted_at"])
        # 台账：预演一行 + 执行一行 + 写记录一行；client 如实记录。
        # **不断言"总数 == 1"**：台账库跨用例共享（setUpClass 起一次服务），
        # 别的用例也会往同一个库写。断言要盯"本次这一条"。
        st3, led = self._get("/api/actions/ledger")
        self.assertEqual(st3, 200)
        self.assertGreaterEqual(led["stats"]["runs"], 2)
        self.assertGreaterEqual(led["stats"]["writes"], 1)
        self.assertIn("pytest", led["stats"]["clients"])
        mine = [w for w in led["writes"]
                if w["action_id"] == "suggest_status"]
        self.assertTrue(mine, "本次 suggest_status 的写记录必须进台账")
        self.assertEqual(mine[0]["target"], "suggestions_meta")
        self.assertIn("adopted", json.dumps(mine[0]["after"], ensure_ascii=False))

    def test_token_reuse_over_http_409(self):
        st, d = self._post("/api/actions/run",
                           {"action_id": "topic_register",
                            "params": {"name": "接线测试主题"}})
        self.assertEqual(st, 200)
        tok = d["confirm_token"]
        st2, _d2 = self._post("/api/actions/run",
                              {"action_id": "topic_register",
                               "params": {"name": "接线测试主题"},
                               "confirm_token": tok})
        self.assertEqual(st2, 200)
        st3, d3 = self._post("/api/actions/run",
                             {"action_id": "topic_register",
                              "params": {"name": "接线测试主题"},
                              "confirm_token": tok})
        self.assertEqual(st3, 409)
        self.assertIn("已用过", d3["error"])
        self.assertEqual(d3["retry"], "preview")

    def test_unknown_and_c_tier_403(self):
        st, d = self._post("/api/actions/run", {"action_id": "rm_rf"})
        self.assertEqual(st, 403)
        self.assertIn("白名单", d["error"])

    def test_preview_run_recorded_even_without_confirm(self):
        _st, _d = self._post("/api/actions/run",
                             {"action_id": "topic_register",
                              "params": {"name": "只预览不确认的主题"}})
        _st, led = self._get("/api/actions/ledger?limit=10")
        statuses = [r["status"] for r in led["runs"]]
        self.assertIn("preview", statuses,
                      "只点了预览没确认，也必须在台账里留一行")


if __name__ == "__main__":
    unittest.main()
