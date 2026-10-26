# -*- coding: utf-8 -*-
"""v0.83 页面侧回归：自动写链没过门时，**留档落在哪**必须回到页面上。

为什么单独立一条：`chain-generate` 一次要等 2–3 分钟（真调模型）。没过门只
回一句"失败"，等于让人对着空气重跑一遍再等 3 分钟——而模型这一版其实已经
写出来了，只是没敢占草稿位（`harvester/chaingen.py` 会把它另存成
`<草稿位>.pending.md`）。所以：

1. 失败 + 留了档 → 错误文案必须**带出那份 .pending 的落点**（能相对
   exports_root 就相对着显示：页面里读一串 `C:\\...\\exports\\...` 太累）；
2. 失败 + 没留档（模型什么正文都没产出）→ 必须**明说没留任何文件**，
   不能让人去翻一个不存在的文件；
3. 成功 → 摘要里 stages / nodes / 引文命中 / 标点修正数都要在（页面只展示
   这一行，数字缺了用户没法判断这一版值不值得发布）。

对齐的是"文案是不是真的讲清了落点"，不测模型、不测门禁本体（那在上游
`test_v83_chaingen.py`）。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import writeactions as WA  # noqa: E402

UP = AS.upstream_root()


def _make_cfg(td: Path) -> AS.Config:
    glossary = td / "glossary.json"
    glossary.write_text("{}", encoding="utf-8")
    cards = td / "cards"
    cards.mkdir(exist_ok=True)
    exports = td / "exports"
    exports.mkdir(exist_ok=True)
    return AS.Config(db=UP / "harvester.db", cards_root=cards,
                     glossary=glossary,
                     topics_meta=td / "topics_meta.db",
                     suggestions_meta=td / "suggestions_meta.db",
                     keywords_meta=td / "keywords_meta.db",
                     actions_meta=td / "actions_meta.db",
                     registry_json=AS.DEFAULT_REGISTRY_JSON,
                     exports_root=exports)


class ChainGenerateMsgTest(unittest.TestCase):
    """只替换「取参」与「调模型」两处，消息拼装逻辑照真跑。"""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        self.cfg = _make_cfg(self.td)
        self.out = self.td / "exports" / "chain-drafts" / "chain-x.md"
        # 只有 1 次尝试、且没过门的最短路径
        self.bad = {"ok": False, "written": False, "path": None,
                    "attempts": [{"ok": False}], "blockers": ["引文逐字门：2 条未命中"],
                    "pending": str(self.out) + ".pending.md"}

    def _exec(self, result):
        err = WA.ensure_harvester_importable()
        self.assertIsNone(err, "上游仓不在，本文件无从验证")
        from harvester import chaingen as CG
        with mock.patch.object(WA, "_chain_generate_args",
                               return_value=("tp-x", {}, "", self.out)), \
                mock.patch.object(CG, "generate", return_value=result):
            return WA._exec_chain_generate({}, self.cfg)  # noqa: SLF001

    def test_fail_shows_pending_location(self):
        with self.assertRaises(WA.WriteError) as cm:
            self._exec(dict(self.bad))
        msg = str(cm.exception)
        # 相对 exports_root 显示，而不是整条绝对路径
        self.assertIn("chain-drafts", msg.replace("\\", "/"))
        self.assertNotIn(str(self.td), msg)
        self.assertIn(".pending.md", msg)
        # 「没过门」这层含义必须让页面用户看得懂（不靠文件名猜）
        self.assertIn("别拿去发布", msg)
        # 门禁给的原因要原样带出来，别吞成"未知原因"
        self.assertIn("引文逐字门：2 条未命中", msg)
        # 草稿位没动这件事要讲清（不然用户以为已经写进草稿位了）
        self.assertIn("草稿位没动", msg)

    def test_fail_without_pending_says_so(self):
        bad = dict(self.bad)
        bad["pending"] = None
        with self.assertRaises(WA.WriteError) as cm:
            self._exec(bad)
        msg = str(cm.exception)
        self.assertIn("没留下任何文件", msg)
        self.assertNotIn(".pending.md", msg)

    def test_pass_summary_has_counts(self):
        ok = {"ok": True, "written": True, "path": str(self.out),
              "attempts": [{"ok": True}], "pending": None,
              "doc": "x" * 120, "model": "deepseek-chat",
              "demoted": {"count": 3},
              "validate": {"stats": {"stages": 6, "nodes": 21}},
              "audit": {"quotes": {"exact": 19, "total": 20}}}
        r = self._exec(ok)
        s = r["summary"]
        # 页面只展示这一行：四个数缺一个都影响"要不要发布"的判断
        for piece in ("stages 6", "nodes 21", "19/20", "标点修正 3 处",
                      "deepseek-chat", "120 字"):
            self.assertIn(piece, s)
        self.assertEqual(r["path"], str(self.out))


if __name__ == "__main__":
    unittest.main()
