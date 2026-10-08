# -*- coding: utf-8 -*-
"""v0.24 渲染层冒烟测试（P0-2/P0-3）：subprocess 跑 tests/render_smoke.js。

H22 教训（G4 过滤器引用错变量永不生效）：过滤器/渲染接线类改动必须有
"过滤真的能滤"断言——render_smoke.js 从 index.html 提取真实函数源码执行，
断言 roots 优先渲染、关键字过滤行数变化、meta 含"已过滤"。
node 缺失时 skip（本地渲染冒烟仍可手动跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "render_smoke.js"


class RenderSmoke(unittest.TestCase):

    def test_render_smoke_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，渲染冒烟请手动跑 tests/render_smoke.js")
        r = subprocess.run([node, str(SMOKE)], capture_output=True,
                           text=True, timeout=30, cwd=str(ROOT))
        self.assertEqual(
            r.returncode, 0,
            f"render_smoke.js 失败：\n{r.stdout}\n{r.stderr}")
        self.assertIn("ALL RENDER CHECKS PASSED", r.stdout)


if __name__ == "__main__":
    unittest.main()
