# -*- coding: utf-8 -*-
"""overview -- 今日工作台的数据面（P3-3：四队列 + 闭环健康，用户口径 = 三根环）。

设计纪律（这是本模块存在的全部理由）：**工作台上每个数字都带口径与真源**，
页面只渲染、不自己编数、不做二次聚合。响应里每条 arm / 每个队列都带：

- `count` / `value`：现值（整数）
- `unit`：单位（条/个/次/张）
- `source`：**哪个上游函数算出来的**（可被审计、可被下一会话复现）
- `definition`：这个数**怎么算**的（人话，写在页面上「口径」一栏）

**v0.49（2026-10-13）本轮的关键改动：数字与明细同源。**

用户实测反馈：「四队列中点击（去处理）即跳转到相关页面，这点很好。但**队列明细
对不上**。」根因是卡片上的数字与跳过去的页面各自算了一遍。现在本模块**不再自己算
任何数字**：全部数字来自 `actions/workitems.py` 的 target 明细（count 由明细按
`mode` 派生），于是「数字 == 明细条数」是构造保证。

同时每个数字都带上 `target`（点它去哪）与 `suggestion`（这批该怎么处理），
闭环健康的每一段也同样可点、有建议——这是用户同一轮的第二条要求。

实测基线（2026-10-13，真库；数字会随语料变，故只写在这里当参照，不进代码）：
四队列 0 / 6 / 30 / 9；环 **381 / 22 / 9**、**32 / 31**、**13 / 3 / 4**；
`/api/overview` 现算约 3–5 s。
"""
from __future__ import annotations

from . import ensure_harvester_importable
from . import severity as _sev
from . import workitems as _wi

_ERR = ensure_harvester_importable()
if _ERR is None:  # pragma: no cover - 缺上游时由端点报 503
    from harvester import apiserve as _hapi
else:  # pragma: no cover
    _hapi = None

VIEW_OVERVIEW_API = 2

#: 队列卡片的 target（顺序即页面顺序）。
QUEUE_TARGETS = ("queue:decide", "queue:repair", "queue:distill",
                 "queue:verify")

#: 每根环：id / 标题 / 口径 / 该环的段（**段就是 target**，故每段都可点、带建议）。
#: 段的顺序就是页面顺序，与 workitems 里的 target_id 一一对应；改一处必须改两处，
#: 对不上时 `_arm` 会 KeyError（响亮），不会静默少一段。
RING_DEFS = (
    ("errors", "错误 → 班规 → 效果",
     "错误面（G2 的 given_up）→ 建议池裁决 → harvest-eval 效果判定",
     ("ring:errors.0", "ring:errors.1", "ring:errors.2")),
    ("skill", "skill → 鉴定（修复段暂无数字）",
     "两段账：行为画像登记了哪些 skill（有料）→ 最近用过但还没定论"
     "是好用法还是坑的（待鉴定）。「修复」一段暂无台账——skill 修没修好"
     "目前数不出来，先只有两段；等有台账再补第三段。",
     ("ring:skill.0", "ring:skill.1")),
    ("topics", "主题 → chain → 卡片",
     "主题注册表 → 已发布 chain → 卡片仓校验",
     ("ring:topics.0", "ring:topics.1", "ring:topics.2")),
)


def build_overview(cfg) -> dict:
    """四队列 + 三环。**全部数字来自 workitems 的明细**（不在这里另算一遍）。"""
    if _hapi is None:
        raise RuntimeError(_ERR or "上游 harvester 不可用")
    g = _wi.collect(cfg)
    idx = _wi.build_index(g)
    by_id = {t["target_id"]: t for t in idx}

    def _lookup(tid: str) -> dict:
        t = by_id.get(tid)
        if t is None:
            raise KeyError("工作台引用了不存在的 target：%s（workitems 里没有）"
                           % tid)
        return t

    queues = []
    for tid in QUEUE_TARGETS:
        t = _lookup(tid)
        queues.append({
            "queue_id": t["queue_id"], "label": t["label"],
            "question": t["question"], "count": t["count"], "unit": t["unit"],
            "source": t["source"], "definition": t["definition"],
            "where": t["where"], "target": tid, "metric": t["primary"],
            "suggestion": t["suggestion"], "owner": t["owner"],
            "kind": t["kind"], "level_why": t["level_why"],
            "level": t["level"], "reconcile_ok": t["reconcile_ok"]})

    kind_by_id = {t["target_id"]: t["kind"] for t in idx}
    queue_details = _wi.build_queue_details(g, idx)
    for rows in queue_details.values():
        for a in rows:
            # 分类判据来自 target（`severity.py` 是唯一判据，这里不再自己写
            # "非零即黄"——那正是第②步要消掉的第二套口径）。聚合数（target
            # 为 None）无私自拍板的余地，按"累计量"中性处理。
            tid = a.get("target")
            a["kind"] = kind_by_id.get(tid, _sev.KIND_STOCK) if tid \
                else _sev.KIND_STOCK
            a["level"] = _sev.level_of(a["kind"], int(a.get("value") or 0))
            a["level_why"] = _sev.explain(a["kind"], int(a.get("value") or 0))

    def _arm(tid: str) -> dict:
        t = _lookup(tid)
        return {"value": t["count"], "unit": t["unit"], "source": t["source"],
                "definition": t["definition"], "where": t["where"],
                "kind": t["kind"], "level_why": t["level_why"],
                "level": t["level"], "target": tid, "metric": t["primary"],
                "suggestion": t["suggestion"], "owner": t["owner"],
                "metrics": t["metrics"], "reconcile_ok": t["reconcile_ok"]}

    rings = [{"ring_id": rid, "title": title, "definition": definition,
              "arms": [_arm(tid) for tid in tids]}
             for rid, title, definition, tids in RING_DEFS]

    by_status: dict[str, int] = {}
    for r in g["records"].values():
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    bad = [t["target_id"] for t in idx if not t["reconcile_ok"]]
    return {
        "view_actions_api": 1,
        "view_overview_api": VIEW_OVERVIEW_API,
        "view_workitems_api": _wi.VIEW_WORKITEMS_API,
        "queues": queues,
        "queue_details": queue_details,
        "rings": rings,
        "by_status": by_status,
        "registry": g["registry"],
        "errors_meta": g["err_meta"],
        "db_fingerprint": g["errs_rep"].get("db_fingerprint"),
        "reconcile_bad": bad,
        "hint": "本页只做**聚合与口径展示**：每个数字都带 source（哪个上游函数"
                "算的）与 definition（怎么算的），点数字（或「去处理」）进"
                "「处理单」看**正好这一批**的待处理记录与处理建议。"
                "该数字与那份清单由同一个函数派生（workitems），对不上会红。"
                "口径冲突时以 source 指向的那一处为准。动线说明（用户 2026-10-14"
                " 批注）：「去处理」进的是**处理单**，不是命令台也不是报告页——"
                "处理单里逐条带建议与命令包，命令包里的「在命令台打开」才去命令台"
                "就地执行；每张卡写的「明细同步在：某页」是同一批数字的同源视图，"
                "供对账用，不是按钮去向。",
    }
