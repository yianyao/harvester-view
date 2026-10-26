# -*- coding: utf-8 -*-
"""P2 #14 页面接线：产物台账 + 评审包 从注册表到渲染的整条线。

为什么单测这一层：算法在 `test_v87` / `test_v88`，这里防的是
**"动作做了、页面没接"**——注册表里没有 / 下拉里没有 / 分发没挂 /
渲染函数没定义，四种都是静默空白，跑起来不报错，人只会以为"没做"。

同时钉两条口径声明（本页最容易滑过去的地方）：
- 台账是**全库**口径，「窗口（天）」对它无效 → meta 行必须明说；
- 台账的覆盖度只覆盖已登记提取的源 → 页面必须把覆盖度那行放在表上方。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import registrygen as RG  # noqa: E402

HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
REG = json.loads((ROOT / "actions" / "actions_registry.json").read_text("utf-8"))
GLO = json.loads((ROOT / "static" / "glossary.json").read_text("utf-8"))

IDS = ("artifact_ledger", "review_pack")


class RegistryWiringTest(unittest.TestCase):
    def test_both_registered_as_view_side_read_only(self):
        by = {x["action_id"]: x for x in REG["read_actions"]}
        for aid in IDS:
            self.assertIn(aid, by, "A 档注册表里没有 %s" % aid)
            self.assertEqual(by[aid]["tier"], "A")
            # 视图侧分析动作：没有对应 CLI，cmd 如实为 null
            self.assertIsNone(by[aid]["cmd"],
                              "%s 没有 CLI，cmd 必须是 null（别编一个）" % aid)
            self.assertEqual(AS.ACTIONS[aid]["writes"], "",
                             "%s 是只读动作，不许声明 writes" % aid)
            self.assertGreaterEqual(int(AS.ACTIONS[aid]["est_sec"]), 1)

    def test_registrygen_declares_none(self):
        for aid in IDS:
            self.assertIn(aid, RG.READ_ACTIONS)
            self.assertIsNone(RG.READ_ACTIONS[aid])

    def test_handlers_exist_and_are_callable(self):
        for aid in IDS:
            self.assertTrue(callable(AS.ACTIONS[aid]["handler"]))

    def test_review_pack_requires_sid(self):
        params = AS.ACTIONS["review_pack"]["params"]
        self.assertIn("sid", params)
        self.assertEqual(params["sid"].get("src"), "sessions_api")

    def test_ledger_exposes_limit_and_sort(self):
        params = AS.ACTIONS["artifact_ledger"]["params"]
        self.assertIn("limit", params)
        self.assertIn("sort", params)
        self.assertEqual(set(params["sort"]["choices"]),
                         {"versions", "recent", "path"})


class PageWiringTest(unittest.TestCase):
    def test_dropdown_option(self):
        self.assertIn('<option value="ledger">产物台账', HTML)

    def test_dispatch_and_renderers(self):
        self.assertIn('kind==="ledger"', HTML)
        for fn in ("runLedgerReport", "renderLedger", "openReviewPack",
                   "runReviewPack", "renderReviewPack"):
            self.assertIn(fn, HTML, "页面缺函数 %s" % fn)
        self.assertIn("renderLedger(d,out)", HTML)

    def test_ledger_row_has_review_pack_button(self):
        self.assertIn("data-rp-sid", HTML)
        self.assertIn("data-rp-file", HTML)
        self.assertIn("打评审包", HTML)

    def test_meta_line_declares_full_db_and_window_void(self):
        self.assertIn("全库产物台账", HTML)
        self.assertIn("选择器对本档无效", HTML)

    def test_coverage_line_rendered_above_table(self):
        # 覆盖度必须在表格**之前**出现（先给分母再给数）
        i_cov = HTML.find("esc(cv.line_text")
        i_tab = HTML.find("h+=\"<table><thead><tr><th>版数</th>")
        self.assertTrue(i_cov > 0 and i_tab > 0, "找不到覆盖度行或台账表")
        self.assertLess(i_cov, i_tab, "覆盖度必须排在台账表之前")

    def test_review_pack_has_copy_button(self):
        self.assertIn("rp-copy", HTML)
        self.assertIn("复制整包", HTML)

    def test_panel_intro_exists(self):
        hit = [p for p in GLO["panels"] if p["panel"] == "ledger"]
        self.assertTrue(hit, "glossary 缺 ledger 导语（这页会没有「怎么读」）")
        intro = hit[0]["intro"]
        for kw in ("覆盖度", "打评审包", "不是因果", "不是质量分"):
            self.assertIn(kw, intro)


class ActionRunTest(unittest.TestCase):
    """真动作：进程内调一次，确认返回的是**结构本身**（不是 CLI 那层包装）。"""

    def setUp(self):
        up = AS.upstream_root()
        self.meta = up / "artifacts_meta.db"
        if not self.meta.is_file():
            self.skipTest("需要真产物台账（H9）")
        self.cfg = AS.Config(db=up / "harvester.db",
                             cards_root=up / "knowledge" / "cards",
                             glossary=ROOT / "static" / "glossary.json",
                             topics_meta=up / "topics_meta.db",
                             artifacts_meta=self.meta)

    def test_ledger_handler_returns_plain_structure(self):
        d = AS.ACTIONS["artifact_ledger"]["handler"](self.cfg, {"limit": 3})
        self.assertEqual(d["schema"], "artifact_ledger/1")
        self.assertIsNone(d.get("exit_code"),
                          "cmd=None 的动作没有 CLI 退出码；出现它是包装层串了")
        self.assertLessEqual(len(d["rows"]), 3)

    def test_review_pack_handler_returns_plain_structure(self):
        con = __import__("sqlite3").connect(
            "file:%s?mode=ro" % self.meta.as_posix(), uri=True)
        try:
            sid, fp = con.execute(
                "SELECT sid, file_path FROM artifacts GROUP BY sid, file_path "
                "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        finally:
            con.close()
        d = AS.ACTIONS["review_pack"]["handler"](
            self.cfg, {"sid": sid, "file_path": fp})
        self.assertEqual(d["schema"], "review_pack/1")
        self.assertTrue(d["versions"])
        self.assertIsNone(d.get("exit_code"))


if __name__ == "__main__":
    unittest.main()
