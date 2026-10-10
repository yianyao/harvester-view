# -*- coding: utf-8 -*-
"""v0.33 跨仓库集成门：真实载荷渲染检查（subprocess 跑 tests/render_real.js）。

为什么单列一条：`render_smoke.js` 用的是**构造夹具**，它证明不了"后端真的会发
这个字段名"。这道门用真库现算的载荷（兄弟仓库 `session-harvester` 的
`harvester.db` + `topics_meta.db`）喂真实 index.html 里的渲染函数。

**未执行 ≠ 不适用 ≠ 通过**（项目 AGENTS.md §五 16）：
  - node 不在 PATH → skip（渲染冒烟仍可手动跑）；
  - 后端仓库/库不在（换机器、只 checkout 了 view）→ skip 并在理由里写明
    "未执行：…"（脚本退出码 3），**不当成通过**；
  - 渲染断言真的失败 → 测试失败。

原为后端仓库的 `docs/reports/check-view-real.py` + `view-real-render-check.js`
（一次性脚本）；它每改一次 view 都该跑，故 v0.33 移进本仓库。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "check_real_payload.py"


class RealPayloadRender(unittest.TestCase):

    def test_real_payload_render(self):
        if shutil.which("node") is None:
            self.skipTest("node 不在 PATH，真实载荷渲染请手动跑 "
                          "tests/check_real_payload.py")
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, cwd=str(ROOT))
        if r.returncode == 3:
            # 脚本自报"未执行"（后端仓库或库不在）——不是不适用，也不是通过
            self.skipTest("未执行：" + (r.stderr or r.stdout).strip())
        self.assertEqual(
            r.returncode, 0,
            f"真实载荷渲染检查失败（退出码 {r.returncode}）：\n{r.stdout}\n{r.stderr}")
        self.assertIn("ALL REAL RENDER CHECKS PASSED", r.stdout)
        # 断言"不适用"项被单独报出来，而不是被当通过吞掉
        self.assertIn("/ n/a ", r.stdout)


if __name__ == "__main__":
    unittest.main()
