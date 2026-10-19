# -*- coding: utf-8 -*-
"""v0.79 测试：卡片触发层 view 侧（P6-卡片触发钩子 §3，与主仓 test_v65 同批）。

钉的验收（每条红=什么坏了）：
1. cardlib.parse_card 透出 triggers（列表原样、非列表空、旧卡零感知）
   → 数据面缺字段或吞内容 → 红；
2. actionsserve 卡本体 payload 原样透传 cardlib dict（_cards_payload
   不改写）→ 有人在中途过滤字段 → 红；
3. index.html renderCards 渲染触发行（带 triggers 才渲染，esc 原样）
   → 有人删渲染行 → 红。

纪律同全仓：view 只渲染服务端给的内容，esc 不解析。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cardlib


def _mk(root: Path, name: str, text: str) -> Path:
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


def _card_md(triggers: str = "") -> str:
    t = f"triggers: [{triggers}]\n" if triggers else ""
    return ("---\nid: kc-t-0001\ntitle: 触发测试卡\ntype: pitfall\n"
            f"{t}"
            'anchors: [{session_id: "s1", turn: 2}]\n'
            "evidence: |\n  证据甲\nconfidence: 0.8\ncreated: 2026-10-19\n"
            "---\n## 现象\n\n结论甲\n")


class TestParseTriggers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_list_parsed_verbatim(self):
        p = _mk(self.root, "a.md",
                _card_md('"file changed", "old_string 不一致"'))
        got = cardlib.parse_card(p)
        self.assertEqual(got["triggers"], ["file changed", "old_string 不一致"])

    def test_nonlist_is_empty(self):
        p = _mk(self.root, "b.md", _card_md())  # 无键
        self.assertEqual(cardlib.parse_card(p)["triggers"], [])
        p2 = _mk(self.root, "c.md", _card_md())  # 非列表形态单独造
        p2.write_text(
            "---\nid: kc-t-0002\ntitle: 非列表\ntype: pitfall\n"
            "triggers: file changed\n"
            'anchors: [{session_id: "s1", turn: 1}]\n'
            "evidence: |\n  证\nconfidence: 0.5\ncreated: 2026-10-19\n"
            "---\n## 现象\n\nx\n", encoding="utf-8")
        self.assertEqual(cardlib.parse_card(p2)["triggers"], [])

    def test_payload_passthrough(self):
        """cardlib.cards_payload 原样带 triggers（actionsserve 直调本函数）。"""
        _mk(self.root, "a.md", _card_md('"词甲"'))
        pay = cardlib.cards_payload(self.root)
        self.assertTrue(all("triggers" in c for c in pay["cards"]),
                        "payload 每张卡都必须带 triggers 键（缺=半路过滤）")


class TestRenderTriggers(unittest.TestCase):
    """index.html renderCards 的触发行（静态结构钉法，与全仓同款）。"""

    def setUp(self):
        self.html = (Path(__file__).resolve().parent.parent
                     / "static" / "index.html").read_text(encoding="utf-8")

    def test_render_line_exists_and_escapes(self):
        self.assertIn("b.triggers", self.html,
                      msg="renderCards 缺触发词渲染行")
        self.assertIn("c-trig", self.html, msg="触发行缺样式类")
        i = self.html.index("b.triggers")
        seg = self.html[max(0, i - 260):i + 200]
        self.assertIn("esc(", seg, msg="触发词必须经 esc 原样展示")


if __name__ == "__main__":
    unittest.main()
