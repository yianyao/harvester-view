# -*- coding: utf-8 -*-
"""skill 治理档案聚合（`skill_matrix` 动作，v0.81 P1-3 / 《优化方案》C1）。

用户定位②（harness 与 skill 的感知与治理）里，skill 侧的内容原本散在
四处：G4 报告（调用/成功率/行为链）、主题 chain 视图的 skilljoin 工具
栏、命令台的 `cards index-md`（触发词消歧素材）、G2 建议条目。本模块把
前三处的**确定性部分**聚成一张档案表：每个 skill 一行 = G4 画像 + 失败
+ 服务的主题 + 关联错误 top1 + 消歧状态。

设计纪律（《优化方案》C1 的修正点，原文执行）：
1. **不做"前端逐主题调 skilljoin"**（主题多时 N 次请求）——本模块在
   进程内对有 chain 的主题逐个调 `skilljoin.build_cross`（**复用上游
   同一函数，不另写一套 join 口径**），按 skill 聚合；
2. 页面必须标注「**本页聚合，非上游口径**」：G4 数字来自 behstats
   skill_summary（上游口径），「服务的主题」来自 skilljoin 交叉
   （聚合口径），两者不同源，说清楚比混着说好；
3. 诚实的空：没有 chain 的主题不猜（主题数照算，服务主题列=0 并明示
   「无 chain 的主题不计入」）；解析不出 skill 名的调用点归
   「（未识别）」并标消歧状态，绝不静默丢弃；
4. 只读：不写任何库与文件。

上游 import 走 `actions.ensure_harvester_importable()` 懒加载（与
kgraph._load_cards 同款）：本模块被 import 时不要求上游在，用到时才要求
——缺上游时抛 ImportError，由动作层的黄条降级接住，不让服务起不来。
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

UNKNOWN = "（未识别）"
#: behstats.skill_summary 把解析不出名字的调用点归到这个键（上游口径，
#: 本模块对外统一换成 UNKNOWN 这个给人看的名字）
_UPSTREAM_UNKNOWN = "?"


def _upstream():
    """按需 import 上游三个函数；返回 (collect_skill_invocations,
    skill_summary, build_cross, load_chain)。上游不在 → ImportError。"""
    from . import ensure_harvester_importable
    err = ensure_harvester_importable()
    if err:
        raise ImportError(err)
    from harvester.behstats import (collect_skill_invocations,
                                    skill_summary)
    from harvester.skilljoin import build_cross
    from harvester.topicchain import load_chain
    return collect_skill_invocations, skill_summary, build_cross, load_chain


def _topic_names(topics_meta: Path | None) -> dict[str, str]:
    """topics_meta → {topic_id: name}；缺库/坏库 = 空 dict（调用方照算，
    不因此失败——chain frontmatter 自带 topic 名可兜底）。"""
    if not topics_meta or not Path(topics_meta).is_file():
        return {}
    con = sqlite3.connect(f"file:{topics_meta}?mode=ro", uri=True)
    try:
        return {r[0]: r[1] for r in con.execute(
            "SELECT id, name FROM topics")}
    except sqlite3.DatabaseError:
        return {}
    finally:
        con.close()


def _chain_topics(chain_root: Path | None, load_chain):
    """chain_root 下的 chain-*.md → (可用清单, 跳过说明)。

    可用清单 = [(topic_id, topic 名, 路径)]；一个主题可有多条 chain
    （v0.25 additive）——按 topic_id 分组在 build_matrix 里做，这里只出
    清单。

    跳过**必须留痕**（`skipped` 带回给页面）：坏文档/缺 topic_id 如果静默
    丢掉，页面上的「服务主题数」就成了假的低——人看不出是"这 skill 没服务
    主题"还是"有 chain 没读进来"。
    """
    root = Path(chain_root) if chain_root else None
    if not root or not root.is_dir():
        return [], []
    out: list[tuple[str, str, Path]] = []
    skipped: list[str] = []
    for p in sorted(root.glob("chain-*.md")):
        try:
            d = load_chain(p)
        except (ValueError, RuntimeError, OSError) as e:
            skipped.append(f"{p.name}：解析失败（{e}）")
            continue
        fm = d.get("fm")
        if not isinstance(fm, dict):
            skipped.append(f"{p.name}：frontmatter 不是映射")
            continue
        tid = str(fm.get("topic_id") or "")
        if not tid:
            skipped.append(f"{p.name}：frontmatter 缺 topic_id"
                           "（无法归属主题）")
            continue
        out.append((tid, str(fm.get("topic") or tid), p))
    return out, skipped


def build_matrix(db: Path, topics_meta: Path | None = None,
                 chain_root: Path | None = None) -> dict:
    """聚合 skill 治理档案。只读，不写任何库与文件。"""
    db = Path(db)
    (collect_skill_invocations, skill_summary,
     build_cross, load_chain) = _upstream()
    invs = collect_skill_invocations(db)
    summary = skill_summary(invs)

    # ① 失败画像：每个 skill 的失败调用错误文本 top1（G4 只数失败次数，
    #    这里补"败在什么上"——档案表要能回答"这 skill 最近在败什么"）。
    err_text: dict[str, Counter] = defaultdict(Counter)
    for i in invs:
        if i["status"] not in ("success", "completed", "ok", "no_result") \
                and (i.get("error") or "").strip():
            err_text[i["skill"] or UNKNOWN][i["error"].strip()[:120]] += 1

    # ② 服务的主题：有 chain 的主题逐个 skilljoin（复用上游同一函数），
    #    按 skill 聚合主题集合。无 chain 的主题不计入——明示，不猜。
    names = _topic_names(topics_meta)
    chains, skipped = _chain_topics(chain_root, load_chain)
    topics_by_skill: dict[str, set[str]] = defaultdict(set)
    chain_errors: list[str] = list(skipped)
    for tid, tname, path in chains:
        try:
            cross = build_cross(path, db)
        except (ValueError, RuntimeError, OSError) as e:
            chain_errors.append(f"{path.name}: {e}")
            continue
        label = names.get(tid) or tname or tid
        rows = [r for st in cross.get("stages", [])
                for r in st.get("rows", [])] + cross.get("unassigned", [])
        for r in rows:
            topics_by_skill[r["skill"] or UNKNOWN].add(label)

    skills = []
    for name, d in summary.items():
        # 上游把解析不出名字的调用点归到 "?"，对外统一换成给人看的 UNKNOWN
        key = UNKNOWN if name in ("", _UPSTREAM_UNKNOWN, None) else name
        calls = d["calls"]
        failed = d["err"]
        etop = err_text.get(key, Counter()).most_common(1)
        served = sorted(topics_by_skill.get(key, set()))
        # 消歧状态（诚实的两种，不发明第三类）：
        # ① 名解析不出 → 需人工定名；② 跨 harness 出现 → 两套口径可能
        #    同名不同物（如 dsh 的 edit 与 workbuddy 的 Edit 是两套工具
        #    名词表，skill 层同理）。
        flags: list[str] = []
        if key == UNKNOWN:
            flags.append("名解析不出（需人工定名）")
        if len(d["sources"]) > 1:
            flags.append("跨 " + str(len(d["sources"])) + " 个 harness"
                         "（同名可能不同物）")
        skills.append({
            "skill": key,
            "calls": calls,
            "sids": d["sids"],
            "sources": d["sources"],
            "ok": d["ok"], "err": failed, "no_result": d["no_result"],
            "fail_rate": round(failed / calls, 4) if calls else None,
            "topics_served": served,
            "topics_count": len(served),
            "top_error": ({"text": etop[0][0], "count": etop[0][1]}
                          if etop else None),
            "args_samples": d["args_samples"],
            "chains": d["chains"],
            "disambig": flags,
        })
    skills.sort(key=lambda s: (-s["calls"], s["skill"]))
    n_unknown = sum(1 for s in skills if s["skill"] == UNKNOWN)
    return {
        "skills": skills,
        "totals": {
            "skills": len(skills),
            "calls": sum(s["calls"] for s in skills),
            "failed": sum(s["err"] for s in skills),
            "unknown_named": n_unknown,
            "chains_scanned": len(chains),
            "chains_total": len(chains) + len(skipped),
            "topics_registered": len(names),
        },
        "chain_errors": chain_errors,
        "note": ("G4 画像（调用/成功/失败）来自 behstats skill_summary"
                 "（上游口径）；「服务的主题」来自 skilljoin 交叉的本页"
                 "聚合——**两者不同源**；无 chain 的主题不计入服务主题数。"),
    }
