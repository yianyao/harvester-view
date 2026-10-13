# -*- coding: utf-8 -*-
"""P4-3 revisions.py：打磨/反转候选（**层二：产出推敲琢磨**）。

语义定位（用户 2026-10-13 裁决，逐字）："反复叮嘱……其二是对产出品
（作品、设计）的**反复打磨**，这明显是为了能得到更好成品，所谓的推敲琢磨"。
本模块挖的就是这一层：相邻 user 轮次的 bigram Jaccard **边界带**（0.80–0.90，
H40 冻结口径）+ **只认轮次开头**的转折词 → 打磨/反转候选带锚点。

口径（探针 v2 收窄判据 + 探针教训，勿擅改）：
- 边界带直接 import `harvester.topicprep` 的 `_grams/_jaccard` 与
  `DUP_THRESHOLD/BAND_THRESHOLD`（H40 冻结口径，本仓不复刻数字）；
- **只剥注入、允许粘贴**——打磨带的主信号恰是长稿子反复贴（探针 v1 把
  粘贴过滤错套到本通道，87 对缩成 32 对且浓度被摊平）；
- 转折词只认轮次开头（`_norm` 后前缀匹配）——转折词在正文里误命中是
  探针 v1 的另一个坑（273 → 9 轮）；
- 只读 harvester.db（mode=ro）、只读 `raw` 列；锚点 = messages.rowid。

用法：
    python -m actions.analysis.revisions --db <harvester.db> [--out <md>]
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

# ---- 口径常量（与探针 v2 逐值一致）----
TURN_WORDS = ("不对", "算了", "推翻", "作废", "改成", "换成", "重来", "不是这个",
              "搞错了", "推翻重来")
MAX_LEN = 1500        # 比对时只取前 N 字符（长文本 bigram 集合会爆）
MIN_LEN = 12          # 太短的轮次不参与相邻比对
HEAD_LEN = 12         # 转折词只看 _norm 后的前 N 字符
PREVIEW = 70

from actions.analysis.nags import _norm, strip_injection  # noqa: E402


def _topicprep():
    from actions import ensure_harvester_importable
    err = ensure_harvester_importable()
    if err:
        raise SystemExit("[revisions] 上游 session-harvester 不可用：%s" % err)
    from harvester.topicprep import (BAND_THRESHOLD, DUP_THRESHOLD,  # noqa: PLC0415
                                     _grams, _jaccard)
    return BAND_THRESHOLD, DUP_THRESHOLD, _grams, _jaccard


def collect(con: sqlite3.Connection) -> dict:
    """对只读连接跑收窄口径，返回 account + band + turn_hits。

    连接若未设 Row 工厂会就地补上（同 nags.collect）。
    """
    if con.row_factory is not sqlite3.Row:
        con.row_factory = sqlite3.Row
    band_lo, dup_hi, grams, jaccard = _topicprep()
    rows = list(con.execute(
        "SELECT sid, rowid AS rid, role, "
        "COALESCE(NULLIF(raw,''), text) AS body FROM messages "
        "WHERE role='user' ORDER BY sid, rowid"))

    n_inject = n_short = 0
    lane: dict[str, list] = defaultdict(list)   # P4-3 通道：允许粘贴
    for r in rows:
        body, has_inj = strip_injection(r["body"] or "")
        if has_inj:
            n_inject += 1
        if len(_norm(body)) < 10:               # MIN_KEEP：纯注入剥完没剩
            n_short += 1
            continue
        lane[r["sid"]].append((r["rid"], body))

    band, turn_hits = [], []
    n_pairs = n_dup = 0
    for sid in sorted(lane):
        prev = None
        for rid, body in lane[sid]:
            text = body[:MAX_LEN]
            if len(text) < MIN_LEN:
                prev = None
                continue
            head = _norm(text)[:HEAD_LEN]
            w = next((tw for tw in TURN_WORDS if head.startswith(tw)), None)
            if w:
                turn_hits.append({"sid": sid, "rowid": rid, "word": w,
                                  "head": _norm(text)[:PREVIEW]})
            if prev is not None:
                n_pairs += 1
                j = jaccard(grams(prev[1]), grams(text))
                if j >= dup_hi:
                    n_dup += 1
                elif j >= band_lo:
                    band.append({"sid": sid, "rowid_a": prev[0],
                                 "rowid_b": rid, "jaccard": round(j, 3)})
            prev = (rid, text)
    band.sort(key=lambda p: -p["jaccard"])
    return {"account": {
                "user_turns": len(rows), "n_inject": n_inject,
                "n_short": n_short, "n_lane": sum(len(v) for v in lane.values()),
                "n_paste_gate": 0,          # 恒 0：本通道无粘贴闸（口径声明位）
                "n_pairs": n_pairs, "n_dup": n_dup, "n_band": len(band),
                "n_turn_words": len(turn_hits)},
            "band": band, "turn_hits": turn_hits,
            "thresholds": {"band": band_lo, "dup": dup_hi}}


def render_markdown(res: dict) -> str:
    a = res["account"]
    th = res["thresholds"]
    band_by_sid: dict[str, int] = defaultdict(int)
    for p in res["band"]:
        band_by_sid[p["sid"]] += 1
    top_sid, top_n = (max(band_by_sid.items(), key=lambda kv: kv[1])
                      if band_by_sid else ("—", 0))
    lines = [
        "# P4-3 打磨/反转候选（revisions · 层二：产出推敲琢磨）",
        "",
        "生成：%s" % datetime.datetime.now().isoformat(timespec="seconds"),
        "",
        "语义定位（用户裁决）：反复叮嘱之**其二**——对产出品（作品、设计）"
        "的反复打磨，为了得到更好成品，所谓推敲琢磨。",
        "",
        "口径：相邻 user 轮次 bigram Jaccard 边界带 **%.2f–%.2f**（H40 冻结）；"
        "只剥注入、**允许粘贴**（长稿子反复贴正是主信号）；转折词只认轮次开头。"
        % (th["band"], th["dup"]),
        "",
        "## 账目",
        "",
        "| 项 | 数量 |", "|---|---|",
        "| user 轮次总数（读 raw） | %d |" % a["user_turns"],
        "| 含注入（剥块后继续） | %d |" % a["n_inject"],
        "| 剥后过短丢弃 | %d |" % a["n_short"],
        "| 参与相邻比对轮次 | %d |" % a["n_lane"],
        "| 相邻对 | %d |" % a["n_pairs"],
        "| 判重复（≥%.2f，非打磨） | %d |" % (th["dup"], a["n_dup"]),
        "| **边界带（打磨候选）** | **%d 对 / %d 会话** |"
        % (a["n_band"], len(band_by_sid)),
        "| 转折词命中（轮次开头） | %d |" % a["n_turn_words"],
        "",
        "边界带 Top1 会话：`%s`（%d 对）。判据原文：接受\"只覆盖少数会话\"。",
        "",
        "## 边界带样例（Jaccard 降序，前 20）",
        "",
        "| Jaccard | 会话 | rowid 对 |", "|---|---|---|",
    ]
    for p in res["band"][:20]:
        lines.append("| %.3f | %s | #%s → #%s |"
                     % (p["jaccard"], p["sid"][:36], p["rowid_a"], p["rowid_b"]))
    lines += ["", "## 转折词样例（前 10）", "",
              "| 词 | 会话 | rowid | 开头 |", "|---|---|---|---|"]
    for h in res["turn_hits"][:10]:
        lines.append("| %s | %s | #%s | %s |"
                     % (h["word"], h["sid"][:28], h["rowid"], h["head"]))
    lines += ["", "---", "",
              "打磨 vs 反转由人甄别（P4-4 双轨时间线消费本清单）；"
              "模块不替人下结论。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m actions.analysis.revisions",
        description="P4-3 打磨/反转候选（只读 harvester.db）")
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
        print("[revisions] 候选清单已写：%s（边界带 %d 对 / 转折词 %d 轮）"
              % (args.out, res["account"]["n_band"],
                 res["account"]["n_turn_words"]))
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
