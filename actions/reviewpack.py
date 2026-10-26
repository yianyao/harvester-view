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

SCHEMA = "review_pack/1"

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
    base = {"view_actions_api": 1, "schema": SCHEMA, "sid": sid,
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
