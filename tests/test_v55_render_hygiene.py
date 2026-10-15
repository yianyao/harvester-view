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
import tempfile
import time
import unittest
from pathlib import Path

VIEW_ROOT = Path(__file__).resolve().parent.parent
INDEX = VIEW_ROOT / "static" / "index.html"
REG = VIEW_ROOT / "actions" / "actions_registry.json"
WORKITEMS = VIEW_ROOT / "actions" / "workitems.py"


class TestV072UxGates(unittest.TestCase):
    """v0.72 四截图批注的结构门（先红后绿）。

    ① 数据源账本：发布镜像属内部机制，用户裁决（2026-10-15）**内容直接删**
       ——页面上只保留迁移句里「发布镜像（kb-publish）」一笔带过的操作口。
    ③ 「从库里选」面板上下文感知：只有当前命令参数确实要 sid/topic 时
       才渲染，且只出需要的那种清单；用不到整个不渲染。
    ② 处置进度可视：已处置/已驳回条目用**实底高对比**背景与徽章区分
       （用户裁决：要鲜明，否则看都看不清——禁用低透明度灰显）。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parent.parent /
                    "static" / "index.html").read_text(encoding="utf-8")

    def test_publish_mirror_content_deleted(self):
        """v0.73（用户裁决：内容直接删了，留来干嘛）：镜像段落整段删，
        只留迁移句里 kb-publish 操作口一笔带过。"""
        self.assertNotIn("发布镜像（内部机制", self.html)
        self.assertNotIn("只是<b>发布镜像</b>", self.html)
        self.assertNotIn("%USERPROFILE%\\.workbuddy\\knowledge", self.html,
                         "镜像位路径不该再出现在给读者看的页面上")
        self.assertIn("kb-publish", self.html, "迁移句保留操作口")

    def test_browse_panel_context_aware(self):
        self.assertIn("function cmdNeededPick()", self.html)
        self.assertIn("这个命令的参数需要指定", self.html)
        self.assertIn('if(!need.sid&&!need.topic){box.innerHTML="";return;}',
                      self.html, "用不到的命令面板必须整个不渲染")

    def test_disposition_visual_distinction(self):
        """v0.73（用户裁决：要鲜明，否则看都看不清）：实底背景+高对比徽章，
        禁用 opacity 灰显。"""
        self.assertIn(".wi-item.done{border-left-color:var(--ok-ink);"
                      "background:var(--ok-bg)}", self.html)
        self.assertIn(".wi-item.partial{border-left-color:var(--warn-ink);"
                      "background:var(--warn-bg)}", self.html)
        self.assertIn("background:var(--ok-ink);color:var(--ok-bg)", self.html,
                      "徽章实底（反色对，明暗主题都可读）")
        done_rule = self.html.split(".wi-item.done{")[1].split("}")[0]
        self.assertNotIn("opacity", done_rule, "done 不得再用 opacity 灰显")


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
        # v0.72 +1 消费方：cmdNeededPick（上下文感知选值面板）也走统一解析
        self.assertIn("function cmdNeededPick(){", self.html)
        self.assertEqual(self.html.count("resolveActionSel()"), 5,
                         "定义 1 处 + 消费 4 处，漏一处就是又回到单形态判断")

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
        self.assertTrue(
            p["cli_prefix"].startswith(
                ("uv run python -m harvester", "python -m harvester", '"')),
            "前缀必须是三种合法形态之一: %r" % p["cli_prefix"])
        # v0.70（用户 2026-10-15 截图：`uv run python draft` 报 can't open
        # file '...\draft'）——cli_prefix 的消费方（_cli / 前端 cmdLine）
        # 直接拼**裸子命令**，所以前缀必须自带 `-m harvester` 模块入口。
        # 少了它，"draft" 会被 python 当成脚本文件名 → Errno 2。
        self.assertTrue(p["cli_prefix"].endswith("-m harvester"),
                        "cli_prefix 缺 '-m harvester' 模块入口: %r"
                        % p["cli_prefix"])
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


class TestTopicLevelPackGate(unittest.TestCase):
    """v0.74（用户 2026-10-15 截图：命令台跑 topic pack 报
    level='chain' 不在五档——「为啥还会到这里去找，完全不能理解」）.

    根因：主题页分层下拉第一项是 chain（演进链），但演进链长文在
    openTopic 里是**自动**拉取展示的，这个选项没有任何查看类消费方；
    它唯一会去的地方是「复制命令 / 去命令台」的 topic pack --level——
    而 pack 只认 title/coarse/mid/fine/artifact 五档，必报错。
    且 chain 是默认选中项 → 不碰下拉直接点「去命令台」必踩。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        m = re.search(r'<select id="topic-level">([\s\S]*?)</select>', cls.html)
        assert m, "index.html 缺 #topic-level 下拉"
        cls.select_body = m.group(1)

    def test_dropdown_has_no_chain_option(self):
        self.assertNotIn('value="chain"', self.select_body,
                         "chain 不是 pack 分层，不该出现在分层下拉里")
        self.assertNotIn('value="artifact"', self.select_body,
                         "artifact 档需先跑 artifacts extract，页面不给选")

    def test_dropdown_defaults_to_title(self):
        first = self.select_body.split("<option", 1)[1]
        self.assertIn('value="title"', first, "默认档必须是 title")
        self.assertIn("selected", first)

    def test_go_cmd_guards_level(self):
        """前端兜底：进命令台前 level 必须过 packLevel() 白名单。"""
        self.assertIn("function packLevel()", self.html)
        self.assertIn("level:packLevel()", self.html,
                      "topicGoCmd 预填 level 必须走 packLevel() 兜底")

    def test_cmd_params_sync_select_state(self):
        """预填值不在下拉选项里时，state 必须跟着界面走——不许「看着是
        title、执行带 chain」（显示与执行分家）。"""
        self.assertIn("select[data-p]", self.html)
        self.assertIn("渲染完立刻把 state 同步成下拉实际选中值", self.html)

    def test_topic_pack_action_level_has_choices(self):
        """命令台参数面板用 choices 渲染下拉——level 只能选五档之一。"""
        from actions import actionsserve as AS
        lvl = AS.ACTIONS["topic_pack"]["params"]["level"]
        self.assertEqual(lvl.get("choices"),
                         ["title", "coarse", "mid", "fine", "artifact"])
        self.assertEqual(lvl.get("default"), "title")


class TestSessionsTopicMergeGate(unittest.TestCase):
    """v0.75（用户 2026-10-15 问：可否在会话检索中勾选会话，合并生成主题；
    用户拍板本轮实施）：勾选放开给所有会话 + 「并入主题」入口。

    后端 B 档动作（topic_register / topic_add，两段提交）早已就绪
    （writeactions.WRITE_ACTIONS），本门只钉 view 入口三件事：
    ① 勾选不再限于异常会话（disabled 徽标删除，全选提示语同步改）；
    ② pager 区有「并入主题 / 注册新主题」两个入口按钮，title 交代
       写主题台账、走两段提交（预演→确认才写）；
    ③ 两按钮接 openInCmd 预填通道（B 档动作 id 冻结注册表内）。
    """

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_selection_open_to_all_sessions(self):
        self.assertNotIn("该会话无错误步骤", self.html,
                         "勾选不再限于异常会话（合并主题需要任意会话）")
        self.assertNotIn("仅勾选含异常的会话", self.html,
                         "全选提示语必须随语义放宽")

    def test_merge_buttons_present_with_two_phase_note(self):
        for bid in ("btn-sel-topic", "btn-sel-topic-new"):
            self.assertIn('id="%s"' % bid, self.html)
        self.assertIn("两段提交", self.html.split('id="btn-sel-topic"')[1][:400],
                      "入口按钮必须交代写主题台账走两段提交")

    def test_merge_buttons_wire_open_in_cmd(self):
        self.assertIn('openInCmd("topic_add"', self.html)
        self.assertIn('openInCmd("topic_register"', self.html)

    def test_update_sel_info_controls_merge_buttons(self):
        self.assertIn('$("btn-sel-topic").disabled=!n', self.html)
        self.assertIn('$("btn-sel-topic-new").disabled=!n', self.html)

    def test_topic_write_actions_in_frozen_registry(self):
        reg = json.loads(REG.read_text(encoding="utf-8"))
        ids = {a["action_id"] for a in reg["write_actions"]}
        self.assertIn("topic_add", ids)
        self.assertIn("topic_register", ids)


class TestPlanDeterminism(unittest.TestCase):
    """v0.71（用户 2026-10-15 截图：两段提交永远「预览指纹不匹配」死循环）.

    两段提交的确认段会**重新预演**并与 token 冻结的预览比对指纹——所以
    预演必须**确定性**：任何「执行时才定」的字段（当前时间等）进了 preview，
    重演结果必然不同 → 用户永远被拒、永远被要求重新预览。

    根因实例：_plan_suggest_status 对新 key 把 adopted_at 预演成
    time.strftime(now)——每秒都不同。修后预演用固定哨兵 `@now`，
    真执行时才由上游 set_status 取当前时间。

    本类钉三道防线：
    1. suggest_status 对**新 key** 连跑两次 plan，指纹必须相等（哨兵生效）；
    2. 源码扫描门：所有 `_plan_*` 函数体内禁用时钟（time.strftime /
       time.time / datetime）——防止未来任何动作再把时钟冻进预览；
    3. 报告页「怎么读」导语不得重复挂载（rep-concl 内不再追挂，
       只保留 rep-intro 一处）。
    """

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        d = Path(cls._td.name)
        cls.cfg = type("P", (), {"suggestions_meta": d / "suggestions_meta.db"})()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_plan_suggest_status_is_deterministic(self):
        from actions.writeactions import _plan_suggest_status
        from actions.runlib import preview_fingerprint
        p = {"key": "新建议句（台账里还没有）", "status": "adopted"}
        p1 = _plan_suggest_status(dict(p), self.cfg)
        time.sleep(1.1)          # 跨过 1 秒边界——旧实现 here 必挂
        p2 = _plan_suggest_status(dict(p), self.cfg)
        self.assertEqual(preview_fingerprint(p1), preview_fingerprint(p2),
                         "两次预演指纹不同 → 两段提交会永远被拒（时钟进了预览）")
        self.assertEqual(p1["after"]["adopted_at"], "@now",
                         "新 key 的 adopted_at 预演必须是固定哨兵 @now")
        self.assertIn("写入时自动取当前时间",
                      "；".join(p1["human"]),
                      "human 文案必须交代 adopted_at 是执行时才定")

    def test_plan_bodies_have_no_clock(self):
        src = (VIEW_ROOT / "actions" / "writeactions.py").read_text(
            encoding="utf-8")
        lines = src.splitlines()
        bad = []
        cur = None
        for i, l in enumerate(lines, 1):
            m = re.match(r"def (_plan_\w+)", l)
            if m:
                cur = m.group(1)
                continue
            if cur and re.match(r"^(def |\S)", l) and not l.startswith((" ", "\t")):
                cur = None       # 出了函数体
                continue
            if cur and re.search(r"time\.strftime|time\.time\(|datetime\.", l):
                bad.append((cur, i, l.strip()[:80]))
        self.assertEqual(bad, [],
                         "预演函数体里用了时钟 → 两段提交必挂死循环：%r" % (bad,))

    def test_report_intro_mounted_once(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotIn("panelIntro(introKey)", html,
                         "rep-concl 里又追挂了一次导语 → 「怎么读」重复渲染")


if __name__ == "__main__":
    unittest.main()
