# -*- coding: utf-8 -*-
"""v0.63 白名单补全门：**commands.json 里每条 A 档命令都必须有可执行动作**。

背景（用户 2026-10-14，命令台截图红批注）：「还有哪些要加到白名单的命令
没有加啊？都加上」——当时 A 档命令 22 条只挂了 6 个动作，剩余的在页面上
只能复制命令。本轮全部补齐；本测试把「A 档全覆盖」钉成门，以后 commands.json
新增 A 档命令而动作面没跟上时**会红**（而不是又靠人眼发现）。

另钉两条安全性：
- `_chain_file` 只收 chain-root 下的**文件名**（拒绝路径成分）——不然
  「传个文件名」就成了「顺手指向任意文件」的读取后门；
- `_cli_report` 对 argparse 不认识的命令显式失败（ValueError），
  不让 usage 文本混进 stdout 冒充报告。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import commandgen as CG  # noqa: E402
from actions import registrygen as RG  # noqa: E402


class WhitelistCoversAllATier(unittest.TestCase):
    """A 档命令全覆盖：commands.json 说只读的，页面就必须能执行。"""

    @classmethod
    def setUpClass(cls):
        cls.reg = RG.build()
        cmds = json.loads(RG.COMMANDS.read_text(encoding="utf-8"))["commands"]
        cls.cmds = {c["cmd"]: c for c in cmds}

    def _read_targets(self) -> set:
        """read_actions 指向的 (cmd, subaction) 集合。"""
        out = set()
        for a in self.reg["read_actions"]:
            if a.get("cmd") is None:      # 视图侧分析动作（dualtrack）
                continue
            out.add((a["cmd"], a.get("subaction")))
        return out

    def test_every_a_tier_command_has_an_action(self):
        targets = self._read_targets()
        missing = []
        for name, c in self.cmds.items():
            if c["tier"] != "A":
                continue
            if (name, None) not in targets:
                missing.append(name)
        self.assertEqual(missing, [],
                         "A 档命令没有可执行动作（页面只能复制命令）：%s"
                         % sorted(missing))

    def test_every_a_tier_topic_subaction_has_an_action(self):
        targets = self._read_targets()
        # topic 的「list」由命令级动作 topic_list（("topic", None)）覆盖——
        # cmdActionMap 的命令级映射正是 list 侧（写子动作才逐条列卡）。
        targets.add(("topic", "list"))
        subs = CG.SUBACTION_TIER.get("topic", {})
        missing = [s for s, tier in subs.items()
                   if tier == "A" and ("topic", s) not in targets]
        self.assertEqual(missing, [],
                         "topic 的 A 档子动作没有可执行动作：%s" % sorted(missing))

    def test_new_actions_are_declared_read_only(self):
        for a in self.reg["read_actions"]:
            spec = AS.ACTIONS.get(a["action_id"])
            self.assertIsNotNone(spec, a["action_id"])
            self.assertEqual(spec.get("writes") or "", "",
                             "%s 声明了写库就不是 A 档" % a["action_id"])
            self.assertTrue(spec.get("reversible"), a["action_id"])


class ChainFileSafety(unittest.TestCase):
    """chain 文件参数只收文件名（路径成分一律拒绝）。"""

    class _Cfg:
        chain_root = Path("/tmp/chain-root")  # 形状够用；不真读盘

    def test_rejects_path_components(self):
        for bad in ("../evil.md", "a/b.md", "C:/x.md", "..", ".", "",
                    "chain.md/../x"):
            with self.assertRaises(Exception, msg=bad):
                AS._chain_file(self._Cfg(), bad)  # noqa: SLF001

    def test_missing_file_is_not_found(self):
        try:
            AS._chain_file(self._Cfg(), "no-such-chain.md")  # noqa: SLF001
        except Exception:
            pass  # 到达即通过（fail loud，报什么都行，就是不许静默返回）
        else:
            self.fail("不存在的 chain 文件应报错（fail loud）")


class CliReportSmoke(unittest.TestCase):
    """_cli_report 的失败路径：不认识的命令显式红，不冒充报告。"""

    def test_unknown_command_raises_valueerror(self):
        with self.assertRaises(ValueError):
            AS._cli_report(["definitely-not-a-command"])  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
