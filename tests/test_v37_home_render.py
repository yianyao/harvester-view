# -*- coding: utf-8 -*-
"""P3-3 今日工作台渲染门（subprocess 跑 tests/render_home.js）。

为什么独立成门：工作台的纪律是「**只渲染服务端给的数，不二次聚合**」。
页面里再算一遍就会出现两套数字，而"页面算错/漏算"在别的门里看不到
（它们只 extract 各自关心的渲染函数）。本门 extract 工作台的渲染函数真执行，
钉住：口径与真源必须逐条显示、数字原样透出、队列深链送到对应页签。

node 缺失时 skip（本地渲染冒烟仍可手动跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tests" / "render_home.js"


class HomeDashboardRender(unittest.TestCase):

    def test_render_home_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，工作台渲染请手动跑 tests/render_home.js")
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_home.js 失败：\n%s\n%s" % (r.stdout, r.stderr))
        self.assertIn("ALL HOME-DASHBOARD CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 15, r.stdout)


if __name__ == "__main__":
    unittest.main()
