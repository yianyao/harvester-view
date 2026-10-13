# -*- coding: utf-8 -*-
"""P4-1 nags.py：反复叮嘱挖掘（**层一：指令遵循提醒**）。

语义定位（用户 2026-10-13 裁决，逐字）："反复叮嘱包括了两层意思：其一是对
工具/skill/Agent 调用等**不能很好遵循指令，过了就忘**，所以需要反复提醒"。
本模块挖的就是这一层：user 轮次命中指令/抱怨词句 → 跨会话聚类 → 候选清单
带锚点，供人裁决归宿（班规条目 / harness 问题清单，见 P4-2）。

口径（探针 v2 全盘继承，勿擅改；改口径要用户点头）：
- 三层前置过滤（交接 §4 第 4 条判据原话）："排除 system-reminder 注入、
  排除'同一串被重复发送 ≥N 次'的模板、排除长正文粘贴"；
- 词句命中 → 逐字归一聚类（KEY_LEN 前缀键）→ 模板过滤 → 相似归并
  （bigram Jaccard ≥0.55，抓"每次换个说法"）；
- 只读 harvester.db（mode=ro）、只读 `raw` 列；锚点 = messages.rowid；
- 相似度工具与阈值 import 自 harvester.topicprep（H40 冻结口径）。

用法：
    python -m actions.analysis.nags --db <harvester.db> [--out <md>]
缺 --out 时打印到 stdout。上游 session-harvester 缺失时 SystemExit 报人话。
"""
from __future__ import annotations

import argparse
import datetime
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# ---- 口径常量（与探针 v2 逐值一致；改任何一处 = 改口径，要用户点头）----
NAG_WORDS = ("不要", "必须", "记得", "别再", "又忘", "说过了", "跟你讲", "注意",
             "禁止", "不能", "一定要", "下次", "重复", "强调", "说过", "再次提醒",
             "为什么还", "怎么回事", "改了又", "还是错", "不是让你")
MIN_KEEP = 10         # 剥完注入后剩不足此数 = 纯注入轮 / 过短轮
PASTE_MAX = 600       # 长正文粘贴闸（仅 P4-1；P4-3 留粘贴——那是主信号）
TEMPLATE_N = 8        # 同一归一串被发送 ≥N 次 = 模板
KEY_LEN = 80          # "同一句"的归一键长度
SIM_THRESHOLD = 0.55  # 候选组相似归并阈值（宽松，宁可多给人看）
PREVIEW = 70          # 候选样例预览长度

_RE_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def _norm(t: str) -> str:
    t = re.sub(r"\s+", "", t or "")
    return t.strip("。！？!?.,，；;：:~～ ")


def strip_injection(t: str):
    """剥掉 system-reminder 注入块。返回 (剥后文本, 是否含注入)。"""
    if not t:
        return t, False
    has = "<system-reminder>" in t
    return _RE_REMINDER.sub("", t), has


def _topicprep():
    """复用上游冻结口径（_grams/_jaccard）。缺失时 SystemExit 报人话。"""
    from actions import ensure_harvester_importable
    err = ensure_harvester_importable()
    if err:
        raise SystemExit("[nags] 上游 session-harvester 不可用：%s" % err)
    from harvester.topicprep import _grams, _jaccard  # noqa: PLC0415
    return _grams, _jaccard


def collect(con: sqlite3.Connection) -> dict:
    """对只读连接跑全口径，返回 account + groups（候选全量，人工过目）。

    连接若未设 Row 工厂会就地补上（只读安全；CLI 与测试夹具都不用记这事）。
    """
    if con.row_factory is not sqlite3.Row:
        con.row_factory = sqlite3.Row
    grams, jaccard = _topicprep()
    rows = list(con.execute(
        "SELECT sid, rowid AS rid, role, "
        "COALESCE(NULLIF(raw,''), text) AS body FROM messages "
        "WHERE role='user' ORDER BY sid, rowid"))

    n_inject = n_short = n_paste = 0
    lane = []
    for r in rows:
        body, has_inj = strip_injection(r["body"] or "")
        if has_inj:
            n_inject += 1
        if len(_norm(body)) < MIN_KEEP:
            n_short += 1
            continue
        if len(_norm(body)) > PASTE_MAX:
            n_paste += 1          # 长粘贴只滤出 P4-1 通道
            continue
        lane.append((r["sid"], r["rid"], body))

    hits = [(sid, rid, body) for (sid, rid, body) in lane
            if any(w in body for w in NAG_WORDS)]

    exact: dict[str, dict] = defaultdict(
        lambda: {"sids": set(), "n": 0, "sample": None})
    for sid, rid, body in hits:
        key = _norm(body)[:KEY_LEN]
        if len(key) < 6:
            continue
        g = exact[key]
        g["sids"].add(sid)
        g["n"] += 1
        g["sample"] = g["sample"] or (sid, rid, body)
    templates = {k for k, g in exact.items() if g["n"] >= TEMPLATE_N}
    n_template_turns = sum(g["n"] for k, g in exact.items() if k in templates)

    cand = [g for k, g in exact.items()
            if k not in templates and len(g["sids"]) >= 2]
    # 相似归并：代表串 bigram Jaccard ≥SIM_THRESHOLD 并组
    merged: list[dict] = []
    for g in sorted(cand, key=lambda g: -len(g["sids"])):
        rep = grams(_norm(g["sample"][2])[:200])
        hit = None
        for m in merged:
            if jaccard(rep, grams(_norm(m["sample"][2])[:200])) >= SIM_THRESHOLD:
                hit = m
                break
        if hit:
            hit["sids"] |= g["sids"]
            hit["n"] += g["n"]
            hit["members"].append(g["sample"])
        else:
            merged.append({"sids": set(g["sids"]), "n": g["n"],
                           "sample": g["sample"], "members": [g["sample"]]})
    merged.sort(key=lambda m: (-len(m["sids"]), -m["n"]))

    groups = [{"n": m["n"], "sids": sorted(m["sids"]),
               "sample": {"sid": m["sample"][0], "rowid": m["sample"][1],
                          "preview": _norm(m["sample"][2])[:PREVIEW]},
               "members": [{"sid": s, "rowid": rid} for s, rid, _ in m["members"]]}
              for m in merged]
    return {"account": {
                "user_turns": len(rows), "n_inject": n_inject,
                "n_short": n_short, "n_paste": n_paste, "n_lane": len(lane),
                "n_hits": len(hits),
                "n_template_groups": len(templates),
                "n_template_turns": n_template_turns,
                "n_groups": len(groups)},
            "groups": groups}


def render_markdown(res: dict) -> str:
    """候选清单 markdown（供人过目；结论由人下，模块不下）。"""
    a = res["account"]
    lines = [
        "# P4-1 反复叮嘱候选（nags · 层一：指令遵循提醒）",
        "",
        "生成：%s" % datetime.datetime.now().isoformat(timespec="seconds"),
        "",
        "语义定位（用户裁决）：反复叮嘱之**其一**——对工具/skill/Agent 调用"
        "不能很好遵循指令、过了就忘，需要反复提醒。",
        "",
        "## 过滤账目",
        "",
        "| 层 | 数量 |", "|---|---|",
        "| user 轮次总数（读 raw） | %d |" % a["user_turns"],
        "| 含 system-reminder 注入（剥块后继续） | %d |" % a["n_inject"],
        "| 剥后过短丢弃 | %d |" % a["n_short"],
        "| 长正文粘贴丢弃（>%d 字） | %d |" % (PASTE_MAX, a["n_paste"]),
        "| 参与命中 | %d |" % a["n_lane"],
        "| 命中指令/抱怨词句 | %d |" % a["n_hits"],
        "| 逐字模板丢弃（≥%d 次） | %d 组 / %d 轮 |"
        % (TEMPLATE_N, a["n_template_groups"], a["n_template_turns"]),
        "| 相似归并后候选组（跨 ≥2 会话） | %d |" % a["n_groups"],
        "",
        "## 候选组（全量，人工甄别；锚点 = sid + messages.rowid）",
        "",
    ]
    if not res["groups"]:
        lines.append("（无候选——本库当前没有跨 ≥2 会话的非模板叮嘱。）")
    for i, g in enumerate(res["groups"], 1):
        lines.append("%d. **%d 会话 × %d 次**：「%s」　锚点 %s#row%s"
                     % (i, len(g["sids"]), g["n"], g["sample"]["preview"],
                        g["sample"]["sid"][:40], g["sample"]["rowid"]))
    lines += ["", "---", "",
              "归宿（P4-2）：真叮嘱 → 班规条目（AGENTS.md / 记忆）；"
              "harness 不听话 → harness 问题清单。模块不替人下结论。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m actions.analysis.nags",
        description="P4-1 反复叮嘱候选（只读 harvester.db）")
    ap.add_argument("--db", default="harvester.db", help="索引库路径")
    ap.add_argument("--out", help="markdown 输出路径（缺省打印 stdout）")
    args = ap.parse_args(argv)
    con = sqlite3.connect("file:%s?mode=ro"
                          % Path(args.db).resolve().as_posix(), uri=True)
    try:
        res = collect(con)
    finally:
        con.close()
    md = render_markdown(res)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8", newline="\n")
        print("[nags] 候选清单已写：%s（候选 %d 组，账目见文件）"
              % (args.out, res["account"]["n_groups"]))
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
