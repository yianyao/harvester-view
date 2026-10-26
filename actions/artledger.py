# -*- coding: utf-8 -*-
"""产物台账（P2 #14 · 《优化方案》D1'，2026-10-26 用户「第5项开工」）。

一句话：**全库翻一遍「AI 到底写过哪些文件、改了几版、属于哪个主题」**。

与 `dualtrack._artifacts_track` 的关系（一处口径两处出口，不另写一套）：
- `_artifacts_track` 是**按主题成员过滤**的下轨（先有主题才看得到产物）；
- 本模块是**不过滤**的全库台账（先看有哪些产物，再反查它落在哪个主题里）。
两者读的是同一张 `artifacts` 表、同一个分组键 `(sid, file_path)`，所以
「双轨下轨的文件数」与「台账里该主题的文件数」必然对得上。

━━ 三条纪律（都写进返回值的 `honest`，不靠读代码猜）━━

1. **这不是文件系统快照**。`artifacts` 表登记的是「某会话里 AI 用写文件/
   改文件工具动过的记录」，长什么样取决于提取时源里有完整的写入内容——
   dsh 源有、豆包/元宝/千问实测只有截断预览（`harvester/artifacts.py:10-18`
   判负登记）。所以台账一定带 `coverage`（已登记会话 / 全会话），
   **本面不代表全库**。
2. **同一文件可能出现两行**。台账的路径原样保留提取时的写法（有的绝对
   路径、有的相对路径），本项目**不做路径归一**——归一要靠猜（相对谁？
   盘符换机还有效吗？），猜错比不归一更坏。页面明说这件事。
3. **「工具」一列实测为空**。`artifacts.tool` 在 dsh 源里没落上值（真实
   数据 2109 行全空）。空就显示空，不拿 `Write/Edit` 之类去补——那是猜。

只读：`mode=ro` 打开 artifacts_meta.db / harvester.db / topics_meta.db，
零写库零落盘。纯标准库。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = "artifact_ledger/1"

#: 一次最多回多少行台账（页面上限；总数另给，不截断总数）
MAX_ROWS = 500
#: 全库候选（打磨/反转、反复叮嘱）每类最多列几条给人过目
CAND_CAP = 10
#: 每个文件路径预览只留前 N 字符（台账是索引，看内容走评审包）
PATH_CAP = 160

DEFINITIONS = {
    "sessions_registered": "台账里有写入记录的会话数（artifacts 表 distinct sid）",
    "files": "出现过的文件路径数（不同写法算不同文件——本项目不做路径归一）",
    "pairs": "「会话 × 文件」组合数：同一文件在两个会话里各算一条",
    "pairs_in_topics": "上面这些组合里，会话已被注册进某个主题的条数",
    "versions": "写入记录总条数（每次写文件/改文件算一版）",
    "n_versions": "该文件在这一会话里被写/改的版数",
    "changed_chars": "该文件各版新内容字符数之和（旧内容不重复计）",
    "topics": "该会话被注册进的主题（topics_meta 的成员反查，可为空）",
    "band": "打磨候选对数：同一会话相邻两轮问话相似度落 0.80–0.90（H40 冻结口径）",
    "turn_hits": "反转词命中轮数（只认轮次开头的「不对/算了/推翻…」）",
    "nags_groups": "反复叮嘱候选组数（过滤版口径，nags.py 冻结常量）",
}

SOURCES = {
    "sessions_registered": "artifacts_meta.db · artifacts.sid",
    "files": "artifacts_meta.db · artifacts.file_path",
    "pairs": "artifacts_meta.db · distinct (sid, file_path)",
    "pairs_in_topics": "topics_meta.db · topics.members 与台账 sid 求交集",
    "versions": "artifacts_meta.db · artifacts 行数",
    "n_versions": "现算：按 (sid, file_path) 分组数行数",
    "changed_chars": "现算：sum(length(new_text))",
    "topics": "topics_meta.db · topics.members（JSON 列表，只取 sid）",
    "band": "actions/analysis/revisions.py · collect()（同一实现，不复刻阈值）",
    "turn_hits": "actions/analysis/revisions.py · collect()",
    "nags_groups": "actions/analysis/nags.py · collect()",
}

HONEST = [
    "本面**只覆盖已登记提取的源**（dsh 一个源；豆包/元宝/千问实测只有截断预览，"
    "提不出完整内容）——已登记会话数 / 全会话数见上方覆盖度，本面不代表全库。",
    "台账记的是「AI 写/改文件的记录」，**不是文件系统快照**：文件后来被谁删了、"
    "被别的工具改了，这里都看不出来。",
    "同一文件可能出现两行：台账原样保留提取时的路径写法（绝对/相对都有），"
    "本项目不做路径归一——归一必须猜「相对谁」，猜错比不归一更坏。",
    "「工具」一列实测为空（dsh 源的提取记录没落上工具名）。空就显示空，"
    "不拿 Write/Edit 去补——那是猜。",
]

HINT_UNCONFIGURED = (
    "产物台账还没接上：起服时没给 --artifacts-meta（或那个库不存在）。"
    "先由人在终端跑 `python -m harvester artifacts extract --sid <会话 id>` "
    "登记某个会话的写入记录，再起服带上 --artifacts-meta。"
    "**本页面绝不代跑提取**——它要回源读原始对话，属于慢动作，由人决定何时跑。"
)

HINT_EMPTY = (
    "台账库在，但一条记录都没有——说明还没跑过提取。终端跑："
    "`python -m harvester artifacts extract --sid <会话 id>`"
    "（一次一个会话；本页面绝不代跑）。"
)


def _ro(db) -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % Path(db).as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    return con


def _cut(s, n: int = PATH_CAP) -> str:
    t = "" if s is None else str(s)
    return t if len(t) <= n else t[:n] + "…"


def _topic_index(topics_meta) -> dict[str, list[dict]]:
    """sid → [{id, name}]（主题反查）。库缺失/坏行一律当"没有主题"，不报错。

    `members` 是**对象列表**（每项 `{sid, evidence}`）——按纯字符串列表写会
    `TypeError: unhashable type: 'dict'`（growth.py 在真库上踩过同一个坑），
    故两种形态都容忍。
    """
    out: dict[str, list[dict]] = {}
    if not topics_meta or not Path(topics_meta).is_file():
        return out
    con = _ro(topics_meta)
    try:
        rows = list(con.execute("SELECT id, name, members FROM topics"))
    except sqlite3.Error:
        return out
    finally:
        con.close()
    for r in rows:
        try:
            mem = json.loads(r["members"] or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(mem, list):
            continue
        sids = [x.get("sid") if isinstance(x, dict) else x for x in mem]
        for sid in sids:
            if isinstance(sid, str) and sid:
                out.setdefault(sid, []).append({"id": r["id"],
                                                "name": r["name"]})
    return out


def _pairs(meta_path: Path) -> list[dict]:
    """全库 (sid, file_path) 聚合（**不过滤成员**）——台账的唯一取数口。"""
    con = _ro(meta_path)
    try:
        rows = list(con.execute(
            "SELECT sid, "
            "       COALESCE(file_path, '（未知文件）') AS file_path, "
            "       COUNT(*) AS n_versions, "
            "       MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
            "       SUM(length(COALESCE(new_text,''))) AS changed_chars, "
            "       GROUP_CONCAT(DISTINCT NULLIF(tool,'')) AS tools "
            "FROM artifacts GROUP BY sid, COALESCE(file_path, '（未知文件）')"))
    finally:
        con.close()
    out = []
    for r in rows:
        out.append({
            "sid": r["sid"],
            "file_path": r["file_path"],
            "file_short": _cut(r["file_path"]),
            "n_versions": int(r["n_versions"] or 0),
            "first_ts": r["first_ts"] or "",
            "last_ts": r["last_ts"] or "",
            "changed_chars": int(r["changed_chars"] or 0),
            # tools 为空串 = 源里没落上工具名（见 HONEST 第 4 条）
            "tools": [t for t in (r["tools"] or "").split(",") if t],
        })
    return out


def _candidates(db) -> dict:
    """全库跑一遍打磨/反转 + 反复叮嘱（**调用冻结口径的同一实现**）。

    为什么在台账页也放这两个账目：产物是「改出来的」，改之前人往往先有
    一轮反复叮嘱或推翻重来。台账只回答"写了哪些文件"，回答不了"为什么
    改这么多版"——这两个账目是那一层的入口。口径一律由
    `analysis/revisions.py`、`analysis/nags.py` 现算，本模块**不复制阈值**。
    """
    from .analysis import nags, revisions
    con = _ro(db)
    try:
        rev = revisions.collect(con)
        nag = nags.collect(con)
    finally:
        con.close()
    return {
        "revisions": {
            "account": rev["account"],
            "thresholds": rev["thresholds"],
            "band": [{"sid": p["sid"], "rowid_a": p["rowid_a"],
                      "rowid_b": p["rowid_b"], "jaccard": p["jaccard"]}
                     for p in rev["band"][:CAND_CAP]],
            "turn_hits": [{"sid": h["sid"], "rowid": h["rowid"],
                           "word": h["word"]}
                          for h in rev["turn_hits"][:CAND_CAP]],
        },
        "nags": {
            "account": nag["account"],
            # sample 是 nags.collect 自己给好的 dict（{sid,rowid,ts,preview}），
            # 原样转出即可——**不要**在这里再切一刀正文，那等于自造第二套预览口径。
            "groups": [{"n": g["n"], "n_sids": len(g["sids"]),
                        "sample": g.get("sample")}
                       for g in nag["groups"][:CAND_CAP]],
        },
    }


def _uniques(pairs: list[dict], key: str) -> int:
    """去重计数（小工具，避免在 build 里写两层推导式看不清）。"""
    return len({p[key] for p in pairs})


def build(cfg, *, limit: int = 200, sort: str = "versions",
          sid: str = "") -> dict:
    """产物台账数据面（A 档只读）。

    参数全部可选：`limit` 回多少行（默认 200，上限 MAX_ROWS）；
    `sort` = versions（版数多的在前）/ recent（最近改的在前）/ path（按路径）；
    `sid` 非空则只看这一个会话。
    """
    from harvester.artifacts import coverage
    try:
        lim = max(1, min(int(limit or 200), MAX_ROWS))
    except (TypeError, ValueError):
        lim = 200
    cov = coverage(cfg.db, cfg.artifacts_meta)
    base = {"view_actions_api": 1, "schema": SCHEMA,
            "definitions": DEFINITIONS, "sources": SOURCES,
            "honest": list(HONEST),
            "sort": sort, "limit": lim, "sid_filter": sid or ""}

    meta = Path(cfg.artifacts_meta) if cfg.artifacts_meta else None
    if meta is None or not meta.is_file():
        base.update({
            "configured": False, "coverage": cov,
            "totals": {"sessions_registered": cov["registered_sessions"],
                       "files": 0, "pairs": 0, "pairs_in_topics": 0,
                       "versions": 0, "changed_chars": 0},
            "rows": [], "rows_total": 0, "truncated": False,
            "topics_available": False,
            "hint": HINT_UNCONFIGURED,
        })
        return base

    pairs = _pairs(meta)
    if sid:
        pairs = [p for p in pairs if p["sid"] == sid]
    tidx = _topic_index(cfg.topics_meta)
    for p in pairs:
        ts = tidx.get(p["sid"]) or []
        p["topics"] = [{"id": t["id"], "name": t["name"]} for t in ts]
        p["topic_names"] = "、".join(t["name"] for t in ts) or ""
    if sort == "recent":
        pairs.sort(key=lambda p: (p["last_ts"], p["n_versions"]), reverse=True)
    elif sort == "path":
        pairs.sort(key=lambda p: (p["file_path"], p["sid"]))
    else:
        pairs.sort(key=lambda p: (-p["n_versions"], p["last_ts"]))
    rows_total = len(pairs)
    rows = pairs[:lim]
    in_topics = sum(1 for p in pairs if p["topics"])
    base.update({
        "configured": True,
        "coverage": cov,
        "totals": {
            "sessions_registered": cov["registered_sessions"],
            "files": _uniques(pairs, "file_path"),
            "pairs": rows_total,
            "versions": sum(p["n_versions"] for p in pairs),
            "pairs_in_topics": in_topics,
            # 键名与 `definitions`/行字段统一用 changed_chars，不叫 chars
            # ——同一个数在三个地方两个名字，迟早有人对不上账。
            "changed_chars": sum(p["changed_chars"] for p in pairs),
        },
        "rows": rows,
        "rows_total": rows_total,
        "truncated": rows_total > len(rows),
        "topics_available": bool(tidx),
        "hint": (HINT_EMPTY if not rows_total else ""),
    })
    if cfg.db and Path(cfg.db).is_file():
        base["candidates"] = _candidates(cfg.db)
    else:
        base["candidates"] = {"unavailable":
                              "索引库不存在：%s（先跑采集更新）" % cfg.db}
    return base


def render_md(d: dict) -> str:
    """人读版（命令台/报告落地用）。数字全部来自 build()，本函数只排版。"""
    t = d.get("totals") or {}
    cv = d.get("coverage") or {}
    lines = [
        "# 产物台账（artifact_ledger · 全库）",
        "",
        "一句话：AI 到底写过哪些文件、各改了几版、属于哪个主题。",
        "",
        "## 覆盖度（先看这个再看表）",
        "",
        "- 已登记 %s / 总会话 %s（%s%%）｜源：%s"
        % (cv.get("registered_sessions", 0), cv.get("total_sessions", 0),
           cv.get("pct", 0.0), "、".join(cv.get("sources") or ["—"])),
        "- %s" % (cv.get("sources_note") or ""),
        "- **本面不代表全库**：只覆盖能提出完整写入内容的源（dsh）。",
        "",
        "## 账目",
        "",
        "| 项 | 数量 |", "|---|---|",
        "| 有写记录的会话 | %s |" % t.get("sessions_registered", 0),
        "| 文件路径数（不做路径归一） | %s |" % t.get("files", 0),
        "| 会话×文件 组合 | %s |" % t.get("pairs", 0),
        "| 其中能反查到主题的 | %s |" % t.get("pairs_in_topics", 0),
        "| 写入记录总条数（版数） | %s |" % t.get("versions", 0),
        "| 改动字数合计 | %s |" % t.get("changed_chars", 0),
        "",
    ]
    if d.get("hint"):
        lines += ["> %s" % d["hint"], ""]
    rows = d.get("rows") or []
    if rows:
        lines += ["## 台账（按%s，前 %d 行 / 共 %d）"
                  % ({"versions": "版数", "recent": "最近改动",
                      "path": "路径"}.get(d.get("sort"), "版数"),
                     len(rows), d.get("rows_total", len(rows))), "",
                  "| 版数 | 文件 | 主题 | 首→末 | 改动字数 |",
                  "|---|---|---|---|---|"]
        for r in rows:
            lines.append("| %d | %s | %s | %s → %s | %d |"
                         % (r["n_versions"], r["file_short"],
                            r["topic_names"] or "—",
                            (r["first_ts"] or "—")[:10],
                            (r["last_ts"] or "—")[:10],
                            r["changed_chars"]))
        lines.append("")
    cand = d.get("candidates") or {}
    if "revisions" in cand:
        ra = cand["revisions"]["account"]
        th = cand["revisions"]["thresholds"]
        na = cand["nags"]["account"]
        lines += ["## 为什么改这么多版（全库候选，只列不裁决）", "",
                  "- 打磨候选（相邻两轮相似度 %.2f–%.2f）：%d 对 / %d 会话"
                  % (th["band"], th["dup"], ra["n_band"], ra["n_dup"]),
                  "- 反转词命中（轮次开头）：%d 轮" % ra["n_turn_words"],
                  "- 反复叮嘱候选组：%d 组 / 命中 %d 轮"
                  % (na.get("n_groups", 0), na.get("n_hits", 0)),
                  ""]
    lines += ["## 口径与来源", "",
              "| 数字 | 怎么算的 | 取自 |", "|---|---|---|"]
    defs, srcs = d.get("definitions") or {}, d.get("sources") or {}
    for k in defs:
        lines.append("| %s | %s | %s |" % (k, defs[k], srcs.get(k, "—")))
    lines += ["", "## 别把这面读过头", ""]
    for x in d.get("honest") or []:
        lines.append("- %s" % x)
    lines.append("")
    return "\n".join(lines)
