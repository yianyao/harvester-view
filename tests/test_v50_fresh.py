# -*- coding: utf-8 -*-
"""P5-2 三身份视觉 + 数据新鲜度常驻条（subprocess 跑 tests/render_fresh.js + 结构断言）。

验收原文（TODO P5-2）：机械事实/AI 判断/人的裁决三种块肉眼可分；新鲜度条
随指纹轮询变色（灰→黄）。

结构断言钉住：① #freshbar 常驻容器 + 轮询接线（setInterval）存在；
② 新鲜度条只消费 /api/meta 只读指纹（切片无动作面请求——红线）；
③ 启动块挂 pollFreshBar；④ CSS 三身份边线配色全走 var。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "static" / "index.html"
GATE = ROOT / "tests" / "render_fresh.js"


def p52_section() -> str:
    src = HTML.read_text(encoding="utf-8")
    i = src.index("===== P5-2 三身份与新鲜度（provenance+freshbar） =====")
    j = src.index("===== P5-2 provenance end =====", i)
    return src[i:j]


class FreshStructure(unittest.TestCase):

    def test_freshbar_container_and_poll_wiring(self):
        src = HTML.read_text(encoding="utf-8")
        self.assertIn('id="freshbar"', src, "缺常驻新鲜度条容器 #freshbar")
        sec = p52_section()
        self.assertIn("setInterval", src, "缺指纹轮询接线（setInterval）")
        self.assertIn("/api/meta", sec, "新鲜度轮询应消费 /api/meta 只读指纹")

    def test_freshbar_red_line_no_actions(self):
        sec = p52_section()
        for banned in ("api/actions", "copyText", "runCmdAction",
                       "XMLHttpRequest"):
            self.assertNotIn(banned, sec,
                             "P5-2 切片出现禁用词「%s」——只读指纹不执行" % banned)

    def test_boot_polls_freshness(self):
        src = HTML.read_text(encoding="utf-8")
        i = src.index("initUpstreams().then(")
        j = src.index("}).catch(", i)
        boot = src[i:j]
        self.assertIn("pollFreshBar()", boot,
                      "启动块应拉一次指纹（灰态基线）")

    def test_css_provenance_uses_vars_only(self):
        src = HTML.read_text(encoding="utf-8")
        for cls in (".p-fact", ".p-ai", ".p-human"):
            m = re.search(re.escape(cls) + r"\{[^}]*\}", src)
            self.assertIsNotNone(m, "缺 CSS 类 " + cls)
            self.assertNotIn("#", m.group(0).replace("var(--", "V"),
                             cls + " 里出现写死色值（应全走 var）")


class FreshRenderGate(unittest.TestCase):

    def test_render_fresh_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，请手动跑 tests/render_fresh.js")
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_fresh.js 失败：\n%s\n%s" % (r.stdout, r.stderr))
        self.assertIn("ALL FRESH/PROVENANCE CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 20, r.stdout)


if __name__ == "__main__":
    unittest.main()
