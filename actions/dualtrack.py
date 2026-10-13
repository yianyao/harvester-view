# -*- coding: utf-8 -*-
"""P4-4 dualtrack.py：主题工作台**双轨时间线**数据面。

上轨 = 讨论流（上游 `topics.timeline`，coarse/mid 两档，由 actionsserve 的
`topic_timeline` 透出并传入）；下轨 = 产物流（artifacts_meta.db 的
Write/Edit 版本序列）；中间挂**打磨带/反转点候选**（消费 P4-3）。

TODO P4-4 验收（逐字，判据即规格）：
「任选有 artifacts 的主题上/下轨同屏对齐；打磨带与 P4-3 候选一致；
  反转点标记可点击看两轮原文对比」

口径与红线（勿擅改）：
- **与 P4-3 候选一致是构造性的**：调 `revisions.collect`（同一实现、同一
  口径）全库跑一遍，再按主题成员 sid 过滤——不重算、不复刻阈值；
- 两轮原文对比数据随候选**自带**：band 带前/后轮预览、turn_hits 带
  cur + prev（同会话内上一个 user 轮），点击即看、不二次请求；
- 只读 `raw`（text 是 FTS bigram 分词列，交接 §5.9）；锚点 =
  messages.rowid（messages 无 seq 列）；
- 只读 harvester.db / artifacts_meta.db（open_ro：mode=ro + authorizer）；
  本模块**零写库**；session-harvester 零改动（铁律 #2）；
- level 只认 coarse/mid：fine/artifact 是按需档（要 sid），不在双轨范围。
"""
from __future__ import annotations

from pathlib import Path

from .analysis import revisions

#: 双轨支持的 timeline 档（title 档与 chain 视图重复，也不收）
LEVELS = ("coarse", "mid")
#: 两轮原文对比的单侧预览上限（字符）
PREVIEW_CAP = 800
#: 产物版本 old/new 预览的单侧上限（字符）
ART_PREVIEW = 300


def _cut(s: str | None, n: int) -> str:
    return (s or "")[:n]


def _fetch_turns(con, rowids: set[int]) -> dict[int, dict]:
    """批量取 rowid → {ts, preview}（读 raw）。FTS5 虚表按 rowid 取合法。"""
    out: dict[int, dict] = {}
    if not rowids:
        return out
    q = ("SELECT rowid, ts, COALESCE(NULLIF(raw,''), text) AS body "
         "FROM messages WHERE rowid IN (%s)"
         % ",".join("?" * len(rowids)))
    for r in con.execute(q, tuple(rowids)):
        out[r["rowid"]] = {"rowid": r["rowid"], "ts": r["ts"] or "",
                           "preview": _cut(r["body"], PREVIEW_CAP)}
    return out


def _prev_user_rowid(con, sid: str, rowid: int) -> int | None:
    """同会话内、rowid 之前最近的一个 user 轮（反转点对比的"前轮"）。"""
    r = con.execute(
        "SELECT rowid FROM messages WHERE sid=? AND role='user' AND rowid<? "
        "ORDER BY rowid DESC LIMIT 1", (sid, rowid)).fetchone()
    return r["rowid"] if r else None


def _revisions_track(cfg, member_sids: list[str]) -> dict:
    """P4-3 候选 → 主题内候选 + 自带两轮原文对比。"""
    from .actionsserve import open_ro
    con = open_ro(cfg.db)
    try:
        rev = revisions.collect(con)
        mset = set(member_sids)
        band = [p for p in rev["band"] if p["sid"] in mset]
        hits = [h for h in rev["turn_hits"] if h["sid"] in mset]
        need: set[int] = set()
        for p in band:
            need.add(p["rowid_a"])
            need.add(p["rowid_b"])
        for h in hits:
            need.add(h["rowid"])
        texts = _fetch_turns(con, need)
        prev_map: dict[int, int | None] = {}
        for h in hits:
            if h["rowid"] not in texts:
                prev_map[h["rowid"]] = None
                continue
            prid = _prev_user_rowid(con, h["sid"], h["rowid"])
            prev_map[h["rowid"]] = prid
            if prid is not None and prid not in texts:
                texts.update(_fetch_turns(con, {prid}))
    finally:
        con.close()
    band_out = []
    for p in band:
        band_out.append({
            "sid": p["sid"], "rowid_a": p["rowid_a"], "rowid_b": p["rowid_b"],
            "jaccard": p["jaccard"],
            "a": texts.get(p["rowid_a"]),
            "b": texts.get(p["rowid_b"])})
    hits_out = []
    for h in hits:
        prid = prev_map.get(h["rowid"])
        hits_out.append({
            "sid": h["sid"], "rowid": h["rowid"], "word": h["word"],
            "head": h["head"], "cur": texts.get(h["rowid"]),
            "prev": texts.get(prid) if prid is not None else None})
    return {"thresholds": rev["thresholds"], "band": band_out,
            "turn_hits": hits_out,
            "account_all": {"band": len(rev["band"]),
                            "turn_hits": len(rev["turn_hits"])},
            "honest": "打磨 vs 反转由人甄别（候选非结论，revisions.py 口径）；"
                      "预览超 %d 字符截断" % PREVIEW_CAP}


def _artifacts_track(cfg, member_sids: list[str]) -> dict:
    """下轨产物流：成员 sid 的 Write/Edit 版本序列，按 (sid, file_path) 分组。"""
    if not cfg.artifacts_meta or not Path(cfg.artifacts_meta).is_file():
        return {"configured": False, "files": [], "n_files": 0,
                "n_versions": 0,
                "hint": "未配置 artifacts_meta（起服加 --artifacts-meta）；"
                        "单会话产物先由人跑 "
                        "`python -m harvester artifacts extract --sid <sid>` "
                        "登记（本视图绝不代跑）"}
    from .actionsserve import open_ro
    con = open_ro(cfg.artifacts_meta)
    try:
        if not member_sids:
            rows = []
        else:
            rows = list(con.execute(
                "SELECT sid, seq, ts, tool, file_path, old_text, new_text "
                "FROM artifacts WHERE sid IN (%s) "
                "ORDER BY sid, file_path, seq" % ",".join("?" * len(member_sids)),
                tuple(member_sids)))
    finally:
        con.close()
    files: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["sid"], r["file_path"] or "（未知文件）")
        f = files.setdefault(key, {"sid": key[0], "file_path": key[1],
                                   "versions": [], "n_versions": 0})
        f["versions"].append({
            "seq": r["seq"], "ts": r["ts"] or "", "tool": r["tool"] or "",
            "old_len": len(r["old_text"] or ""), "new_len": len(r["new_text"] or ""),
            "old_preview": _cut(r["old_text"], ART_PREVIEW),
            "new_preview": _cut(r["new_text"], ART_PREVIEW)})
        f["n_versions"] += 1
    out = sorted(files.values(),
                 key=lambda f: (f["versions"][0]["ts"], f["sid"]))
    return {"configured": True, "files": out,
            "n_files": len(out),
            "n_versions": sum(f["n_versions"] for f in out),
            "honest": "版本为 Write/Edit 提取记录（artifacts.py 口径，"
                      "锚点 = (sid, seq)），非文件系统快照"}


def build(cfg, topic_id: str, level: str, timeline_dict: dict) -> dict:
    """双轨数据面。timeline_dict 由 actionsserve.topic_timeline 产出
    （已含库指纹与 view_actions_api），本函数只追加轨道，不改它的字段。"""
    from harvester.topics import show_topic
    topic = show_topic(cfg.topics_meta, topic_id)
    member_sids = [m["sid"] for m in topic["members"]]
    rev = _revisions_track(cfg, member_sids)
    art = _artifacts_track(cfg, member_sids)
    return {
        "view_actions_api": 1,
        "topic_id": topic_id,
        "level": level,
        "topic": {"id": topic["id"], "name": topic["name"]},
        "members": len(member_sids),
        "timeline": timeline_dict,
        "revisions": rev,
        "artifacts": art,
        "account": {
            "members": len(member_sids),
            "band": len(rev["band"]),
            "turn_hits": len(rev["turn_hits"]),
            # 全库口径（未按成员过滤），供"与 P4-3 候选一致"的页面对账
            "band_all": rev["account_all"]["band"],
            "turn_hits_all": rev["account_all"]["turn_hits"],
            "artifact_files": art["n_files"],
            "artifact_versions": art["n_versions"],
        },
    }
