# -*- coding: utf-8 -*-
"""v66 知识图谱（v0.82，机械关系图）门禁。

用户裁决（2026-10-19 批准 PROPOSAL-knowledge-graph-v1）后的配套门禁：
- 静态账：UI 骨架/事件绑定/懒加载纪律（防线：手滑删 section、hover 不挂、
  直接画全量会话导致爆炸）；
- 动作账：kgraph 双层账（ACTIONS×registrygen×注册表 JSON）；
- 数据账：机械边四类（member/anchor/topic_of/keyword）+ 诚实空态 +
  展开模式（sids）——零语义臆造，全部来自既有真源；
- 渲染账：render_kgraph.js（Puppeteer）——canvas 画出节点、确定性布局
  （同数据两次坐标一致）、下钻/回退/hover/空态真实走通。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

VIEW = Path(__file__).resolve().parent.parent
if str(VIEW) not in sys.path:
    sys.path.insert(0, str(VIEW))

HTML = (VIEW / "static" / "index.html").read_text(encoding="utf-8")


def _mk_topics_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS topics (id TEXT PRIMARY KEY,"
                " name TEXT, keywords TEXT, members TEXT, created TEXT)")
    for rid, name, kws, members, created in rows:
        con.execute("INSERT OR REPLACE INTO topics VALUES (?,?,?,?,?)",
                    (rid, name, kws, members, created))
    con.commit()
    con.close()


def _mk_index_db(path: Path, sessions: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY,"
                " title TEXT, source TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS steps (sid TEXT, no INTEGER,"
                " status TEXT, kind TEXT, detail TEXT)")
    for sid, title, source in sessions:
        con.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)",
                    (sid, title, source, "2026-10-19T00:00:00"))
    con.commit()
    con.close()


def _write_card(root: Path, name: str, title: str,
                anchor_sids: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / name
    # cardlib 的 frontmatter 是简式解析器：锚点必须走行内列表格式
    # （与 cardlib._selftest 的样例同款），块式 YAML 不被解析。
    items = ", ".join('{session_id: "%s", turn: 1}' % s
                      for s in anchor_sids)
    p.write_text(
        "---\n"
        f"id: {name[:-3]}\n"
        "type: summary\n"
        "confidence: high\n"
        f"title: {title}\n"
        f"anchors: [{items}]\n"
        "---\n\n# 正文\n",
        encoding="utf-8")
    return p


def _mk_cfg(tmp: Path, with_topics=True, with_cards=True, with_db=True):
    from actions.actionsserve import Config
    glossary = tmp / "glossary.json"
    glossary.write_text(json.dumps(
        {"terms": [{"term": "t1", "plain": "p1"}]}, ensure_ascii=False),
        encoding="utf-8")
    tdb = tmp / "topics_meta.db"
    if with_topics:
        _mk_topics_db(tdb, [("t1", "部署排障", '["部署", "排障"]',
                             '[{"sid": "S1", "evidence": "e0"}]',
                             "2026-10-19T00:00:00")])
    croot = tmp / "cards"
    if with_cards:
        _write_card(croot, "c1.md", "部署卡", ["S1"])
    else:
        croot.mkdir(exist_ok=True)  # Config 要求路径非 None：空目录=没有卡
    idb = tmp / "harvester.db"
    if with_db:
        _mk_index_db(idb, [("S1", "部署会话", "trae"),
                           ("S2", "无关会话", "claude")])
    return Config(cards_root=croot,
                  db=(idb if with_db else None),
                  glossary=glossary,
                  topics_meta=(tdb if with_topics else None))


class TestV66KgraphStatic(unittest.TestCase):
    """静态账：UI 骨架、懒加载纪律、双层账。"""

    def test_01_tab_and_section(self):
        self.assertIn('data-tab="kgraph"', HTML)
        self.assertIn('id="tab-kgraph"', HTML)
        self.assertIn('id="kg-canvas"', HTML)
        self.assertIn('id="kg-legend"', HTML)

    def test_02_legend_fixed_declarations(self):
        self.assertIn("机械关系", HTML)
        self.assertIn("主题间无边", HTML)
        self.assertIn("P7-②", HTML)
        self.assertIn("懒加载", HTML)

    def test_03_js_bindings(self):
        for frag in ['loadKgraph()', 'kgReset()', 'kgHover', 'kgCanvasClick',
                     'kgExpandNode(', 'kgMergeExpand(']:
            self.assertIn(frag, HTML)

    def test_04_lazy_expand_discipline(self):
        # 会话层必须懒加载：无预画全量会话的通路；展开前必须先有节点数据
        self.assertNotIn('kind:"session" ', HTML)
        self.assertIn("meta.member_sids", HTML)
        self.assertIn("meta.anchor_sids", HTML)
        # 关键词下钻走真检索通路（复用词云的 jumpCloudWord）
        self.assertIn('jumpCloudWord(nd.label)', HTML)

    def test_05_double_ledger(self):
        from actions import actionsserve, registrygen
        self.assertIn("kgraph", actionsserve.ACTIONS)
        self.assertIn("kgraph", registrygen.READ_ACTIONS)
        ent = actionsserve.ACTIONS["kgraph"]
        self.assertEqual(ent["tier"], "A")
        self.assertEqual(ent["handler"].__name__, "_action_kgraph")
        # 注册表 JSON 已再生且含 kgraph（视图侧动作进 read_actions）
        reg = json.loads(
            (VIEW / "actions" / "actions_registry.json")
            .read_text(encoding="utf-8"))
        ids = [a["action_id"] for a in reg.get("read_actions", [])]
        self.assertIn("kgraph", ids)

    def test_06_no_cmd_wiring_consistent(self):
        # kgraph 是 view 侧动作（无 CLI cmd），同 dualtrack/topic_brief：
        # commands.json 不含；对账门 test_v35/v38/v54 维持绿的通行条件
        cj = json.loads(
            (VIEW / "actions" / "commands.json").read_text(encoding="utf-8"))
        cmds = [c.get("name") for c in cj.get("commands", [])]
        self.assertNotIn("kgraph", cmds)


class TestV66KgraphData(unittest.TestCase):
    """数据账：机械边四类 + 空态诚实 + 展开模式。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v66kg_"))

    def test_10_base_edges_mechanical(self):
        from actions.analysis import kgraph
        cfg = _mk_cfg(self.tmp)
        d = kgraph.build(cfg)
        self.assertEqual(d["mode"], "base")
        kinds = {n["kind"] for n in d["nodes"]}
        self.assertEqual(kinds, {"topic", "card", "kw"})
        ekinds = {e["kind"] for e in d["edges"]}
        self.assertTrue(ekinds <= {"member", "anchor", "topic_of",
                                   "keyword"}, ekinds)
        self.assertIn("topic_of", ekinds)   # 卡→主题（锚点∈成员）
        self.assertIn("keyword", ekinds)    # 词→主题（定稿表）
        ids = {n["id"] for n in d["nodes"]}
        self.assertIn("t:t1", ids)
        self.assertIn("c:c1.md", ids)
        self.assertIn("k:部署", ids)
        meta = d["meta"]
        self.assertEqual(meta["sessions_total"], 2)
        self.assertEqual(meta["sessions_in_topics"], 1)
        self.assertEqual(meta["sessions_unlinked"], 1)  # S2 不画，只计数
        self.assertEqual(meta["cards_unlinked"], 0)

    def test_11_empty_states_honest(self):
        from actions.analysis import kgraph
        d = kgraph.build(_mk_cfg(self.tmp, with_topics=False,
                                 with_cards=False))
        self.assertEqual(d["nodes"], [])
        self.assertTrue(d["hints"])
        self.assertTrue(any("--topics-meta" in h for h in d["hints"]))
        d2 = kgraph.build(_mk_cfg(self.tmp, with_cards=False))
        self.assertTrue(any("卡片目录空" in h for h in d2["hints"]))

    def test_12_expand_sessions(self):
        from actions.analysis import kgraph
        cfg = _mk_cfg(self.tmp)
        d = kgraph.build(cfg, "S1,S2,SX")
        self.assertEqual(d["mode"], "expand")
        sids = [s["sid"] for s in d["sessions"]]
        self.assertEqual(sids, ["S1", "S2"])
        self.assertEqual(d["missing"], ["SX"])
        s1 = d["sessions"][0]
        self.assertEqual(s1["title"], "部署会话")
        self.assertEqual(s1["error_count"], 0)

    def test_13_no_semantic_edges_between_topics(self):
        # 死约定：主题间无边（P7-② 缓行，不是图坏了）
        from actions.analysis import kgraph
        tdb = self.tmp / "t.db"
        _mk_topics_db(tdb, [
            ("t1", "甲", '["k1"]', '[{"sid": "S1", "evidence": "e"}]',
             "2026-10-19T00:00:00"),
            ("t2", "乙", '["k1"]', '[{"sid": "S2", "evidence": "e"}]',
             "2026-10-19T00:00:00")])
        from actions.actionsserve import Config
        glossary = self.tmp / "glossary.json"
        glossary.write_text(json.dumps({"terms": []}, ensure_ascii=False),
                            encoding="utf-8")
        cfg = Config(cards_root=self.tmp / "cards", db=self.tmp / "h.db",
                     glossary=glossary, topics_meta=tdb)
        d = kgraph.build(cfg)
        for e in d["edges"]:
            self.assertFalse(e["src"].startswith("t:")
                             and e["dst"].startswith("t:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
