# -*- coding: utf-8 -*-
"""P4-1/P4-3（v0.52 会话 8）测试：反复叮嘱 / 打磨候选 两个分析模块。

用户裁决（2026-10-13）：**两个模块都建**——"反复叮嘱"有两层语义：
  层一（P4-1 nags）：对工具/skill/Agent 调用不能很好遵循指令、过了就忘，
    需要反复提醒 → 挖**指令遵循类**反复叮嘱；
  层二（P4-3 revisions）：对产出品（作品/设计）反复打磨、推敲琢磨 →
    挖**打磨带**候选。
（探针会话 7 的"不建"建议被推翻；但探钉下的**口径**全部保留——
  三层过滤只属 P4-1；P4-3 只剥注入、允许粘贴。）

本文件钉住：
1. 口径继承（探针 v2 = 判据原话）：注入剥离 / 剥后过短 / 长粘贴（仅 P4-1）/
   逐字模板（≥8 次）/ 相似归并（0.55）/ 边界带 0.80–0.90（H40 冻结口径，
   直接 import harvester.topicprep 的阈值，勿在本仓复刻数字）；
2. **必须读 raw**（text 是 FTS bigram 分词列——交接 §5 第 9 条）；
3. 锚点 = messages.rowid（messages 无 seq 列）；
4. 只读：模块源码不得出现任何写库语句（INSERT/UPDATE/DELETE/CREATE/
   DROP/ATTACH），对真库零改动是 P4-1/P4-3 的验收项；
5. 上游缺失时诚实报错（SystemExit 带人话），不静默降级出空报告。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
VIEW_ROOT = _HERE.parent
sys.path.insert(0, str(VIEW_ROOT))

from actions import ensure_harvester_importable  # noqa: E402

_ERR = ensure_harvester_importable()
if _ERR is None:
    from harvester.topicprep import BAND_THRESHOLD, DUP_THRESHOLD  # noqa: E402
    from actions.analysis import nags, revisions  # noqa: E402

MSG_DDL = ("CREATE TABLE messages(sid TEXT, role TEXT, ts TEXT, "
           "text TEXT DEFAULT '', raw TEXT DEFAULT '')")


def _mkdb(rows):
    """rows: list[(sid, role, raw)]；text 列故意留空——模块必须走 raw。"""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    con = sqlite3.connect(f.name)
    con.execute(MSG_DDL)
    con.executemany("INSERT INTO messages(sid, role, ts, text, raw) "
                    "VALUES (?,?,datetime('now'),'',?)", rows)
    con.commit()
    return con


def _u(sid, raw):
    return (sid, "user", raw)


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestNagsCollect(unittest.TestCase):
    """P4-1 nags.collect：三层过滤 + 跨会话聚类。"""

    def test_account_and_cross_session_group(self):
        instr = "踩到的坑记到记忆中以后不要再踩"
        doc = "做完了注意要更新文档和操作手册"
        rows = [_u("s1", instr), _u("s2", instr),          # 跨 2 会话逐字组
                _u("s1", "这是一个与叮嘱无关的正常发言"),
                # 纯注入轮：剥完没剩东西 → 剥后过短
                _u("s1", "<system-reminder>只有注入</system-reminder>"),
                # 注入+正文：剥掉注入后正文参与（跨 2 会话）
                _u("s2", "<system-reminder>运行时上下文</system-reminder>" + doc),
                _u("s3", doc),
                # 长粘贴（>600 字）：只出 P4-1 通道
                _u("s3", "长" * 700)]
        con = _mkdb(rows)
        try:
            r = nags.collect(con)
        finally:
            con.close()
        a = r["account"]
        self.assertEqual(a["user_turns"], 7)
        self.assertEqual(a["n_inject"], 2)      # 两轮含注入标记
        self.assertEqual(a["n_paste"], 1)       # 长粘贴只滤出 P4-1 通道
        # 跨会话逐字组在候选里，锚点是 rowid
        g2 = [g for g in r["groups"] if "踩到的坑" in g["sample"]["preview"]]
        self.assertEqual(len(g2), 1)
        self.assertEqual(len(g2[0]["sids"]), 2)
        self.assertEqual(g2[0]["n"], 2)
        self.assertTrue(all(m["rowid"] > 0 for m in g2[0]["members"]))
        self.assertTrue(g2[0]["sample"].get("ts"),
                        "样例自带 ts（双轨候选一跳原文的精确锚点，v0.78）")
        # 注入+正文轮：剥注入后参与命中（"注意"在词表里）
        g1 = [g for g in r["groups"] if "更新文档" in g["sample"]["preview"]]
        self.assertEqual(len(g1), 1)
        self.assertEqual(len(g1[0]["sids"]), 2)

    def test_template_filtered(self):
        t = "记得先跑测试再交付产物"
        rows = [_u("s%d" % (i % 2), t) for i in range(8)]  # ≥8 次 = 模板
        con = _mkdb(rows)
        try:
            r = nags.collect(con)
        finally:
            con.close()
        self.assertEqual(r["account"]["n_template_groups"], 1)
        self.assertEqual(r["account"]["n_template_turns"], 8)
        self.assertEqual(r["groups"], [])

    def test_similar_variants_merged(self):
        """同句话换个说法、各自跨 ≥2 会话 → 相似归并成一组（0.55 宽松）。"""
        a = "做完了注意要更新文档仓库结构说明"
        b = "做完了注意要更新文档仓库结构说明啊"   # 换个说法，J≥0.55
        con = _mkdb([_u("s1", a), _u("s2", a), _u("s3", b), _u("s4", b)])
        try:
            r = nags.collect(con)
        finally:
            con.close()
        self.assertEqual(len(r["groups"]), 1)
        self.assertEqual(len(r["groups"][0]["sids"]), 4)
        self.assertEqual(r["groups"][0]["n"], 4)

    def test_reads_raw_not_text(self):
        """raw 为空时才回退 text；raw 在就必须赢（text 可能是 bigram 列）。"""
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        con = sqlite3.connect(f.name)
        con.execute(MSG_DDL)
        con.executemany(
            "INSERT INTO messages(sid,role,text,raw) VALUES(?,?,?,?)",
            [("s1", "user", "丁樾 樾瘫 瘫坐", "踩到的坑记到记忆中以后不要再踩"),
             ("s2", "user", "了樾 樾瘫 瘫坐", "踩到的坑记到记忆中以后不要再踩")])
        con.commit()
        try:
            r = nags.collect(con)
        finally:
            con.close()
        self.assertEqual(len(r["groups"]), 1)   # 按 raw 命中
        self.assertIn("踩到的坑", r["groups"][0]["sample"]["preview"])


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestRevisionsCollect(unittest.TestCase):
    """P4-3 revisions.collect：边界带 + 转折词（只认轮次开头）。"""

    # 相邻重复串会多出"接缝 bigram"破坏 J 值——用 12 个互异字保证
    # bigram 全单发：T1 vs T2 恰差末字 → J = 10/12 = 0.833（边界带）
    T1 = "甲乙丙丁戊己庚辛壬癸子丑"
    T2 = "甲乙丙丁戊己庚辛壬癸子寅"

    def test_band_and_dup_classification(self):
        con = _mkdb([_u("s1", self.T1), _u("s1", self.T2),   # J=0.80 → band
                     _u("s1", self.T1),                      # (T2,T1)=0.80 band
                     _u("s1", self.T1)])                     # (T1,T1)=1.0 → dup
        try:
            r = revisions.collect(con)
        finally:
            con.close()
        a = r["account"]
        self.assertEqual(a["n_pairs"], 3)
        self.assertEqual(a["n_dup"], 1)                     # 1.0 ≥ DUP_THRESHOLD
        self.assertEqual(len(r["band"]), 2)
        self.assertTrue(all(BAND_THRESHOLD <= p["jaccard"] < DUP_THRESHOLD
                            for p in r["band"]))
        self.assertEqual(BAND_THRESHOLD, 0.80)              # H40 冻结口径勿改
        self.assertEqual(DUP_THRESHOLD, 0.90)

    def test_turn_word_head_only(self):
        rows = [_u("s1", "不对，这个方案方向错了，我们重来一版"),
                _u("s1", "我觉得不对，但还是要先跑通"),      # 不在开头 → 不算
                _u("s2", "算了，按你说的来吧，就到这里")]
        con = _mkdb(rows)
        try:
            r = revisions.collect(con)
        finally:
            con.close()
        hits = [(h["sid"], h["word"]) for h in r["turn_hits"]]
        self.assertIn(("s1", "不对"), hits)
        self.assertIn(("s2", "算了"), hits)
        self.assertNotIn(("s1", "我觉得不对"), [h["word"] for h in r["turn_hits"]])
        self.assertEqual(len(hits), 2)

    def test_paste_allowed_and_injection_stripped(self):
        """探针教训回归：长粘贴是打磨主信号，P4-3 通道必须留着。"""
        base = "".join(chr(0x4E00 + i) for i in range(800))   # 800 互异字
        near = base[:400] + "改" + base[401:]                 # 单字替换 → J≈0.99
        rows = [_u("s1", "<system-reminder>注入</system-reminder>" + base),
                _u("s1", near)]
        con = _mkdb(rows)
        try:
            r = revisions.collect(con)
        finally:
            con.close()
        self.assertEqual(r["account"]["n_paste_gate"], 0)   # P4-3 无粘贴闸
        self.assertGreaterEqual(r["account"]["n_dup"], 1)


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestReadOnly(unittest.TestCase):
    """验收项：session-harvester 零改动——模块源码不得含写库语句。"""

    def test_modules_are_read_only(self):
        for mod in (nags, revisions):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            for bad in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ",
                        "DROP ", "ATTACH ", "PRAGMA writable"):
                self.assertNotIn(bad, src, "%s 含写库语句 %r" % (mod.__name__, bad))


@unittest.skipIf(_ERR is not None, "上游 session-harvester 不在（%s）" % (_ERR,))
class TestMarkdown(unittest.TestCase):
    def test_render_markdown_contains_account_and_anchors(self):
        instr = "踩到的坑记到记忆中以后不要再踩"
        con = _mkdb([_u("s1", instr), _u("s2", instr),
                     _u("s1", TestRevisionsCollect.T1),
                     _u("s1", TestRevisionsCollect.T2)])
        try:
            nr = nags.collect(con)
            rr = revisions.collect(con)
        finally:
            con.close()
        md = nags.render_markdown(nr) + "\n" + revisions.render_markdown(rr)
        self.assertIn("踩到的坑", md)
        self.assertIn("rowid", md)
        self.assertIn("0.80", md)                # 边界带口径可见
        self.assertNotIn("\r\n", md)             # 落盘纪律：LF


if __name__ == "__main__":
    unittest.main(verbosity=2)
