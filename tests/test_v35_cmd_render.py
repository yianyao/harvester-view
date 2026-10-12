# -*- coding: utf-8 -*-
"""P2-4 命令台渲染门（subprocess 跑 tests/render_cmd.js）。

为什么独立成门：index.html 是 92k 单文件，其余渲染门只 extract 它们各自
关心的函数——**新加的 JS 语法错了、或解引用了不存在的全局**，那些门照样
绿（P1 会话为此红过三轮）。本门 extract 命令台的全部函数真执行，并钉住
一条**已修的真缺陷**：`updateCmdPreview` 曾在"命令级 B 档且无对应动作"
（= 43 条命令里的绝大多数）时读 `undefined.est_sec` 整页抛错。

node 缺失时 skip（本地渲染冒烟仍可手动跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tests" / "render_cmd.js"


class CommandPaletteRender(unittest.TestCase):

    def test_render_cmd_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，命令台渲染请手动跑 "
                          "tests/render_cmd.js")
        # encoding 显式 utf-8：Windows 下 text=True 走 locale(GBK) 会导致
        # UnicodeDecodeError 且 stdout 为 None（test_v24 的同类踩坑）
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_cmd.js 失败：\n%s\n%s" % (r.stdout, r.stderr))
        self.assertIn("ALL COMMAND-PALETTE CHECKS PASSED", r.stdout)
        # 不许"跳过伪装成通过"：断言检查条数不低于门里实际写的数量
        self.assertGreaterEqual(r.stdout.count("ok - "), 20, r.stdout)


if __name__ == "__main__":
    unittest.main()
