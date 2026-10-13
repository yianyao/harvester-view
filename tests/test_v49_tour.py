# -*- coding: utf-8 -*-
"""P5-1 五页引导「5 分钟之旅」门（subprocess 跑 tests/render_tour.js + 结构断言）。

验收原文（TODO P5-1）：新手未读文档能跟随五页完成一次完整闭环浏览。
本门钉住：① 五页 tab 序列 = 闭环动线（工作台→报告→会话→处理单→命令台）；
② 故事线覆盖「Edit 前先 Read」真实班规闭环；③ 红线——引导只导航不执行
（源码切片无 fetch / api/actions / copyText / runCmdAction）；
④ 启动块挂 maybeAutoTour（首次访问自动开，seen 过不再弹）。

node 缺失时渲染门 skip（可手动跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "static" / "index.html"
GATE = ROOT / "tests" / "render_tour.js"


def tour_section() -> str:
    src = HTML.read_text(encoding="utf-8")
    i = src.index("===== P5-1 五页引导（tour） =====")
    j = src.index("===== P5-1 tour end =====", i)
    return src[i:j]


class TourStructure(unittest.TestCase):

    def test_tour_container_and_entry_button_exist(self):
        src = HTML.read_text(encoding="utf-8")
        self.assertIn('id="tour"', src, "缺引导卡片容器 #tour")
        self.assertIn('id="btn-tour"', src, "缺入口按钮 btn-tour")
        self.assertIn("5 分钟之旅", src)

    def test_tour_red_line_no_execution(self):
        sec = tour_section()
        for banned in ("fetch(", "api/actions", "copyText", "runCmdAction",
                       "XMLHttpRequest"):
            self.assertNotIn(banned, sec,
                             "引导切片出现禁用词「%s」——引导只导航不执行" % banned)

    def test_boot_block_has_auto_tour(self):
        src = HTML.read_text(encoding="utf-8")
        i = src.index("initUpstreams().then(")
        j = src.index("}).catch(", i)
        boot = src[i:j]
        self.assertIn("maybeAutoTour()", boot)
        self.assertGreater(boot.index("maybeAutoTour()"),
                           boot.index('switchTab("home")'),
                           "maybeAutoTour 必须在 switchTab(home) 之后")


class TourRenderGate(unittest.TestCase):

    def test_render_tour_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，引导渲染请手动跑 tests/render_tour.js")
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_tour.js 失败：\n%s\n%s" % (r.stdout, r.stderr))
        self.assertIn("ALL TOUR CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 20, r.stdout)


if __name__ == "__main__":
    unittest.main()
