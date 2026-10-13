# -*- coding: utf-8 -*-
"""P5-4 小白三问全站抽查（test_v52）。

小白三问：①这是什么（页级「怎么读」导语）②数据哪来/新鲜吗（口径标注 +
freshbar）③下一步做什么（空态引导 + 动作出口）。②③由 P5-2/P5-3 门钉死，
本门钉①的覆盖闭环：

  1. glossary panels 必须覆盖 index.html 中**全部** panelIntro 挂点 key
     （挂点无数据 = 导语静默为空，白挂）。
  2. 已知缺口补齐：home（挂点在、数据缺）、reports（结论卡承诺
     「见怎么读」但页面无实体）、cmd（整页无导语）。
  3. 三问留痕断言：全站「口径/真源」标注在位、freshbar 在位、
     结论卡 sub 指向的「怎么读」真实可达。
"""
import json
import re
import unittest
from pathlib import Path

VIEW = Path(__file__).resolve().parents[1]
IDX = VIEW / "static" / "index.html"
GLO = VIEW / "static" / "glossary.json"


class TestP54NoviceThreeQuestions(unittest.TestCase):
    def setUp(self):
        self.src = IDX.read_text(encoding="utf-8")
        self.glo = json.loads(GLO.read_text(encoding="utf-8"))
        self.panel_keys = {p.get("panel") for p in self.glo.get("panels", [])}

    def test_every_intro_hook_has_data(self):
        """全部 panelIntro("key") 挂点必须在 glossary.panels 里有数据。"""
        hooks = set(re.findall(r'panelIntro\("([^"]+)"\)', self.src))
        self.assertTrue(hooks, "panelIntro 挂点全丢了？")
        missing = hooks - self.panel_keys
        self.assertEqual(
            missing, set(),
            "挂点无导语数据（导语静默为空）：%s —— 补 glossary.json panels" % sorted(missing))

    def test_home_panel_exists(self):
        """首页导语：panelIntro("home") 挂点早已在，glossary 必须有 home 条目。"""
        self.assertIn("home", self.panel_keys, "glossary 缺 home 面板导语")
        intro = next(p["intro"] for p in self.glo["panels"] if p["panel"] == "home")
        self.assertTrue(intro.strip())
        self.assertIn("四队列", intro, "首页导语应覆盖四队列（小白第①问）")

    def test_cmd_panel_exists(self):
        """命令台导语：小白第③问的出口页，档位话术必须与 tour/确认流一致。"""
        self.assertIn("cmd", self.panel_keys, "glossary 缺 cmd 面板导语")
        intro = next(p["intro"] for p in self.glo["panels"] if p["panel"] == "cmd")
        for kw in ("A 档", "B 档", "预演", "C 档"):
            self.assertIn(kw, intro, "命令台导语缺档位话术关键词：%s" % kw)

    def test_reports_intro_hook(self):
        """报告页导语：结论卡 sub 承诺「见怎么读」，页面必须有实体挂点。"""
        ok = 'id="rep-intro"' in self.src
        self.assertTrue(ok, "reports 页缺导语容器 rep-intro")
        # loadReport 内按当前 kind 挂导语（glossary key=tools/errors/agents/skills/cards）
        ok2 = bool(re.search(r'panelIntro\((?:kind|state\.repKind)\)', self.src))
        self.assertTrue(ok2, "loadReport 未按报告类别挂 panelIntro")
        for k in ("tools", "errors", "agents", "skills", "cards"):
            self.assertIn(k, self.panel_keys, "glossary 缺报告导语：%s" % k)

    def test_freshbar_and_calibration_intact(self):
        """小白第②问留痕：freshbar 常驻 + 口径标注在位（防回归）。"""
        self.assertIn('id="freshbar"', self.src)
        self.assertIn("真源", self.src, "口径/真源标注丢失")

    def test_next_step_hints_intact(self):
        """小白第③问留痕：空态引导与档位出口在位（防回归）。"""
        self.assertIn("去命令台", self.src)
        self.assertIn("tierLabel", self.src)
        self.assertIn("预演（先看清楚，再确认才写）", self.src)


if __name__ == "__main__":
    unittest.main()
