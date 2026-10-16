# -*- coding: utf-8 -*-
"""v0.76 V6 测试：卡片裁决直给（警告行 [接受] / 整卡一键 → card_review）。

覆盖（每条对应一个会红的场景）：
1. **warn 单串归一**：页面直给传单条完整警告文本（字符串）——
   归一后必须按 [该串] 匹配，而不是 tuple() 逐字符拆散（v0.76 V6 修的
   真隐患：单串 "abc" → ('a','b','c') 会误匹配所有含 a/b/c 的警告）；
2. **warn 列表 / 缺省**：列表按子串；缺省=整卡当前警告一言而决；
3. **预演无副作用**：plan 前后 reviews_meta 字节不变；
4. **真落账**：execute 后 load_review_map 有行（verdict=accepted）；
5. **渲染门**：render_summary.js 已覆盖本体渲染；警告行按钮由
   render_review_direct.js 真执行断言（data-cracc/data-crfile 在位）；
6. **evidence 警告复现**（2026-10-16 真机实测踩中）：预演不带 db 参数
   时必须默认用 cfg.db 与展示层同库核引文——否则警告集不同、取键为空、
   接受变 no-op。

夹具：临时 cards 目录（卡片锚点指向不存在的 sid → 必产生锚点类警告），
reviews_meta 用临时库；db 传真库路径（只读）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import writeactions as WA  # noqa: E402

UP = AS.upstream_root()

CARD = """---
id: kc-v62-test
title: 直给夹具卡
type: pitfall
anchors: [{session_id: "dsh:not-exist/session.v4.jsonl.zstd", turn: 1}]
evidence: |
  这张卡故意指向不存在的 sid，校验必报锚点类警告。
confidence: 0.5
created: 2026-10-16
---
## 现象

夹具。

## 做法

仅测试用。
"""

CARD_EVIDENCE = """---
id: kc-v62-evidence
title: 引文未命中夹具卡
type: pitfall
anchors: [{session_id: "dsh:v62-fixture/session.v4.jsonl.zstd", turn: 1}]
evidence: |
  这句引文绝不会出现在锚点会话原文里。
confidence: 0.5
created: 2026-10-16
---
## 现象

引文核对未命中。

## 做法

仅测试用。
"""


def _make_cfg(td: Path, db: Path | None = None) -> AS.Config:
    return AS.Config(
        db=db or (UP / "harvester.db"),
        cards_root=td / "cards",
        glossary=td / "glossary.json",
        topics_meta=td / "topics_meta.db",
        reviews_meta=td / "reviews_meta.db",
        suggestions_meta=td / "suggestions_meta.db",
        keywords_meta=td / "keywords_meta.db",
        actions_meta=td / "actions_meta.db",
        chain_root=UP / "knowledge" / "topics",
        registry_json=AS.DEFAULT_REGISTRY_JSON)


def _spec(cfg) -> dict:
    spec = AS.registry_specs(cfg).get("card_review")
    if not spec:
        raise AssertionError("注册表里没有 card_review（registrygen 未更新）")
    return spec


class CardReviewDirectTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "cards").mkdir()
        (self.td / "cards" / "kc-v62-test.md").write_text(
            CARD, encoding="utf-8")
        self.cfg = _make_cfg(self.td)
        self.spec = _spec(self.cfg)
        from harvester.cards import card_current_warnings
        self.warns = card_current_warnings(
            self.td / "cards" / "kc-v62-test.md", db=self.cfg.db)
        if not self.warns:
            raise unittest.SkipTest("夹具未触发任何警告（上游校验口径变了）")

    def tearDown(self):
        self._td.cleanup()

    def _fp(self) -> tuple:
        import hashlib
        f = self.cfg.reviews_meta
        if not f.exists():
            return (0, "")
        b = f.read_bytes()
        return (len(b), hashlib.sha256(b).hexdigest())

    def test_warn_single_string_not_char_split(self):
        """单串=完整警告文本：只裁决该行（逐字符拆散会误杀其他警告）。"""
        w = self.warns[0]
        f, keys = WA._card_review_keys(
            {"file": "kc-v62-test.md", "warn": w}, self.cfg)
        from harvester.cards import _warn_key
        self.assertEqual(keys, [_warn_key(w)])
        # 若逐字符拆散：子串 'a' 等会命中本卡其他警告（有 ≥2 条时必误杀）
        if len(self.warns) > 1:
            _, all_keys = WA._card_review_keys(
                {"file": "kc-v62-test.md"}, self.cfg)
            self.assertGreater(len(all_keys), len(keys))

    def test_warn_list_and_default(self):
        w = self.warns[0]
        _, k1 = WA._card_review_keys(
            {"file": "kc-v62-test.md", "warn": [w]}, self.cfg)
        _, kall = WA._card_review_keys({"file": "kc-v62-test.md"}, self.cfg)
        self.assertEqual(len(k1), 1)
        self.assertEqual(len(kall), len(self.warns))

    def test_plan_no_side_effect_and_execute_records(self):
        fp = self._fp()
        prev = WA.plan("card_review", self.spec,
                       {"file": "kc-v62-test.md"}, self.cfg)
        self.assertEqual(self._fp(), fp, "预演写了台账（副作用！）")
        self.assertFalse(prev.get("noop"))
        out = WA.execute("card_review", self.spec,
                         {"file": "kc-v62-test.md", "verdict": "accepted",
                          "note": "V6 测试裁决"}, self.cfg)
        self.assertEqual(out["recorded"], len(self.warns))
        from harvester.cards import load_review_map
        m = load_review_map(self.cfg.reviews_meta)
        self.assertIn("kc-v62-test.md", m)
        self.assertTrue(m["kc-v62-test.md"])
        import sqlite3
        con = sqlite3.connect(self.cfg.reviews_meta)
        try:
            rows = con.execute(
                "SELECT verdict, note FROM card_review").fetchall()
        finally:
            con.close()  # with 不 close——Windows 句柄占住会让 cleanup 炸
        self.assertTrue(rows and all(v == "accepted" for v, _ in rows))
        self.assertTrue(all(n == "V6 测试裁决" for _, n in rows))

    def test_registry_params_declared(self):
        names = {p["name"] for p in self.spec["params"]}
        self.assertEqual(names, {"file", "verdict", "warn", "note", "by"})

    def test_evidence_warning_reproduced_without_db_param(self):
        """预演不带 db 参数也必须复现 evidence 类警告（2026-10-16 真机
        实测踩中）：_card_review_keys 原先 db=None → 引文核对退化成
        「原文不可得」→ 直给警告串匹配不上 → 取键空 → 接受变 no-op。
        修后默认用 cfg.db（与展示层 /api/cards 同库同口径）。"""
        import sqlite3
        hdb = self.td / "h.db"
        con = sqlite3.connect(hdb)
        try:
            con.executescript(
                "CREATE TABLE sessions(sid TEXT PRIMARY KEY,"
                " session_id TEXT);"
                "CREATE TABLE messages(sid TEXT, raw TEXT, text TEXT);")
            sid = "dsh:v62-fixture/session.v4.jsonl.zstd"
            con.execute("INSERT INTO sessions VALUES (?,?)", (sid, sid))
            con.execute("INSERT INTO messages VALUES (?,?,?)",
                        (sid, None, "锚点会话的原文，与卡片引文毫无重叠。"))
            con.commit()
        finally:
            con.close()  # 不 close 会让 Windows 的 cleanup 炸
        (self.td / "cards" / "kc-v62-evidence.md").write_text(
            CARD_EVIDENCE, encoding="utf-8")
        from harvester.cards import _warn_key, card_current_warnings
        cfg2 = _make_cfg(self.td, db=hdb)
        warns = card_current_warnings(
            self.td / "cards" / "kc-v62-evidence.md", db=hdb)
        ev = [w for w in warns if w.startswith("evidence 第")]
        self.assertTrue(ev, "夹具未触发 evidence 类警告（上游口径变了）")
        # 修前：不带 db → 警告集不同 → keys=[]（no-op）；修后必须取到键
        _, keys = WA._card_review_keys(
            {"file": "kc-v62-evidence.md", "warn": ev[0]}, cfg2)
        self.assertEqual(keys, [_warn_key(ev[0])])


if __name__ == "__main__":
    unittest.main()
