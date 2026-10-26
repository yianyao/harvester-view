# -*- coding: utf-8 -*-
"""P2 #12 页面接线：个人成长总线（按月）从注册表到渲染的整条线。

为什么单测这一层：数字算法在上游 `test_v84_growth`，而这里要防的是
**"命令做了、页面没接"**——注册表里没有 / 下拉里没有 / 渲染函数没挂，
三种都是页面静默空白，跑起来不报错，人只会以为"这功能没做"。

一条真跑：进程内调 A 档动作 `report_growth`（真 CLI、真库），确认机读结构
`schema=growth/1` 且 `months[]` 非空。真库不在时按 H9 跳过（不可用≠失败）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402

UP = AS.upstream_root()
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
REG = json.loads((ROOT / "actions" / "actions_registry.json").read_text("utf-8"))
CMDS = json.loads((ROOT / "actions" / "commands.json").read_text("utf-8"))
GLO = json.loads((ROOT / "static" / "glossary.json").read_text("utf-8"))


class GrowthRegistryTest(unittest.TestCase):
    def test_action_registered_as_read_only(self):
        a = {x["action_id"]: x for x in REG["read_actions"]}
        self.assertIn("report_growth", a, "A 档注册表里没有 report_growth")
        self.assertEqual(a["report_growth"]["cmd"], "report-growth")
        self.assertEqual(a["report_growth"]["tier"], "A")
        # A 档不许有写目标（与 test_v38/test_v54 同一纪律，这里就近再钉一次）
        spec = AS.ACTIONS["report_growth"]
        self.assertEqual(spec["writes"], "", "只读动作不许声明 writes")
        self.assertGreaterEqual(int(spec["est_sec"]), 1)

    def test_command_present_in_commands_json(self):
        names = [c["cmd"] for c in CMDS["commands"]]
        self.assertIn("report-growth", names)
        cmd = next(c for c in CMDS["commands"] if c["cmd"] == "report-growth")
        self.assertIn("months", [a["dest"] for a in cmd["args"]])


class GrowthPageTest(unittest.TestCase):
    def test_dropdown_option(self):
        self.assertIn('<option value="growth">个人成长总线', HTML)

    def test_dispatch_and_renderers(self):
        self.assertIn('kind==="growth"', HTML)
        self.assertIn("runGrowthReport", HTML)
        self.assertIn("function renderGrowth", HTML)
        self.assertIn('renderGrowth(d,out)', HTML)

    def test_meta_line_declares_bucket_not_window(self):
        # 这档按月分桶，"窗口（天）"对它无效——必须明说，否则是静默口径欺骗
        self.assertIn("按月分桶", HTML)

    def test_panel_intro_exists(self):
        hit = [p for p in GLO["panels"] if p["panel"] == "growth"]
        self.assertTrue(hit, "glossary 缺 growth 导语（结论卡会空着）")
        self.assertIn("趋势", hit[0]["intro"])


@unittest.skipUnless((UP / "harvester.db").is_file(), "需要真索引库（H9）")
class GrowthActionRunTest(unittest.TestCase):
    def test_action_returns_machine_readable_months(self):
        cfg = AS.Config(db=UP / "harvester.db",
                        cards_root=UP / "knowledge" / "cards",
                        glossary=ROOT / "static" / "glossary.json",
                        topics_meta=UP / "topics_meta.db",
                        suggestions_meta=UP / "suggestions_meta.db",
                        registry_json=ROOT / "actions" / "actions_registry.json")
        r = AS.ACTIONS["report_growth"]["handler"](cfg, {"months": 3})
        self.assertEqual(r["exit_code"], 0, "真 CLI 退出码非 0：%s" % r.get("stderr"))
        j = r.get("json") or {}
        self.assertEqual(j.get("schema"), "growth/1")
        d = j["data"]
        ms = d["months"]
        self.assertTrue(1 <= len(ms) <= 3, "months 参数没生效：%s" % len(ms))
        for m in ms:
            for k in ("month", "sessions", "steps", "errors", "err_per_session",
                      "active_topics", "new_cards", "rules_adopted", "thin"):
                self.assertIn(k, m)
        # 人读文本同时要在（--json 不许把 stdout 让给 JSON，那是回归）
        self.assertIn("个人成长总线", r["stdout"])


if __name__ == "__main__":
    unittest.main()
