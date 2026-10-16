# -*- coding: utf-8 -*-
"""v0.76 V7 测试：chain_audit_accept（审计软锚点裁决的 view 直给）。

覆盖四条，每条都能说出"它在什么情况下会红"：

1. **预演无副作用**：plan 前后 topics_meta 字节不变（两段提交的信任前提）；
2. **取键同一真源**：plan 列的键 = 上游 audit_chain 可疑集的
   `_node_key`（`node:<sid>#turn:<turn>`），不自拼格式；
3. **exec 真落账**：execute 后 audit_chain（review_meta 视角）可疑集缩小，
   list_audit_reviews 有行；重开（UPSERT 改判）走上游语义，不在此重复测；
4. **乱键拒绝**：accepts 指定不在当前可疑集的键 → WriteError（不静默跳过）；
   非法路径（.. / 绝对路径 / 不存在）→ WriteError。

夹具：真 harvester.db（只读）+ 真 chain 文件（只读）+ 临时 topics_meta
（记录裁决的写入全部落在临时副本，绝不碰真台账）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import writeactions as WA  # noqa: E402

UP = AS.upstream_root()
CHAIN_DIR = UP / "knowledge" / "topics"


def _first_chain_with_suspicious(td: Path) -> str:
    """找一条当前仍有未接受软锚点的真 chain（没有则整类跳过）。"""
    from harvester.chainaudit import audit_chain
    for f in sorted(CHAIN_DIR.glob("chain-*.md")):
        try:
            r = audit_chain(f, UP / "harvester.db", quotes=False,
                            coverage=False, anchors=True,
                            review_meta=td / "probe.db", show_reviewed=True)
        except Exception:
            continue
        if r["anchors"]["suspicious"]:
            return f.name
    raise unittest.SkipTest("真库当前没有未接受的软锚点（台账已清空）")


def _make_cfg(td: Path) -> AS.Config:
    cfg = WA.__dict__  # noqa: F841 - 占位说明：直接用 AS.Config
    return AS.Config(
        db=UP / "harvester.db",
        cards_root=td / "cards",
        glossary=td / "glossary.json",
        topics_meta=td / "topics_meta.db",
        suggestions_meta=td / "suggestions_meta.db",
        keywords_meta=td / "keywords_meta.db",
        actions_meta=td / "actions_meta.db",
        chain_root=CHAIN_DIR,
        registry_json=AS.DEFAULT_REGISTRY_JSON)


def _spec(cfg) -> dict:
    spec = AS.registry_specs(cfg).get("chain_audit_accept")
    if not spec:
        raise AssertionError("注册表里没有 chain_audit_accept（registrygen 未更新）")
    return spec


class ChainAuditAcceptTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.cfg = _make_cfg(self.td)
        self.spec = _spec(self.cfg)
        self.chain = _first_chain_with_suspicious(self.td)
        # 预跑一次拿可疑键（与被测代码同一上游函数，真源对账）
        from harvester.chainaudit import audit_chain, _node_key
        r = audit_chain(CHAIN_DIR / self.chain, self.cfg.db, quotes=False,
                        coverage=False, anchors=True,
                        review_meta=self.cfg.topics_meta, show_reviewed=True)
        self.suspicious = [_node_key(s["sid"], s["turn"])
                           for s in r["anchors"]["suspicious"]]

    def tearDown(self):
        self._td.cleanup()

    def _fingerprint(self) -> tuple:
        if not self.cfg.topics_meta.exists():
            return (0, "")
        b = self.cfg.topics_meta.read_bytes()
        import hashlib
        return (len(b), hashlib.sha256(b).hexdigest())

    def test_plan_lists_suspicious_keys_without_side_effect(self):
        fp = self._fingerprint()
        prev = WA.plan("chain_audit_accept", self.spec,
                       {"file": self.chain}, self.cfg)
        self.assertEqual(self._fingerprint(), fp, "预演写了台账（副作用！）")
        self.assertFalse(prev.get("noop"))
        keys = [ln.strip(" -") for ln in prev["human"]
                if ln.strip().startswith("- node:")]
        # human 里截前 8 条——可疑集 ≤8 时应全列
        if len(self.suspicious) <= 8:
            self.assertEqual(sorted(keys), sorted(self.suspicious))
        self.assertIn("audit_review", " ".join(prev["human"]))

    def test_execute_records_and_shrinks_suspicious(self):
        prev = WA.plan("chain_audit_accept", self.spec,
                       {"file": self.chain}, self.cfg)
        out = WA.execute("chain_audit_accept", self.spec,
                         {"file": self.chain, "accept_note": "V7 测试裁决"},
                         self.cfg)
        self.assertEqual(out["recorded"], len(self.suspicious))
        from harvester.chainaudit import (audit_chain, list_audit_reviews)
        r = audit_chain(CHAIN_DIR / self.chain, self.cfg.db, quotes=False,
                        coverage=False, anchors=True,
                        review_meta=self.cfg.topics_meta)
        self.assertTrue(r["anchors"]["ok"],
                        "全部裁决后锚点门应通过（可疑集清空）")
        rows = list_audit_reviews(self.cfg.topics_meta, self.chain)
        self.assertEqual(len(rows), len(self.suspicious))
        self.assertTrue(all(x["verdict"] == "accepted" for x in rows))
        self.assertTrue(all(x["note"] == "V7 测试裁决" for x in rows))
        _ = prev  # plan 返回值只用于验证流程可走通

    def test_unknown_key_rejected(self):
        with self.assertRaises(WA.WriteError):
            WA.plan("chain_audit_accept", self.spec,
                    {"file": self.chain,
                     "accept": "node:dsh:nowhere#turn:999"}, self.cfg)

    def test_bad_path_rejected(self):
        for bad in ("../escape.md", "C:/abs.md", "没有这条.md"):
            with self.assertRaises(WA.WriteError):
                WA.plan("chain_audit_accept", self.spec,
                        {"file": bad}, self.cfg)

    def test_registry_declared_and_tier_b(self):
        self.assertEqual(self.spec["tier"], "B")
        self.assertEqual(self.spec["risk"], "reversible")
        names = {p["name"] for p in self.spec["params"]}
        self.assertEqual(names, {"file", "accept", "accept_note"})


if __name__ == "__main__":
    unittest.main()
