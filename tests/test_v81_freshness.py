# -*- coding: utf-8 -*-
"""v0.81 P1-4 库龄 / STALE（actions/overview.py 的 freshness 块）测试。

验收口径（与 build_freshness docstring 一一对应）：
1. **三值分开**：db_mtime / corpus_indexed_mtime / stale 各自独立，不合成
   一个"新鲜度分数"；
2. stale 的**基准是入库水位**（corpus_indexed_mtime），不是库文件 mtime
   ——后者会给出假 STALE（无变化的 update 不落盘）；
3. **"没判定"（None）绝不写成"新鲜"（False）**——未开 --corpus-watermark、
   或库里没水位记录，两种都必须是 None 并各有各的说明；
4. 库龄 age_days 优先取入库水位；没有水位才退让到 db_mtime，并把
   age_basis 标成 `db_mtime`（页面据此写明"可能偏旧"）；
5. _fp_with_freshness 在库快照上覆写真值，但**不改**上游原有的其他字段。
"""

from __future__ import annotations

import time
import unittest

from actions.overview import _fp_with_freshness, build_freshness


class _Cfg:
    """最小 cfg 桩：只需要 corpus_mtime()（Config 的同名方法）。"""

    def __init__(self, cm=None):
        self._cm = cm

    def corpus_mtime(self):
        return self._cm


def _ts(days_ago: float) -> str:
    """days_ago 天前 → 'YYYY-MM-DD HH:MM:SS'（本地时间，与 dbmeta 同格式）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.localtime(time.time() - days_ago * 86400.0))


def _fp(**kw) -> dict:
    base = {"sessions": 1720, "steps": 70864, "errors": 362,
            "db_mtime": _ts(10), "generated_at": _ts(0)}
    base.update(kw)
    return base


class TestFreshness(unittest.TestCase):

    # ---- ① 语料比入库水位新 ⇒ STALE（真阳性） ----

    def test_stale_true_when_corpus_newer(self):
        water = _ts(5)                       # 5 天前入库
        d = build_freshness(_Cfg(time.time() - 86400.0),   # 语料 1 天前动过
                            _fp(corpus_indexed_mtime=water))
        self.assertIs(d["stale"], True)
        self.assertEqual(d["corpus_indexed_mtime"], water)
        self.assertIsNotNone(d["corpus_mtime"])
        self.assertIn("语料比入库水位新", d["note"])
        self.assertIn("跑一次 update", d["note"])
        self.assertEqual(d["age_basis"], "corpus_indexed_mtime")
        self.assertAlmostEqual(d["age_days"], 5.0, delta=0.2)

    # ---- ② 语料不比水位新 ⇒ 新鲜（真阴性） ----

    def test_stale_false_when_corpus_older(self):
        water = _ts(2)
        d = build_freshness(_Cfg(time.time() - 3 * 86400.0),
                            _fp(corpus_indexed_mtime=water))
        self.assertIs(d["stale"], False)
        self.assertIn("一致", d["note"])
        self.assertIn("不是", d["note"])       # 明说基准不是库文件 mtime

    # ---- ③ 未开开关 = 没判，不是新鲜 ----

    def test_no_watermark_switch_is_none_not_false(self):
        d = build_freshness(_Cfg(None), _fp(corpus_indexed_mtime=_ts(30)))
        self.assertIsNone(d["stale"])
        self.assertIsNone(d["corpus_mtime"])
        self.assertIn("--corpus-watermark", d["note"])
        self.assertIn("没判", d["note"])
        self.assertIn("不是「新鲜」", d["note"])   # 明着否认，别让人读成新鲜

    # ---- ④ 库里没有水位记录 = 没判，不是新鲜 ----

    def test_no_water_record_is_none_not_false(self):
        d = build_freshness(_Cfg(time.time()), _fp())
        self.assertIsNone(d["stale"])
        self.assertIsNone(d["corpus_indexed_mtime"])
        self.assertIn("还没有入库水位记录", d["note"])
        self.assertIn("没判", d["note"])
        # 没有水位 → 库龄退让到 db_mtime，并标出基准
        self.assertEqual(d["age_basis"], "db_mtime")
        self.assertAlmostEqual(d["age_days"], 10.0, delta=0.2)

    # ---- ⑤ 什么都没有：诚实的 None，不造数 ----

    def test_empty_fingerprint(self):
        d = build_freshness(_Cfg(None), None)
        self.assertIsNone(d["stale"])
        self.assertIsNone(d["age_days"])
        self.assertIsNone(d["age_basis"])
        self.assertIsNone(d["db_mtime"])

    # ---- ⑥ cfg 没有 corpus_mtime 方法（旧 cfg）也不炸 ----

    def test_cfg_without_corpus_mtime(self):
        class _Bare:
            pass
        d = build_freshness(_Bare(), _fp(corpus_indexed_mtime=_ts(1)))
        self.assertIsNone(d["stale"])

    # ---- ⑦ 覆写：真值进指纹，上游原字段不动 ----

    def test_fp_with_freshness_overrides_only_age_fields(self):
        water = _ts(5)
        src = _fp(corpus_indexed_mtime=water, stale=None)
        out = _fp_with_freshness(_Cfg(time.time() - 86400.0), src)
        self.assertIs(out["stale"], True)
        self.assertIsNotNone(out["corpus_mtime"])
        self.assertAlmostEqual(out["age_days"], 5.0, delta=0.2)
        # 上游原有字段一个都不能被吃掉
        for k in ("sessions", "steps", "errors", "db_mtime",
                  "corpus_indexed_mtime", "generated_at"):
            self.assertEqual(out[k], src[k])
        # 不凭空造字段（未开开关时 corpus_mtime 仍不该被写进去）
        off = _fp_with_freshness(_Cfg(None), src)
        self.assertNotIn("corpus_mtime", off)
        self.assertIsNone(off["stale"])

    def test_fp_with_freshness_handles_none(self):
        out = _fp_with_freshness(_Cfg(None), None)
        self.assertIsNone(out["stale"])
        self.assertIsNone(out["age_days"])


if __name__ == "__main__":
    unittest.main()
