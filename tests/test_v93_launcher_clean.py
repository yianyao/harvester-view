# -*- coding: utf-8 -*-
"""启动器一致性与清场门（2026-10-26 用户令）。

**报障原话**：「如果是因为截图这个原因，那桌面端，包括包里的三个启动
脚本，都要做到一件事：关闭时都关闭，启动时都启动。或者换句话说，要保证
启动桌面端（或通过脚本启动），所有配置和服务都是最新的」。

截图里的报错是 `动作参数类型未定义：group=enum`，尾巴还写着「若刚升级
代码…两个窗口都要关掉重启」。当天实测主机上**同时跑着两整套服务**
（两个 api-serve + 两个 actions-serve + 两个 view，pid 各不同）——页面
连到哪一套全看谁先绑上端口，所以"时好时坏"，重启"一个窗口"根本不够。
那句提示把「实现缺了一个类型」误导向「你要重启」，两条都得修：类型实现
本门不管（见 test_v92），本门钉**启动器不许让旧进程活过启动这一瞬**。

钉六件事：
1. 三个启动脚本都在起服务**之前** `call reclaim.cmd`（顺序反了等于没清）；
2. `stop.cmd` 存在并复用同一把清场刀（一个入口停全部，不靠人关三个窗口）；
3. 清场判据与 `desktop/main.js` 同源：三个特征词都在、都限定 python/pythonw、
   都不许碰 node/electron；
4. `view.py` 判据必须带**边界**——否则 `test_view.py` / `render_view.py`
   这类测试脚本会被当成视图服务杀掉；
5. 走 `taskkill /T /F`（树杀）；
6. 这些 .cmd 必须纯 ASCII + CRLF（cmd.exe 按 ANSI 码页解析）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHERS = ("start-viewv2.cmd", "start.cmd", "start-system.cmd")


class LaunchersClearBeforeStart(unittest.TestCase):
    def test_each_launcher_calls_reclaim_before_starting(self):
        self.assertTrue((ROOT / "reclaim.cmd").is_file(),
                        "缺 reclaim.cmd——启动器没有统一的清场刀")
        for name in LAUNCHERS:
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            call_at = [i for i, l in enumerate(lines)
                       if 'call "%~dp0reclaim.cmd"' in l]
            self.assertTrue(call_at, "%s 没有 call reclaim.cmd——"
                                     "上一轮留下的旧进程会活着占端口" % name)
            start_at = [i for i, l in enumerate(lines)
                        if l.strip().startswith('start "harvester')]
            self.assertTrue(start_at, "%s 里找不到 start \"harvester 行" % name)
            self.assertLess(call_at[0], start_at[0],
                            "%s 先起服务再清场——顺序反了等于没清（新起的"
                            "服务会被自己刚清掉，或者旧服务继续占着端口）"
                            % name)

    def test_stop_script_reuses_the_same_knife(self):
        p = ROOT / "stop.cmd"
        self.assertTrue(p.is_file(), "缺 stop.cmd：三个服务各占一个窗口，"
                                     "关启动器窗口不会停它们，必须有一个"
                                     "「一键全停」的入口")
        text = p.read_text(encoding="utf-8", errors="replace")
        self.assertIn('call "%~dp0reclaim.cmd"', text,
                      "stop.cmd 自己另写一套杀进程规则——两套判据迟早分叉，"
                      "总有一类残留谁都停不掉")

    def test_launchers_tell_the_truth_about_closing(self):
        """不许再说「关掉三个窗口就行」——那是要人记三个窗口的旧办法。"""
        for name in LAUNCHERS:
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
            self.assertIn("stop.cmd", text,
                          "%s 没告诉人怎么一键全停" % name)


class ReclaimMatchesDesktopShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cmd = (ROOT / "reclaim.cmd").read_text(encoding="utf-8",
                                                   errors="replace")
        js = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8",
                                                      errors="replace")
        m = re.search(r"function ourProc[\s\S]*?\n}", js)
        assert m, "main.js 里找不到 ourProc"
        cls.js_body = m.group(0)

    def test_same_three_needles(self):
        """三种起法两边都要认。

        写法不同（main.js 用 `-m\\s+harvester` 正则、.cmd 用字面空格、
        `view\\.py` 在两边都是正则写法），所以按**语义**比，不按逐字比。
        """
        pats = (("api-serve 子命令", r"-m.{0,4}harvester"),
                ("动作面", r"actionsserve"),
                ("视图", r"view\\\.py"))
        for where, text in (("reclaim.cmd", self.cmd),
                            ("desktop/main.js", self.js_body)):
            for label, pat in pats:
                self.assertRegex(text, pat,
                                 "%s 的判据漏了 %s 这种起法" % (where, label))

    def test_only_python_never_node(self):
        for where, text in (("reclaim.cmd", self.cmd),
                            ("main.js", self.js_body)):
            self.assertRegex(text, r"python\|pythonw",
                             "%s 没限定 python/pythonw" % where)
            self.assertNotRegex(text, r"node\|electron",
                                "%s 的判据里出现 node/electron——桌面壳自己"
                                "是 electron，杀到自己会崩窗口" % where)

    def test_view_needle_has_a_boundary(self):
        """`view.py` 必须带边界：`test_view.py` 也在命令行里，误杀测试进程。"""
        self.assertIn(r"(^|[\\/\s])view\.py", self.cmd,
                      "reclaim.cmd 的 view.py 判据没有边界——`python -m "
                      "unittest test_view.py` 会被误判成视图服务")
        self.assertIn(r"(^|[\/\s])view\.py", self.js_body,
                      "main.js 的 view.py 判据没有边界（同 reclaim.cmd）")

    def test_tree_kill(self):
        self.assertIn("/T", self.cmd, "reclaim.cmd 没有树杀（/T）")
        self.assertIn("/F", self.cmd)


class LauncherFilesAreDosSafe(unittest.TestCase):
    def test_ascii_and_crlf(self):
        for name in ("reclaim.cmd", "stop.cmd") + LAUNCHERS:
            raw = (ROOT / name).read_bytes()
            self.assertNotIn(b"\r\r", raw)
            text = raw.decode("utf-8")
            self.assertTrue(text.isascii(),
                            "%s 含非 ASCII 字符——cmd 按 GBK 解析会乱码并"
                            "把注释当命令跑" % name)
            self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"),
                             "%s 不是全 CRLF（混行会让 cmd 的行解析出错）"
                             % name)


if __name__ == "__main__":
    unittest.main()
