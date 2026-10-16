# -*- coding: utf-8 -*-
"""v0.76 V5（evidence/summary 拆角色，view 侧认 summary）。

背景：主仓 v0.74 拆角色后，新规范卡综述写在 frontmatter `summary`，
evidence 只放逐字证据；insight 的知识本就是蒸馏综述（强求逐字是范畴错误）。
view 的 cardlib 不认 summary → 综述丢失、insight 卡证据栏空白。

覆盖：
1. cardlib.parse_card 透出 summary（additive：evidence 仍原样）；
2. insight 卡可 summary-only（真库即将出现的形态）；
3. evidence+summary 并存卡两字段都透出；
4. 渲染门（render_summary.js）单独真执行 renderCards 双栏与 C 位——
   本文件只管解析层，渲染层由 JS 门负责（惯例：py 测数据形状，
   js 测渲染行为）。
5. 主库零改动守卫：只读写自身 tmp 目录。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cardlib  # noqa: E402

# v0.74 新规范卡：insight 型，summary-only（无 evidence——合法）
INSIGHT_SUMMARY_ONLY = """---
id: kc-20261016-0001-test
title: 蒸馏综述型卡
type: insight
tags: [distill, summary]
anchors: [{session_id: "dsh:a/session.v4.jsonl.zstd", turn: 12}]
summary: |
  长任务卡片验收的瓶颈在证据核对而非写作——机器核它核得了的，
  语义裁决还给人，人裁过不再烦人。
confidence: 0.7
created: 2026-10-16
---
## 结论

复核台账把"人的裁决"固化下来，重复打扰归零。

## 依据

三批蒸馏实测（2026-10-15）。
"""

# 并存卡：evidence 逐字 + summary 综述（pitfall 型新规范）
BOTH_FIELDS = """---
id: kc-20261016-0002-test
title: 逐字证据加综述
type: pitfall
anchors: [{session_id: "wc:b/session.v4.jsonl.zstd", turn: 3}]
evidence: |
  Error: old_string was not found in "C://x//y.md"
summary: |
  Edit/Write 前必须先 Read 最新内容，四类报错同源。
confidence: 0.9
created: 2026-10-16
---
## 现象

old_string 不匹配。

## 做法

先 Read 再 Edit。
"""


class CardlibSummaryTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "kc-0001.md").write_text(
            INSIGHT_SUMMARY_ONLY, encoding="utf-8")
        (self.root / "kc-0002.md").write_text(BOTH_FIELDS, encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def test_insight_summary_only(self):
        """summary-only 卡：summary 透出、evidence 空串（不吞不编）。"""
        got = cardlib.parse_card(self.root / "kc-0001.md")
        self.assertTrue(got["parsed"])
        self.assertEqual(got["type"], "insight")
        self.assertIn("语义裁决还给人", got["summary"])
        self.assertFalse(got["summary"].startswith(" "))
        self.assertEqual(got["evidence"], "")

    def test_both_fields(self):
        """并存卡：两字段各归各位。"""
        got = cardlib.parse_card(self.root / "kc-0002.md")
        self.assertIn("old_string was not found", got["evidence"])
        self.assertIn("四类报错同源", got["summary"])

    def test_legacy_card_summary_empty(self):
        """v0.74 之前的旧卡（只有 evidence）：summary 必须是空串而非
        KeyError/None——渲染层按 falsy 跳过，旧卡不受影响。"""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "old.md"
            p.write_text(
                "---\nid: kc-old\ntitle: 旧卡\ntype: pitfall\n"
                'anchors: [{session_id: "dsh:o/s.v4.jsonl.zstd", turn: 1}]\n'
                "evidence: |\n  旧证据\nconfidence: 0.5\ncreated: 2026-10-01\n"
                "---\n## 现象\n\n旧结论\n", encoding="utf-8")
            got = cardlib.parse_card(p)
            self.assertEqual(got["summary"], "")
            self.assertEqual(got["evidence"], "旧证据")

    def test_payload_has_summary(self):
        """/view/cards 载荷透出 summary（前端免二次解析）。"""
        d = cardlib.cards_payload(self.root)
        self.assertEqual(d["view_api"], 1)
        by_path = {c["path"]: c for c in d["cards"]}
        self.assertIn("语义裁决还给人", by_path["kc-0001.md"]["summary"])
        self.assertIn("old_string was not found",
                      by_path["kc-0002.md"]["evidence"])


if __name__ == "__main__":
    unittest.main()


class SummaryRenderGateTest(unittest.TestCase):
    """V5 渲染门包装（惯例同 test_v45：py 测试 subprocess 跑 JS 门，
    unittest 发现即可全量覆盖渲染门；node 缺失时 skip）。"""

    def test_render_summary_js(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 不在 PATH，综述渲染请手动跑 tests/render_summary.js")
        r = subprocess.run(
            [node, str(ROOT / "tests" / "render_summary.js")],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "render_summary.js 失败：\n%s\n%s"
                         % (r.stdout, r.stderr))
        self.assertIn("ALL SUMMARY CHECKS PASSED", r.stdout)
        self.assertGreaterEqual(r.stdout.count("ok - "), 11, r.stdout)
