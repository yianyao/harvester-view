# -*- coding: utf-8 -*-
"""P5-3 空态与确认流统一（test_v51）。

任务（TODO P5-3）：全部"复制命令"空态改为"选参数→执行"；确认弹窗文案统一。
验收：全站 grep 无残留"复制命令"空态（C 档按钮除外）。

静态门设计（文案级改动，锚行切片断言）：
  1. chain-404 空态：引导「去命令台」（选参数→执行），不再引导「复制命令」。
  2. timeline 深挖说明：同上。
  3. 全站白名单：含「复制命令」的每一行必须落在 C 档白名单
     （按钮本体 title 提示 / 代码注释），UI 空态文案零残留。
  4. 确认流术语统一：无过时的「P3-2 才提供」；确认框内重试话术统一为
     「预演」（无「重新预览」混用）。
  5. C 档按钮本体仍在（白名单不是删光，是划界）。
"""
import unittest
from pathlib import Path

IDX = Path(__file__).resolve().parents[1] / "static" / "index.html"


def lines_with(src: str, needle: str):
    return [ln for ln in src.splitlines() if needle in ln]


def slice_around(src: str, anchor: str, span: int = 3):
    """锚串所在行 ±span 行拼成切片（文案断言用）。"""
    ls = src.splitlines()
    for i, ln in enumerate(ls):
        if anchor in ln:
            return "\n".join(ls[max(0, i - span):i + span + 1])
    return ""


class TestP53EmptyStates(unittest.TestCase):
    def setUp(self):
        self.src = IDX.read_text(encoding="utf-8")

    def test_chain_404_empty_state_go_cmd(self):
        """chain 404 空态 → 给**可点的入口**（P11 起是「生成链创作包」按钮）。

        原始判据（P5-3）：空态永远指向"能就地做的事"，不许只让用户复制命令。
        P11 前那个入口是「去命令台」（topic pack）；用户 2026-10-24 说
        「要补一个生成链的命令给我，更干脆的是我能直接就看到链」之后，
        入口换成 chain_build 一键出包 + 发布指引——**判据不变，落点更近**。
        所以这里断的是：有按钮 id、有"校验并发布"、且不引导复制命令。
        """
        s = slice_around(self.src, "btn-chain-empty-build", span=8)
        self.assertTrue(s, "锚点丢失：空态按钮 btn-chain-empty-build 不存在")
        self.assertIn("尚无 chain 长文", s, "按钮必须长在 chain 空态里")
        self.assertIn("链创作包", s, "空态要点出这是「链创作包」这条路")
        self.assertIn("校验并发布", s, "空态要说明写完怎么落地（两道门）")
        self.assertNotIn("复制命令", s, "空态残留「复制命令」引导")

    def test_timeline_hint_go_cmd(self):
        """timeline 深挖说明 → 动作通道（去命令台执行），不引导复制命令。"""
        s = slice_around(self.src, "深挖分层用上方")
        self.assertTrue(s, "锚点丢失：timeline 深挖说明不存在")
        self.assertIn("去命令台", s, "应引导「去命令台」")
        self.assertNotIn("复制命令", s, "说明文案残留「复制命令」引导")

    def test_copy_cmd_confined_to_tier_c_allowlist(self):
        """全站「复制命令」逐行白名单：只允许 C 档按钮本体与代码注释。"""
        allow_markers = (
            "复制命令行（粘贴到终端运行",  # 按钮本体 title（C 档兜底按钮，
                                          # v0.90b 人话版）
            "保留作动作面不可达时的兜底",  # 注释：按钮保留原因
            "页面按档位只给",           # 注释：命令台档位说明
            "纯文本复制命令",           # render_smoke 注释同类（本文件无，防误伤）
        )
        for ln in lines_with(self.src, "复制命令"):
            ok = any(m in ln for m in allow_markers) or \
                ln.strip().startswith(("/*", "*", "//")) or "*/" in ln
            self.assertTrue(
                ok,
                "「复制命令」出现在白名单外（疑似空态/引导残留）：%r" % ln.strip()[:90])

    def test_tier_c_buttons_intact(self):
        """C 档按钮本体仍在：白名单是划界，不是删光。"""
        self.assertIn("btn-topic-cmd", self.src, "主题页 C 档兜底按钮丢失")
        self.assertIn("data-cmd=", self.src, "报告页 C 档复制按钮丢失")

    def test_confirm_flow_no_stale_p32(self):
        """确认流无过时进度标注：B 档确认流 P3-2 已交付，「才提供」不再成立。"""
        self.assertNotIn("P3-2 才提供", self.src,
                         "过时文案：P3-2 已交付，B 档确认流已可用")

    def test_confirm_flow_yuyan_term_unified(self):
        """确认流术语统一：两段之间状态变化的提示统一为「预演」。

        只锚定确认框与重试提示切片（服务端透传消息与打磨带 a/b
        原文「预览」名词不在此列——前者属主库、后者非确认流术语）。
        """
        s1 = slice_around(self.src, "服务端会拒绝并要求")
        self.assertTrue(s1, "锚点丢失：确认框两段保护提示不存在")
        self.assertNotIn("预览", s1, "确认框内「预览/预演」混用")
        self.assertIn("重新预演", s1)
        s2 = slice_around(self.src, "需要重新预演")
        if s2:  # 改后应存在且指向「预演」按钮
            self.assertNotIn("重新预览", s2)
            self.assertIn("「预演」", s2, "重试提示应指向真实按钮文案「预演」")
        else:
            # 未改前该切片按「需要重新预览」锚定——此时必须红
            s0 = slice_around(self.src, "需要重新预览")
            self.fail("重试提示仍是旧术语「需要重新预览」：" + (s0[:80] or "锚点丢失"))

    def test_confirm_button_terms(self):
        """确认框按钮话术统一：确认执行 / 取消（不写）/ 预演。"""
        self.assertIn("data-cfm='yes'>确认执行", self.src)
        self.assertIn("data-cfm='no'>取消（不写）", self.src)
        self.assertIn("预演（先看清楚，再确认才写）", self.src,
                      "B 档执行按钮标准话术丢失")


if __name__ == "__main__":
    unittest.main()
