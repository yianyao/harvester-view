# -*- coding: utf-8 -*-
"""v0.67 渲染卫生门（2026-10-15，用户三截图批注的回归钉）。

抓三类已修缺陷：
① 「</html> 之后残留 JS 注释」——浏览器把尾随内容当正文渲染，
   页面顶部漏出 `/* ===== P5-3 空态与确认流统一 ===== */`（用户批注：
   别叫空态，啥叫确认流——它根本不该被看见）。
② 命令行裸奔无悬停说明（用户批注：命令最好有个悬停提示其作用，
   所有命令、所有页面）——处理单两处命令框必须带 title。
③ 处理单/报告页给的动作 id 若不在动作注册表里，页面会给「在命令台
   打开」按钮而命令台无动作可跑（用户批注：玩人哪）——数据面审计：
   workitems.py 引用的 action_id 必须 ⊆ 注册表。
"""
import json
import re
import unittest
from pathlib import Path

VIEW_ROOT = Path(__file__).resolve().parent.parent
INDEX = VIEW_ROOT / "static" / "index.html"
REG = VIEW_ROOT / "actions" / "actions_registry.json"
WORKITEMS = VIEW_ROOT / "actions" / "workitems.py"


class TestHtmlTailClean(unittest.TestCase):
    """</html> 之后不许有任何非空白内容（浏览器会把它当正文渲染）。"""

    def test_tail_after_last_html_close_is_whitespace_only(self):
        html = INDEX.read_text(encoding="utf-8")
        tail = html[html.rindex("</html>") + len("</html>"):]
        self.assertEqual(tail.strip(), "",
                         "</html> 之后有残留内容（会漏到页面正文）: %r" % tail[:120])


class TestCommandHoverTitle(unittest.TestCase):
    """命令行不许裸奔：处理单两处命令框必须带悬停说明。"""

    def setUp(self):
        self.html = INDEX.read_text(encoding="utf-8")

    def test_work_pkg_and_item_cmd_boxes_have_title(self):
        self.assertIn("class='wi-cmd' title=", self.html)
        # 不许再有无 title 的裸命令框
        self.assertNotRegex(self.html, r"class='wi-cmd'>")

    def test_hover_title_helper_exists_and_covers_usage(self):
        self.assertIn("function cmdHoverTitle(row)", self.html)
        self.assertIn("这条命令干嘛用：", self.html)
        self.assertIn("复制到仓根目录", self.html)

    def test_cmd_preview_and_write_panel_have_tooltip(self):
        # 命令台预览行与写动作面板也要有解释（不仅处理单）
        self.assertIn("命令行预览（与真实 CLI 同形，供核对参数", self.html)
        self.assertIn("写动作不显示命令行预览", self.html)

    def test_source_line_has_plain_tooltip(self):
        # 真源机器键行必须自带白话解释（用户批注：这一行是啥意思得说明白）
        self.assertIn("真源=这页数字从哪本账现算出来的标记", self.html)


class TestActionIdFormsResolved(unittest.TestCase):
    """双形态选中解析：runCmdAction 不许再报「没有已实现的动作」。"""

    def setUp(self):
        self.html = INDEX.read_text(encoding="utf-8")

    def test_old_misleading_banner_gone(self):
        # 旧横幅调用必须消失（注释里的历史记述不算——只钉可执行代码）
        self.assertNotIn('showBanner("warn","这条命令没有已实现的动作")',
                         self.html)
        self.assertNotIn('label:c.cmd};', self.html)

    def test_resolve_action_sel_exists_and_used(self):
        self.assertIn("function resolveActionSel()", self.html)
        # 三处消费方都走统一解析
        for fn in ("async function runCmdAction(){",
                   "function updateCmdPreview(){",
                   "function renderCmdParams(){"):
            self.assertIn(fn, self.html)
        self.assertEqual(self.html.count("resolveActionSel()"), 4,
                         "定义 1 处 + 消费 3 处，漏一处就是又回到单形态判断")

    def test_pinned_invariants_still_hold(self):
        # test_v38 钉的两条 enable 分支不变式必须原样保留
        self.assertIn('if(act&&act.tier==="B"&&!writeDisabled()){', self.html)
        self.assertIn('const canRun=!!act&&act.tier==="A"&&', self.html)


class TestWorkitemsActionIdsRegistered(unittest.TestCase):
    """处理单给的 action_id 必须都在动作注册表里——否则页面给
    「在命令台打开」按钮、命令台却无动作可跑（数据面的「玩人哪」）。"""

    def test_all_workitems_action_ids_in_registry(self):
        src = WORKITEMS.read_text(encoding="utf-8")
        used = set(re.findall(r'action_id="([a-z_]+)"', src))
        reg = json.loads(REG.read_text(encoding="utf-8"))
        known = {a["action_id"] for a in
                 reg["read_actions"] + reg["write_actions"]}
        self.assertTrue(used, "workitems.py 里应至少引用一个 action_id")
        self.assertFalse(used - known,
                         "注册表缺这些动作（页面按钮会打脸）: %s" % (used - known))


class TestEnvprobeAndCopyButtons(unittest.TestCase):
    """环境探测命令形 + 一键复制（2026-10-15 用户批注三项的收口门）。"""

    def setUp(self):
        self.html = INDEX.read_text(encoding="utf-8")

    def test_envprobe_probe_shape(self):
        from actions.envprobe import probe
        p = probe()
        for k in ("uv", "python", "cli_prefix", "repo_root", "repo_root_ok"):
            self.assertIn(k, p)
        self.assertTrue(p["cli_prefix"].startswith(("uv run python", "python", '"')),
                        "前缀必须是三种合法形态之一: %r" % p["cli_prefix"])
        self.assertTrue(p["repo_root"].endswith("session-harvester"))

    def test_cli_prefix_not_hardcoded(self):
        # workitems._cli 不许再写死 "python -m harvester "
        src = (VIEW_ROOT / "actions" / "workitems.py").read_text(encoding="utf-8")
        self.assertNotIn('return "python -m harvester " + " ".join(parts)', src)
        self.assertIn("envprobe.probe()", src)

    def test_copy_buttons_present(self):
        # 命令台预览行 + 处理单两处命令框都要有一键复制
        self.assertIn("btn-cmd-copy", self.html)
        self.assertGreaterEqual(self.html.count("data-clicopy="), 2)
        # 三处按钮的 title 都带「复制到剪贴板」标记（test_v51 白名单按行扫
        # 「复制命令」，按钮文案用「复制」不触雷；title 标记保证白名单成立）
        self.assertGreaterEqual(self.html.count("复制到剪贴板"), 3)
        # 每处按钮 title 都要交代粘贴位置（仓根），不只是一颗孤零零的按钮
        self.assertGreaterEqual(self.html.count("到仓根目录的终端里粘贴执行"), 3)

    def test_target_line_speaks_human(self):
        self.assertIn("function targetHuman(t)", self.html)
        self.assertIn("写哪个库", self.html)


if __name__ == "__main__":
    unittest.main()
