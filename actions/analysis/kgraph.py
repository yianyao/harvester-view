# -*- coding: utf-8 -*-
"""知识图谱数据面（v0.84）——用户发起 × 相近聚合 × 簇代表放射。

用户裁决（2026-10-20 连续纠偏，v0.82 懒加载被否 → v0.83 全量仍不够）：
1. 只取**用户先发起**的会话——判据=该会话首条消息 role='user'
   （messages FTS 表真源，机械可验证；后台/系统产生的会话不画）；
2. 相近语义会话**聚类分层**：机械相似判据（不是真语义 embedding——
   主仓 M2/M4 未落地，判据透明写死）：① 标题 token 重叠 Jaccard≥0.45
   （CJK 连续串切二元组+ASCII 词，与主仓检索分词同思路）；
   ② 共享任一定稿关键词（kw_hit 同判据：标题含词）。满足其一即归同簇
   （并查集连通）；
3. 一簇**只显示一个代表节点**（簇内 updated_at 最新），点击才展开簇内
   成员（放射下层）——与 v0.82 被否决的懒加载不同：数据面一次给全
   （成员与边都下发），聚合显隐纯前端，展开零等待；单会话簇的代表就是
   自己，点击直达原文；
4. 黑底画布（CSS 层，见 index.html）。

**机械关系，零语义臆造**（边五类，同 v0.83）：
- 主题—成员会话 member（topics.members，注册时人工给证据）；
- 卡片—锚点会话 anchor（frontmatter §8 冻结字段）；
- 卡片—主题 topic_of（锚点 sid ∈ 主题成员，cards validate 同判据）；
- 关键词—主题 keyword（topics.keywords 定稿表）；
- 关键词—会话 kw_hit（标题含词，与检索框同判据）。

诚实边界：主题间无边（P7-② 缓行）；会话间**无相似度边**——聚类是
聚合展示手段，不是「语义相似边」；真语义近似待 M2/M4。被过滤/聚合的
账目全部进 meta（画了多少/滤了多少/聚成几簇），UI 明示。

只读：harvester.db / topics_meta.db / 卡片目录全部 mode=ro 或纯读。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

KG_MAX_SESSIONS = 1500   # 代表层画布上限（超出画最新，账目诚实）
KG_MAX_KW_HITS = 200     # 单关键词最多连多少会话（辐射枢纽封顶）
KG_SIM_JACCARD = 0.45    # 标题 token Jaccard 归簇阈值（机械判据，写死）
KG_SIM_CONT = 0.6        # bigram 包含率归簇阈值（|交|/min 侧，v0.85：
                         # 「X一词释义解析」族靠骨架包含归簇）
KG_HUB_MIN_DF = 8        # 自动枢纽：文档频率下限（机械频率，无语义判断）
KG_HUB_MAX = 40          # 自动枢纽词数量上限
KG_HUB_MAXLEN = 4        # 候选片段最大长度（CJK 子串 2~4 字 + ASCII 词）
KG_HUB_STOP = set("的了与和及或是在")  # 含虚词字的片段不做枢纽（机械过滤）


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


def _title_tokens(title: str) -> frozenset:
    """标题切 token：CJK 连续串→二元组（「部署排障」→{部署,署排,排障}），
    ASCII→小写词。与主仓检索的 CJK bigram 思路一致（机械分词，不臆造）。"""
    t = str(title or "").lower()
    toks: set[str] = set()
    for w in re.findall(r"[a-z0-9]+", t):
        toks.add(w)
    for run in re.findall(r"[\u4e00-\u9fff]+", t):
        if len(run) == 1:
            toks.add(run)
        else:
            toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return frozenset(toks)


def _hub_words(sessions: list[dict]) -> dict[str, int]:
    """自动枢纽词（v0.85，用户「实用性不够」）：标题高频片段机械提取。
    候选=标题 CJK 连续串的 2~4 字子串 + ASCII 词（每标题去重），按文档
    频率挑——**纯频率统计，零语义判断**；虚词字过滤挡「的含/与改」类
    碎片；贪心去重（候选的命中集 ≥80% 已被已选枢纽覆盖则跳过，防
    「分析/文本分析/文本分析请求」占满名额）。返回 {词: df}。
    **只做 kw_hit 辐射节点，不进聚类**（真库实测：共享「分析」枢纽会把
    874 会话连锁拖成一簇，不可用）。"""
    df: dict[str, int] = {}
    for s in sessions:
        t = str(s["title"] or "").lower()
        seen: set[str] = set()
        for w in re.findall(r"[a-z0-9]{2,}", t):
            seen.add(w)
        for run in re.findall(r"[\u4e00-\u9fff]+", t):
            for ln in range(2, KG_HUB_MAXLEN + 1):
                for i in range(len(run) - ln + 1):
                    seen.add(run[i:i + ln])
        for w in seen:
            if not any(ch in KG_HUB_STOP for ch in w):
                df[w] = df.get(w, 0) + 1
    cand = sorted(((w, c) for w, c in df.items() if c >= KG_HUB_MIN_DF),
                  key=lambda kv: (-kv[1], kv[0]))
    titles = [(s["sid"], str(s["title"] or "")) for s in sessions]
    out: dict[str, int] = {}
    covered: set[str] = set()
    for w, c in cand:
        hit = {sid for sid, ti in titles if w in ti}
        if len(hit & covered) / max(len(hit), 1) >= 0.8:
            continue
        out[w] = c
        covered |= hit
        if len(out) >= KG_HUB_MAX:
            break
    return out


def _cluster(sessions: list[dict], kw_titles: dict[str, set[str]]
             ) -> list[list[int]]:
    """并查集聚类：标题 Jaccard≥阈值 或 共享定稿关键词 → 同簇。
    O(n²) 标题对比（1500²/2≈112 万次集合交并，毫秒级~秒级可接受）。
    返回簇列表（每个是 sessions 下标列表）。"""
    n = len(sessions)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    toks = [_title_tokens(s["title"]) for s in sessions]
    for i in range(n):
        ti = toks[i]
        for j in range(i + 1, n):
            tj = toks[j]
            sim = False
            if ti and tj:
                inter = len(ti & tj)
                if inter:
                    if inter / len(ti | tj) >= KG_SIM_JACCARD:
                        sim = True
                    elif inter / min(len(ti), len(tj)) >= KG_SIM_CONT:
                        sim = True   # 骨架包含：「X一词释义解析」族（v0.85）
            if not sim:
                si, sj = sessions[i]["sid"], sessions[j]["sid"]
                for kw, hit in kw_titles.items():
                    if si in hit and sj in hit:
                        sim = True
                        break
            if sim:
                union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    # 簇序稳定：按簇内最小下标（sessions 已按 updated_at DESC，代表=簇首）
    return sorted(groups.values(), key=lambda g: g[0])


def _load_topics(meta_path: Path | None,
                 hints: list[str]) -> list[dict]:
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


def _first_user_sids(db: Path) -> tuple[set[str], int]:
    """「用户先发起」判定：每 sid 首条消息（按 ts 最早）role='user'。
    返回 (通过的 sid 集, 有消息的会话总数)。无消息会话=不通过（无法
    证明是用户发起——诚实判据，不猜）。"""
    con = _con_ro(db)
    try:
        first: dict[str, tuple[str, str]] = {}  # sid -> (ts, role)
        for r in con.execute("SELECT sid, role, ts FROM messages"):
            ts = r["ts"] or ""
            cur = first.get(r["sid"])
            if cur is None or (ts or "9999") < (cur[0] or "9999"):
                first[r["sid"]] = (ts, r["role"] or "")
        ok = {sid for sid, (_ts, role) in first.items() if role == "user"}
        return ok, len(first)
    finally:
        con.close()


def _load_sessions(db: Path | None, hints: list[str], limit: int
                   ) -> tuple[list[dict], dict]:
    """会话装载：全量→用户发起过滤→updated_at 最新截断。
    返回 (rows, 账目)。账目字段：total/user_initiated/filtered/drawn/
    truncated。"""
    acc = {"total": 0, "user_initiated": 0, "filtered": 0,
           "drawn": 0, "truncated": False}
    if db is None or not Path(db).is_file():
        hints.append("索引库未配置或不存在：会话层无处可画（起服时确认 --db）")
        return [], acc
    con = _con_ro(db)
    try:
        rows = [{"sid": r["sid"], "title": r["title"] or "",
                 "source": r["source"] or "",
                 "error_count": int(r["errors"] or 0)}
                for r in con.execute(
                "SELECT s.sid, s.title, s.source, s.updated_at, "
                "(SELECT COUNT(*) FROM steps st "
                " WHERE st.sid=s.sid AND st.status='error') AS errors "
                "FROM sessions s ORDER BY s.updated_at DESC, s.sid")]
    finally:
        con.close()
    acc["total"] = len(rows)
    user_sids, with_msg = _first_user_sids(db)
    rows = [r for r in rows if r["sid"] in user_sids]
    acc["user_initiated"] = len(rows)
    acc["filtered"] = acc["total"] - len(rows)
    if acc["total"] and not rows:
        hints.append(
            f"没有「用户先发起」的会话（判据=首条消息 role='user'；"
            f"{acc['total']} 个会话里 {with_msg} 个有消息，全被滤掉）——"
            "这不是坏了，是判据下无合格会话")
    if len(rows) > limit:
        rows = rows[:limit]
        acc["truncated"] = True
    acc["drawn"] = len(rows)
    return rows, acc


def build(cfg, max_sessions: int | None = None) -> dict:
    """v0.84 全量装载+聚类聚合：数据面一次下发全部节点/边与簇结构，
    代表层显隐由前端控制（展开零等待）。max_sessions 仅供测试注入。"""
    hints: list[str] = []
    topics = _load_topics(cfg.topics_meta, hints)
    cards = _load_cards(cfg.cards_root, hints)
    cap = KG_MAX_SESSIONS if max_sessions is None else int(max_sessions)
    sessions, sess_acc = _load_sessions(cfg.db, hints, cap)

    # kw_hit 判定表：关键词 → 标题含词的会话 sid 集（检索框同判据）。
    # 自动枢纽（v0.85）也进词命中表——但**只辐射不归簇**：真库实测共享
    # 「分析」枢纽会把 874 会话连锁拖成一簇，故 _cluster 只吃定稿表
    # kw_titles_fixed，含枢纽的全量表只用于连边（2026-10-20 实测修正：
    # 首版误把全量表喂进聚类，连锁炸聚复现——435 簇/14 个多簇均 77 会话）。
    auto_hubs = _hub_words(sessions) if sessions else {}
    kw_titles_fixed: dict[str, set[str]] = {}
    for t in topics:
        for kw in t["keywords"]:
            if kw not in kw_titles_fixed:
                kw_titles_fixed[kw] = {s["sid"] for s in sessions
                                       if kw in (s["title"] or "")}
    kw_titles = dict(kw_titles_fixed)
    for w in auto_hubs:
        if w not in kw_titles:
            kw_titles[w] = {s["sid"] for s in sessions
                            if w in (s["title"] or "")}

    # 聚类（先算，代表决定 kw_hit 连边对象：词边连代表，展开后见成员）
    clusters = _cluster(sessions, kw_titles_fixed) if sessions else []
    rep_of: dict[str, str] = {}       # 成员 sid -> 代表 sid
    members_of: dict[str, list[str]] = {}  # 代表 sid -> 成员 sids（含自身）
    for g in clusters:
        rep = sessions[g[0]]["sid"]   # g[0]=updated_at 最新（代表）
        sids = [sessions[i]["sid"] for i in g]
        members_of[rep] = sids
        for sid in sids:
            rep_of[sid] = rep

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    # 会话节点：数据面一次下发全部（显隐聚合纯前端，展开零等待）——
    # 簇代表带 meta.cluster 成员账（点击展开/收起）；簇成员带 meta.merged=
    # 代表 sid（默认隐藏，代表展开才可见）；单会话两者皆无（点击直达原文）。
    # （2026-10-20 修：成员原先 cluster=[] 无并入标记，kgVisible 的
    # 「一簇只显代表」整条失效——成员恒可见，聚合形同虚设。）
    for s in sessions:
        sid = s["sid"]
        rep = rep_of[sid]
        mem = members_of.get(sid, [sid])
        meta = {"sid": sid, "source": s["source"],
                "error_count": s["error_count"]}
        badge = s["source"] + \
            (f" · 错{s['error_count']}" if s["error_count"] else "")
        if rep == sid and len(mem) > 1:
            meta["cluster"] = mem
            badge += f" · 簇{len(mem)}"
        elif rep != sid:
            meta["merged"] = rep
        nodes[f"s:{sid}"] = {"id": f"s:{sid}", "kind": "session",
                             "label": s["title"] or sid,
                             "badge": badge, "meta": meta}

    # 主题层 + keyword/member 边
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
        for sid in t["member_sids"]:
            rep = rep_of.get(sid)
            if rep and f"s:{rep}" in nodes:
                edges.append({"src": f"t:{t['id']}", "dst": f"s:{rep}",
                              "kind": "member"})

    # 自动枢纽节点（v0.85）：同 kw 层，badge 带文档频率账
    for w, dfc in auto_hubs.items():
        kid = f"k:{w}"
        if kid not in nodes:
            nodes[kid] = {"id": kid, "kind": "kw", "label": w,
                          "badge": f"自动枢纽 {dfc}", "meta": {"auto": True}}

    # kw_hit 边：关键词→代表（词命中簇内任一成员即连代表；封顶记账）
    kw_hit_counts: dict[str, int] = {}
    kw_hit_capped: list[str] = []
    for kw, hit in kw_titles.items():
        reps = {rep_of[sid] for sid in hit if sid in rep_of}
        n = 0
        for rep in sorted(reps):  # sid 排序，确定性
            n += 1
            if n > KG_MAX_KW_HITS:
                kw_hit_capped.append(kw)
                break
            edges.append({"src": f"k:{kw}", "dst": f"s:{rep}",
                          "kind": "kw_hit"})
        kw_hit_counts[kw] = min(n, KG_MAX_KW_HITS)

    # 卡片层 + anchor/topic_of 边（锚点指向代表）
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
        for sid in anchor_sids:
            rep = rep_of.get(sid)
            if rep and f"s:{rep}" in nodes:
                edges.append({"src": cid, "dst": f"s:{rep}",
                              "kind": "anchor"})
        for t in topics:
            if anchor_sids and set(anchor_sids) & set(t["member_sids"]):
                edges.append({"src": cid, "dst": f"t:{t['id']}",
                              "kind": "topic_of"})

    cards_unlinked = sum(1 for c in cards
                         if not any(e["src"] == f"c:{c['path']}"
                                    for e in edges))
    multi = sum(1 for g in clusters if len(g) > 1)
    meta = {
        "topics": len(topics), "cards": len(cards),
        "keywords": len(kw_titles), "auto_hubs": len(auto_hubs),
        "sessions": sess_acc,
        "clusters": len(clusters), "clusters_multi": multi,
        "merged_into_rep": sess_acc["drawn"] - len(clusters),
        "kw_hit_counts": kw_hit_counts,
        "kw_hit_capped": sorted(set(kw_hit_capped)),
        "cards_unlinked": cards_unlinked,
        "caps": {"sessions": cap, "kw_hits": KG_MAX_KW_HITS},
        "criteria": {
            "user_initiated": "首条消息 role='user'（messages 表真源）",
            "cluster": f"标题 token Jaccard≥{KG_SIM_JACCARD} 或 bigram "
                       f"包含率≥{KG_SIM_CONT} 或共享定稿关键词（机械近似，"
                       "真语义待 M2/M4；自动枢纽只辐射不归簇）",
            "hubs": f"自动枢纽=标题高频片段（文档频率≥{KG_HUB_MIN_DF} 的 "
                    f"2~{KG_HUB_MAXLEN} 字 CJK 子串/ASCII 词，虚词字过滤"
                    "+贪心去重，机械频率提取，无语义判断）",
            "representative": "簇内 updated_at 最新",
        },
        "source": "topics_meta.topics / cardlib.scan_cards / harvester.db",
        "derivation": "卡片→主题=锚点 sid∈主题成员；关键词→主题=定稿表；"
                      "关键词→会话=标题含词（检索框同判据）；"
                      "主题间无边（P7-② 缓行）、会话间无相似度边（聚类只是"
                      "聚合展示，不是语义边）",
    }
    if not nodes:
        hints.append("没有可画的节点（索引库/主题注册表/卡片目录都空或全被"
                     "过滤）——先跑采集入库，或 topic register 注册主题")
    elif not kw_titles:
        hints.append("没有定稿关键词：词枢纽层为空（topic keywords 设置后"
                     "即有关键词→会话的辐射边）")
    return {"view_actions_api": 1, "kind": "kgraph", "mode": "clustered",
            "nodes": [nodes[k] for k in sorted(nodes)],
            "edges": sorted(edges, key=lambda e: (e["src"], e["dst"],
                                                  e["kind"])),
            "meta": meta, "hints": hints}
