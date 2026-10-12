# -*- coding: utf-8 -*-
"""v0.34 本地数据面测试（工程决策 D9）：cardlib 解析 + viewserver 端点。

覆盖三类风险（对应验收）：
1. cardlib 必须吃下**真实形态**卡片（v0.16 教训：夹具缺真实形态=口径假真）——
   夹具按 ~/.workbuddy/knowledge/cards 真卡形态构造（anchors 内联序列带引号
   长 sid、evidence 块标量、confidence 浮点）；
2. viewserver 端点：/view/cards 形状、/view/glossary 透出、POST 405、404；
3. 主库零改动守卫：本测试只读写自身 tmp 目录。
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cardlib  # noqa: E402
import viewserver  # noqa: E402

REAL_CARD = """---
id: kc-20261006-0001
title: Edit/Write 前必须先 Read 目标文件最新内容
type: pitfall
tags: [edit, tool-usage, harvester]
anchors: [{session_id: "dsh:--C-Users-yianyao-WorkBuddy--/session-295f208c-542a-412b-a2b5-22a29b9d7fac/session.v4.jsonl.zstd", turn: 263}]
evidence: |
  Error: old_string was not found in "C://Users//yianyao//WorkBuddy//..."
confidence: 0.9
created: 2026-10-06
---
## 现象

old_string 不匹配 / file changed since read / not been read，四类报错同源。

## 做法

1. Edit/Write 前先 Read 最新内容；
2. 报 file changed 后必须重读。
"""

REAL_CARD_NO_H2 = """---
id: kc-x-0002
title: 无小节标题的卡
type: workflow
anchors: [{session_id: "wc:abc", turn: 7}, {session_id: "wc:def", turn: 8}]
evidence: 单行引文
confidence: 0.6
created: 2026-10-10
---
直接一句话结论，没有小节标题。
"""

BROKEN_CARD = "没有 frontmatter 的普通 markdown。\n第二行。\n"


class CardlibTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "kc-0001.md").write_text(REAL_CARD, encoding="utf-8")
        (self.root / "kc-0002.md").write_text(REAL_CARD_NO_H2, encoding="utf-8")
        (self.root / "kc-0003.md").write_text(BROKEN_CARD, encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def test_parse_real_shape(self):
        got = cardlib.parse_card(self.root / "kc-0001.md")
        self.assertTrue(got["parsed"])
        self.assertEqual(got["id"], "kc-20261006-0001")
        self.assertEqual(got["title"],
                         "Edit/Write 前必须先 Read 目标文件最新内容")
        self.assertEqual(got["type"], "pitfall")
        self.assertEqual(got["confidence"], 0.9)
        self.assertEqual(got["created"], "2026-10-06")
        self.assertEqual(got["tags"], ["edit", "tool-usage", "harvester"])
        # evidence 块标量去掉脚手架缩进
        self.assertIn("old_string was not found", got["evidence"])
        self.assertFalse(got["evidence"].startswith(" "))
        # 长带引号 sid 原样保留 + turn 整数
        self.assertEqual(len(got["anchors"]), 1)
        a = got["anchors"][0]
        self.assertTrue(a["sid"].startswith("dsh:--C-Users"))
        self.assertIn("session.v4.jsonl.zstd", a["sid"])
        self.assertEqual(a["turn"], 263)
        # 结论 = 「## 现象」第一节首行（非空）
        self.assertIn("四类报错同源", got["conclusion"])

    def test_parse_no_h2_and_multi_anchor(self):
        got = cardlib.parse_card(self.root / "kc-0002.md")
        self.assertTrue(got["parsed"])
        self.assertEqual(got["confidence"], 0.6)
        self.assertEqual([a["turn"] for a in got["anchors"]], [7, 8])
        self.assertEqual(got["conclusion"], "直接一句话结论，没有小节标题。")

    def test_broken_card_never_throws(self):
        got = cardlib.parse_card(self.root / "kc-0003.md")
        self.assertFalse(got["parsed"])
        self.assertIsNone(got["title"])
        self.assertEqual(got["anchors"], [])

    def test_scan_sorted_and_relpath(self):
        cards = cardlib.scan_cards(self.root)
        self.assertEqual([c["path"] for c in cards],
                         ["kc-0001.md", "kc-0002.md", "kc-0003.md"])

    def test_payload_shape(self):
        d = cardlib.cards_payload(self.root)
        self.assertEqual(d["view_api"], 1)
        self.assertEqual(len(d["cards"]), 3)
        missing = cardlib.cards_payload(self.root / "no-such-dir")
        self.assertEqual(missing["cards"], [])


class ViewServerTest(unittest.TestCase):
    """起真实 viewserver（随机端口）打端点——H22：接线必须真打。"""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._td = tempfile.TemporaryDirectory()
        root = Path(cls._td.name)
        (root / "c.md").write_text(REAL_CARD, encoding="utf-8")
        glossary = root / "glossary.json"
        glossary.write_text(json.dumps(
            {"terms": [{"term": "t1", "plain": "p1"}]}, ensure_ascii=False),
            encoding="utf-8")
        cls.srv = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            viewserver.make_handler(root, glossary))
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.socket.getsockname()[1]

    @classmethod
    def tearDownClass(cls):
        # v0.16 教训：shutdown + server_close 收干净，避免 ResourceWarning
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.th.join(timeout=2)
        cls._td.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_cards_endpoint(self):
        st, d = self._get("/view/cards")
        self.assertEqual(st, 200)
        self.assertEqual(d["view_api"], 1)
        self.assertEqual(len(d["cards"]), 1)
        self.assertTrue(d["cards"][0]["parsed"])

    def test_glossary_endpoint(self):
        st, d = self._get("/view/glossary")
        self.assertEqual(st, 200)
        self.assertEqual(d["terms"][0]["term"], "t1")

    def test_health_and_404(self):
        st, d = self._get("/view/health")
        self.assertEqual(st, 200)
        self.assertTrue(d["ok"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/view/nope")
        self.assertEqual(cm.exception.code, 404)

    def test_post_405(self):
        req = urllib.request.Request(self.base + "/view/cards", data=b"{}")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("POST 应被 405 拒绝")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)


if __name__ == "__main__":
    unittest.main()