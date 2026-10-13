# -*- coding: utf-8 -*-
"""v0.49 处理单渲染门（subprocess 跑 `tests/render_work.js`）。

为什么独立成门：处理单的纪律是「**只渲染服务端给的那一批** + 逐条给建议 +
命令包可复制可送命令台」。渲染函数自己算一遍或筛一遍，就会出现两套清单
（用户 2026-10-13 报的"明细对不上"正是这么来的），而这类缺陷在只查源码字符串的
门里看不到——故本门 extract 那些函数**真执行**，并把"对账断了要响亮报警"也钉住。

node 缺失时 skip（本地渲染冒烟仍可手动跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tests" / "render_work.js"


class WorkItemRenderTest(unittest.TestCase):

    def test_render_work_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，处理单渲染请手动跑 tests/render_work.js")
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_work.js 失败：\n%s\n%s" % (r.stdout, r.stderr))
        self.assertIn("ALL WORK-ITEM CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 25, r.stdout)

    def test_gate_really_runs_functions_not_just_source(self):
        """元测试：门必须 extract 并**执行**函数。

        反例防线（SOP §5 第 5 条）：如果门退化成只 grep 源码字符串，
        它照样会打印"ALL ... PASSED"却什么都验不了。故这里要求门里
        出现 `eval(extract(` 与至少四个被测函数名。
        """
        src = GATE.read_text(encoding="utf-8")
        self.assertIn("eval(extract(", src)
        for fn in ("renderWorkHead", "workRows", "renderWorkPkg",
                   "renderWorkItems", "openInCmd"):
            self.assertIn('"%s"' % fn, src, "门没有覆盖 %s" % fn)


if __name__ == "__main__":
    unittest.main()
