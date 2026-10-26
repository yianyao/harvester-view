# -*- coding: utf-8 -*-
"""P2 #13（2026-10-26）：中台 tab 降格 + 三处"错误模式"入口互相指路。

两件事都不改数据、只改"能不能找到"，所以门也只能盯结构（源码级）——
但必须盯，因为这类改动的坏法就是**悄悄回退**：有人再加一个平级 tab、
或者把互指链接删了，页面照样能跑，只是用户又找不到路。

判据：
1. 中台**不在** `#tabs` 的平级按钮里（降格），但**页面与路由都还在**
   （`id="tab-agg"` + 顶栏 `id="btn-agg"` + switchTab 仍认 "agg"）；
2. 三处入口各有指向别人的链接：报告·错误（全库口径）、主题·研读面（按主题切片）、
   处理单（按该不该修排队）；
3. 链接用**全局事件委托**接线（静态页里那段和渲染出来的那些走同一处），
   否则新加一处就漏绑，表现为"点了没反应"——最难发现的一种坏法。
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _nav_block() -> str:
    """截出 <nav id="tabs"> … </nav>，避免把正文里同名串误算成 tab。"""
    i = HTML.index('<nav id="tabs">')
    j = HTML.index("</nav>", i)
    return HTML[i:j]


class TestAggDemoted(unittest.TestCase):
    def test_not_a_peer_tab_anymore(self):
        nav = _nav_block()
        self.assertNotIn('data-tab="agg"', nav,
                         "中台又变成平级 tab 了（P2 #13 已降格成顶栏按钮）")
        self.assertIn('data-tab="topics"', nav)   # 主线 tab 仍在
        self.assertIn('data-tab="cmd"', nav)

    def test_page_and_route_kept(self):
        # 降格 ≠ 删掉：页面、入口按钮、路由分支三样都必须在
        self.assertIn('id="tab-agg"', HTML)
        self.assertIn('id="btn-agg"', HTML)
        self.assertIn('$("btn-agg").onclick=()=>switchTab("agg")', HTML)
        self.assertIn('if(name==="agg"){ loadAggFacets()', HTML)

    def test_button_lights_up(self):
        # 它不在 #tabs 里，不单独点亮的话进这页整条 tab 栏没有一个高亮
        self.assertIn('ba.classList.toggle("on",name==="agg")', HTML)

    def test_cross_links_delegate_bound(self):
        self.assertIn('closest("a[data-goto-reports],a[data-goto-tab]")', HTML)


class TestErrorEntrancesCrossLinked(unittest.TestCase):
    """三处入口：报告·错误 / 主题·研读面 / 处理单。"""

    def test_reports_points_to_other_two(self):
        # 报告·错误 顶部那段说明里两个外链都要有
        i = HTML.index("这页是<b>全库口径</b>")
        seg = HTML[i:i + 900]
        self.assertIn("data-goto-tab='topics'", seg)
        self.assertIn("data-goto-tab='work'", seg)
        self.assertIn("口径不同，不是账错", seg)

    def test_brief_points_to_other_two(self):
        i = HTML.index("这一节只数<b>该主题成员会话</b>里的错误")
        seg = HTML[i:i + 700]
        self.assertIn("data-goto-reports='errors'", seg)
        self.assertIn("data-goto-tab='work'", seg)

    def test_work_points_to_other_two(self):
        i = HTML.index("同一个坑在三个页面各有口径")
        seg = HTML[i:i + 900]
        self.assertIn('data-goto-reports="errors"', seg)
        self.assertIn('data-goto-tab="topics"', seg)

    def test_goto_reports_switches_kind_then_loads(self):
        # 只切 tab 不拨 kind 的话，用户看到的是上次那一档报告（容易以为没生效）
        i = HTML.index("function gotoReports(kind){")
        seg = HTML[i:i + 400]
        self.assertIn('$("rep-kind")', seg)
        self.assertIn("loadReport()", seg)


if __name__ == "__main__":
    unittest.main()
