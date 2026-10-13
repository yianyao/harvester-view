# -*- coding: utf-8 -*-
"""overview -- 今日工作台的数据面（P3-3：四队列 + 闭环健康三根环）。

设计纪律（这是本模块存在的全部理由）：**四队列与三环的每个数字都带口径与
真源**，页面只渲染、不自己编数、不做二次聚合。响应里每条 arm 都带：

- `value`：现值（整数）
- `unit`：单位（条/个/次）
- `source`：**哪个上游函数算出来的**（可被审计、可被下一会话复现）
- `definition`：这个数**怎么算**的（人话，写在页面上"口径"一栏）

数量级都很小（13 主题 / 24 台账条目 / 57 模式），故全部**现算**；
`collect_errors_from_db`（约 0.3s）与 `collect_triage`（约 1s）在每次请求里
各跑一次，结果是确定性的，不需要缓存层。

实测基线（2026-10-12，真库；数字会随语料变，故只写在这里当参照，不进代码）：
待裁决 0（台账 24 条已全裁决）/ 待修补 6 条建议（17 次未解决）/ 待蒸馏 30 个
新错误模式 + 31 条 skill 行为链 / 待验证 9 条"不下结论"；
环1 16 模式 31 次未解决、环2 31 skill、环3 主题 13 / chain 3 / 卡片 4。
"""
from __future__ import annotations

from pathlib import Path

from . import ensure_harvester_importable

_ERR = ensure_harvester_importable()
if _ERR is None:  # pragma: no cover - 缺上游时由端点报 503
    from harvester import apiserve as _hapi
    from harvester import harvesteval as _he
    from harvester import suggestmeta as _sm
    from harvester.errstats import collect_errors_from_db, pattern_stats
    from harvester.triage import collect_triage
else:  # pragma: no cover
    _hapi = _he = _sm = None
    collect_errors_from_db = pattern_stats = collect_triage = None

#: 每根环的两段：数据段（有没有料）+ 闭环段（有没有做完）＋ 效果段。
#: 三段都用**同一批真源**算，页面不额外造数。
RING_DEFS = (
    ("errors", "错误 → 班规 → 效果",
     "错误面（G2 的 given_up）→ 建议池裁决 → harvest-eval 效果判定"),
    ("skill", "skill → 鉴定 → 修复",
     "skill 调用（G4/triage 行为链）→ 行为画像 → 待鉴定数"),
    ("topics", "主题 → chain → 卡片",
     "主题注册表 → 已发布 chain → 卡片仓校验"),
)


def _arm(value, unit, source, definition, where=None, level=None):
    """一条臂：值 + 单位 + 真源 + 口径 + 去哪处理。level ∈ ok/warn/bad/na。"""
    return {"value": int(value), "unit": unit, "source": source,
            "definition": definition, "where": where,
            "level": level or ("ok" if value == 0 else "warn")}


def _queue(qid, label, question, count, source, definition, where):
    return {"queue_id": qid, "label": label, "question": question,
            "count": int(count), "source": source, "definition": definition,
            "where": where}


def build_overview(cfg) -> dict:
    """四队列 + 三环。全部现算；每个数字带 source 与 definition。"""
    if _hapi is None:
        raise RuntimeError(_ERR or "上游 harvester 不可用")
    from . import actionsserve as _as  # 延迟导入，避免循环依赖

    con = _as.open_ro(cfg.db)
    try:
        errs, err_meta = collect_errors_from_db(cfg.db, con=con)
        pstats = pattern_stats(errs)
        agents = _hapi.api_reports_agents(con, cfg.db, {},
                                          meta_db=cfg.suggestions_meta)
        errs_rep = _hapi.api_reports_errors(con, cfg.db, {})
        tri = collect_triage(cfg.db, cards_root=cfg.cards_root, min_count=2,
                             top_n=10, con=con)
        skills_rep = _hapi.api_reports_skills(con, cfg.db, {})
        topics = _hapi.api_topics(con, cfg.db, cfg.topics_meta, cfg.chain_root)
        cards = None
        if cfg.cards_root and Path(cfg.cards_root).is_dir():
            try:
                cards = _hapi.api_cards(con, cfg.db, cfg.cards_root, {})
            except (ValueError, FileNotFoundError):
                cards = None
        records = _sm.load_records(cfg.suggestions_meta)
        reg = _sm.registry_counts(cfg.topics_meta)
    finally:
        con.close()

    # ---------- 跨队列共用的量（算一次，多处引用，保证互相对得上） ----------
    by_status: dict[str, int] = {}
    for r in records.values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    given_up_patterns = sum(1 for v in pstats.values() if v["given_up"])
    given_up_total = sum(v["given_up"] for v in pstats.values())
    pending_entries = [e for e in agents["entries"] if e["status"] == "pending"]
    unres_entries = [e for e in agents["entries"] if e["unresolved_count"] >= 1]
    unres_total = sum(e["unresolved_count"] for e in agents["entries"])
    new_patterns = tri.get("new_patterns") or []
    old_patterns = tri.get("old_patterns") or []
    skill_chains = tri.get("skills") or []
    cards_pending = []
    if cfg.cards_root:
        # 卡片脚手架（待蒸馏的成品位）：与知识库卡片仓**分开**列
        pdir = Path(cfg.cards_root).parent / "cards_pending"
        if pdir.is_dir():
            cards_pending = sorted(pdir.glob("*.md"))
    ev = _he.run_eval(cfg.db, cfg.suggestions_meta) if cfg.suggestions_meta \
        else {"counts": {}, "adopted_total": 0, "skipped": 0,
              "skipped_topic": 0, "entries": []}
    ev_counts = ev.get("counts") or {}
    inconclusive = ev_counts.get(_he.VERDICT_INCONCLUSIVE, 0)
    unchanged = ev_counts.get(_he.VERDICT_UNCHANGED, 0)

    # ---------- 四队列 ----------
    queues = [
        _queue("decide", "待你裁决",
               "有裁决等着我做吗？",
               len(pending_entries),
               "suggestmeta.load_records[status=pending]",
               "建议台账里 status=pending 的条数（未被裁决的建议句）；"
               "已裁决的不算。注意本题只数**建议句**，主题登记另计。",
               "报告页 → 建议条目（G2 建议池）"),
        _queue("repair", "待修补",
               "哪里有坑该填？",
               len(unres_entries),
               "api_reports_agents.entries[unresolved_count>=1]",
               "建议条目里 unresolved_count>=1 的条数（= 失败之后同会话同工具"
               "再没被调用过的次数）；与 G2 的 given_up 同源不同粒度："
               "本数是**条目数**，G2 那个是**模式数/次数**。",
               "报告页 → G2 建议条目"),
        _queue("distill", "待蒸馏",
               "有什么素材值得做成卡片/主题？",
               len(new_patterns),
               "triage.collect_triage.new_patterns",
               "分诊队列里的**新错误模式**数（pattern 在建议池里还没有对应条目）。"
               "另有 skill 行为链与待做卡片，见下方明细。",
               "蒸馏队列页"),
        _queue("verify", "待验证",
               "采纳的班规真的有效吗？",
               inconclusive,
               "harvesteval.run_eval.counts[不下结论]",
               "已采纳（adopted）但 A/B 样本不足、**下不了结论**的条数。"
               "它是「还看不出来」而不是「没用」——不混进变差。",
               "报告页 → G1/G2 对照（跑 harvest-eval）"),
    ]
    queue_details = {
        "decide": [
            _arm(len(records) - len(pending_entries),
                 "条", "suggestmeta.load_records",
                 "台账里**已裁决**的条数（adopted+rejected）",
                 "报告页 → 建议条目"),
            _arm(reg.get("noise", 0), "条", "suggestmeta.registry_counts",
                 "零散登记（不属于任何主题的会话，待裁决归属）",
                 "主题页 → 零散登记"),
        ],
        "repair": [
            _arm(unres_total, "次", "api_reports_agents.entries[unresolved_count]",
                 "未解决次数合计（条目维度求和）",
                 "报告页 → G2 建议条目"),
            _arm(given_up_patterns, "个", "errstats.pattern_stats.given_up",
                 "**错误模式**维度：至少有 1 次未解决的归一模式数",
                 "报告页 → G2 错误三分类"),
            _arm(given_up_total, "次", "errstats.pattern_stats.given_up",
                 "**错误模式**维度：未解决次数合计（全库 381 条错误里占 "
                 f"{given_up_total}/{err_meta['error_count']}）",
                 "报告页 → G2 错误三分类"),
        ],
        "distill": [
            _arm(len(old_patterns), "个", "triage.collect_triage.old_patterns",
                 "旧坑重现（曾是建议/卡片、又在新窗口里出现的模式）",
                 "蒸馏队列页"),
            _arm(len(skill_chains), "条", "triage.collect_triage.skills",
                 "有待鉴定行为的 skill 行为链数",
                 "报告页 → G4 Skill 画像"),
            _arm(len(cards_pending), "张", "目录扫描 cards_pending/*.md",
                 "已生成脚手架、还没定稿入卡片仓的卡片数",
                 "卡片页 → G3 卡片校验"),
        ],
        "verify": [
            _arm(ev.get("adopted_total", 0), "条", "harvesteval.run_eval.adopted_total",
                 "**可评估**的已采纳条数（不含 `topic:` 命名空间登记）",
                 "报告页 → 建议条目"),
            _arm(unchanged, "条", "harvesteval.run_eval.counts[无变化]",
                 "A/B 判为「无变化」的条数（工具判据建议退休，但**工具不改"
                 "AGENTS.md**，退休由人决定）",
                 "报告页 → 建议条目"),
            _arm(ev.get("skipped", 0) + ev.get("skipped_topic", 0), "条",
                 "harvesteval.run_eval.skipped(_topic)",
                 "**未评估**的条数（pending/rejected 与主题登记）——"
                 "「未执行」与「评估了没变化」必须分得开",
                 "—"),
        ],
    }

    # ---------- 三根环 ----------
    rings = []
    for rid, title, definition in RING_DEFS:
        if rid == "errors":
            arms = [
                _arm(err_meta["error_count"], "条",
                     "errstats.collect_errors_from_db",
                     "有料：全库错误步骤数", "报告页 → G2"),
                _arm(by_status.get("adopted", 0), "条",
                     "suggestmeta.load_records[status=adopted]",
                     "已行动：已采纳为班规的条数", "报告页 → 建议条目"),
                _arm(inconclusive, "条",
                     "harvesteval.run_eval.counts[不下结论]",
                     "待验证：样本不足、下不了结论的条数", "报告页 → 建议条目"),
            ]
        elif rid == "skill":
            arms = [
                _arm(len(skills_rep.get("skills") or []), "个",
                     "api_reports_skills.skills",
                     "有料：行为画像里的 skill 数", "报告页 → G4"),
                _arm(len(skill_chains), "条",
                     "triage.collect_triage.skills",
                     "待鉴定：有待鉴定行为的 skill 行为链数", "蒸馏队列页"),
            ]
        else:
            zero_members = [t for t in topics.get("topics") or []
                            if t["members_count"] == 0]
            chains = topics.get("topics") or []
            n_chains = sum(int(t.get("chains_count") or 0) for t in chains)
            card_bad = None
            if cards is not None:
                s = cards["summary"]
                card_bad = int(s.get("error", 0)) + int(s.get("warn", 0))
            arms = [
                _arm(len(chains), "个", "api_topics.topics",
                     "有料：注册主题数", "主题页"),
                _arm(n_chains, "条", "api_topics.topics[chains_count]",
                     "已行动：已发布 chain 长文数", "主题页"),
                _arm(card_bad if card_bad is not None else 0, "张",
                     "api_cards.summary[error+warn]",
                     "待确认：卡片仓里有警告/错误的卡片数"
                     + ("" if cards is not None else "（未配置 --cards-root，"
                                                     "本项按 0 计，口径见下）"),
                     "卡片页 → G3"),
            ]
        rings.append({"ring_id": rid, "title": title, "definition": definition,
                      "arms": arms})

    fingerprints = {"errors": errs_rep.get("db_fingerprint")}
    return {
        "view_actions_api": 1,
        "queues": queues,
        "queue_details": queue_details,
        "rings": rings,
        "by_status": by_status,
        "registry": reg,
        "errors_meta": err_meta,
        "db_fingerprint": fingerprints["errors"],
        "hint": "本页只做**聚合与口径展示**：每个数字都带 source（哪个上游函数"
                "算的）与 definition（怎么算的），点数字去对应页面处理。"
                "口径冲突时以 source 指向的那一处为准。",
    }
