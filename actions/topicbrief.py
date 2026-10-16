# -*- coding: utf-8 -*-
"""主题研读面（v0.76，批次二 V3）——把"这个主题里攒了什么知识"拼成一页。

用户裁决（2026-10-15 四原则）：主题是知识的聚合与分析；人打开主题页要直接
看到聚合结果，而不是先操作喂料命令。本模块是**确定性聚合层**：全部数字来自
既有真源（errstats/behstats/kwstats/topicprep/topicexport 同一批只读函数），
**零语义臆造**；语义结论（关键转折/元结论/未决）只来自已发布 chain，没有就
诚实空着——与 `topic md` 的纪律一致。

与 dualtrack（P4-4）的分工：dualtrack 管"讨论流×产物流"两条时间轨道；
本模块管"聚合分析"（构成/活跃/高频问题/错误模式/skill 行为/重复会话/健康）。
两者都走"全库跑→按成员 sid 过滤，不重算口径"的同一先例（dualtrack 先例）。

只读：harvester.db / topics_meta.db / chain 文件全部 mode=ro 或纯读。
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path


def _con_ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _members_by_source(con: sqlite3.Connection,
                       member_sids: list[str]) -> dict:
    """成员按来源分布 + 活跃跨度（created_at 月度分布）。"""
    out: dict[str, int] = {}
    months: Counter = Counter()
    first = last = ""
    for sid in member_sids:
        r = con.execute(
            "SELECT source, created_at FROM sessions WHERE sid=? "
            "OR session_id=? ORDER BY CASE WHEN sid=? THEN 0 ELSE 1 END "
            "LIMIT 1", (sid, sid, sid)).fetchone()
        if r is None:
            continue
        out[r["source"] or "?"] = out.get(r["source"] or "?", 0) + 1
        month = (r["created_at"] or "")[:7]
        if month:
            months[month] += 1
        ts = r["created_at"] or ""
        if ts:
            first = min(first or ts, ts)
            last = max(last or ts, ts)
    return {"by_source": dict(sorted(out.items(), key=lambda kv: -kv[1])),
            "months": dict(sorted(months.items())),
            "first": first, "last": last,
            "source": "sessions 表按成员 sid 查询",
            "definition": "by_source=成员会话的来源分布；months=按 "
                          "created_at 月份计成员会话数"}


def _questions(con: sqlite3.Connection, member_sids: list[str],
               top: int = 15) -> dict:
    """高频问题：成员 user 消息的 2/3-gram（keywords 命令同一函数）。
    全库扫一遍按 sid 过滤（dualtrack 同先例，不重算口径）。"""
    from harvester.kwstats import extract_grams, load_stopwords, \
        DEFAULT_STOPWORDS
    stop = load_stopwords(DEFAULT_STOPWORDS)
    members = set(member_sids)
    grams: Counter = Counter()
    n_msgs = 0
    for sid, raw in con.execute(
            "SELECT sid, raw FROM messages WHERE role='user' ORDER BY rowid"):
        if sid not in members or not raw:
            continue
        n_msgs += 1
        flat = " ".join(raw.split())
        for g in extract_grams(flat, 2):
            if g not in stop:
                grams[g] += 1
        for g in extract_grams(flat, 3):
            if g not in stop and " " not in g:
                grams[g] += 1
    top_grams = [(g, c) for g, c in grams.most_common(top * 2)
                 if not g.isdigit()][:top]
    return {"top": [{"gram": g, "count": c} for g, c in top_grams],
            "n_user_msgs": n_msgs,
            "source": "kwstats.extract_grams（keywords 命令同函数）",
            "definition": "成员 user 消息的 2/3-gram 计数，随包停用词过滤；"
                          "count=出现总次数（freq 口径）"}


def _errors(con: sqlite3.Connection, member_sids: list[str],
            top: int = 8) -> dict:
    """错误模式 top：errstats 同一函数，全库跑→成员过滤。"""
    members = set(member_sids)
    from harvester.errstats import classify_error, normalize_error
    rows = [{"sid": r["sid"], "seq": r["seq"], "tool": r["tool"],
             "error": r["error"] or "", "resolved": True,
             "class": classify_error(r["error"] or ""),
             "pattern": normalize_error(r["error"] or "")}
            for r in con.execute(
                "SELECT sid, seq, tool, error FROM steps "
                "WHERE status='error' AND error IS NOT NULL ORDER BY rowid")
            if r["sid"] in members]
    from harvester.errstats import pattern_stats
    stats = pattern_stats(rows)
    top_patterns = sorted(stats.items(), key=lambda kv: -kv[1]["count"])[:top]
    return {"patterns": [{"pattern": p, "class": d["class"],
                          "count": d["count"],
                          "given_up": d.get("given_up", 0)}
                         for p, d in top_patterns],
            "total": len(rows),
            "source": "errstats.pattern_stats（report-errors 同函数）",
            "definition": "成员会话内错误步骤按归一模式聚类，count=次数；"
                          "given_up=错误后同会话同工具无再次调用"}


def _skills(con: sqlite3.Connection, db_path: Path,
            member_sids: list[str], top: int = 10) -> dict:
    """skill 行为概要：behstats.collect_skill_invocations 同一函数，
    全库跑→成员过滤（dualtrack 先例）。"""
    from harvester.behstats import collect_skill_invocations
    members = set(member_sids)
    agg: Counter = Counter()
    ok: Counter = Counter()
    try:
        for i in collect_skill_invocations(db_path):
            if i["sid"] not in members:
                continue
            name = i["skill"] or "?"
            agg[name] += 1
            if i["status"] == "ok":
                ok[name] += 1
    except Exception as e:  # noqa: BLE001 - 上游口径变化：诚实降级
        return {"skills": [], "error": f"{type(e).__name__}: {e}",
                "source": "behstats.collect_skill_invocations",
                "definition": "成员会话内 skill 类调用计数与成功数"}
    return {"skills": [{"skill": s, "calls": c, "ok": ok.get(s, 0)}
                       for s, c in agg.most_common(top)],
            "source": "behstats.collect_skill_invocations",
            "definition": "成员会话内 skill 类调用计数与成功数"}


def _dupes(meta_path: Path, db_path: Path, topic_id: str) -> dict:
    """重复会话计数（H40 口径单一真源）。大主题 O(n²) 可能慢——诚实降级。"""
    try:
        from harvester.topicprep import dedupe_topic
        r = dedupe_topic(meta_path, db_path, topic_id)
        return {"reps": r["reps"], "duplicates": r["duplicates"],
                "band_pairs": len(r["band_pairs"]),
                "source": "topicprep.dedupe_topic（topic dedupe 同函数）",
                "definition": "user raw bigram Jaccard ≥0.90 判重复；"
                              "0.80–0.90 为边界带"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "reps": None,
                "definition": "重复会话计数本次不可得（见 error）"}


def _chains(chain_root: Path | None, topic_id: str) -> dict:
    """已发布 chain 的结论层：结论/未决小节（截断，topicexport 同函数）。"""
    if not chain_root or not Path(chain_root).is_dir():
        return {"configured": False, "chains": [],
                "honest": "未传 --chain-root（未执行——不等于该主题没有 chain）"}
    from harvester.topicexport import (_load_chains, _h2_sections,
                                       _pick_section, _SECTION_ALIASES,
                                       SECTION_CAP)
    chains = _load_chains(Path(chain_root), topic_id)
    out = []
    for c in chains:
        try:
            body = Path(c["path"]).read_text(encoding="utf-8",
                                             errors="replace")
        except OSError:
            body = ""
        concl = _pick_section(_h2_sections(body), _SECTION_ALIASES["结论"])
        openq = _pick_section(_h2_sections(body), _SECTION_ALIASES["未决"])
        out.append({"name": c["name"], "stages": c["stages"],
                    "nodes": c["nodes"],
                    "conclusion": {"title": concl[0],
                                   "text": (concl[1] or "")[:SECTION_CAP]}
                    if concl else None,
                    "open": {"title": openq[0],
                             "text": (openq[1] or "")[:SECTION_CAP]}
                    if openq else None})
    return {"configured": True, "chains": out,
            "honest": "" if out else "该主题还没有已发布的 chain"
                                     "（确实没有，不是没去读）"}


def _health(con: sqlite3.Connection, meta_path: Path,
            member_sids: list[str]) -> dict:
    missing = [s for s in member_sids
               if con.execute("SELECT 1 FROM sessions WHERE sid=? OR "
                              "session_id=?", (s, s)).fetchone() is None]
    noise: set[str] = set()
    try:
        mcon = sqlite3.connect(
            f"file:{Path(meta_path).as_posix()}?mode=ro", uri=True)
        mcon.row_factory = sqlite3.Row
        try:
            for r in mcon.execute("SELECT sid FROM sessions_noise"):
                noise.add(r["sid"])
        except sqlite3.OperationalError:
            pass
        finally:
            mcon.close()
    except sqlite3.Error:
        pass
    return {"members_not_in_index": missing,
            "noise_registered": sorted(noise & set(member_sids)),
            "source": "sessions 表 + topics_meta.sessions_noise",
            "definition": "不在索引库的成员=数据健康缺口；登记为零散的成员"
                          "不应出现在主题里（自相矛盾）"}


def build(cfg, topic_id: str, level: str, timeline_dict: dict) -> dict:
    """研读面数据面。timeline_dict 由 actionsserve.topic_timeline 产出
    （复用其库指纹），本函数只追加聚合层。全部只读。"""
    from harvester.topics import show_topic
    topic = show_topic(cfg.topics_meta, topic_id)
    member_sids = [m["sid"] for m in topic["members"]]
    con = _con_ro(cfg.db)
    try:
        comp = _members_by_source(con, member_sids)
        ques = _questions(con, member_sids)
        errs = _errors(con, member_sids)
        skills = _skills(con, cfg.db, member_sids)
        health = _health(con, cfg.topics_meta, member_sids)
    finally:
        con.close()
    dupes = _dupes(cfg.topics_meta, cfg.db, topic_id)
    chains = _chains(getattr(cfg, "chain_root", None), topic_id)
    return {
        "view_actions_api": 1,
        "kind": "topic-brief",
        "topic_id": topic_id,
        "topic": {"id": topic["id"], "name": topic["name"],
                  "keywords": topic["keywords"],
                  "created": topic["created"]},
        "members": len(member_sids),
        "level": level,
        "composition": comp,
        "questions": ques,
        "errors": errs,
        "skills": skills,
        "dupes": dupes,
        "chains": chains,
        "health": health,
        "timeline_ref": {"level": timeline_dict.get("level"),
                         "rows": len(timeline_dict.get("rows") or [])},
        "account": {"members": len(member_sids),
                    "n_user_msgs": ques["n_user_msgs"],
                    "errors": errs["total"],
                    "chains": len(chains.get("chains") or [])},
    }
