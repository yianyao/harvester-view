# -*- coding: utf-8 -*-
"""启动序列真跑门（subprocess 跑 tests/boot_sequence.js）。

**为什么独立成门**：其它渲染门只 extract **渲染函数**喂夹具，所以
"启动序列把加载函数挡在守卫外面"这类缺陷它们抓不到——2026-10-12 用户实测撞到的
正是这个：`state.loaded.home=true; switchTab("home");` 顺序反了 →
`switchTab` 里 `if(!state.loaded[name])` 判定为已加载 → `loadOverview()` 永不执行
→ 工作台永远停在「加载中…」。本门真执行 index.html 的启动块并断言加载函数被调用。

node 缺失时 skip（不是"通过"）。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "tests" / "boot_sequence.js"


class BootSequence(unittest.TestCase):

    def test_boot_sequence_js(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，启动序列门请手动跑 "
                          "tests/boot_sequence.js")
        r = subprocess.run([node, str(GATE)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "boot_sequence.js 失败：\n%s\n%s" % (r.stdout, r.stderr))
        self.assertIn("ALL BOOT-SEQUENCE CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 3, r.stdout)


if __name__ == "__main__":
    unittest.main()
