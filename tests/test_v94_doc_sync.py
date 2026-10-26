# -*- coding: utf-8 -*-
"""v0.94 门：页面文案不许写死「有几张卡 / 有几段」。

**为什么要有这道门（2026-10-26 用户原话）**：
「现在还是叫四队列么？」——页面上确实还写着「四队列 / 四队活（裁决/修补/
蒸馏/验证）」，而 P14 已经加了第五张卡 `queue:artifact`。**数据是动态渲染的
（页面显示 5 张），文案是硬编码的（说 4 张）**，人一眼看上去就是自相矛盾。

这是个反复出现的坑，不是一次疏忽：
- P14 加第五张卡 → 页面标题/legend/帮助文案 8 处「四队列」没跟；
- 同一句 legend 里还写着「每根环是一条流水线的三段」，而 skill 环只有两段
  （「修复」段没有台账），人在页面上读到的和点开的数字对不上。

所以这道门的判据不是「现在的数字对不对」，而是 **「页面上敢不敢写死数量」**：
一旦写死，下次设计变更必然过期。数字只能来自服务端，文案只能说「有几张由
登记表决定」。

三条断言：
1. 页面正文不许出现「<数字>队」写法（四队列 / 五队活 / 4 队…）；
2. 页面不许断言某条流水线固定「三段」（skill 环只有两段）；
3. 反向：`QUEUE_TARGETS` 与 `workitems` 里登记的 `queue:*` 必须一一对应——
   门 1 管文案、这条管数据，两边都不许偷偷少一张。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import overview as OV  # noqa: E402
from actions import workitems as WI  # noqa: E402

HTML = ROOT / "static" / "index.html"

#: 「数量 + 队」的写法：四队列 / 五队活 / 4 队 / ３队（全角数字也算）。
#: **只拦 2 以上**：中文里「一队」是量词口语（「新加一队活」「这一队是空的」），
#: 不是在设计上写死数量——第一版正则连它也拦，误报两条，故收紧到 2..9。
NUM_TEAM = re.compile(r"[2-9０-９二三四五六七八九十]\s*队")

#: 被写死段数的句子（不是"两段提交"那种无关用法：这里要求同时出现流水线/环）。
HARD_SEG = re.compile(r"流水线\s*的\s*[0-9０-９一二三四五六七八九十]\s*段")


class NoHardcodedCounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_bytes().decode("utf-8")
        #: 只扫正文与脚本，注释里的历史记录不拦（注释不给人看，也不会误导人）。
        cls.live = "\n".join(
            ln for ln in cls.html.splitlines()
            if not ln.lstrip().startswith("<!--"))

    def test_no_hardcoded_queue_count(self):
        hits = [(i + 1, ln.strip()[:120])
                for i, ln in enumerate(self.live.splitlines())
                if NUM_TEAM.search(ln)]
        self.assertEqual(hits, [],
                         "页面写死了队列张数——数字必须由服务端给，文案不许写"
                         "「四队列 / 五队活」这类带数量的说法（加一张卡就过期）：\n"
                         + "\n".join("  行 %d: %s" % h for h in hits))

    def test_no_hardcoded_ring_segment_count(self):
        hits = [(i + 1, ln.strip()[:120])
                for i, ln in enumerate(self.live.splitlines())
                if HARD_SEG.search(ln)]
        self.assertEqual(hits, [],
                         "页面断言了流水线固定几段——skill 环只有两段（修复段"
                         "没有台账），写「三段」与点开的数字自相矛盾：\n"
                         + "\n".join("  行 %d: %s" % h for h in hits))

    def test_legend_points_at_the_registry_not_a_number(self):
        """文案要给出「数量从哪来」，否则下次加卡还是没人知道该改哪。"""
        self.assertIn("KINDS", self.html,
                      "legend 没指向登记表（workitems.KINDS）——人不知道该看哪"
                      "才知道一共有几张卡")

    def test_queue_targets_match_registered_kinds(self):
        """数据面：QUEUE_TARGETS 与登记表里的「卡片级」queue target 必须相等。

        KINDS 里 `queue:distill.cards` 这种带点的是**明细**（同一张卡的下一层），
        不是卡片本身；卡片是 `queue:<名>` 不带点。混进判据会把明细当卡片数。
        """
        registered = {k for k in WI.KINDS
                      if k.startswith("queue:") and "." not in k}
        self.assertEqual(set(OV.QUEUE_TARGETS), registered,
                         "overview 的队列卡与 workitems 登记的 queue 类 target "
                         "对不上：页面会少卡或多一张没有明细的卡")

    def test_ring_defs_do_not_promise_a_third_skill_segment(self):
        """skill 环只有两段：不许在 RING_DEFS 里登记第三段（登记了就会 KeyError 或假数据）。"""
        seg = dict((rid, targets) for rid, _title, _defn, targets in OV.RING_DEFS)
        self.assertEqual(len(seg["skill"]), 2,
                         "skill 环的「修复」段还没有台账，不能凑第三段")


if __name__ == "__main__":
    unittest.main()
