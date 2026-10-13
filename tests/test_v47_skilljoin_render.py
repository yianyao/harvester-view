# -*- coding: utf-8 -*-
"""v0.55 skill 调用点挂轨道渲染门（P4-5；subprocess 跑 tests/render_skilljoin.js）。

验收「pdf-text-extractor 案例在时间线上可见调用点与成败」的**前端侧**：
挂轨道工具行、每阶段 sk-line、✅/❌/⚠ 成败可视、margin 区虚线标记、
汇总行对账数字、chip 跳转钩子。渲染函数真执行（render_work 纪律）；
数据面与端点在 test_v46_skilljoin 钉死（上游 build_cross 直通）。

node 缺失时 skip（本地可手动跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tests" / "render_skilljoin.js"


class SkillJoinRenderTest(unittest.TestCase):

    def test_render_skilljoin_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，挂轨道渲染请手动跑 tests/render_skilljoin.js")
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_skilljoin.js 失败：\n%s\n%s"
                         % (r.stdout, r.stderr))
        self.assertIn("ALL SKILLJOIN CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 15, r.stdout)

    def test_gate_really_runs_functions_not_just_source(self):
        """元测试：门必须 extract 并执行函数（防门退化成 grep）。"""
        src = GATE.read_text(encoding="utf-8")
        self.assertIn("eval(extract(", src)
        self.assertIn("renderChain", src)


if __name__ == "__main__":
    unittest.main()
