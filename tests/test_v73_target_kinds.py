# -*- coding: utf-8 -*-
"""第②步门（用户 2026-10-23 批准）：**累计量不再发黄**，判据只有一处。

用户原话（转述）："今日工作台中的各种项目，异常很低也显示似乎没必要……需要设置
异常阈值，不到的就不显示"，并当场拍板"累计量改中性色同意"。

病（`docs/reports/probe-target-kinds.py` 实测）：18 个 target 里 14 个 `level=warn`，
其中 8 个是**累计量/已完成**——全库错误总数 532、已拍板 24、已登记 skill 38、
已发布 chain 3……颜色不再携带信息。

本文件钉住五件事：
  1. **分类完备**：每个 target 都在 `workitems.KINDS` 里有明确一类（漏登记 → KeyError）；
  2. **累计量永不 warn**（`stock` → `info`，含 0 与 532 两端）；
  3. **待处理三段制**：0=ok、1–2=watch、≥3=warn（阈值取 `severity.ACTION_MIN`）；
  4. **比率要样本下限**：分母 <20 或比率 <10% 都不 warn；
  5. **未知 kind 必须炸**（fail loud），不许默认当成"待处理"。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import severity as sev  # noqa: E402
from actions import workitems as WI  # noqa: E402

#: 用户当场点名要改中性色的那几张（钉住决定，防止回退）
STOCK_IDS = {
    "queue:decide.done",      # 已裁决（已完成）
    "ring:errors.0",          # 全库错误总数
    "ring:errors.1",          # 已采纳
    "ring:errors.2",          # 不下结论（子集）
    "ring:skill.0",           # 已登记 skill 数
    "ring:topics.0",          # 已注册主题数
    "ring:topics.1",          # 已发布 chain
}

HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


class _Cfg:
    """只有路径的最小 cfg（与 test_v40 同款；空目录也能跑）。"""

    def __init__(self, d: Path):
        self.db = d / "harvester.db"
        self.cards_root = d / "cards"
        self.suggestions_meta = d / "suggestions_meta.db"
        self.topics_meta = d / "topics_meta.db"
        self.keywords_meta = d / "keywords_meta.db"
        self.artifacts_meta = d / "artifacts_meta.db"
        self.chain_root = d / "chains"
        self.exports_root = d / "exports"


def _empty_g(cfg) -> dict:
    return {
        "cfg": cfg, "pstats": {}, "err_meta": {"error_count": 0},
        "errs_rep": {"patterns": [], "db_fingerprint": {"sessions": 0}},
        "agents": {"entries": []}, "skills_rep": {"skills": []},
        "topics": {"topics": []},
        "triage": {"new_patterns": [], "old_patterns": [], "skills": []},
        "cards": None, "records": {}, "registry": {"topics": 0, "noise": 0},
        "ev": {"counts": {}, "adopted_total": 0, "skipped": 0,
               "skipped_topic": 0, "entries": []},
        "cards_pending": [],
    }


class TestClassificationIsComplete(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _Cfg(Path(self._tmp.name))
        self.targets = {t["target_id"]: t for t in WI.targets(_empty_g(self.cfg))}

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_target_has_kind_in_kinds_map(self):
        self.assertEqual(set(self.targets), set(WI.KINDS),
                         "target 与分类表必须一一对应（多一个/少一个都会让分类失真）")

    def test_every_target_level_matches_severity(self):
        for tid, t in self.targets.items():
            self.assertEqual(
                t["level"], sev.level_of(t["kind"], t["count"]),
                "%s 的 level 不是 severity.py 算的（第二套口径回来了）" % tid)
            self.assertTrue(t.get("level_why"), "%s 缺 level_why（人话理由）" % tid)

    def test_stock_ids_are_stock(self):
        for tid in STOCK_IDS:
            self.assertEqual(self.targets[tid]["kind"], sev.KIND_STOCK, tid)


class TestStockNeverWarns(unittest.TestCase):
    def test_stock_is_info_at_both_ends(self):
        for n in (0, 1, 2, 3, 24, 38, 532):
            self.assertEqual(sev.level_of(sev.KIND_STOCK, n), sev.LEVEL_INFO,
                             "累计量 %d 不许发黄" % n)

    def test_action_three_bands(self):
        self.assertEqual(sev.level_of(sev.KIND_ACTION, 0), sev.LEVEL_OK)
        for n in (1, 2):
            self.assertEqual(sev.level_of(sev.KIND_ACTION, n), sev.LEVEL_WATCH)
        for n in (sev.ACTION_MIN, 3, 51):
            self.assertEqual(sev.level_of(sev.KIND_ACTION, n), sev.LEVEL_WARN)

    def test_threshold_edge_is_pinned(self):
        self.assertEqual(sev.level_of(sev.KIND_ACTION, sev.ACTION_MIN - 1),
                         sev.LEVEL_WATCH)
        self.assertEqual(sev.level_of(sev.KIND_ACTION, sev.ACTION_MIN),
                         sev.LEVEL_WARN)


class TestRateNeedsSample(unittest.TestCase):
    def test_small_denominator_never_warns(self):
        # 1 次调用 1 次失败 = 100%，但分母 <20 → 不下结论
        self.assertEqual(sev.level_of(sev.KIND_RATE, 1, denom=1), sev.LEVEL_OK)

    def test_big_sample_low_rate_is_ok(self):
        self.assertEqual(sev.level_of(sev.KIND_RATE, 5, denom=100), sev.LEVEL_OK)

    def test_big_sample_high_rate_warns(self):
        self.assertEqual(sev.level_of(sev.KIND_RATE, 12, denom=100), sev.LEVEL_WARN)

    def test_rate_boundary(self):
        # 刚好 10% 就要报警（阈值是 >=）
        self.assertEqual(sev.level_of(sev.KIND_RATE, 2, denom=20), sev.LEVEL_WARN)


class TestFailLoud(unittest.TestCase):
    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            sev.level_of("severity", 5)
        with self.assertRaises(ValueError):
            sev.level_of("", 5)


class TestLegendMatchesPolicy(unittest.TestCase):
    """图例在页面上替人解释颜色——阈值改了图例不改，人就又被骗一次。"""

    def test_legend_states_action_min(self):
        self.assertIn("≥%d" % sev.ACTION_MIN, HTML,
                      "首页图例必须写出待处理阈值（≥3）")

    def test_legend_explains_stock_neutral(self):
        self.assertIn("不是待办的活", HTML)
        self.assertIn("累计", HTML)

    def test_report_threshold_equals_severity_constant(self):
        """页面里 `REP_MIN_DEFAULT` 是 JS 常量，后端阈值是 Python 常量——
        两边各写一份就会漂（漂了页面报的是别的阈值）。这里钉住相等。"""
        import re
        m = re.search(r"const REP_MIN_DEFAULT\s*=\s*(\d+);", HTML)
        self.assertIsNotNone(m, "页面必须有 REP_MIN_DEFAULT（报告页次数阈值）")
        self.assertEqual(int(m.group(1)), sev.ACTION_MIN,
                         "页面阈值 REP_MIN_DEFAULT 与 severity.ACTION_MIN 不一致")


class TestThresholdLedger(unittest.TestCase):
    """方案 B（用户 2026-10-23 批准）：**每个阈值都要说清用途**，
    否则"同一个词四种口径"的漂移还会再来（A 区用 2、报告页用 3 时无人解释）。"""

    def test_ledger_covers_all_four_meanings(self):
        got = {t["id"] for t in sev.THRESHOLDS}
        self.assertEqual(got, {"recur", "sample", "fresh", "zero"})

    def test_ledger_values_are_the_live_constants(self):
        """登记表里的数必须就是代码用的数（写两份 = 又一套口径）。"""
        by = {t["id"]: int(t["value"]) for t in sev.THRESHOLDS}
        self.assertEqual(by["recur"], sev.ACTION_MIN)
        self.assertEqual(by["sample"], sev.RATE_MIN_SAMPLE)
        self.assertEqual(by["fresh"], sev.NEW_PATTERN_MIN)
        self.assertEqual(by["zero"], sev.WATCH_MAX)

    def test_each_entry_says_purpose_and_scope(self):
        for t in sev.THRESHOLDS:
            self.assertTrue(t["purpose"].strip(), "阈值 %s 缺用途" % t["id"])
            self.assertTrue(t["applies"].strip(), "阈值 %s 缺适用范围" % t["id"])
        notes = sev.threshold_notes()
        self.assertEqual(len(notes), len(sev.THRESHOLDS))
        self.assertTrue(all("：" in n for n in notes))

    def test_page_reports_first_seen_among_hidden(self):
        """被滤掉的尾部里若有"窗口内首见"的坑，必须单独报数——
        这是方案 B 里唯一真正新增的可见性（新意 ≠ 次数）。"""
        self.assertIn("freshAmong", HTML)
        self.assertIn("本窗口首见的新坑", HTML)
        self.assertIn("freshAmong(pGate.under", HTML,
                      "报告页余量句必须真的把首见条数算进去（不是写死）")


class _RealCfg:
    """真库 cfg（与 test_v40 同款路径；缺库时整类 skip）。"""

    def __init__(self, up: Path):
        self.db = up / "harvester.db"
        self.suggestions_meta = up / "suggestions_meta.db"
        self.topics_meta = up / "topics_meta.db"
        self.keywords_meta = up / "keywords_meta.db"
        self.artifacts_meta = up / "artifacts_meta.db"
        self.cards_root = Path.home() / ".workbuddy" / "knowledge" / "cards"
        self.chain_root = Path.home() / ".workbuddy" / "knowledge" / "topics"
        self.exports_root = up / "exports"


UP = ROOT.parent / "session-harvester"


@unittest.skipUnless((UP / "harvester.db").is_file(),
                     "缺真库 harvester.db（跨仓路径），跳过对账")
class TestOverviewArmsUseSeverity(unittest.TestCase):
    """页面队列明细段原先**自己算一遍** "非零即黄"（第二套口径）。

    第②步把它并到 `severity.py`；本用例用真库跑 `build_overview`，
    断言明细段也带 kind，且累计型明细段不许是 warn。
    """

    @classmethod
    def setUpClass(cls):
        from actions import overview as OV
        cls.d = OV.build_overview(_RealCfg(UP))

    def test_every_detail_arm_has_kind(self):
        arms = [a for rows in (self.d.get("queue_details") or {}).values()
                for a in rows]
        self.assertTrue(arms, "真库应当有队列明细段")
        for a in arms:
            self.assertIn(a.get("kind"), sev.KINDS,
                          "明细段 %r 没有 kind（页面会退回非零即黄）"
                          % (a.get("metric"),))

    def test_stock_detail_arms_are_never_warn(self):
        stock = [a for rows in (self.d.get("queue_details") or {}).values()
                 for a in rows if a.get("kind") == sev.KIND_STOCK]
        for a in stock:
            if int(a.get("value") or 0) > 0:
                self.assertEqual(a["level"], sev.LEVEL_INFO,
                                 "累计型明细段 %r=%s 仍在报警"
                                 % (a.get("metric"), a.get("value")))

    def test_ring_arms_carry_kind(self):
        for r in self.d.get("rings") or []:
            for a in r.get("arms") or []:
                self.assertIn(a.get("kind"), sev.KINDS)
                self.assertIn("level_why", a)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()