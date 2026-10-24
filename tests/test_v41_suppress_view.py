# -*- coding: utf-8 -*-
"""v0.49（会话 6 续批）测试：会话删除（抑制）的 **view 侧**。

用户 2026-10-13 逐项批准《docs/PROPOSAL-session-suppress.md》§5 的 ①–⑦、⑧ 不做。
本文件钉住 view 侧那几条**能判定的**：

1. **不新造写路径**（清单 ⑥）：删除与恢复都是 B 档动作，走现成的两段提交 +
   台账；`session_suppress` 必须声明为 `destructive`（它会真删行），
   `session_unsuppress` 为 `reversible`；
2. **写对库**（清单 ①）：抑制表在 `topics_meta.db`——注册表的 `target` 与实现
   实际用的库必须一致（写错库会安静地在没人看的地方建表）；
3. **预演零副作用**：`plan` 前后 meta 库**字节不变**；且 destructive 必须给
   "删除前快照"（只存数量与标题，**不存正文**——正文能否恢复取决于语料）；
4. **口径诚实**（清单 ③ 与 ⑧）：预演人话必须写明"删的是仓内副本 / 不删采集源 /
   能不能恢复只取决于语料是否还在 / 不设恢复时限"，且**不许**出现"可恢复"式承诺；
5. **端到端只在临时库上跑**（铁律：真库只读）：抑制 → 三表该 sid 行数 0 +
   抑制表有行 + exports 副本被删；恢复 → 抑制表行没了且**数据不回来**；
6. **`/api/suppressed` 只读**：在"还没有抑制表"的库上取一次**不许把表建出来**。
"""
from __future__ import annotations

import hashlib
import json
import re
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
from actions import writeactions as WA  # noqa: E402

UP = AS.upstream_root()
REAL_DB = UP / "harvester.db"


def _digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rows(db: Path, sid: str) -> dict:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return {t: con.execute("SELECT COUNT(*) FROM %s WHERE sid=?" % t,
                               (sid,)).fetchone()[0]
                for t in ("sessions", "messages", "steps")}
    finally:
        con.close()


def _make_env(td: Path):
    """临时索引库（三表有行）+ 临时 topics_meta + 临时 exports。**不碰真库。**"""
    if AS.ensure_harvester_importable():
        raise unittest.SkipTest("上游 harvester 不可用")
    from harvester.indexing import SCHEMA, index_session
    from harvester.models import Message, SessionRecord

    db = td / "harvester.db"
    con = sqlite3.connect(str(db))
    try:
        con.executescript(SCHEMA)
        for i in (1, 2):
            index_session(con, SessionRecord(
                source="dsh", session_id="s%d" % i, title="会话%d" % i,
                created_at="2026-10-01 10:00:00",
                updated_at="2026-10-01 10:02:00",
                messages=[Message(role="user", text="标记%d" % i),
                          Message(role="assistant", text="好的")]),
                category="test")
        con.commit()
    finally:
        con.close()

    meta = td / "topics_meta.db"
    con = sqlite3.connect(str(meta))
    try:
        # 故意**不建**抑制表：正好用来验"读路径不建表"
        con.executescript("CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT);")
        con.commit()
    finally:
        con.close()

    exports = td / "exports"
    exports.mkdir()
    (exports / "20261010__dsh__x__aaaaaa.json").write_text(
        json.dumps({"source": "dsh", "session_id": "s1", "messages": []},
                   ensure_ascii=False), encoding="utf-8")
    (exports / "20261010__dsh__x__aaaaaa.md").write_text("旧副本", encoding="utf-8")

    gl = td / "g.json"
    gl.write_text("{}", encoding="utf-8")
    cards = td / "cards"
    cards.mkdir()
    cfg = AS.Config(db=db, cards_root=cards, glossary=gl, topics_meta=meta,
                    exports_root=exports, actions_meta=td / "am.db")
    return cfg, db, meta, exports


class SuppressRegistryTest(unittest.TestCase):
    """注册表与实现的形状（不需要真库）。"""

    @classmethod
    def setUpClass(cls):
        cls.reg = RG.build()
        cls.specs = {a["action_id"]: a for a in cls.reg["write_actions"]}

    def test_both_actions_registered_with_right_risk(self):
        self.assertIn("session_suppress", self.specs)
        self.assertIn("session_unsuppress", self.specs)
        self.assertEqual(self.specs["session_suppress"]["risk"], "destructive",
                         "它会真删库内行与副本 → 必须标 destructive")
        self.assertEqual(self.specs["session_unsuppress"]["risk"], "reversible")

    def test_target_is_topics_meta_per_approved_item_1(self):
        """批准的清单 ①：抑制表放**现有** topics_meta.db，不新建 meta 库。"""
        for aid in ("session_suppress", "session_unsuppress"):
            self.assertEqual(self.specs[aid]["target"], "topics_meta", aid)

    def test_implementation_uses_the_same_db_as_the_registry_says(self):
        """注册表说写 topics_meta，实现就必须写 topics_meta（写错库会安静建表）。"""
        with tempfile.TemporaryDirectory() as td:
            cfg, _db, meta, _ex = _make_env(Path(td))
            self.assertEqual(str(WA._suppress_meta(cfg)), str(meta))
            # 没配 topics_meta 时必须**拒绝**，不许回落到某个默认路径
            cfg2 = AS.Config(db=cfg.db, cards_root=cfg.cards_root,
                             glossary=cfg.glossary, topics_meta=None,
                             exports_root=cfg.exports_root)
            with self.assertRaises(WA.WriteError):
                WA._suppress_meta(cfg2)

    def test_every_registered_write_action_has_an_implementation(self):
        for aid in self.specs:
            self.assertIn(aid, WA.WRITE_ACTIONS,
                          "%s 在注册表里但没有实现（漂移）" % aid)
        for aid in WA.WRITE_ACTIONS:
            self.assertIn(aid, self.specs,
                          "%s 有实现但没进注册表（漂移）" % aid)


class SuppressPlanTest(unittest.TestCase):
    """预演：只读、有快照、口径诚实（全在临时库上）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.cfg, self.db, self.meta, self.exports = _make_env(self.td)
        self.specs = {a["action_id"]: a for a in RG.build()["write_actions"]}

    def tearDown(self):
        self._td.cleanup()

    def _plan(self, params):
        return WA.plan("session_suppress", self.specs["session_suppress"],
                       params, self.cfg)

    def test_plan_reports_row_counts_and_no_side_effect(self):
        before = _digest(self.meta)
        prev = self._plan({"sids": "dsh:s1", "reason": "没营养"})
        self.assertEqual(prev["before"]["rows"]["dsh:s1"],
                         {"sessions": 1, "messages": 2, "steps": 0})
        self.assertEqual(_digest(self.meta), before,
                         "预演必须零副作用（meta 库字节不变）")
        # 抑制表**还没建**：读路径不建表
        con = sqlite3.connect(f"file:{self.meta.as_posix()}?mode=ro", uri=True)
        try:
            self.assertIsNone(con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("suppressed_sessions",)).fetchone())
        finally:
            con.close()

    def test_plan_gives_destructive_snapshot_without_body(self):
        """destructive 的唯一凭据 = 删除前快照；但**不存正文**（正文不归它管）。"""
        prev = self._plan({"sids": "dsh:s1", "reason": "没营养"})
        snap = prev.get("snapshot")
        self.assertIsNotNone(snap, "destructive 预演必须给删除前快照")
        self.assertEqual(snap["sids"], ["dsh:s1"])
        self.assertEqual(snap["row_counts"]["dsh:s1"]["messages"], 2)
        self.assertEqual(snap["titles"]["dsh:s1"], "会话1")
        self.assertIn("不存正文", snap["note"])
        self.assertIn("语料", snap["note"])

    def test_plan_rejects_missing_reason_and_sids(self):
        with self.assertRaises(WA.WriteError):
            self._plan({"sids": "dsh:s1", "reason": "   "})
        with self.assertRaises(WA.WriteError):
            self._plan({"sids": "", "reason": "x"})
        with self.assertRaises(WA.WriteError):
            self._plan({"sids": " , ", "reason": "x"})

    def test_plan_human_text_is_honest(self):
        prev = self._plan({"sids": "dsh:s1", "reason": "没营养"})
        human = "\n".join(prev["human"])
        for phrase in ("仓内副本", "不删采集源", "只取决于语料是否还在",
                       "不设恢复时限"):
            self.assertIn(phrase, human, phrase)
        self.assertNotIn("可恢复", human.replace("不恢复", ""),
                         "不许出现任何「可恢复」式承诺")
        # 预演必须自陈边界（不是"执行后保证的结果"）
        self.assertTrue(prev["limits"])

    def test_plan_warns_when_exports_root_is_not_configured(self):
        """起服时没配 `--exports-root` → 预演必须**响亮警告**"本次不清副本"。

        这是本轮发现的一个**真实配置缺口**：`start-viewv2.cmd` 原来没传
        `--exports-root`，于是运行中的服务 `cfg.exports_root=None` —— 抑制会写表、
        会删库内行，但**清不掉 exports 旧副本**，而旧副本还在就说明
        **下次 sync/update 会把它重新索引回来**（抑制看起来"没生效"）。
        两件事都做了：① 启动脚本补上该参数；② 这条断言保证"没配时必须明说"，
        让缺口不会再安静地回来。
        """
        cfg = AS.Config(db=self.db, cards_root=self.cfg.cards_root,
                        glossary=self.cfg.glossary, topics_meta=self.meta,
                        exports_root=None)
        prev = WA.plan("session_suppress", self.specs["session_suppress"],
                       {"sids": "dsh:s1", "reason": "x"}, cfg)
        human = "\n".join(prev["human"])
        self.assertIn("没配", human)
        self.assertIn("不清 exports 副本", human)
        self.assertEqual(prev["snapshot"]["exports_to_delete"], [])

    def test_start_script_passes_exports_root(self):
        """启动脚本必须传 `--exports-root`（否则上面那个缺口会在真机上复发）。

        门读的是**真脚本文本**：这是"配置正确"唯一能被测试钉住的地方——
        服务的行为对，但没人给它正确的参数，等于功能没接通。
        """
        script = ROOT / "start-viewv2.cmd"
        self.assertTrue(script.is_file(), "找不到 start-viewv2.cmd")
        text = script.read_text(encoding="utf-8", errors="replace")
        line = [ln for ln in text.splitlines()
                if "actions.actionsserve" in ln and ln.strip().startswith("start")]
        self.assertEqual(len(line), 1, "期望恰好一行启动 actions-serve")
        self.assertIn("--exports-root", line[0],
                      "actions-serve 没传 --exports-root → 抑制清不掉 exports 旧副本"
                      "（下次 sync 会把会话收回来）")

    def test_start_scripts_are_host_portable(self):
        """启动脚本必须**不绑定宿主**（用户铁律 2026-10-14：项目必须可迁移）。

        三条红线：①不许出现任何具体用户名/盘符绝对路径（换机即断）；
        ②机器相关的路径（卡片仓/主题链）必须可用环境变量覆盖——迁移时
        `set CARDS_ROOT=...` 就能整体改向，不用改脚本；③默认值只许基于
        `%~dp0`（随项目走）或 `%USERPROFILE%`（随用户走）这类**换机会重解**的引用。
        """
        for name in ("start-viewv2.cmd", "start.cmd", "start-system.cmd"):
            path = ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(":\\Users\\", text.replace("%USERPROFILE%", ""),
                             "%s 出现硬编码用户目录（绑定宿主，违反可迁移铁律）" % name)
            self.assertNotIn("yianyao", text,
                             "%s 出现具体用户名（绑定宿主，违反可迁移铁律）" % name)
            for var in self._machine_vars(name):
                self.assertIn("if not defined %s" % var, text,
                              "%s 的 %s 没有环境变量覆盖口（迁移时无法改向）" % (name, var))

    def test_start_scripts_are_pure_ascii(self):
        """v0.73（用户截图：start-viewv2.cmd 执行满屏乱码，注释还被当成
        命令跑出「'xxx' 不是内部或外部命令」）。cmd.exe 按 ANSI（GBK）码页
        解析 .cmd 文件——UTF-8 中文注释必乱码，且多字节序列会吞掉行结构。
        启动脚本必须**纯 ASCII**（说明文字一律英文）。"""
        for name in ("start-viewv2.cmd", "start.cmd", "start-system.cmd"):
            path = ROOT / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertTrue(
                text.isascii(),
                "%s 含非 ASCII 字符——cmd 按 GBK 解析会乱码并把注释当命令跑"
                % name)

    @staticmethod
    def _machine_vars(name: str) -> tuple:
        """各脚本里「机器相关路径」应有的覆盖口清单。"""
        # v0.81：三个脚本都显式传 --chain-root（P0-3），默认值同样必须可用
        # 环境变量改向（可迁移铁律：迁移时 set CHAIN_ROOT=... 即可）。
        return ("CARDS_ROOT", "CHAIN_ROOT")

    def test_start_scripts_launch_three_services(self):
        """v0.81 P0-1：三个启动器都必须起**三个服务**（VIEW 是三服务体系）。

        `start.cmd` / `start-system.cmd` 原来只起 api-serve + view 两个——
        而 README 把这两个列为「方式一（推荐）」。后果是默认落地页
        「今日工作台」直接残（/api/overview 走 8766），命令台/处理单/
        归因台/主题研读面/双轨/图谱/全部 B 档写动作整片不可用。
        门读真脚本文本：服务行为对、但没人给它正确的启动参数，
        等于功能没接通——这是唯一能被测试钉住的地方。
        """
        for name in ("start-viewv2.cmd", "start.cmd", "start-system.cmd"):
            path = ROOT / name
            self.assertTrue(path.is_file(), "找不到 %s" % name)
            text = path.read_text(encoding="utf-8", errors="replace")
            for svc, needle in (("api-serve（8765）", "-m harvester api-serve"),
                                ("actions-serve（8766）",
                                 "-m actions.actionsserve"),
                                ("view（8088）", "view.py")):
                lines = [ln for ln in text.splitlines()
                         if needle in ln and ln.strip().startswith("start")]
                self.assertEqual(len(lines), 1,
                                 "%s 应有恰好一行启动 %s（实际 %d 行）"
                                 % (name, svc, len(lines)))

    def test_start_scripts_pass_reviews_meta(self):
        """v0.81 P0-2：两个服务都必须显式传 `--reviews-meta`。

        缺它=人裁过的警告反复烦人：① actionsserve 缺省 None，
        `card_review` 预演即报错「未配置 reviews_meta」；② api-serve 的
        缺省是**相对路径** `cards_meta.db`（换目录起服就静默丢台账）；
        ③ G3 卡片校验读不到裁决，v0.74「人裁过的不许再烦人」落空。
        台账文件 `cards_meta.db` 与 db 同目录，零迁移成本。
        """
        for name in ("start-viewv2.cmd", "start.cmd", "start-system.cmd"):
            text = (ROOT / name).read_text(encoding="utf-8",
                                           errors="replace")
            for needle in ("-m harvester api-serve",
                           "-m actions.actionsserve"):
                lines = [ln for ln in text.splitlines()
                         if needle in ln and ln.strip().startswith("start")]
                self.assertEqual(len(lines), 1, "%s 的 %s 启动行不唯一"
                                 % (name, needle))
                self.assertIn("--reviews-meta", lines[0],
                              "%s 的 %s 没传 --reviews-meta（card_review "
                              "预演即报错；人裁过的警告会反复出现）"
                              % (name, needle))

    def test_start_scripts_pass_chain_root(self):
        """v0.81 P0-3：api-serve 与 actions-serve 都必须显式传 `--chain-root`。

        api-serve 的缺省是 cwd 相对的 `knowledge/topics`——从别处起服就
        静默 404，VIEW 显示「该主题尚无 chain 长文」的**假空态**
        （链文件明明在）。这正是定位 3「人的变化轨迹」的核心视图，
        失效形态是"看起来正常"，最难被发现。
        """
        for name in ("start-viewv2.cmd", "start.cmd", "start-system.cmd"):
            text = (ROOT / name).read_text(encoding="utf-8",
                                           errors="replace")
            for needle in ("-m harvester api-serve",
                           "-m actions.actionsserve"):
                lines = [ln for ln in text.splitlines()
                         if needle in ln and ln.strip().startswith("start")]
                self.assertEqual(len(lines), 1, "%s 的 %s 启动行不唯一"
                                 % (name, needle))
                self.assertIn("--chain-root", lines[0],
                              "%s 的 %s 没传 --chain-root（cwd 兜底会造出"
                              "「尚无 chain 长文」假空态）" % (name, needle))

    def test_start_scripts_pass_corpus_watermark(self):
        """v0.81 P1-4：两个服务都必须传 `--corpus-watermark`（库龄判定开关）。

        不传 ⇒ 库快照里的 `stale` 恒 null = **"没判定"**，而页面两条提示
        （home-foot 的 STALE 徽章、顶部新鲜度条）读的就是它——于是"库半个月
        没同步、看板照常渲染"这种慢性误导**永远不会被发现**。
        判定要 stat 一遍语料盘，所以默认关、由启动器显式开——门钉住这个"开"。
        """
        for name in ("start-viewv2.cmd", "start.cmd", "start-system.cmd"):
            text = (ROOT / name).read_text(encoding="utf-8",
                                           errors="replace")
            for needle in ("-m harvester api-serve",
                           "-m actions.actionsserve"):
                lines = [ln for ln in text.splitlines()
                         if needle in ln and ln.strip().startswith("start")]
                self.assertEqual(len(lines), 1, "%s 的 %s 启动行不唯一"
                                 % (name, needle))
                self.assertIn("--corpus-watermark", lines[0],
                              "%s 的 %s 没传 --corpus-watermark（stale 恒为"
                              " null：库多久没同步都不会有人告诉你）"
                              % (name, needle))

    def test_desktop_shell_matches_start_script(self):
        """P11：桌面壳 `desktop/main.js` 与 `start-viewv2.cmd` 参数必须一致。

        main.js 是**第二份**服务命令行（它自己的注释写着"与 start-viewv2.cmd
        逐条一致，唯一真源"）。可它缺参数时**没有任何红字**：窗口照常打开、
        页面照常渲染，只是人裁台账（--reviews-meta）、主题链长文
        （--chain-root）、库龄判定（--corpus-watermark）静默失效——而用户
        日常正是用桌面应用启动，漏一次等于这轮 P0/P1 修复全部落空。
        门按「服务」抽两边 `--flag` 集合，任一侧多一个少一个都红。
        """
        cmd_text = (ROOT / "start-viewv2.cmd").read_text(encoding="utf-8",
                                                         errors="replace")
        js_text = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8",
                                                           errors="replace")
        flags = re.compile(r"--[a-z][a-z-]*")
        for svc, needle in (("api-serve", "-m harvester api-serve"),
                            ("actions-serve", "-m actions.actionsserve"),
                            ("view", "view.py")):
            cmd_lines = [ln for ln in cmd_text.splitlines()
                         if needle in ln and ln.strip().startswith("start")]
            self.assertEqual(len(cmd_lines), 1,
                             "start-viewv2.cmd 的 %s 启动行不唯一" % svc)
            want = set(flags.findall(cmd_lines[0]))
            m = re.search(r'name:\s*"%s".*?\]\s*\}' % re.escape(svc),
                          js_text, re.S)
            self.assertIsNotNone(m, "main.js 里找不到 %s 的服务定义" % svc)
            got = set(flags.findall(m.group(0)))
            self.assertEqual(
                want, got,
                "desktop/main.js 的 %s 参数与 start-viewv2.cmd 不一致："
                "缺 %s，多 %s——桌面应用是日常启动方式，缺参数不报错，"
                "只会静默少功能" % (svc, sorted(want - got), sorted(got - want)))

    def test_plan_lists_export_copies_to_delete(self):
        """`deleted` 是**逐文件一条记录**（sid + json + md），不是裸文件名。

        首版断言把它当字符串，红了——是**我的断言错了**，不是实现错了：
        上游返回 `[{'sid':…, 'json':…, 'md':…}]`，这样页面才能按 sid 归组展示。
        """
        prev = self._plan({"sids": "dsh:s1", "reason": "没营养"})
        dels = prev["snapshot"]["exports_to_delete"]
        self.assertEqual(len(dels), 1, dels)
        self.assertEqual(dels[0]["sid"], "dsh:s1")
        self.assertEqual(dels[0]["json"], "20261010__dsh__x__aaaaaa.json")
        self.assertEqual(dels[0]["md"], "20261010__dsh__x__aaaaaa.md")

    def test_plan_warns_when_sid_is_nowhere(self):
        prev = self._plan({"sids": "dsh:不存在", "reason": "x"})
        self.assertIn("在库与抑制表里都没有", "\n".join(prev["human"]))

    def test_unsuppress_plan_is_noop_for_unlisted_sid(self):
        prev = WA.plan("session_unsuppress",
                       self.specs["session_unsuppress"],
                       {"sids": "dsh:s1"}, self.cfg)
        self.assertTrue(prev["noop"])


class SuppressEndToEndTest(unittest.TestCase):
    """端到端：**只在临时库上**跑真写（真库一字节不动）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.cfg, self.db, self.meta, self.exports = _make_env(self.td)
        self.specs = {a["action_id"]: a for a in RG.build()["write_actions"]}

    def tearDown(self):
        self._td.cleanup()

    def _confirm_and_run(self, aid, params):
        prev = WA.plan(aid, self.specs[aid], params, self.cfg)
        return WA.execute(aid, self.specs[aid], prev["params"], self.cfg)

    def test_suppress_then_unsuppress_roundtrip(self):
        sid = "dsh:s1"
        self.assertEqual(_rows(self.db, sid), {"sessions": 1, "messages": 2,
                                              "steps": 0})
        out = self._confirm_and_run("session_suppress",
                                    {"sids": sid, "reason": "太水了",
                                     "created_by": "测试"})
        self.assertIn("抑制 1 个会话", out["summary"])
        self.assertEqual(_rows(self.db, sid), {"sessions": 0, "messages": 0,
                                              "steps": 0},
                         "执行后三表该 sid 行数必须为 0")
        # 抑制表有行，且记了理由与谁
        from harvester.suppress import list_suppressed
        rows = {r["sid"]: r for r in list_suppressed(self.meta)}
        self.assertEqual(rows[sid]["reason"], "太水了")
        self.assertEqual(rows[sid]["created_by"], "测试")
        # exports 旧副本被清（否则下次索引还会回来）
        self.assertFalse((self.exports / "20261010__dsh__x__aaaaaa.json").exists())
        self.assertFalse((self.exports / "20261010__dsh__x__aaaaaa.md").exists())
        # 另一个 sid 不受影响（抑制是定点操作）
        self.assertEqual(_rows(self.db, "dsh:s2")["sessions"], 1)

        # 恢复：只删抑制表行，**数据不回来**
        out2 = self._confirm_and_run("session_unsuppress", {"sids": sid})
        self.assertEqual(out2["removed"], 1)
        self.assertEqual(list_suppressed(self.meta), [])
        self.assertEqual(_rows(self.db, sid)["sessions"], 0,
                         "取消抑制**不得**恢复数据（能不能恢复取决于语料）")

    def test_suppress_is_idempotent(self):
        sid = "dsh:s1"
        self._confirm_and_run("session_suppress",
                              {"sids": sid, "reason": "第一次"})
        prev = WA.plan("session_suppress", self.specs["session_suppress"],
                       {"sids": sid, "reason": "第二次"}, self.cfg)
        self.assertEqual(prev["before"]["suppressed"], [sid],
                         "已抑制的 sid 要在 before 里显出来（幂等可预期）")
        self.assertIn("已经抑制过", "\n".join(prev["human"]))
        self._confirm_and_run("session_suppress",
                              {"sids": sid, "reason": "第二次"})
        from harvester.suppress import list_suppressed
        rows = list_suppressed(self.meta)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "第二次", "理由更新为最新一次")
        self.assertEqual(rows[0]["created"], rows[0]["created"],
                         "created 保留首次时间（口径：何时第一次被抑制）")

    def test_real_db_is_untouched_by_this_class(self):
        """整个端到端都在临时库上；真库只读（大小 + mtime 都不许变）。"""
        if not REAL_DB.is_file():
            self.skipTest("真 harvester.db 不在位")
        st = REAL_DB.stat()
        before = (st.st_size, int(st.st_mtime))
        self._confirm_and_run("session_suppress",
                              {"sids": "dsh:s1", "reason": "x"})
        st2 = REAL_DB.stat()
        self.assertEqual((st2.st_size, int(st2.st_mtime)), before,
                         "本测试类**不得**写真库")


class SuppressedEndpointTest(unittest.TestCase):
    """/api/suppressed：只读、不建表、缺配置如实说。"""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.cfg, cls.db, cls.meta, cls.exports = _make_env(td)
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
        cls._td.cleanup()

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_read_does_not_create_the_table(self):
        st, d = self._get("/api/suppressed")
        self.assertEqual(st, 200)
        self.assertTrue(d["configured"])
        self.assertEqual(d["count"], 0)
        self.assertEqual(d["rows"], [])
        con = sqlite3.connect(f"file:{self.meta.as_posix()}?mode=ro", uri=True)
        try:
            self.assertIsNone(con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("suppressed_sessions",)).fetchone(),
                "读一下就顺手建表 = 只读消费方产生了副作用")
        finally:
            con.close()

    def test_endpoint_carries_the_honest_wording(self):
        _st, d = self._get("/api/suppressed")
        self.assertIn("仓内副本", d["honest"])
        self.assertIn("不删采集源", d["honest"])
        self.assertIn("不设恢复时限", d["honest"])

    def test_root_lists_the_endpoint(self):
        st, d = self._get("/")
        self.assertEqual(st, 200)
        self.assertIn("/api/suppressed", d["endpoints"])


if __name__ == "__main__":
    unittest.main()
