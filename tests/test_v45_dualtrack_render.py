# -*- coding: utf-8 -*-
"""v0.53 双轨时间线渲染门（P4-4；subprocess 跑 `tests/render_dualtrack.js`）。

钉 P4-4 验收三句的前端侧：「上/下轨同屏对齐；打磨带与 P4-3 候选一致；
反转点标记可点击看两轮原文对比」。渲染函数**真执行**（render_work 的
教训：只查源码字符串抓不到执行路径缺陷）；数据一致性与后端侧已在
test_v44_dualtrack 钉死（同源 revisions.collect，构造性一致）。

node 缺失时 skip（本地可手动跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tests" / "render_dualtrack.js"


class DualtrackRenderTest(unittest.TestCase):

    def test_render_dualtrack_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，双轨渲染请手动跑 tests/render_dualtrack.js")
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_dualtrack.js 失败：\n%s\n%s"
                         % (r.stdout, r.stderr))
        self.assertIn("ALL DUALTRACK CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 15, r.stdout)

    def test_gate_really_runs_functions_not_just_source(self):
        """元测试：门必须 extract 并执行函数（防门退化成 grep）。"""
        src = GATE.read_text(encoding="utf-8")
        self.assertIn("eval(extract(", src)
        self.assertIn("renderDualtrack", src)


if __name__ == "__main__":
    unittest.main()
