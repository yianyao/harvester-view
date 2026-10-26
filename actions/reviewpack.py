# -*- coding: utf-8 -*-
"""评审包（P2 #14 · 《优化方案》D2'，2026-10-26 用户「第5项开工」）。

一句话：**把「一个文件为什么被改成这样」的材料打成一个包**，交给任何
Agent（或人）评，评完的结论回到 `cards new`（两段提交）落成一张卡。

━━ 包里放什么（缺一不可，缺了就等于让人猜）━━

| 块 | 内容 | 为什么必须有 |
|---|---|---|
| 版本序列 | 该文件在这一会话里的每次写入（时间/前后字符数/预览） | 没有它，"改了什么"无从判断 |
| 打磨/反转候选 | 同会话里相邻两轮问话相似度落边界带的对、轮次开头转折词 | 改动的**动机**常在这一层（用户反复推敲/推翻） |
| 相关历史卡 | 卡片 anchors 命中本会话（强），或 triggers 关键词命中文件名/改动内容（弱） | 已经踩过的坑不该再踩 |
| 相关错误模式 | 本会话的错误步，按归一模式聚类 + 首条原文 | 改坏的证据在这 |
| 锚点清单 | 每条材料指回哪里（artifacts 的 (sid,seq) / 消息 rowid / 卡片路径 / 步骤 seq） | 别人复核时必须能回原文 |

━━ 三条纪律（都写进返回值的 `honest`）━━

1. **「改动之前你在反复打磨」是按时间排队，不是因果**。台账只记「什么时候
   写了什么」，消息只记「什么时候说了什么」；两者只有时间先后，没有指针
   相连。所以包里一律叫「时间上紧挨着」，不叫「因为」。
2. **过程指标不是质量分**。包里给的「打磨密度 / 反转词次数」衡量的是**过程**
   （改得多么反复），不是结果好不好。数字变低可能意味着一次说清了，也可能
   意味着放弃了——本模块不判。
3. **卡片命中靠关键词，会漏**。triggers 是人工登记的现象词，卡片没登记
   triggers 就一条都命中不了；弱命中的每一条都写明「命中了哪个词」，好让人
   一眼看出是真命中还是撞词。

只读：mode=ro 读四本账与卡片目录，零写库零落盘。裁决回填走 `cards new`
（B 档两段提交），本模块不造任何写路径。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = "review_pack/2"

#: 版本预览单侧长度（与 dualtrack 的 ART_PREVIEW 同口径，看全文去 artifacts show）
ART_PREVIEW = 300
#: 各块最多列几条（包是给人看的，不是全量导出）
VERSION_CAP = 12
TURN_CAP = 6
CARD_CAP = 8
ERR_CAP = 8

DEFINITIONS = {
    "n_versions": "该文件在这一会话里的写入记录条数（每次写/改算一版）",
    "changed_chars": "各版新内容字符数之和（旧内容不重复计）",
    "band_pairs": "同会话相邻两轮问话相似度落 0.80–0.90 的对数（打磨候选，H40 冻结口径）",
    "turn_words": "轮次开头出现「不对/算了/推翻…」的轮数（反转候选）",
    "band_per_version": "打磨候选对数 ÷ 版数——**过程指标**：改一版之前磨了几轮",
    "err_patterns": "本会话错误步按归一模式聚类的组数（同一条错误反复犯才算一组）",
}

SOURCES = {
    "n_versions": "artifacts_meta.db · artifacts（按 sid + file_path）",
    "changed_chars": "artifacts_meta.db · sum(length(new_text))",
    "band_pairs": "actions/analysis/revisions.py · collect()（同一实现，不复刻阈值）",
    "turn_words": "actions/analysis/revisions.py · collect()",
    "band_per_version": "由上两行现算（不另存一份）",
    "err_patterns": "harvester.errstats.collect_errors_from_db（归一模式同一实现）",
}

HONEST = [
    "**「改动之前你在反复打磨」是按时间排的队，不是因果。**台账只记「什么时候"
    "写了什么」，消息只记「什么时候说了什么」，两者没有指针相连；包里写的都是"
    "「时间上紧挨着」，不是「因为」。",
    "**过程指标不是质量分。**打磨密度（每版之前磨了几轮）、反转词次数衡量的是"
    "改得多么反复，不是结果好不好。数字变低可能是一次说清了，也可能是放弃了——"
    "本包不判。",
    "**卡片命中靠关键词，会漏。**triggers 是人工登记的现象词；没登记 triggers "
    "的卡片一条都命中不了。弱命中的每条都写明命中了哪个词，撞词一眼看得出。",
    "**卡片的会话号写法不统一。**真实卡片里有的锚点带源前缀（`dsh:…`），有的不带；"
    "本项目按「去掉源前缀后逐字相等」认作同一会话（强命中），并在那一条的"
    "「为什么出现在这里」里写明是哪种。",
    "**版本≠文件快照。**台账记的是提取时源里的写入内容；文件后来被别的工具改过、"
    "或已被删除，这里都看不出来。",
]

STEPS = [
    "① 生成包（本页/命令台，只读）",
    "② 整段复制交给任何 Agent（或自己读）：让它只依据包里的事实判「为什么改、"
    "改得对不对」，缺证据就明说缺什么，不许编根因",
    "③ 结论回到 `cards new`（两段提交：先预演再确认），落成一张卡——"
    "本页面不代跑写入",
]


def _ro(db) -> sqlite3.Connection:
    con = sqlite3.connect("file:%s?mode=ro" % Path(db).as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    return con


def _cut(s, n: int = ART_PREVIEW) -> str:
    t = "" if s is None else str(s)
    return t if len(t) <= n else t[:n] + "…"


def _version_rows(meta_path: Path, sid: str, file_path: str) -> list[dict]:
    con = _ro(meta_path)
    try:
        q = ("SELECT seq, ts, tool, file_path, old_text, new_text "
             "FROM artifacts WHERE sid=?")
        args: tuple = (sid,)
        if file_path:
            q += " AND COALESCE(file_path,'（未知文件）')=?"
            args += (file_path,)
        q += " ORDER BY seq"
        return [dict(r) for r in con.execute(q, args)]
    finally:
        con.close()


def _files_of(meta_path: Path, sid: str) -> list[dict]:
    """该会话登记过哪些文件（file_path 为空时让调用方挑一个）。"""
    con = _ro(meta_path)
    try:
        rows = list(con.execute(
            "SELECT COALESCE(file_path,'（未知文件）') AS file_path, "
            "COUNT(*) AS n, MIN(ts) AS a, MAX(ts) AS b FROM artifacts "
            "WHERE sid=? GROUP BY COALESCE(file_path,'（未知文件）') "
            "ORDER BY n DESC", (sid,)))
    finally:
        con.close()
    return [{"file_path": r["file_path"], "n_versions": int(r["n"]),
             "first_ts": r["a"] or "", "last_ts": r["b"] or ""} for r in rows]


def _ts_of(con, rowids: set[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    if not rowids:
        return out
    q = ("SELECT rowid, ts FROM messages WHERE rowid IN (%s)"
         % ",".join("?" * len(rowids)))
    for r in con.execute(q, tuple(rowids)):
        out[r["rowid"]] = r["ts"] or ""
    return out


def _candidates_for_sid(cfg, sid: str) -> dict:
    """打磨/反转候选（**调冻结口径的同一实现**，再按本会话过滤）。"""
    from .analysis import revisions
    con = _ro(cfg.db)
    try:
        rev = revisions.collect(con)
        band = [p for p in rev["band"] if p["sid"] == sid]
        hits = [h for h in rev["turn_hits"] if h["sid"] == sid]
        need = {p["rowid_a"] for p in band} | {p["rowid_b"] for p in band}
        need |= {h["rowid"] for h in hits}
        tsmap = _ts_of(con, need)
    finally:
        con.close()
    for p in band:
        # 取两轮里较晚那一轮的时间做「这次打磨发生在什么时候」的定位
        p["ts"] = tsmap.get(p["rowid_b"]) or tsmap.get(p["rowid_a"]) or ""
    for h in hits:
        h["ts"] = tsmap.get(h["rowid"]) or ""
    return {"account": rev["account"], "thresholds": rev["thresholds"],
            "band": band, "turn_hits": hits}


def _errors_for_sid(cfg, sid: str) -> list[dict]:
    """本会话的错误步（归一模式来自 errstats，与报告页 G2 同一实现）。"""
    from harvester.errstats import collect_errors_from_db
    con = _ro(cfg.db)
    try:
        errs, _meta = collect_errors_from_db(Path(cfg.db), con=con)
    finally:
        con.close()
    return [e for e in errs if e["sid"] == sid]


def _err_groups(errs: list[dict]) -> list[dict]:
    """按 (class, pattern) 聚类，组内留最早一条原文当证据。"""
    out: dict[tuple[str, str], dict] = {}
    for e in errs:
        key = (e.get("class") or "?", e.get("pattern") or "（空）")
        g = out.setdefault(key, {"class": key[0], "pattern": key[1],
                                 "n": 0, "first": None})
        g["n"] += 1
        if g["first"] is None:
            g["first"] = {"seq": e["seq"], "ts": e.get("ts") or "",
                          "tool": e.get("tool") or "",
                          "text": _cut(e.get("error"), 240),
                          "resolved": bool(e.get("resolved"))}
    rows = sorted(out.values(), key=lambda g: -g["n"])
    return rows[:ERR_CAP]


def _bare_sid(s: str) -> str:
    """去掉「源前缀」（第一个冒号之前那段，如 `dsh:`）。"""
    t = str(s or "")
    return t.split(":", 1)[1] if ":" in t else t


def _same_session(a: str, b: str) -> bool:
    """同一会话？逐字相等，或**去掉源前缀后**逐字相等。

    为什么需要这条：真实卡片里锚点写法不统一——`kc-20261006-0004` 的锚点是
    `--D-Data-git-specSkill--/…session.v4.jsonl.zstd`（不带 `dsh:`），而台账
    里的 sid 是 `dsh:--D-Data-…`。逐字比会把明明同一个会话判成"没关系"。
    只剥一次源前缀、不做别的归一（大小写/斜杠/盘符一律不动），因为再往下
    就得猜，猜错比漏配更坏。
    """
    if not a or not b:
        return False
    return a == b or _bare_sid(a) == _bare_sid(b)


def _card_matches(cfg, sid: str, file_path: str,
                  new_texts: list[str]) -> list[dict]:
    """卡片命中（强：anchors 指本会话；弱：triggers 关键词出现在文件名/改动内容）。

    弱命中一定带 `why`（命中了哪个词）——不写清就是让人去猜为什么这张卡出现了。
    """
    from cardlib import scan_cards
    root = Path(cfg.cards_root) if cfg.cards_root else None
    if root is None or not root.is_dir():
        return []
    # 弱命中的比对池：文件名 + 最后一条改动的新内容（改动内容才是这次的对象）
    pool = " ".join([file_path or ""] + [_cut(t, 4000) for t in new_texts[-1:]])
    pool_low = pool.lower()
    out = []
    for c in scan_cards(root):
        why = ""
        trig_hit = []
        trig = c.get("triggers") if isinstance(c.get("triggers"), list) else []
        for t in trig:
            w = t if isinstance(t, str) else str((t or {}).get("raw") or "")
            w = w.strip()
            if len(w) >= 2 and w.lower() in pool_low:
                trig_hit.append(w)
        anchors = c.get("anchors") if isinstance(c.get("anchors"), list) else []
        matched = [a for a in anchors if isinstance(a, dict)
                   and _same_session(a.get("sid"), sid)]
        if matched:
            exact = any(a.get("sid") == sid for a in matched)
            why = ("强命中：这张卡的锚点就指向本会话" if exact
                   else "强命中：卡片锚点指向同一个会话"
                        "（锚点没写源前缀，剥掉前缀后逐字相等）")
        elif trig_hit:
            why = "弱命中：triggers 里 " + "、".join("「%s」" % w
                                                    for w in trig_hit[:4]) + \
                  " 出现在文件名或改动内容里"
        if not why:
            continue
        out.append({"path": c.get("path") or "", "title": c.get("title") or "",
                    "type": c.get("type") or "", "confidence": c.get("confidence"),
                    "why": why, "strong": bool(matched),
                    "anchors": anchors[:3]})
    out.sort(key=lambda c: (not c["strong"], -(c["confidence"] or 0)))
    return out[:CARD_CAP]


def _window(versions: list[dict], band: list[dict],
            hits: list[dict]) -> list[dict]:
    """把候选按「落在哪两版之间」归位（时间序，不声称因果）。"""
    marks = [(v["seq"], v["ts"] or "") for v in versions]
    def _at(ts: str) -> tuple:
        # 找最后一个 ts <= 该时间的版本；没有就是「第 1 版之前」
        pick = None
        for seq, vts in marks:
            if vts and ts and vts <= ts:
                pick = seq
        return pick
    out = []
    for b in band:
        out.append({"kind": "打磨候选", "ts": b["ts"], "jaccard": b["jaccard"],
                    "rowid_a": b["rowid_a"], "rowid_b": b["rowid_b"],
                    "before_version": _at(b["ts"])})
    for h in hits:
        out.append({"kind": "反转候选", "ts": h["ts"], "word": h["word"],
                    "rowid": h["rowid"], "before_version": _at(h["ts"])})
    out.sort(key=lambda x: (x["ts"] or "", x["kind"]))
    return out


def build(cfg, *, sid: str, file_path: str = "", version_cap: int = VERSION_CAP,
          turn_cap: int = TURN_CAP) -> dict:
    """评审包数据面（A 档只读）。`sid` 必填；`file_path` 空则自动挑版数最多的那个。"""
    sid = (sid or "").strip()
    if not sid:
        raise ValueError("缺会话 id（--sid）——从产物台账点一行进来，或手填 sid")
    meta = Path(cfg.artifacts_meta) if cfg.artifacts_meta else None
    base = {"view_actions_api": 1, "schema": SCHEMA, "kind": "file", "sid": sid,
            "file_path": file_path or "",
            "definitions": DEFINITIONS, "sources": SOURCES,
            "honest": list(HONEST), "steps": list(STEPS)}
    if meta is None or not meta.is_file():
        base.update({"configured": False, "versions": [], "files": [],
                     "hint": "产物台账没接上（起服没给 --artifacts-meta，"
                             "或那个库不存在）。先在终端跑 "
                             "`python -m harvester artifacts extract --sid %s` "
                             "登记，再起服带上 --artifacts-meta。"
                             "**本页面绝不代跑提取。**" % sid})
        return base
    files = _files_of(meta, sid)
    auto = False
    if not file_path:
        if not files:
            base.update({"configured": True, "versions": [], "files": [],
                         "hint": "这个会话在台账里一条写入记录都没有。"
                                 "换一个会话，或先跑一次提取。"})
            return base
        file_path = files[0]["file_path"]
        auto = True
    rows = _version_rows(meta, sid, file_path)
    if not rows:
        base.update({"configured": True, "versions": [], "files": files,
                     "hint": "会话 %s 里没有这个文件的写入记录（路径要逐字一致；"
                             "台账保留提取时的原始写法，绝对/相对都可能有）。"
                             % sid})
        return base
    versions = [{
        "seq": r["seq"], "ts": r["ts"] or "", "tool": r["tool"] or "",
        "old_len": len(r["old_text"] or ""), "new_len": len(r["new_text"] or ""),
        "old_preview": _cut(r["old_text"]),
        "new_preview": _cut(r["new_text"]),
    } for r in rows]
    cand = _candidates_for_sid(cfg, sid)
    errs = _errors_for_sid(cfg, sid) if Path(cfg.db).is_file() else []
    err_groups = _err_groups(errs)
    cards = _card_matches(cfg, sid, file_path,
                          [r["new_text"] or "" for r in rows])
    n_ver = len(versions)
    anchors = [{"kind": "产物", "ref": "(sid, seq=%d)" % v["seq"],
                "note": "台账行；看全文 `python -m harvester artifacts show "
                        "--sid %s`" % sid} for v in versions[:version_cap]]
    for c in cand["band"][:turn_cap]:
        anchors.append({"kind": "打磨候选", "ref": "messages.rowid=%d / %d"
                        % (c["rowid_a"], c["rowid_b"]),
                        "note": "两轮问话相似度 %.3f（时间 %s）"
                                % (c["jaccard"], c["ts"] or "—")})
    for h in cand["turn_hits"][:turn_cap]:
        anchors.append({"kind": "反转候选", "ref": "messages.rowid=%d" % h["rowid"],
                        "note": "轮次开头「%s」（时间 %s）"
                                % (h["word"], h["ts"] or "—")})
    for g in err_groups:
        anchors.append({"kind": "错误步",
                        "ref": "steps.seq=%d（工具 %s）" % (g["first"]["seq"],
                                                          g["first"]["tool"]),
                        "note": "归一模式 %s ×%d" % (g["pattern"], g["n"])})
    for c in cards:
        anchors.append({"kind": "卡片", "ref": c["path"],
                        "note": c["why"]})
    base.update({
        "configured": True,
        "file_path": file_path,
        "auto_picked": auto,
        "files": files,
        "target": {"n_versions": n_ver,
                   "changed_chars": sum(v["new_len"] for v in versions),
                   "first_ts": versions[0]["ts"],
                   "last_ts": versions[-1]["ts"]},
        "versions": versions[:version_cap],
        "versions_truncated": n_ver > version_cap,
        "revisions": {
            "thresholds": cand["thresholds"],
            "band": cand["band"][:turn_cap],
            "turn_hits": cand["turn_hits"][:turn_cap],
            # 过程指标：只回答"改一版之前磨了几轮"，不回答"改得好不好"
            "process": {
                "band_pairs": len(cand["band"]),
                "turn_words": len(cand["turn_hits"]),
                "band_per_version": (round(len(cand["band"]) / n_ver, 2)
                                     if n_ver else None),
            },
        },
        "timeline": _window(versions, cand["band"][:turn_cap],
                            cand["turn_hits"][:turn_cap]),
        "errors": {"total": len(errs), "groups": err_groups},
        "cards": cards,
        "cards_matched": len(cards),
        "anchors": anchors,
        "hint": "",
    })
    base["md"] = render_md(base)
    return base


def render_md(p: dict) -> str:
    """人读版：整段复制交给 Agent（或自己看）。"""
    t = p.get("target") or {}
    rev = p.get("revisions") or {}
    pr = rev.get("process") or {}
    th = rev.get("thresholds") or {}
    lines = [
        "# 评审包：一个文件为什么被改成这样",
        "",
        "- 会话：`%s`" % p.get("sid"),
        "- 文件：`%s`%s" % (p.get("file_path"),
                          "（本次自动挑的：这个会话里改得最多的那个）"
                          if p.get("auto_picked") else ""),
        "- 版本：%s 版｜改动 %s 字｜%s → %s"
        % (t.get("n_versions", 0), t.get("changed_chars", 0),
           (t.get("first_ts") or "—")[:19], (t.get("last_ts") or "—")[:19]),
        "",
        "> 说明：下面「时间上紧挨着」只表示先后，**不是因果**；"
        "过程指标衡量改得多么反复，**不是质量分**。",
        "",
        "## 1 版本序列",
        "",
        "| 版 | 时间 | 旧→新（字符） | 新内容开头 |",
        "|---|---|---|---|",
    ]
    for v in p.get("versions") or []:
        lines.append("| #%d | %s | %d→%d | %s |"
                     % (v["seq"], (v["ts"] or "—")[:19], v["old_len"],
                        v["new_len"],
                        (v["new_preview"] or "").replace("\n", " ")[:90]))
    if p.get("versions_truncated"):
        lines.append("")
        lines.append("（版本较多，只列前 %d 版；全量见 "
                     "`python -m harvester artifacts show --sid %s`）"
                     % (len(p.get("versions") or []), p.get("sid")))
    lines += ["", "## 2 改动前后，你在磨什么", "",
              "打磨候选（相邻两轮问话相似度 %.2f–%.2f）：%d 对；"
              "反转词命中（只认轮次开头）：%d 轮；每版之前平均磨 %.2f 轮"
              % (th.get("band", 0.8), th.get("dup", 0.9),
                 pr.get("band_pairs", 0), pr.get("turn_words", 0),
                 pr.get("band_per_version") or 0.0), ""]
    tl = p.get("timeline") or []
    if tl:
        lines += ["| 时间 | 类型 | 内容 | 落在第几版之前 |", "|---|---|---|---|"]
        for x in tl:
            what = ("相似度 %.3f（rowid %d↔%d）"
                    % (x.get("jaccard", 0), x.get("rowid_a", 0),
                       x.get("rowid_b", 0))) if x["kind"] == "打磨候选" \
                else ("「%s」（rowid %d）" % (x.get("word"), x.get("rowid", 0)))
            lines.append("| %s | %s | %s | %s |"
                         % ((x.get("ts") or "—")[:19], x["kind"], what,
                            "—" if x.get("before_version") is None
                            else "#%d" % x["before_version"]))
        lines.append("")
    eg = (p.get("errors") or {}).get("groups") or []
    lines += ["", "## 3 这个会话踩过的错（按归一模式聚，同一条反复犯才算一组）", ""]
    if not eg:
        lines.append("（这个会话没有错误步——不是「改得对」，只是「没报错」）")
    else:
        lines += ["| 组 | 类别 | 归一模式 | 首条原文 | 之后有没有重试 |",
                  "|---|---|---|---|---|"]
        for g in eg:
            lines.append("| ×%d | %s | %s | %s | %s |"
                         % (g["n"], g["class"], g["pattern"],
                            (g["first"]["text"] or "").replace("\n", " ")[:110],
                            "有" if g["first"]["resolved"] else "没有（放弃）"))
    lines += ["", "## 4 相关历史卡", ""]
    if not p.get("cards"):
        lines.append("（没有卡命中：要么还没为这类问题建过卡，要么触发词没登记"
                     "——`cards new` 建卡时把 triggers 填上，下次就能命中）")
    else:
        lines += ["| 卡 | 为什么出现在这里 | 置信度 |", "|---|---|---|"]
        for c in p.get("cards") or []:
            lines.append("| %s（%s） | %s | %s |"
                         % (c["title"] or c["path"], c["path"], c["why"],
                            c["confidence"] if c["confidence"] is not None
                            else "—"))
    lines += ["", "## 5 锚点清单（复核时按这里回原文）", "",
              "| 类型 | 指向 | 备注 |", "|---|---|---|"]
    for a in p.get("anchors") or []:
        lines.append("| %s | %s | %s |" % (a["kind"], a["ref"], a["note"]))
    lines += ["", "## 6 怎么用（三步）", ""]
    for s in p.get("steps") or []:
        lines.append("- %s" % s)
    lines += ["", "## 每个数字怎么算的", "", "| 数字 | 口径 | 来源 |", "|---|---|---|"]
    defs, srcs = p.get("definitions") or {}, p.get("sources") or {}
    for k in defs:
        lines.append("| %s | %s | %s |" % (k, defs[k], srcs.get(k, "—")))
    lines += ["", "## 别把这个包读过头", ""]
    for x in p.get("honest") or []:
        lines.append("- %s" % x)
    lines.append("")
    return "\n".join(lines)


# ================================================================ 主题评审包
#
# 用户 2026-10-26 的第二问：「**一个主题千百个文件，打成个评审包能看出啥
# 东西来？**」——单文件包回答的是「这个文件为什么改成这样」，但一个主题下
# 几百个文件，逐个点开等于没有台账。
#
# 所以补一层**主题包**：一个主题（或「未归主题」）一个包，包里先按会话分成
# 几批（一次会话 = 一次工作），再只把**核心产物**（同会话里改过 ≥3 版的）
# 摊开，其余（1–2 版，包括成百上千个一次过的文件）只给计数、目录分布和
# 文件名样例。这样「一个主题千百个文件」压成「几批 + 十几个真在磨的文件」。

#: 主题包最多摊开几个核心产物（其余只计数）
CORE_CAP = 10
#: 主题包最多用几个核心产物的正文做卡片弱命中池（多了没意义，只烧时间）
TEXT_POOL = 12


def _topic_pick(cfg, topic_id: str) -> tuple[str, str, list[str]]:
    """`topic_id` → (id, 显示名, 成员会话)。`unfiled` 表示「未归主题的那批」。"""
    from . import artledger as _al
    tid = (topic_id or "").strip()
    tlist = _al._topic_list(cfg.topics_meta)
    if tid in ("", "unfiled", "_unfiled"):
        meta = Path(cfg.artifacts_meta) if cfg.artifacts_meta else None
        if meta is None or not meta.is_file():
            return "unfiled", "（未归主题）", []
        tidx = _al._topic_index(cfg.topics_meta)
        filed = {sid for sid, ts in tidx.items() if ts}
        sids = sorted({p["sid"] for p in _al._pairs(meta)
                       if p["sid"] not in filed})
        return "unfiled", "（未归主题）", sids
    by_id = {t["id"]: t for t in tlist}
    t = by_id.get(tid)
    if t is None:   # 也接受名字（人可能手填）
        t = next((x for x in tlist if x["name"] == tid), None)
    if t is None:
        raise ValueError("没有这个主题：%s（从产物台账点一行进来，或填主题 id）"
                         % topic_id)
    return t["id"], t["name"], list(t["sids"])


def _errors_multi(cfg, sids: set[str]) -> dict[str, list[dict]]:
    """一次全库 collect，再按会话分组（逐会话各 collect 一遍是同一份数据算 N 次）。"""
    from harvester.errstats import collect_errors_from_db
    con = _ro(cfg.db)
    try:
        errs, _meta = collect_errors_from_db(Path(cfg.db), con=con)
    finally:
        con.close()
    out: dict[str, list[dict]] = {}
    for e in errs:
        if e["sid"] in sids:
            out.setdefault(e["sid"], []).append(e)
    return out


def _candidates_multi(cfg, sids: set[str]) -> dict:
    """打磨/反转候选（冻结实现，一次 collect，再按会话集合过滤）。"""
    from .analysis import revisions
    con = _ro(cfg.db)
    try:
        rev = revisions.collect(con)
        band = [p for p in rev["band"] if p["sid"] in sids]
        hits = [h for h in rev["turn_hits"] if h["sid"] in sids]
        need = {p["rowid_a"] for p in band} | {p["rowid_b"] for p in band}
        need |= {h["rowid"] for h in hits}
        tsmap = _ts_of(con, need)
    finally:
        con.close()
    for p in band:
        p["ts"] = tsmap.get(p["rowid_b"]) or tsmap.get(p["rowid_a"]) or ""
    for h in hits:
        h["ts"] = tsmap.get(h["rowid"]) or ""
    return {"account": rev["account"], "thresholds": rev["thresholds"],
            "band": band, "turn_hits": hits}


def _last_texts(meta: Path, keys: set[tuple[str, str]]) -> dict[tuple, str]:
    """每个 (sid, 文件) 的**最后一版**正文（只给弱命中当比对池，不展示）。"""
    if not keys:
        return {}
    con = _ro(meta)
    try:
        rows = list(con.execute(
            "SELECT a.sid AS sid, COALESCE(a.file_path,'（未知文件）') AS fp, "
            "       a.new_text AS t FROM artifacts a JOIN ("
            "  SELECT sid, COALESCE(file_path,'（未知文件）') AS fp, MAX(seq) AS m "
            "  FROM artifacts GROUP BY 1, 2) b "
            "  ON a.sid=b.sid AND COALESCE(a.file_path,'（未知文件）')=b.fp "
            " AND a.seq=b.m"))
    finally:
        con.close()
    out = {}
    for r in rows:
        k = (r["sid"], r["fp"])
        if k in keys:
            out[k] = r["t"] or ""
    return out


def _cards_multi(cfg, sids: set[str],
                 pool_texts: list[str]) -> list[dict]:
    """卡片命中（跨会话版）：强 = 锚点指向本主题任一成员会话；弱 = triggers 命中。"""
    from cardlib import scan_cards
    root = Path(cfg.cards_root) if cfg.cards_root else None
    if root is None or not root.is_dir():
        return []
    pool_low = " ".join(pool_texts).lower()
    out = []
    for c in scan_cards(root):
        trig = c.get("triggers") if isinstance(c.get("triggers"), list) else []
        hit = []
        for t in trig:
            w = t if isinstance(t, str) else str((t or {}).get("raw") or "")
            w = w.strip()
            if len(w) >= 2 and w.lower() in pool_low:
                hit.append(w)
        anchors = c.get("anchors") if isinstance(c.get("anchors"), list) else []
        mine = [a for a in anchors if isinstance(a, dict)
                and any(_same_session(a.get("sid"), s) for s in sids)]
        if mine:
            why = ("强命中：这张卡的锚点指向本主题的 %d 个会话（%s）"
                   % (len(mine), "、".join(str(a.get("sid") or "")[-24:]
                                         for a in mine[:2])))
        elif hit:
            why = ("弱命中：triggers 里 " + "、".join("「%s」" % w
                                                    for w in hit[:4])
                   + " 出现在核心产物的路径或最后一版内容里")
        else:
            continue
        out.append({"path": c.get("path") or "", "title": c.get("title") or "",
                    "type": c.get("type") or "", "confidence": c.get("confidence"),
                    "why": why, "strong": bool(mine), "anchors": anchors[:3]})
    out.sort(key=lambda c: (not c["strong"], -(c["confidence"] or 0)))
    return out[:CARD_CAP]


TOPIC_DEFINITIONS = {
    "sessions": "本主题里在台账有写记录的会话数（主题成员 ∩ 台账）",
    "files": "本主题的产物文件路径数（不做路径归一）",
    "versions": "写入记录条数（每次写/改算一版）",
    "core_pairs": "核心产物条数：同一会话里同一文件改过 ≥3 版",
    "bulk_files": "其余文件数（1–2 版）：本包**只计数不展开**的那批",
    "batches": "把本主题的产物按会话分成的批数（一次会话 = 一次工作）",
    "core_files": "本包实际摊开的核心产物条数（最多 %d 条）" % CORE_CAP,
}

TOPIC_SOURCES = {
    "sessions": "artifacts_meta.db · artifacts.sid ∩ topics_meta.members",
    "files": "artifacts_meta.db · artifacts.file_path",
    "versions": "artifacts_meta.db · artifacts 行数",
    "core_pairs": "现算：按 (sid, file_path) 分组数行数 ≥3",
    "bulk_files": "现算：pairs − core_pairs 的去重文件数",
    "batches": "现算：按 sid 分组",
    "core_files": "现算：核心产物按版数降序取前 %d 条" % CORE_CAP,
}

TOPIC_HONEST = [
    "**这个包只摊开「核心产物」。**一个主题几百个文件里，绝大多数只被写过"
    "一次（批量生成、搬目录、改配置）——把它们逐个摊开，版本序列只有一行，"
    "看不出任何东西。所以本包只展开改过 ≥3 版的那些，其余只给个数与目录分布。",
    "**同一个文件可能在两个会话里各算一条。**`handoff/会话交接.md` 这类文件"
    "在两次工作里都改过，就是两条——那是两次不同的工作，不合并成一条。",
    "**分批不是质量分级。**一次过不等于做得差；「改过 10 版」也不等于改得好，"
    "只说明来回磨得多。",
    "**时间上紧挨着不是因果。**包里的「落在第几版之前」只表示先后顺序。",
    "**主题没产物 ≠ 主题没工作。**有些主题的会话是豆包/元宝/千问来的，"
    "源里只有截断预览，提不出完整写入内容——台账根本看不到它们。",
]


def build_topic(cfg, *, topic_id: str = "", file_cap: int = CORE_CAP) -> dict:
    """主题评审包数据面（A 档只读）。`topic_id` 空 = 「未归主题」那一批。"""
    from . import artledger as _al
    tid, name, sids = _topic_pick(cfg, topic_id)
    meta = Path(cfg.artifacts_meta) if cfg.artifacts_meta else None
    base = {"view_actions_api": 1, "schema": SCHEMA, "kind": "topic",
            "topic": {"id": tid, "name": name, "members": len(sids)},
            "definitions": TOPIC_DEFINITIONS, "sources": TOPIC_SOURCES,
            "honest": list(TOPIC_HONEST), "steps": list(STEPS),
            "batches_label": _al.BATCH_LABEL}
    if meta is None or not meta.is_file():
        base.update({"configured": False, "batches": [], "core_files": [],
                     "bulk": {"files": 0, "versions": 0, "top_dirs": [],
                              "sample": []},
                     "errors": {"total": 0, "groups": []}, "cards": [],
                     "revisions": {"band": [], "turn_hits": []},
                     "anchors": [], "md": "",
                     "hint": "产物台账没接上（起服没给 --artifacts-meta）。"
                             "先在终端跑 `python -m harvester artifacts extract "
                             "--sid <会话 id>` 登记，再起服带上 --artifacts-meta。"})
        return base
    sid_set = set(sids)
    pairs = [p for p in _al._pairs(meta)
             if p["sid"] in sid_set] if sid_set else []
    if not pairs:
        base.update({"configured": True, "batches": [], "core_files": [],
                     "bulk": {"files": 0, "versions": 0, "top_dirs": [],
                              "sample": []},
                     "errors": {"total": 0, "groups": []}, "cards": [],
                     "revisions": {"band": [], "turn_hits": []},
                     "anchors": [], "md": "",
                     "hint": "这个主题在台账里一条产物都没有——"
                             "成员会话没接入产物提取，或确实没写过文件。"})
        return base

    agg = _al._agg(pairs)
    # 用**按文件合并**后的核心产物：同一文件在两个会话里各改一遍时只算一条
    # （实测 `会话交接-2026-10-04.md` 在两个会话里各 63 版），否则表里同一行
    # 出现两遍，看着像 bug。
    core_all = agg["core_files"]
    core = core_all[:file_cap]
    # 「其余」= **核心文件之外**的全部 pair。按文件路径互斥（不是按 pair 的
    # 版数）：否则稿.md 在 A 会话 3 版、B 会话 1 版时，B 那条既算进核心文件
    # 的 4 版里、又被算进「其余」一次——同一版记两处。
    core_paths = {c["file_path"] for c in core_all}
    bulk_pairs = [p for p in pairs if p["file_path"] not in core_paths]
    bulk_files = {p["file_path"] for p in bulk_pairs}

    errs_by_sid = _errors_multi(cfg, sid_set) if Path(cfg.db).is_file() else {}
    cand = (_candidates_multi(cfg, sid_set) if Path(cfg.db).is_file()
            else {"band": [], "turn_hits": [], "account": {}, "thresholds": {}})

    by_sid: dict[str, list[dict]] = {}
    for p in pairs:
        by_sid.setdefault(p["sid"], []).append(p)
    batches = []
    for sid, sub in sorted(by_sid.items(), key=lambda kv: -len(kv[1])):
        a = _al._agg(sub)
        errs = errs_by_sid.get(sid) or []
        batches.append({
            "sid": sid, "short": _al._sid_tail(sid),
            "files": a["files"], "versions": a["versions"],
            "core": len(a["core"]), "once": a["buckets"]["once"]["pairs"],
            "changed_chars": a["changed_chars"],
            "first_ts": a["first_ts"], "last_ts": a["last_ts"],
            "errors": {"total": len(errs), "groups": _err_groups(errs)[:5]},
            "band_pairs": len([b for b in cand["band"] if b["sid"] == sid]),
            "turn_words": len([h for h in cand["turn_hits"]
                               if h["sid"] == sid]),
        })

    keys = {(sid, c["file_path"]) for c in core[:TEXT_POOL]
            for sid in c["sessions"]}
    texts = _last_texts(meta, keys)
    pool = [c["file_path"] for c in core] + [
        _cut(t, 2000) for t in texts.values()]
    cards = _cards_multi(cfg, sid_set, pool) if cfg.cards_root else []

    all_errs = [e for v in errs_by_sid.values() for e in v]
    err_groups = _err_groups(all_errs)
    anchors = []
    for c in core[:file_cap]:
        anchors.append({"kind": "产物",
                        "ref": "%s（%d 个会话）"
                               % (c["file_path"], len(c["sessions"])),
                        "note": "%d 版，改动 %d 字，末次 %s"
                                % (c["n_versions"], c["changed_chars"],
                                   (c["last_ts"] or "—")[:19])})
    for g in err_groups[:6]:
        anchors.append({"kind": "错误步",
                        "ref": "steps.seq=%d（%s）" % (g["first"]["seq"],
                                                      g["first"]["tool"]),
                        "note": "归一模式 %s ×%d" % (g["pattern"], g["n"])})
    for b in cand["band"][:TURN_CAP]:
        anchors.append({"kind": "打磨候选", "ref": "messages.rowid=%d / %d"
                        % (b["rowid_a"], b["rowid_b"]),
                        "note": "相似度 %.3f（%s）" % (b["jaccard"],
                                                     (b["ts"] or "—")[:19])})
    for h in cand["turn_hits"][:TURN_CAP]:
        anchors.append({"kind": "反转候选", "ref": "messages.rowid=%d" % h["rowid"],
                        "note": "轮次开头「%s」（%s）" % (h["word"],
                                                        (h["ts"] or "—")[:19])})
    for c in cards:
        anchors.append({"kind": "卡片", "ref": c["path"], "note": c["why"]})

    base.update({
        "configured": True,
        "coverage": (lambda: __import__("harvester.artifacts", fromlist=["x"])
                     .coverage(cfg.db, cfg.artifacts_meta))(),
        "target": {"sessions": agg["sessions"], "files": agg["files"],
                   "pairs": agg["pairs"], "versions": agg["versions"],
                   "changed_chars": agg["changed_chars"],
                   "core_pairs": len(core_all),
                   "bulk_files": len(bulk_files),
                   "batches": len(batches)},
        "buckets": agg["buckets"],
        "core_files": [{"file_path": c["file_path"],
                        "file_short": c["file_short"],
                        "n_versions": c["n_versions"],
                        "changed_chars": c["changed_chars"],
                        "n_sessions": len(c["sessions"]),
                        "short": "、".join(_al._sid_tail(x) for x in
                                           c["sessions"][:3]),
                        "first_ts": c["first_ts"], "last_ts": c["last_ts"]}
                       for c in core],
        "core_total": len(core_all),
        "bulk": {"files": len(bulk_files),
                 "pairs": len(bulk_pairs),
                 "versions": sum(p["n_versions"] for p in bulk_pairs),
                 "top_dirs": agg["top_dirs"],
                 "sample": sorted(bulk_files)[:12]},
        "batches": batches,
        "errors": {"total": len(all_errs), "groups": err_groups},
        "revisions": {"thresholds": cand.get("thresholds") or {},
                      "band": cand["band"][:TURN_CAP],
                      "turn_hits": cand["turn_hits"][:TURN_CAP]},
        "cards": cards,
        "anchors": anchors,
        "hint": "",
    })
    base["md"] = render_topic_md(base)
    return base


def render_topic_md(p: dict) -> str:
    """主题包人读版：整段复制交给任何 Agent（或自己读）。"""
    t = p.get("target") or {}
    tp = p.get("topic") or {}
    bulk = p.get("bulk") or {}
    lines = [
        "# 评审包（主题）：%s" % tp.get("name"),
        "",
        "- 主题 id：`%s`｜成员会话 %s 个（其中 %s 个在台账有写入记录）"
        % (tp.get("id"), tp.get("members"), t.get("sessions", 0)),
        "- 产物：%s 个文件 / %s 版 / 改动 %s 字"
        % (t.get("files", 0), t.get("versions", 0), t.get("changed_chars", 0)),
        "- 分 %s 批（一次会话 = 一次工作）｜核心产物 %s 条（改过 ≥3 版）｜"
        "其余 %s 个文件只计数" % (t.get("batches", 0), t.get("core_pairs", 0),
                                t.get("bulk_files", 0)),
        "",
        "> 为什么只摊开核心产物：这个主题几百个文件里绝大多数只被写过一次"
        "（批量生成/搬目录/改配置），摊开也只是一行版本序列。核心 = 反复磨的"
        "那些，才是「为什么改成这样」有得看的部分。",
        "",
        "## 0 分批概览",
        "",
        "| 会话 | 文件 | 版数 | 核心 | 一次过 | 错误步 | 打磨候选 | 首→末 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for b in p.get("batches") or []:
        lines.append("| %s | %d | %d | %d | %d | %d | %d | %s → %s |"
                     % (b["short"], b["files"], b["versions"], b["core"],
                        b["once"], b["errors"]["total"], b["band_pairs"],
                        (b["first_ts"] or "—")[:10],
                        (b["last_ts"] or "—")[:10]))
    lines += ["", "## 1 核心产物（按版数；同一文件在几个会话里改过算一条）", "",
              "| 版数 | 文件 | 改动字数 | 会话 | 首→末 |",
              "|---|---|---|---|---|"]
    for c in p.get("core_files") or []:
        lines.append("| %d | %s | %d | %d 个（%s） | %s → %s |"
                     % (c["n_versions"], c["file_short"], c["changed_chars"],
                        c.get("n_sessions", 1), c.get("short", ""),
                        (c["first_ts"] or "—")[:10],
                        (c["last_ts"] or "—")[:10]))
    if p.get("core_total", 0) > len(p.get("core_files") or []):
        lines.append("")
        lines.append("（核心产物共 %s 条，本包只摊开前 %d 条；其余同样算法）"
                     % (p.get("core_total"), len(p.get("core_files") or [])))
    lines += ["", "## 2 其余（1–2 版，只计数不展开）", "",
              "- %s 个文件 / %s 版（占本主题版数的大头，通常是一次过的批量改动）"
              % (bulk.get("files", 0), bulk.get("versions", 0))]
    if bulk.get("top_dirs"):
        lines.append("- 主目录：" + "、".join(
            "%s（%d 版）" % (d["dir"], d["versions"])
            for d in bulk["top_dirs"][:5]))
    if bulk.get("sample"):
        lines.append("- 样例：" + "、".join("`%s`" % x for x in bulk["sample"][:8]))
    eg = (p.get("errors") or {}).get("groups") or []
    lines += ["", "## 3 这个主题踩过的错（跨会话按归一模式聚）", ""]
    if not eg:
        lines.append("（本主题成员会话没有错误步——不是「做得对」，只是「没报错」）")
    else:
        lines += ["| 组 | 类别 | 归一模式 | 首条原文 |", "|---|---|---|---|"]
        for g in eg:
            lines.append("| ×%d | %s | %s | %s |"
                         % (g["n"], g["class"], g["pattern"],
                            (g["first"]["text"] or "").replace("\n", " ")[:110]))
    lines += ["", "## 4 相关历史卡", ""]
    if not p.get("cards"):
        lines.append("（没有卡命中。triggers 是人工登记的现象词——"
                     "`cards new` 建卡时把 triggers 填上，下次才可能命中）")
    else:
        lines += ["| 卡 | 为什么出现在这里 |", "|---|---|"]
        for c in p["cards"]:
            lines.append("| %s | %s |" % (c["title"] or c["path"], c["why"]))
    lines += ["", "## 5 锚点清单", "", "| 类型 | 指向 | 备注 |", "|---|---|---|"]
    for a in p.get("anchors") or []:
        lines.append("| %s | %s | %s |" % (a["kind"], a["ref"], a["note"]))
    lines += ["", "## 6 怎么用（三步）", ""]
    for s in p.get("steps") or []:
        lines.append("- %s" % s)
    lines += ["", "## 每个数字怎么算的", "", "| 数字 | 口径 | 来源 |", "|---|---|---|"]
    defs, srcs = p.get("definitions") or {}, p.get("sources") or {}
    for k in defs:
        lines.append("| %s | %s | %s |" % (k, defs[k], srcs.get(k, "—")))
    lines += ["", "## 别把这个包读过头", ""]
    for x in p.get("honest") or []:
        lines.append("- %s" % x)
    lines.append("")
    return "\n".join(lines)
