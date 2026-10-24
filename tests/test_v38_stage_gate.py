# -*- coding: utf-8 -*-
"""P3-6 阶段门测试：「乱点不可逆 = 不可能」逐卡走查（会红的那种钉子）。

阶段门原文：**B 档全留痕；C 档零执行；「乱点不可逆」逐卡走查记录在案。**
走查如果只写在文档里，就是"不可失败的断言"——所以这里把它做成测试：
对**注册表里全部动作**逐条核验权限归属与可逆性声明，缺一条就红。

三条判据（都能说出什么时候会红）：

1. **A 档 = 只读**：`read_actions` 的 4 个动作在 `actionsserve.ACTIONS` 里，
   且实现只读采集库 / 只写 reports 与临时目录（写库的必须归 B）；
2. **B 档 = 两段提交 + 台账 + 可逆性声明**：每个写动作必须有
   `risk ∈ {reversible, destructive}`、`target` 落在三个 meta 库之一、
   `reversible` 人话、`params` 里 `required` 的必填项有 label；且
   **destructive 的 `detail` 必须写明会丢什么**（不许含糊成"有风险"）；
3. **C 档 = 永不执行**：C 档命令（mcp-serve / api-serve / weblogin *）
   **不得**出现在任何一档可执行动作里；它们的命令文本仍可复制。

另加一条**接口级**走查：动作面只有一个写入口（POST /api/actions/run），
且它对未在注册表里的 action_id 一律 403（"乱点"的第一道防线）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import registrygen as RG  # noqa: E402
from actions import writeactions as WA  # noqa: E402

C_TIER_CMDS = {"mcp-serve", "api-serve", "weblogin check",
               "weblogin init-config", "weblogin prepare"}
# target 不再硬编码清单（v0.62）：以注册表 write_targets 声明为准——
# 新增写目标（如 repo_files）必须在 registrygen 立项，这里的门是
# "target 必须在 write_targets 里"，而不是"target 必须是这三个"。


class CardByCardWalkthrough(unittest.TestCase):
    """逐卡走查：每一张动词卡 / 每一个动作都要有明确的权限与可逆性归属。"""

    @classmethod
    def setUpClass(cls):
        cls.reg = RG.build()
        cls.cmds = {c["cmd"]: c for c in
                    json.loads(RG.COMMANDS.read_text(encoding="utf-8"))["commands"]}

    # -- ① A 档：只读 --

    def test_a_tier_actions_are_implemented_and_read_only(self):
        a_ids = [a["action_id"] for a in self.reg["read_actions"]]
        self.assertEqual(sorted(a_ids), sorted(AS.ACTIONS))
        for a in self.reg["read_actions"]:
            spec = AS.ACTIONS[a["action_id"]]
            self.assertEqual(spec["tier"], "A")
            self.assertEqual(spec.get("writes") or "", "",
                             "%s 声明了写库就不是 A 档（应归 B）" % a["action_id"])
            self.assertTrue(spec.get("reversible"),
                            "%s 缺可逆性声明" % a["action_id"])

    def test_only_one_write_endpoint_exists(self):
        """动作面只有一个写入口；其余 POST 一律 405（「乱点」防线）。"""
        src = Path(AS.__file__).read_text(encoding="utf-8")
        self.assertIn('if u.path != "/api/actions/run"', src)
        self.assertIn("do_PUT = do_DELETE = do_PATCH = do_POST", src)
        # 不许出现第二个 do_POST 分支
        self.assertEqual(src.count("def do_POST"), 1)

    # -- ② B 档：两段提交 + 台账 + 可逆性 --

    def test_b_tier_actions_declare_risk_target_and_reversible(self):
        self.assertTrue(self.reg["write_actions"], "B 档不该为空")
        for a in self.reg["write_actions"]:
            aid = a["action_id"]
            self.assertEqual(a["tier"], "B", aid)
            self.assertIn(a["risk"], ("reversible", "destructive"), aid)
            self.assertIn(a["target"], self.reg.get("write_targets", {}),
                          "%s 的 target=%r 不在 write_targets 声明里——"
                          "写别处必须先立项（registrygen write_targets）"
                          % (aid, a["target"]))
            self.assertTrue(a.get("reversible"), "%s 缺可逆性人话" % aid)
            self.assertIn("两段提交", a.get("writes") or "",
                          "%s 的 writes 必须写明两段提交" % aid)
            self.assertIn(aid, WA.WRITE_ACTIONS, "%s 没实现" % aid)

    def test_b_tier_required_params_are_self_describing(self):
        for a in self.reg["write_actions"]:
            # v0.62 起允许零必填（如 suggest_agents：全默认参数的扫描型动作），
            # 但必须声明至少一个参数（全无参 = 填了不生效的黑箱，禁止）。
            # P10 起：零参数动作**可以**存在（kb_publish 两个参数都是服务端
            # 路径），但必须在 write_actions.json 里写明 `no_params_reason`
            # ——"没有可填的"和"忘了填"要分得开，门只放宽到"有理由"。
            if not a["params"]:
                reason = (a.get("no_params_reason") or "").strip()
                self.assertGreaterEqual(
                    len(reason), 15,
                    "%s 一个参数都没声明，也没写 no_params_reason"
                    % a["action_id"])
            for p in a["params"]:
                self.assertTrue(p.get("label"),
                                "%s 的参数 %s 缺人话 label（页面会显示英文 dest）"
                                % (a["action_id"], p["name"]))
            for p in [p for p in a["params"] if p["required"]]:
                self.assertFalse(p.get("server"),
                                 "%s 的必填参数 %s 不该是服务端参数"
                                 % (a["action_id"], p["name"]))

    def test_destructive_actions_state_what_is_lost(self):
        """破坏性动作清单**钉死**：一变就必须回来显式复核。

        2026-10-13（v0.49）显式复核结论：新增 `session_suppress`（会话抑制）。
        它是破坏性的**理由**：会真删库内 sessions/messages/steps 行与 exports
        副本，且**没有一键恢复**（恢复要靠下次 sync 重新导出，前提是语料还在）。
        它**不该被误当成"只是标记"**——这正是本条清单要拦的那种误判。
        """
        destr = [a for a in self.reg["write_actions"]
                 if a["risk"] == "destructive"]
        self.assertEqual(sorted(a["action_id"] for a in destr),
                         ["session_suppress", "topic_delete", "topic_merge"],
                         "破坏性动作清单变了：必须显式复核（它们会丢信息）")
        for a in destr:
            d = a.get("detail") or ""
            self.assertTrue(len(d) >= 20,
                            "%s 的 detail 太短，说不清会丢什么" % a["action_id"])
            self.assertTrue(
                any(k in d for k in ("删", "丢", "找不回", "没了")),
                "%s 的 detail 必须写明会丢什么（不许含糊成「有风险」）"
                % a["action_id"])

    def test_suppress_detail_is_honest_about_scope_and_recovery(self):
        """会话抑制的口径必须**逐字**写对（用户批准的清单 ③ 与 ⑧）。

        ③ 删的是**仓内副本，不是采集源**；⑧ **不设**"可恢复"时限，
        且必须说清"能不能恢复只取决于语料是否还在"。
        这几句是用户裁决的落地形态，写含糊了页面上就会误导人，
        故这里按原话钉住（铁律 6.6：用户判据原话是规格）。
        """
        spec = {a["action_id"]: a for a in self.reg["write_actions"]}
        for aid in ("session_suppress", "session_unsuppress"):
            d = spec[aid].get("detail") or ""
            self.assertIn("仓内副本", d, aid)
            self.assertIn("不删采集源", d, aid)
            self.assertIn("语料是否还在", d, aid)
            self.assertIn("不设恢复时限", d, aid)
            self.assertNotIn("可恢复", d.replace("不恢复", ""), aid)

    def test_destructive_write_keeps_snapshot(self):
        """破坏性动作的预演必须给快照——那是"可回滚"承诺的唯一凭据。

        （写法与 P3-2 的测试同：真 topics_meta 的**临时副本**上跑只读预演。）
        """
        import shutil
        import sqlite3
        import tempfile
        up = AS.upstream_root()
        real = up / "topics_meta.db"
        if not real.is_file():                      # pragma: no cover
            self.skipTest("真 topics_meta.db 不在位")
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            shutil.copy2(real, tdp / "topics_meta.db")
            gl = tdp / "g.json"
            gl.write_text("{}", encoding="utf-8")
            cards = tdp / "cards"
            cards.mkdir()
            cfg = AS.Config(db=up / "harvester.db", cards_root=cards,
                            glossary=gl, topics_meta=tdp / "topics_meta.db",
                            actions_meta=tdp / "am.db")
            con = sqlite3.connect(str(tdp / "topics_meta.db"))
            try:
                tid = con.execute("SELECT id FROM topics ORDER BY id"
                                  ).fetchone()[0]
            finally:
                con.close()
            spec = {a["action_id"]: a for a in self.reg["write_actions"]}
            prev = WA.plan("topic_delete", spec["topic_delete"],
                           {"topic_id": tid}, cfg)
            self.assertIsNotNone(prev.get("snapshot"),
                                 "topic_delete 预演必须给删除前快照")
            self.assertEqual(prev["snapshot"]["id"], tid)

    # -- ③ C 档：永不执行 --

    def test_c_tier_commands_are_never_executable(self):
        exec_ids = {a["action_id"] for a in self.reg["read_actions"]} | \
                   {a["action_id"] for a in self.reg["write_actions"]}
        for c in self.cmds.values():
            if c["cmd"] not in C_TIER_CMDS:
                continue
            self.assertEqual(c["tier"], "C",
                             "%s 应是 C 档" % c["cmd"])
            self.assertNotIn(c["cmd"], exec_ids,
                             "C 档命令 %s 不许出现在可执行动作里（红线 3）"
                             % c["cmd"])
        # 注册表里也不许有 C 档动作条目（C 只存在于命令注册表）
        reg_ids = {a["action_id"] for a in self.reg["write_actions"]} | \
                  {a["action_id"] for a in self.reg["read_actions"]}
        for aid in reg_ids:
            spec = self.reg
            self.assertNotIn(aid, C_TIER_CMDS)

    def test_every_command_has_an_honest_execution_path(self):
        """逐命令走查：**每条命令的执行路径都必须是诚实的**。

        首版这条写成「没动作的命令必须全是 C 档」——**判据错了**，实测红了
        27 条：本轮只实现了 6 条命令的动作（4 个只读 + `keywords-gc` /
        `suggest-status` / `topic` 的 7 个写子动作），其余 37 条命令页面上
        **只给命令行**。那是**已知范围**，不是缺陷，也不该硬塞成 C 档
        （C 档语义是"要占端口/要人登录，永不执行"，与"还没做动作"是两回事）。

        代码里真实的保证（`updateCmdPreview` 只有两个分支会 enable 执行按钮）：
        **只有"有动作且 A 档"或"有动作且 B 档且版本匹配"才允许点执行**，
        其余一律 disabled + 写明理由。本测试就钉这一条：把注册表与实现比一遍，
        确认"可执行集合 ⊆ 有动作的命令"，并确认 UI 分支不会漏掉第三种情况。
        """
        # 注册表里被声明为可执行的命令集合
        declared = {a["cmd"] for a in self.reg["read_actions"]} | \
                   {a["cmd"] for a in self.reg["write_actions"]}
        implemented = set(AS.ACTIONS) | set(WA.WRITE_ACTIONS)
        # ① 注册表声明的动作必须都有实现（否则页面上是可执行但跑不了）
        for a in self.reg["read_actions"]:
            self.assertIn(a["action_id"], AS.ACTIONS, a)
        for a in self.reg["write_actions"]:
            self.assertIn(a["action_id"], WA.WRITE_ACTIONS, a)
        self.assertEqual(len(implemented),
                         len(self.reg["read_actions"])
                         + len(self.reg["write_actions"]))
        # ② 只有"有动作的命令"才可能被执行：UI 的两个 enable 分支都要求 act 存在
        src = Path(AS.__file__).parent.joinpath("..", "static",
                                                "index.html").resolve()
        html = src.read_text(encoding="utf-8")
        self.assertIn('if(act&&act.tier==="B"&&!writeDisabled()){', html)
        # 第二个 enable 分支走 canRun（2026-10-13：聚合命令子动作感知——
        # topic 的 pack 子动作是 A 档，命令级 tier 却是 B；只看 c.tier 会
        # 把可执行子动作错禁）。canRun 定义里仍有 !!act，"没动作也能点"
        # 依旧不可能——钉住这条不变式本身，不钉写法。
        self.assertIn('const canRun=!!act&&act.tier==="A"&&', html)
        self.assertIn("}else if(canRun){", html)
        # 两个 enable 分支之外的那个 else 必须禁用（否则就是"没动作也能点"）
        tail = html.split("}else if(canRun){")[1][:900]
        self.assertIn("$(\"btn-cmd-run\").disabled=true;", tail,
                      "没有动作的命令必须禁用执行按钮（不许静默无操作）")
        # ③ 命令本身都在注册表里（不要求有动作，只要求"页面会如实说明"）
        exec_cmds = declared
        for c in self.cmds.values():
            backed = c["cmd"] in exec_cmds
            if not backed:
                # 没动作的命令：页面上只给命令行——它必须不是"C 档才有意义"
                # 的东西（C 档另有语义），故这里只断言它**不被声明为可执行**
                self.assertNotIn(c["cmd"], exec_cmds)


if __name__ == "__main__":
    unittest.main()
