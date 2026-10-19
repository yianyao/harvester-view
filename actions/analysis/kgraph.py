# -*- coding: utf-8 -*-
"""知识图谱数据面（v0.82，机械关系图）——主题×卡片×关键词×会话一次组装。

用户裁决（2026-10-19 批准 PROPOSAL-knowledge-graph-v1）：词云（v0.81）解决了
"词粒度漫游"，本模块补"结构粒度漫游"——哪张卡挂在哪个主题、哪些会话被哪些
卡引用、哪个主题孤零零没卡没链，一张图看到。

**机械关系，零语义臆造**：全部边来自既有真源——
- 主题—成员会话：topics_meta.topics.members（sid+evidence，注册时人工给证据）；
- 卡片—锚点会话：卡片 frontmatter anchors（cardlib 解析，§8 规范冻结字段）；
- 卡片—主题：锚点 sid ∈ 主题成员（与 cards validate 同一机械判据，
  session-harvester/cards.py _topic_membership 同源）；
- 关键词—主题：topics.keywords 定稿表（人工定稿，非臆测）。
诚实边界：主题与主题之间**没有机械边**（成员会话唯一归属主题，想要主题层级
需主仓 P7-② 先行）；未入主题的会话不画节点、只给计数（防几百个孤立点糊屏）。

会话节点**懒加载**：基础图只画 主题+卡片+关键词（防节点爆炸）；展开模式
（传 sids）按需回会话节点的标题/来源/错误数——同样只读、零臆造。

只读：harvester.db / topics_meta.db / 卡片目录全部 mode=ro 或纯读。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _con_ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _parse_json_list(raw, what: str, hints: list[str]) -> list:
    """topics 表里 JSON 字符串列（members/keywords）的宽容解析：
    解析失败不吞内容——原样进 hints（诚实：坏了要说哪里坏）。"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        hints.append(f"{what} 不可解析（{type(e).__name__}），该列内容已跳过："
                     f"{str(raw)[:80]}")
        return []
    return v if isinstance(v, list) else []


def _load_topics(meta_path: Path | None,
                 hints: list[str]) -> list[dict]:
    """主题注册表 → 节点原料（id/name/keywords/member_sids/chains 计数）。"""
    if meta_path is None or not Path(meta_path).is_file():
        hints.append("topics_meta 未配置或不存在：起服时加 --topics-meta"
                     "（主题层与成员/关键词关系无处可查，这不是「没有主题」）")
        return []
    con = _con_ro(meta_path)
    try:
        out = []
        for r in con.execute(
                "SELECT id, name, keywords, members, created "
                "FROM topics ORDER BY id"):
            members = _parse_json_list(r["members"],
                                       f"主题 {r['id']} 的 members", hints)
            kws = _parse_json_list(r["keywords"],
                                   f"主题 {r['id']} 的 keywords", hints)
            sids = [str(m["sid"]) if isinstance(m, dict) else str(m)
                    for m in members]
            out.append({"id": str(r["id"]), "name": str(r["name"] or r["id"]),
                        "keywords": [str(k) for k in kws if str(k).strip()],
                        "member_sids": sids, "created": r["created"]})
        return out
    finally:
        con.close()


def _load_cards(cards_root: Path | None,
                hints: list[str]) -> list[dict]:
    """卡片目录 → 节点原料（cardlib 原样复用：只读解析，不校验）。"""
    root = Path(cards_root) if cards_root else None
    if root is None or not root.is_dir():
        hints.append("cards_root 未配置或不存在：起服时确认 --cards-root"
                     "（卡片层与锚点关系无处可查，这不是「没有卡片」）")
        return []
    try:
        import cardlib
    except ImportError:  # 与 actionsserve._cards_payload 同款兜底
        import sys
        pkg = str(Path(__file__).resolve().parent.parent.parent)
        if pkg not in sys.path:
            sys.path.insert(0, pkg)
        import cardlib
    cards = cardlib.scan_cards(root)
    if not cards:
        hints.append(f"卡片目录空（{root}）：没有可画的卡片节点"
                     "（蒸馏产出卡片后刷新即见）")
    return cards


def build_base(cfg) -> dict:
    """基础图：主题 + 卡片 + 关键词 三层节点与边（会话层懒加载不在此处）。"""
    hints: list[str] = []
    topics = _load_topics(cfg.topics_meta, hints)
    cards = _load_cards(cfg.cards_root, hints)

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    member_all: set[str] = set()
    for t in topics:
        member_all.update(t["member_sids"])
        nodes[f"t:{t['id']}"] = {
            "id": f"t:{t['id']}", "kind": "topic", "label": t["name"],
            "badge": f"{len(t['member_sids'])}成员",
            "meta": {"topic_id": t["id"], "member_sids": t["member_sids"],
                     "keywords": t["keywords"]}}
        for kw in t["keywords"]:
            kid = f"k:{kw}"
            if kid not in nodes:
                nodes[kid] = {"id": kid, "kind": "kw", "label": kw,
                              "badge": "", "meta": {}}
            edges.append({"src": kid, "dst": f"t:{t['id']}",
                          "kind": "keyword"})
    for c in cards:
        cid = f"c:{c['path']}"
        anchor_sids = sorted({str(a.get("sid") or "")
                              for a in c.get("anchors", [])
                              if a.get("sid")})
        nodes[cid] = {
            "id": cid, "kind": "card",
            "label": c.get("title") or c.get("id") or c["path"],
            "badge": c.get("type") or "",
            "meta": {"path": c["path"], "anchor_sids": anchor_sids,
                     "confidence": c.get("confidence")}}
        for t in topics:
            if anchor_sids and set(anchor_sids) & set(t["member_sids"]):
                edges.append({"src": cid, "dst": f"t:{t['id']}",
                              "kind": "topic_of"})

    # 会话覆盖账（诚实计数，不画未入主题的会话——提案 §2 死约定）
    sessions_total = in_topics = 0
    db_sids: set[str] = set()
    if cfg.db and Path(cfg.db).is_file():
        con = _con_ro(cfg.db)
        try:
            sessions_total = int(con.execute(
                "SELECT COUNT(*) FROM sessions").fetchone()[0])
            db_sids = {r["sid"] for r in con.execute("SELECT sid FROM sessions")}
        finally:
            con.close()
    in_topics = len(member_all & db_sids)
    cards_unlinked = sum(1 for c in cards
                         if not any(e["src"] == f"c:{c['path']}"
                                    for e in edges))

    meta = {
        "topics": len(topics), "cards": len(cards),
        "keywords": len([n for n in nodes.values() if n["kind"] == "kw"]),
        "sessions_total": sessions_total,
        "sessions_in_topics": in_topics,
        "sessions_unlinked": sessions_total - in_topics,
        "cards_unlinked": cards_unlinked,
        "source": "topics_meta.topics / cardlib.scan_cards / harvester.db",
        "derivation": "卡片→主题=锚点 sid ∈ 主题成员（cards validate 同判据）；"
                      "关键词→主题=topics.keywords 定稿表；"
                      "未入主题的会话不画节点只给计数",
    }
    if not nodes:
        hints.append("没有可画的节点（主题注册表与卡片目录都空）——"
                     "先 topic register 注册主题 / 蒸馏产出卡片")
    return {"view_actions_api": 1, "kind": "kgraph", "mode": "base",
            "nodes": [nodes[k] for k in sorted(nodes)],
            "edges": sorted(edges, key=lambda e: (e["src"], e["dst"],
                                                  e["kind"])),
            "meta": meta, "hints": hints}


def expand_sessions(cfg, sids: list[str]) -> dict:
    """懒加载展开：按 sid 清单回会话节点原料（标题/来源/错误数）。
    只读；查不到的 sid 如实进 missing（不编节点、不静默丢）。"""
    hints: list[str] = []
    found: dict[str, dict] = {}
    err_cnt: dict[str, int] = {}
    if cfg.db and Path(cfg.db).is_file() and sids:
        con = _con_ro(cfg.db)
        try:
            marks = ",".join("?" * len(sids))
            for r in con.execute(
                    f"SELECT sid, title, source, updated_at FROM sessions "
                    f"WHERE sid IN ({marks})", sids):
                found[r["sid"]] = {"sid": r["sid"],
                                   "title": r["title"] or "",
                                   "source": r["source"] or "",
                                   "updated_at": r["updated_at"] or ""}
            for r in con.execute(
                    f"SELECT sid, COUNT(*) AS n FROM steps "
                    f"WHERE status='error' AND sid IN ({marks}) "
                    f"GROUP BY sid", sids):
                err_cnt[r["sid"]] = int(r["n"])
        finally:
            con.close()
    else:
        hints.append("索引库未配置或不存在：会话层无从展开（起服时确认 --db）")
    for sid, row in found.items():
        row["error_count"] = err_cnt.get(sid, 0)
    missing = [s for s in sids if s not in found]
    return {"view_actions_api": 1, "kind": "kgraph", "mode": "expand",
            "sids": sids,
            "sessions": [found[s] for s in sorted(found)],
            "missing": missing, "hints": hints}


def build(cfg, sids_param: str | None = None) -> dict:
    """动作入口：sids 空=基础图；非空=懒加载展开该批会话（逗号分隔）。"""
    raw = str(sids_param or "").strip()
    if not raw:
        return build_base(cfg)
    sids = [s.strip() for s in raw.split(",") if s.strip()]
    return expand_sessions(cfg, sids)
