# -*- coding: utf-8 -*-
"""v66 知识图谱（v0.84，用户发起×相近聚合×黑底）门禁。

用户裁决（2026-10-20 四点）：①只取用户先发起的会话；②相近会话聚类
分层；③一簇只显示一个代表，点击才展开；④黑底画布。

- 静态账：UI 骨架/黑底钉色/簇交互绑定/四点声明进图例（防线：手滑删
  section、黑底没钉 scheme、展开交互丢失）；
- 动作账：kgraph 双层账（ACTIONS×registrygen×注册表 JSON）；
- 数据账：用户发起过滤（首条 role='user'，机械真源 messages 表）+
  聚类判据（标题 Jaccard≥0.45 或共享定稿关键词）+ 簇代表结构 +
  五类机械边 + 账目诚实（过滤/聚合/截断/封顶）+ 无边诚实；
- 渲染账：render_kgraph.js（DOM 桩）——显隐集、簇展开/收起、
  缩放平移、hover 邻域、下钻、空态、失败态。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

VIEW = Path(__file__).resolve().parent.parent
if str(VIEW) not in sys.path:
    sys.path.insert(0, str(VIEW))

HTML = (VIEW / "static" / "index.html").read_text(encoding="utf-8")


def _cjk_bigram(text: str) -> str:
    """索引侧 bigram 改写（与主仓 indexing._cjk_bigram 同规则）——
    messages.text 必须模拟真索引形态，FTS 查询才能命中。"""
    out: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", text):
        if "\u4e00" <= run[0] <= "\u9fff" and len(run) > 1:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            out.append(run)
    return " ".join(out)


def _mk_topics_db(path: Path, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS topics (id TEXT PRIMARY KEY,"
                " name TEXT, keywords TEXT, members TEXT, created TEXT)")
    for rid, name, kws, members, created in rows:
        con.execute("INSERT OR REPLACE INTO topics VALUES (?,?,?,?,?)",
                    (rid, name, kws, members, created))
    con.commit()
    con.close()


def _mk_index_db(path: Path, sessions: list[tuple],
                 first_roles: dict[str, str] | None = None) -> None:
    """sessions: (sid, title, source, updated_at)。
    first_roles: sid -> 首条消息 role（默认全 user）；
    messages 行按传入顺序视作时间序（ts 递增生成）。"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY,"
                " title TEXT, source TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS steps (sid TEXT, no INTEGER,"
                " status TEXT, kind TEXT, detail TEXT)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages USING"
                " fts5(sid UNINDEXED, role UNINDEXED, ts UNINDEXED, text)")
    for sid, title, source, updated in sessions:
        con.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?)",
                    (sid, title, source, updated))
    fr = first_roles or {}
    for sid, title, _s, _u in sessions:
        role0 = fr.get(sid, "user")
        con.execute("INSERT INTO messages (sid, role, ts, text)"
                    " VALUES (?,?,?,?)", (sid, role0, "2026-10-19T09:00:00",
                                          _cjk_bigram(title)))
        con.execute("INSERT INTO messages (sid, role, ts, text)"
                    " VALUES (?,?,?,?)",
                    (sid, "assistant", "2026-10-19T09:01:00", "好的"))
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
        _mk_index_db(idb, [
            ("S1", "部署会话：网关重启", "trae", "2026-10-19T10:00:00"),
            ("S2", "无关会话：买菜清单", "claude", "2026-10-19T11:00:00"),
        ])
        con = sqlite3.connect(idb)
        con.execute("INSERT INTO steps VALUES ('S1', 1, 'error', 'tool',"
                    " 'boom')")
        con.commit()
        con.close()
    return Config(cards_root=croot,
                  db=idb,  # with_db=False 时不建文件：不存在=诚实空态路径
                  glossary=glossary,
                  topics_meta=(tdb if with_topics else None))


class TestV66KgraphStatic(unittest.TestCase):
    """静态账：UI 骨架、四点纪律、黑底钉色、双层账。"""

    def test_01_tab_and_section(self):
        self.assertIn('data-tab="kgraph"', HTML)
        self.assertIn('id="tab-kgraph"', HTML)
        self.assertIn('id="kg-chart"', HTML)
        self.assertIn('id="kg-legend"', HTML)
        self.assertIn('id="kg-fit"', HTML)

    def test_02_legend_four_points(self):
        # 四点裁决全部进图例（防误读+防回退）
        self.assertIn("只画用户先发起", HTML)
        self.assertIn("只显示代表", HTML)
        self.assertIn("代表点击直达原文", HTML)
        self.assertIn("Jaccard", HTML)              # 聚类判据声明
        self.assertIn("黑底", HTML)
        self.assertIn("P7-②", HTML)
        self.assertIn("M2/M4", HTML)
        self.assertIn("标题含词", HTML)

    def test_03_dark_canvas_pinned(self):
        # 黑底固定配色画面：钉 color-scheme:dark（v40 配色门镜像先例）
        m = list(__import__("re").finditer(
            r"#kg-out\{[^}]*\}", HTML))
        self.assertTrue(m, "找不到 #kg-out 规则")
        body = m[0].group(0)
        self.assertIn("#12151c", body)              # 深底写死
        self.assertIn("color-scheme:dark", body)    # 钉声明
        self.assertIn("#d8dae0", body)              # 前景浅色钉住

    def test_04_js_bindings_and_search_ui(self):
        for frag in ['loadKgraph()', 'kgFitView()', 'kgCanvasClick(',
                     'kgAdjBuild()', 'kgVisible()']:
            self.assertIn(frag, HTML)
        # v0.87 照搬 KnowAtlas graph.html（用户裁决「就照搬吧」）：
        # ECharts graph force 渲染器（vendor 外链 + view.py 白名单路由），
        # force/边/交互参数逐字照搬参考实现
        self.assertIn('<script src="vendor/echarts.min.js"></script>', HTML)
        self.assertIn('echarts.init(', HTML)
        self.assertIn('type:"graph",layout:"force",roam:true,draggable:true',
                      HTML)
        self.assertIn('force:{repulsion:90,edgeLength:[30,120],'
                      'gravity:0.08}', HTML)
        self.assertIn('curveness:0.1', HTML)
        self.assertIn('focus:"adjacency"', HTML)
        self.assertIn('notMerge:true', HTML)
        self.assertIn('kgChart.resize()', HTML)
        # 手绘渲染与旧交互函数不得残留（kg 域已整体换渲染器）
        self.assertNotIn('id="kg-canvas"', HTML)
        for gone in ('kgWheel', 'kgMouseDown', 'kgMouseMove', 'kgMouseUp',
                     'kgDblClick', 'kgHover', 'kgDraw', 'kgLayout',
                     'kgPos', 'kgView', 'kgRng', 'quadraticCurveTo(',
                     'createRadialGradient('):
            self.assertNotIn(gone, HTML)
        # v0.86 搜索驱动（用户裁决「开始只显示大类，输入关键字再只显示
        # 相关的」）：搜索框 + kgQuery 联动 + 会话节点蓝色
        self.assertIn('id="kg-q"', HTML)
        self.assertIn('kgQuery', HTML)
        self.assertIn('if(kgQuery)p.q=kgQuery', HTML)
        self.assertIn('if(kgQuery&&sq===qn){', HTML)   # 搜索态命中子图（带守卫）
        self.assertIn('nd.kind==="session")vis.add(nd.id)', HTML)
        self.assertIn("e.src.charAt(0)===\"s\"", HTML)  # 只扩会话直接邻居
        self.assertIn("不得冒充命中集", HTML)           # 守卫声明在注释
        self.assertIn('session:"#4fb0ff"', HTML)    # 用户「黄圆圈改蓝」
        self.assertNotIn("#ffab70", HTML)           # 旧黄不得残留
        self.assertIn('正文 FTS', HTML)              # 搜索判据声明进图例

    def test_05_double_ledger(self):
        from actions import actionsserve, registrygen
        self.assertIn("kgraph", actionsserve.ACTIONS)
        self.assertIn("kgraph", registrygen.READ_ACTIONS)
        ent = actionsserve.ACTIONS["kgraph"]
        self.assertEqual(ent["tier"], "A")
        self.assertEqual(ent["handler"].__name__, "_action_kgraph")
        self.assertIn("q", ent["params"])           # v0.86 搜索参数
        reg = json.loads(
            (VIEW / "actions" / "actions_registry.json")
            .read_text(encoding="utf-8"))
        ids = [a["action_id"] for a in reg.get("read_actions", [])]
        self.assertIn("kgraph", ids)

    def test_06_no_cmd_wiring_consistent(self):
        cj = json.loads(
            (VIEW / "actions" / "commands.json").read_text(encoding="utf-8"))
        cmds = [c.get("name") for c in cj.get("commands", [])]
        self.assertNotIn("kgraph", cmds)


class TestV66KgraphData(unittest.TestCase):
    """数据账：用户发起过滤 + 聚类 + 簇代表 + 五类机械边 + 账目诚实。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="v66kg_"))

    def test_10_user_initiated_filter(self):
        from actions.analysis import kgraph
        tmp = self.tmp
        tdb = tmp / "t.db"
        _mk_topics_db(tdb, [("t1", "甲", '[]', '[]',
                             "2026-10-19T00:00:00")])
        idb = tmp / "h.db"
        _mk_index_db(idb, [
            ("SU", "用户会话", "trae", "2026-10-19T10:00:00"),
            ("SB", "机器会话", "trae", "2026-10-19T11:00:00"),
        ], first_roles={"SU": "user", "SB": "assistant"})
        glossary = tmp / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        from actions.actionsserve import Config
        cfg = Config(cards_root=tmp / "cards", db=idb, glossary=glossary,
                     topics_meta=tdb)
        d = kgraph.build(cfg, max_sessions=100)
        sids = {n["id"] for n in d["nodes"] if n["kind"] == "session"}
        self.assertEqual(sids, {"s:SU"})            # 机器发起的 SB 不画
        sa = d["meta"]["sessions"]
        self.assertEqual(sa["total"], 2)
        self.assertEqual(sa["user_initiated"], 1)
        self.assertEqual(sa["filtered"], 1)
        self.assertTrue(any("role='user'" in h for h in d["hints"])
                        if not d["nodes"] else True)

    def test_11_cluster_by_title_jaccard(self):
        from actions.analysis import kgraph
        tmp = self.tmp
        tdb = tmp / "t.db"
        _mk_topics_db(tdb, [("t1", "甲", '[]', '[]',
                             "2026-10-19T00:00:00")])
        idb = tmp / "h.db"
        _mk_index_db(idb, [
            ("S1", "部署排障网关重启失败", "trae", "2026-10-19T09:00:00"),
            ("S2", "部署排障网关重启超时", "trae", "2026-10-19T10:00:00"),
            ("S3", "买菜清单周末火锅", "claude", "2026-10-19T11:00:00"),
        ])
        glossary = tmp / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        from actions.actionsserve import Config
        cfg = Config(cards_root=tmp / "cards", db=idb, glossary=glossary,
                     topics_meta=tdb)
        d = kgraph.build(cfg, max_sessions=100)
        sess = {n["id"]: n for n in d["nodes"] if n["kind"] == "session"}
        # S1/S2 标题高重叠→同簇；S3 独立。
        # 数据面一次下发全部会话节点（显隐聚合是前端 kgVisible 的账）：
        # 代表带 cluster 成员账、成员带 merged 并入标记、单会话两者皆无。
        self.assertEqual(len(sess), 3)
        rep = sess["s:S2"]                          # updated_at 最新为代表
        self.assertEqual(sorted(rep["meta"]["cluster"]),
                         ["S1", "S2"])
        self.assertIn("簇2", rep["badge"])
        self.assertNotIn("merged", rep["meta"])
        self.assertEqual(sess["s:S1"]["meta"]["merged"], "S2")
        lone = sess["s:S3"]
        self.assertNotIn("cluster", lone["meta"])
        self.assertNotIn("merged", lone["meta"])
        m = d["meta"]
        self.assertEqual(m["clusters"], 2)
        self.assertEqual(m["clusters_multi"], 1)
        self.assertEqual(m["merged_into_rep"], 1)

    def test_12_cluster_by_shared_keyword(self):
        # 共享定稿关键词也归簇（kw_hit 同判据：标题含词）
        from actions.analysis import kgraph
        tmp = self.tmp
        tdb = tmp / "t.db"
        _mk_topics_db(tdb, [("t1", "部署", '["部署"]', '[]',
                             "2026-10-19T00:00:00")])
        idb = tmp / "h.db"
        _mk_index_db(idb, [
            ("S1", "部署：环境准备", "trae", "2026-10-19T09:00:00"),
            ("S2", "部署：网关切流", "trae", "2026-10-19T10:00:00"),
        ])
        glossary = tmp / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        from actions.actionsserve import Config
        cfg = Config(cards_root=tmp / "cards", db=idb, glossary=glossary,
                     topics_meta=tdb)
        d = kgraph.build(cfg, max_sessions=100)
        sess = {n["id"]: n for n in d["nodes"] if n["kind"] == "session"}
        self.assertEqual(len(sess), 2)              # 数据面全下发（显隐前端管）
        rep = sess["s:S2"]                          # updated_at 最新为代表
        self.assertEqual(len(rep["meta"]["cluster"]), 2)
        self.assertEqual(sess["s:S1"]["meta"]["merged"], "S2")

    def test_13_edges_mechanical_and_rep_targeted(self):
        from actions.analysis import kgraph
        cfg = _mk_cfg(self.tmp)
        d = kgraph.build(cfg)
        self.assertEqual(d["mode"], "clustered")
        ekinds = {e["kind"] for e in d["edges"]}
        self.assertTrue(ekinds <= {"member", "anchor", "topic_of",
                                   "keyword", "kw_hit"}, ekinds)
        for must in ("topic_of", "keyword", "member", "anchor"):
            self.assertIn(must, ekinds)
        # S1 首条 user → 入图；S1 是单会话簇代表；锚点/成员边指向代表
        ids = {n["id"] for n in d["nodes"]}
        self.assertIn("s:S1", ids)
        self.assertIn(("t:t1", "s:S1"),
                      [(e["src"], e["dst"]) for e in d["edges"]
                       if e["kind"] == "member"])
        self.assertEqual(d["meta"]["cards_unlinked"], 0)
        # kw_hit：S1 标题含「部署」
        hit = [(e["src"], e["dst"]) for e in d["edges"]
               if e["kind"] == "kw_hit"]
        self.assertIn(("k:部署", "s:S1"), hit)

    def test_14_empty_and_all_filtered_honest(self):
        from actions.analysis import kgraph
        d = kgraph.build(_mk_cfg(self.tmp, with_topics=False,
                                 with_cards=False, with_db=False))
        self.assertEqual(d["nodes"], [])
        self.assertTrue(any("--topics-meta" in h for h in d["hints"]))
        # 全部非 user 发起 → 诚实空态说判据
        tmp2 = Path(tempfile.mkdtemp(prefix="v66kg2_"))
        tdb = tmp2 / "t.db"
        _mk_topics_db(tdb, [("t1", "甲", '[]', '[]',
                             "2026-10-19T00:00:00")])
        idb = tmp2 / "h.db"
        _mk_index_db(idb, [("SB", "机器会话", "trae",
                            "2026-10-19T10:00:00")],
                     first_roles={"SB": "assistant"})
        from actions.actionsserve import Config
        glossary = tmp2 / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        cfg = Config(cards_root=tmp2 / "cards", db=idb, glossary=glossary,
                     topics_meta=tdb)
        d2 = kgraph.build(cfg)
        self.assertEqual([n for n in d2["nodes"]
                          if n["kind"] == "session"], [])
        self.assertTrue(any("用户先发起" in h for h in d2["hints"]))

    def test_15_no_semantic_edges(self):
        # 死约定：主题间无边、会话间无相似度边（聚类只是聚合展示）
        from actions.analysis import kgraph
        from actions.actionsserve import Config
        tdb = self.tmp / "t.db"
        _mk_topics_db(tdb, [
            ("t1", "甲", '["k1"]', '[{"sid": "S1", "evidence": "e"}]',
             "2026-10-19T00:00:00"),
            ("t2", "乙", '["k1"]', '[{"sid": "S2", "evidence": "e"}]',
             "2026-10-19T00:00:00")])
        glossary = self.tmp / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        cfg = Config(cards_root=self.tmp / "cards", db=self.tmp / "h.db",
                     glossary=glossary, topics_meta=tdb)
        d = kgraph.build(cfg)
        for e in d["edges"]:
            sk = e["src"].split(":")[0]
            dk = e["dst"].split(":")[0]
            self.assertFalse(sk == "t" and dk == "t", f"主题间边混入：{e}")
            self.assertFalse(sk == "s" and dk == "s", f"会话间边混入：{e}")

    def test_16_criteria_declared(self):
        # 判据透明：meta.criteria 全声明（可解释，不黑箱）
        from actions.analysis import kgraph
        d = kgraph.build(_mk_cfg(self.tmp))
        cr = d["meta"]["criteria"]
        self.assertIn("role='user'", cr["user_initiated"])
        self.assertIn("Jaccard", cr["cluster"])
        self.assertIn("包含率", cr["cluster"])
        self.assertIn("M2/M4", cr["cluster"])
        self.assertIn("文档频率", cr["hubs"])
        self.assertIn("无语义判断", cr["hubs"])
        self.assertIn("updated_at", cr["representative"])
        self.assertEqual(d["meta"]["auto_hubs"],
                         sum(1 for n in d["nodes"]
                             if n["kind"] == "kw" and n["meta"].get("auto")))

    def test_17_auto_hubs_radiate_not_cluster(self):
        """v0.85：自动枢纽=标题高频片段机械提取，做 kw_hit 辐射节点。
        **反面断言**：枢纽不得参与聚类——同库同数据只切枢纽开关
        （KG_HUB_MIN_DF），两次 build 的簇数必须逐项相等（真库实测
        枢纽进聚类会把数百会话连锁拖成一簇）。"""
        from actions.actionsserve import Config
        from actions.analysis import kgraph
        tmp = self.tmp
        tdb = tmp / "t.db"
        _mk_topics_db(tdb, [("t1", "甲", '[]', '[]',
                             "2026-10-19T00:00:00")])
        idb = tmp / "h.db"
        _mk_index_db(idb, [
            ("S1", "甲乙词释义解析", "trae", "2026-10-19T09:00:00"),
            ("S2", "丙丁词释义解析", "trae", "2026-10-19T10:00:00"),
            ("S3", "戊己词释义解析", "claude", "2026-10-19T11:00:00"),
        ])
        glossary = tmp / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        cfg = Config(cards_root=tmp / "cards", db=idb, glossary=glossary,
                     topics_meta=tdb)
        old = kgraph.KG_HUB_MIN_DF
        try:
            kgraph.KG_HUB_MIN_DF = 2          # 枢纽开
            d_on = kgraph.build(cfg)
            kgraph.KG_HUB_MIN_DF = 10 ** 9    # 枢纽关
            d_off = kgraph.build(cfg)
        finally:
            kgraph.KG_HUB_MIN_DF = old
        # 开关只改枢纽账，聚类账逐项不变（枢纽不进 union-find）
        for key in ("clusters", "clusters_multi", "merged_into_rep"):
            self.assertEqual(d_on["meta"][key], d_off["meta"][key],
                             "枢纽开关改变了聚类账（%s）" % key)
        self.assertGreater(d_on["meta"]["auto_hubs"], 0)
        self.assertEqual(d_off["meta"]["auto_hubs"], 0)
        autos = [n for n in d_on["nodes"]
                 if n["kind"] == "kw" and n["meta"].get("auto")]
        self.assertTrue(autos, "没提取出任何自动枢纽")
        for n in autos:
            self.assertIn("自动枢纽", n["badge"])
        # 辐射边：枢纽 → 代表（kw_hit）
        auto_labels = {n["label"] for n in autos}
        rad = [e for e in d_on["edges"] if e["kind"] == "kw_hit"
               and e["src"].removeprefix("k:") in auto_labels]
        self.assertTrue(rad, "自动枢纽没有 kw_hit 辐射边")

    def test_18_cluster_by_bigram_containment(self):
        """v0.85：骨架包含归簇——短标题 token 集被长标题覆盖
        （|交|/min≥0.6）即同簇，不要求整标题 Jaccard。"""
        from actions.actionsserve import Config
        from actions.analysis import kgraph
        tmp = self.tmp
        tdb = tmp / "t.db"
        _mk_topics_db(tdb, [("t1", "甲", '[]', '[]',
                             "2026-10-19T00:00:00")])
        idb = tmp / "h.db"
        _mk_index_db(idb, [
            ("S1", "部署网关重启失败", "trae", "2026-10-19T09:00:00"),
            ("S2", "部署网关重启失败告警通知", "trae",
             "2026-10-19T10:00:00"),
            ("S3", "买菜清单", "claude", "2026-10-19T11:00:00"),
        ])
        glossary = tmp / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        cfg = Config(cards_root=tmp / "cards", db=idb, glossary=glossary,
                     topics_meta=tdb)
        d = kgraph.build(cfg)
        sess = {n["id"]: n for n in d["nodes"] if n["kind"] == "session"}
        self.assertEqual(d["meta"]["clusters"], 2)
        self.assertEqual(d["meta"]["merged_into_rep"], 1)
        merged = sess["s:S1"]["meta"].get("merged")
        self.assertEqual(merged, "S2")     # S2 updated_at 最新为代表

    def test_19_search_fts_subgraph(self):
        """v0.86 搜索模式：q 非空=正文 FTS（messages 索引）命中会话+
        邻域子图——标题不含但正文含关键字的会话也能命中（「梦中救人」
        标题只中 4 个的教训）。q 空=默认全量（大类态显隐前端管）。"""
        from actions.actionsserve import Config
        from actions.analysis import kgraph
        tmp = self.tmp
        tdb = tmp / "t.db"
        _mk_topics_db(tdb, [("t1", "甲", '[]', '[]',
                             "2026-10-19T00:00:00")])
        idb = tmp / "h.db"
        _mk_index_db(idb, [
            ("S1", "部署网关重启", "trae", "2026-10-19T09:00:00"),
            ("S2", "买菜清单", "claude", "2026-10-19T10:00:00"),
        ])
        glossary = tmp / "g.json"
        glossary.write_text('{"terms": []}', encoding="utf-8")
        cfg = Config(cards_root=tmp / "cards", db=idb, glossary=glossary,
                     topics_meta=tdb)
        # 搜索态：命中 S1（正文=标题），S2 排除；search 账如实
        d = kgraph.build(cfg, query="部署")
        sess = {n["id"] for n in d["nodes"] if n["kind"] == "session"}
        self.assertEqual(sess, {"s:S1"})
        self.assertEqual(d["meta"]["search"],
                         {"q": "部署", "fts_matched": 1})
        self.assertEqual(d["meta"]["sessions"]["drawn"], 1)
        # 邻域随命中下发（词/主题边只连命中会话）
        self.assertTrue(all(e["src"] != "k:买菜" for e in d["edges"]))
        # FTS 语法字符清洗：不报错（引号/括号丢弃后按残余匹配）
        d2 = kgraph.build(cfg, query='"部署"(')
        self.assertEqual(d2["meta"]["search"]["q"], "部署")
        # 查无此词：诚实空子图（无会话节点，search 账=0）
        d3 = kgraph.build(cfg, query="不存在词xyzq")
        self.assertEqual(
            {n["id"] for n in d3["nodes"] if n["kind"] == "session"},
            set())
        self.assertEqual(d3["meta"]["search"]["fts_matched"], 0)
        # 默认态：search=None，全量会话都在数据面
        d4 = kgraph.build(cfg)
        self.assertIsNone(d4["meta"]["search"])
        self.assertEqual(len({n["id"] for n in d4["nodes"]
                              if n["kind"] == "session"}), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
