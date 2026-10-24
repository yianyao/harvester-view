# -*- coding: utf-8 -*-
"""writeactions -- B 档写动作的编排（SOP P3-1/P3-2）。

每个动作由三件东西构成，**都在本模块内、可被测试直接调**：

- `plan(params, cfg)`：**只读**。规范化 + 校验参数、读当前状态、算出
  "将写什么"（含 before/after 计数与 destructive 快照）。
  **必须无副作用**——有测试专门比对"plan 前后 meta 库字节不变"。
- `execute(params, cfg)`：真执行（调 session-harvester 的写函数，只写 meta 库）。
- 注册表条目（来自 `actions_registry.json` 的 `write_actions`）：参数 schema、
  风险等级、写哪个库。

**与上游的分工**：本模块只做"编排 + 台账 + 预演"，写入语义完全是上游的
（`topics.register_topic` / `suggestmeta.set_status` / `kwstats.prune_runs`）；
预演读的是上游同一批只读函数，故"预览说 3 个成员，写下去就是 3 个"有同一个
真源，不是两套算法。

**预览的诚实边界**（写进响应字段 `preview.limits`）：预演不重放上游的完整
执行路径（那等于先写一遍），故它报的是"当前状态 + 本动作会做的操作"，而不是
"执行后保证得到的结果"。凡是预演里算不准的（如 register 的自动编号），
就在 `limits` 里说明，不用猜的数字糊过去。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from . import ensure_harvester_importable

_ERR = ensure_harvester_importable()
if _ERR is None:  # pragma: no cover - 缺上游时由调用方报 503
    from harvester import suggestmeta as _sm
    from harvester import topics as _tp
    from harvester.cards import (card_current_warnings as _cr_current,
                                 record_review as _cr_record,
                                 _warn_key as _cr_key)
    from harvester.chainaudit import (audit_chain as _ca_audit,
                                      record_audit_review as _ca_record,
                                      _node_key as _ca_node_key)
    # P6-9 归因台：裁决只走上游台账函数（同一真源，不自拼 SQL）。
    from harvester.attrib import (list_verdicts as _at_list_verdicts,
                                 record_verdict as _at_record_verdict)
else:  # pragma: no cover
    _sm = _tp = None
    _cr_current = _cr_record = _cr_key = None
    _ca_audit = _ca_record = _ca_node_key = None
    _at_list_verdicts = _at_record_verdict = None


class WriteError(Exception):
    """参数/前置条件不满足 → 400（与"服务坏了"区分开）。"""


# ---------- 只读小工具（预演与执行共用，保证口径同一） ----------

def _topics_con(meta: Path):
    return _tp._con(meta)  # noqa: SLF001 - 上游的只读连接工厂


def _topic_row(meta: Path, topic_id: str) -> dict | None:
    con = _topics_con(meta)
    try:
        row = con.execute("SELECT id, name, keywords, members FROM topics "
                          "WHERE id=?", (topic_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"],
            "keywords": _loads(row["keywords"]),
            "members": _loads(row["members"])}


def _loads(v):
    try:
        return json.loads(v) if v else []
    except (json.JSONDecodeError, TypeError):
        return []


def _member_sids(row: dict) -> list[str]:
    return [m["sid"] if isinstance(m, dict) else m for m in row["members"]]


def _fingerprint(path: Path) -> dict:
    """文件级指纹：mtime_ns + size。meta 库很小，这就够判"两段之间被改过"。
    比读全表便宜，也比只有 mtime 更硬（同一秒内的改写靠 size 兜住）。"""
    p = Path(path)
    if not p.is_file():
        return {"exists": False, "mtime_ns": None, "size": None}
    st = p.stat()
    return {"exists": True, "mtime_ns": st.st_mtime_ns, "size": st.st_size}


# ---------- topic 家族 ----------

def _topic_meta(cfg) -> Path:
    if not cfg.topics_meta:
        raise WriteError("未配置 topics_meta（起服时加 --topics-meta）")
    return Path(cfg.topics_meta)


def _plan_topic_register(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    name = (p.get("name") or "").strip()
    if not name:
        raise WriteError("name 不能为空")
    kws = [k.strip() for k in (p.get("keywords") or "").split(",") if k.strip()]
    exists = None
    if meta.is_file():
        con = _topics_con(meta)
        try:
            row = con.execute("SELECT id FROM topics WHERE name=?",
                              (name,)).fetchone()
        finally:
            con.close()
        exists = row["id"] if row else None
    before = {"topics": _count(meta, "topics")}
    return {
        "target_path": str(meta),
        "before": before,
        "after": ({"topics": before["topics"]} if exists
                  else {"topics": before["topics"] + 1}),
        "snapshot": None,
        "limits": [] if exists else [
            "新主题的 id 由上游按当日最大序号自动编号（tp-YYYYMMDD-NNN），"
            "预览时无法预知具体号"],
        "human": [
            "动作：在 %s 注册主题「%s」" % (meta.name, name),
            ("关键词：%s" % "、".join(kws)) if kws else "关键词：（空）",
            ("库中已有同名主题 → 幂等返回旧 id %s，**不会新建**" % exists)
            if exists else "库中无同名主题 → 将新建一行",
        ],
        "idempotent_hit": exists,
    }


def _plan_topic_add(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    tid = (p.get("topic_id") or "").strip()
    row = _topic_row(meta, tid)
    if row is None:
        raise WriteError("主题不存在：%s" % tid)
    sids = [s.strip() for s in (p.get("sids") or "").split(",") if s.strip()]
    if not sids:
        raise WriteError("sids 不能为空（逗号分隔）")
    cur = _member_sids(row)
    new = [s for s in sids if s not in cur]
    return {
        "target_path": str(meta),
        "before": {"members": len(cur)},
        "after": {"members": len(cur) + len(new)},
        "snapshot": None,
        "limits": [],
        "human": [
            "动作：把 %d 个 sid 加进主题 %s「%s」" % (len(sids), tid, row["name"]),
            "其中 %d 个已是成员（幂等跳过），实际新增 %d 个" % (len(sids) - len(new),
                                                              len(new)),
            "新增：%s" % ("、".join(new) if new else "（无）"),
        ],
        "noop": not new,
    }


def _plan_topic_remove(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    tid = (p.get("topic_id") or "").strip()
    sid = (p.get("sid") or "").strip()
    row = _topic_row(meta, tid)
    if row is None:
        raise WriteError("主题不存在：%s" % tid)
    if not sid:
        raise WriteError("sid 不能为空")
    cur = _member_sids(row)
    if sid not in cur:
        return {"target_path": str(meta), "before": {"members": len(cur)},
                "after": {"members": len(cur)}, "snapshot": None,
                "limits": [], "noop": True,
                "human": ["动作：从主题 %s 移除成员" % tid,
                          "%s **不是**该主题成员 → 上游会 KeyError（无改动）"
                          % sid]}
    return {
        "target_path": str(meta), "before": {"members": len(cur)},
        "after": {"members": len(cur) - 1}, "snapshot": None, "limits": [],
        "human": ["动作：从主题 %s「%s」移除成员 %s"
                  % (tid, row["name"], sid),
                  "成员数 %d → %d（只动注册表，不删会话数据）"
                  % (len(cur), len(cur) - 1)],
    }


def _plan_topic_rename(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    tid = (p.get("topic_id") or "").strip()
    new = (p.get("name") or "").strip()
    row = _topic_row(meta, tid)
    if row is None:
        raise WriteError("主题不存在：%s" % tid)
    if not new:
        raise WriteError("新主题名不能为空")
    dup = None
    con = _topics_con(meta)
    try:
        r = con.execute("SELECT id FROM topics WHERE name=?", (new,)).fetchone()
    finally:
        con.close()
    if r is not None and r["id"] != tid:
        raise WriteError("主题名已被占用：%s（%s）——上游会拒绝，"
                         "重名要不要合并是语义判断，请显式 merge"
                         % (new, r["id"]))
    return {
        "target_path": str(meta),
        "before": {"name": row["name"]}, "after": {"name": new},
        "snapshot": None, "limits": [],
        "human": ["动作：主题 %s 改名「%s」→「%s」" % (tid, row["name"], new),
                  "id 不变 → 已发布的 chain 仍能按 topic_id 对上"],
        "noop": row["name"] == new,
    }


def _plan_topic_keywords(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    tid = (p.get("topic_id") or "").strip()
    row = _topic_row(meta, tid)
    if row is None:
        raise WriteError("主题不存在：%s" % tid)
    kws = [k.strip() for k in (p.get("keywords") or "").split(",") if k.strip()]
    if not kws:
        raise WriteError("keywords 不能为空（覆盖式定稿：给的是定稿后的完整列表）")
    return {
        "target_path": str(meta),
        "before": {"keywords": row["keywords"]}, "after": {"keywords": kws},
        "snapshot": None, "limits": [],
        "human": ["动作：**覆盖**主题 %s 的关键词（不是追加）" % tid,
                  "旧（%d 个）：%s" % (len(row["keywords"]),
                                       "、".join(row["keywords"]) or "（空）"),
                  "新（%d 个）：%s" % (len(kws), "、".join(kws))],
        "noop": row["keywords"] == kws,
    }


def _plan_topic_merge(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    tid = (p.get("topic_id") or "").strip()
    srcs = [s.strip() for s in (p.get("from_") or "").split(",") if s.strip()]
    if not tid or not srcs:
        raise WriteError("merge 需要 topic_id（目标）与 from_（源，逗号分隔）")
    tgt = _topic_row(meta, tid)
    if tgt is None:
        raise WriteError("目标主题不存在：%s" % tid)
    src_rows = []
    for s in srcs:
        r = _topic_row(meta, s)
        if r is None:
            raise WriteError("源主题不存在：%s" % s)
        src_rows.append(r)
    keep = bool(p.get("keep_sources"))
    cur = set(_member_sids(tgt))
    add = 0
    for r in src_rows:
        add += len([x for x in _member_sids(r) if x not in cur])
    return {
        "target_path": str(meta),
        "before": {"topics": _count(meta, "topics"),
                   "target_members": len(cur)},
        "after": {"topics": _count(meta, "topics") if keep
                  else _count(meta, "topics") - len(src_rows),
                  "target_members": len(cur) + add},
        "snapshot": [{"id": r["id"], "name": r["name"],
                      "members": _member_sids(r)} for r in src_rows],
        "limits": [],
        "human": [
            "动作：把 %s 合并进 %s「%s」"
            % ("、".join(s["id"] for s in src_rows), tid, tgt["name"]),
            "目标成员 %d → %d（新增 %d）" % (len(cur), len(cur) + add, add),
            ("**保留**源主题行（keep_sources）" if keep
             else "**删除**源主题行 %d 个（快照已存台账，但页面上找不回来）"
                  % len(src_rows)),
        ],
    }


def _plan_topic_delete(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    tid = (p.get("topic_id") or "").strip()
    row = _topic_row(meta, tid)
    if row is None:
        raise WriteError("主题不存在：%s" % tid)
    return {
        "target_path": str(meta),
        "before": {"topics": _count(meta, "topics")},
        "after": {"topics": _count(meta, "topics") - 1},
        "snapshot": row,   # ← destructive：快照进台账（唯一"可回滚"凭据）
        "limits": [],
        "human": [
            "动作：删除主题行 %s「%s」（成员 %d 个）"
            % (tid, row["name"], len(_member_sids(row))),
            "**只删注册行，不动任何会话数据**",
            "被删主题的完整快照（含成员与关键词）会写进 actions_meta.db 台账；"
            "要恢复得人工照快照重建",
        ],
    }


def _count(meta: Path, table: str) -> int:
    if not Path(meta).is_file():
        return 0
    try:
        con = _topics_con(Path(meta))
    except Exception:  # noqa: BLE001 - 库损坏时预览不该崩
        return 0
    try:
        return con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    except Exception:  # noqa: BLE001 - 表不存在
        return 0
    finally:
        con.close()


# ---------- suggest-status ----------

def _suggest_meta(cfg) -> Path:
    if not cfg.suggestions_meta:
        raise WriteError("未配置 suggestions_meta（起服时加 --suggestions-meta）")
    return Path(cfg.suggestions_meta)


def _status_row(meta: Path, key: str) -> dict | None:
    if not meta.is_file():
        return None
    import sqlite3
    con = sqlite3.connect(str(meta))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT key, status, adopted_at, note "
                          "FROM suggestion_status WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:   # 表还没建
        return None
    finally:
        con.close()
    return dict(row) if row else None


def _plan_suggest_status(p: dict, cfg) -> dict:
    meta = _suggest_meta(cfg)
    key = (p.get("key") or "").strip()
    status = (p.get("status") or "").strip()
    if not key:
        raise WriteError("key 不能为空（建议句原文，逐字）")
    if status not in _sm.STATUSES:
        raise WriteError("status=%r 不在 %s" % (status, list(_sm.STATUSES)))
    adopted_at = p.get("adopted_at") or None
    if adopted_at:
        try:
            _sm.validate_stamp(adopted_at, "adopted_at")
        except ValueError as e:
            raise WriteError(str(e))
    note = p.get("note")
    if note == "":
        note = None      # 空串 = 不改（上游语义：'' 才是显式清空）
    cur = _status_row(meta, key)
    before = cur or {"status": None}
    after = dict(before)
    after["status"] = status
    # v0.71：预演必须**确定性**——两段提交的确认段会重新预演并比对指纹，
    # 时钟进了 preview 就是死循环（用户实测：永远「预览指纹不匹配」）。
    # 故"执行时才定的 adopted_at"在预演里用固定哨兵 @now 占位，
    # 真执行时由上游 set_status 取当前时间（_exec_suggest_status 传 None）。
    if status == "adopted":
        after["adopted_at"] = adopted_at or before.get("adopted_at") or "@now"
    else:
        after["adopted_at"] = None
    if note is not None:
        after["note"] = note
    human = ["动作：建议台账 key=「%s」" % key,
             "状态：%s → **%s**" % (before.get("status") or "（新条目）", status)]
    if status != "adopted" and before.get("adopted_at"):
        human.append("adopted_at 将被**清空**（不再是采纳态）——"
                     "harvest-eval 的 A/B 分界随之消失")
    elif status == "adopted":
        human.append("adopted_at=%s（A/B 评估的分界 T）"
                     % (after["adopted_at"] if after["adopted_at"] != "@now"
                        else "写入时自动取当前时间"))
    if note is not None:
        human.append("note 写入：%s" % note)
    return {
        "target_path": str(meta),
        "before": before, "after": after,
        "snapshot": cur if status != "adopted" else None,
        "limits": [],
        "human": human,
        "noop": bool(cur and cur.get("status") == status
                     and cur.get("adopted_at") == after.get("adopted_at")),
    }


# ---------- keywords-gc ----------

def _kw_meta(cfg) -> Path:
    if not cfg.keywords_meta:
        raise WriteError("未配置 keywords_meta（起服时加 --keywords-meta）")
    return Path(cfg.keywords_meta)


def _kw_counts(meta: Path) -> dict:
    import sqlite3
    if not Path(meta).is_file():
        return {"runs": 0, "stats": 0, "size_mb": 0.0, "run_ids": []}
    con = sqlite3.connect(str(meta))
    try:
        try:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM keyword_runs ORDER BY id DESC")]
            stats = con.execute("SELECT COUNT(*) FROM keyword_stats").fetchone()[0]
        except sqlite3.OperationalError:
            ids, stats = [], 0
    finally:
        con.close()
    return {"runs": len(ids), "stats": stats, "run_ids": ids,
            "size_mb": round(Path(meta).stat().st_size / 1048576, 1)}


def _plan_keywords_gc(p: dict, cfg) -> dict:
    meta = _kw_meta(cfg)
    keep = p.get("keep_runs")
    try:
        keep = int(keep)
    except (TypeError, ValueError):
        raise WriteError("keep_runs 需要整数（>=1）")
    if keep < 1:
        raise WriteError("keep_runs 必须 >= 1（清空历史请显式删库）")
    cur = _kw_counts(meta)
    vacuum = bool(p.get("vacuum"))
    drop = max(0, cur["runs"] - keep)
    return {
        "target_path": str(meta),
        "before": {"runs": cur["runs"], "size_mb": cur["size_mb"]},
        "after": {"runs": min(keep, cur["runs"])},
        "snapshot": {"deleted_run_ids": cur["run_ids"][keep:]},
        "limits": [],
        "human": ["动作：回收 %s（保留最近 %d 次 run）" % (meta.name, keep),
                  "现有 run %d 个 / %d 统计行 / %.1f MB；将删除 run %d 个"
                  % (cur["runs"], cur["stats"], cur["size_mb"], drop),
                  "VACUUM：%s（删了东西才会真 VACUUM）"
                  % ("开" if vacuum else "关")],
        "noop": drop == 0,
    }


# ---------- 注册表：预览 + 执行 ----------

def _exec_topic(p: dict, cfg) -> dict:
    meta = _topic_meta(cfg)
    before = _count(meta, "topics")
    kind = p.pop("__kind")
    if kind == "register":
        kws = [k.strip() for k in (p.get("keywords") or "").split(",")
               if k.strip()]
        tid = _tp.register_topic(meta, p["name"], keywords=kws)
        return {"topic_id": tid, "summary": "已注册 %s「%s」" % (tid, p["name"])}
    if kind == "add":
        sids = [s.strip() for s in (p.get("sids") or "").split(",") if s.strip()]
        n = _tp.add_members(meta, p["topic_id"], sids,
                            evidence=p.get("evidence") or "")
        return {"topic_id": p["topic_id"], "added": n,
                "summary": "%s 新增成员 %d 个（重复幂等跳过）"
                           % (p["topic_id"], n)}
    if kind == "remove":
        _tp.remove_member(meta, p["topic_id"], p["sid"])
        return {"topic_id": p["topic_id"], "removed": p["sid"],
                "summary": "%s 移除成员 %s" % (p["topic_id"], p["sid"])}
    if kind == "rename":
        r = _tp.rename_topic(meta, p["topic_id"], p["name"])
        return {"topic_id": r["id"], "old_name": r["old_name"],
                "new_name": r["new_name"],
                "summary": "%s 改名：「%s」→「%s」"
                           % (r["id"], r["old_name"], r["new_name"])}
    if kind == "keywords":
        kws = [k.strip() for k in (p.get("keywords") or "").split(",")
               if k.strip()]
        r = _tp.set_keywords(meta, p["topic_id"], kws)
        return {"topic_id": r["id"], "old": r["old"], "new": r["new"],
                "summary": "%s 关键词 %d 个 → %d 个（覆盖式）"
                           % (r["id"], len(r["old"]), len(r["new"]))}
    if kind == "merge":
        srcs = [s.strip() for s in (p.get("from_") or "").split(",") if s.strip()]
        r = _tp.merge_topics(meta, p["topic_id"], srcs,
                             delete_sources=not p.get("keep_sources"))
        return {"target": r["target"], "added": r["added"], "kept": r["kept"],
                "deleted": r["deleted"],
                "summary": "合并入 %s：新增成员 %d、既有 %d；删除源 %s"
                           % (r["target"], r["added"], r["kept"],
                              "、".join(r["deleted"]) or "（无）")}
    if kind == "delete":
        snap = _tp.delete_topic(meta, p["topic_id"])
        return {"snapshot": snap,
                "summary": "已删除 %s「%s」（成员 %d 个，快照见台账）"
                           % (snap["id"], snap["name"], len(snap["members"]))}
    raise WriteError("未知 topic 子动作：%s" % kind)  # pragma: no cover


def _exec_suggest_status(p: dict, cfg) -> dict:
    meta = _suggest_meta(cfg)
    try:
        _sm.set_status(meta, p["key"], p["status"],
                       adopted_at=p.get("adopted_at") or None,
                       note=p.get("note") or None)
    except ValueError as e:
        raise WriteError(str(e))
    extra = []
    if p.get("adopted_at"):
        extra.append("adopted_at=%s" % p["adopted_at"])
    if p.get("note"):
        extra.append("note 已更新")
    return {"key": p["key"], "status": p["status"],
            "summary": "已记录：%s → %s%s"
                       % (p["key"], p["status"],
                          ("（%s）" % "，".join(extra)) if extra else "")}


def _exec_keywords_gc(p: dict, cfg) -> dict:
    from harvester.kwstats import prune_runs
    meta = _kw_meta(cfg)
    before = meta.stat().st_size if meta.is_file() else 0
    try:
        r = prune_runs(meta, keep=int(p["keep_runs"]),
                       vacuum=bool(p.get("vacuum")))
    except ValueError as e:
        raise WriteError(str(e))
    after = meta.stat().st_size if meta.is_file() else 0
    return {"kept": r["kept"], "deleted_runs": r["deleted_runs"],
            "deleted_stats": r["deleted_stats"], "vacuumed": r["vacuumed"],
            "size_before": before, "size_after": after,
            "summary": "保留最近 %d 次 run；删除 run %d 个 / 统计行 %d 条%s；"
                       "体积 %.1f MB → %.1f MB"
                       % (int(p["keep_runs"]), r["deleted_runs"],
                          r["deleted_stats"],
                          "，已 VACUUM" if r["vacuumed"] else "（未删则跳过）",
                          before / 1048576, after / 1048576)}


# ---------- 会话抑制（v0.49，用户批准的提案 §2.5 / 清单 ⑥：不新造机制）----------
#
# 纪律：
# - 抑制表**放现有的 topics_meta.db**（用户批准 ①：不新建 meta 库）；
# - 预演走上游的**只读**入口（`suppress.db_row_counts` +
#   `exporter.prune_suppressed(dry_run=True)`），故"预演零副作用"是上游保证的，
#   本模块不自己算行数（自己算就是两套算法）；
# - `execute` 调上游 `suppress.suppress(...)`——**它是唯一会写采集库的函数**，
#   本模块不直接 DELETE 任何表；
# - 口径照抄上游 docstring：删的是**仓内副本**，**不删采集源**；取消抑制
#   **不恢复**数据，能不能恢复只取决于语料是否还在；**不设恢复时限**。

def _suppress_meta(cfg) -> Path:
    """抑制表所在库 = `topics_meta`（**故意复用**，用户批准 ①）。

    写成"另起一个库"会让抑制表建到没人看的地方，且与清单 ① 相悖；
    故这里有测试钉住"注册表 target=topics_meta"与实现一致。
    """
    if not cfg.topics_meta:
        raise WriteError("未配置 topics_meta（起服时加 --topics-meta）")
    return Path(cfg.topics_meta)


def _exports_dir(cfg) -> Path | None:
    """导出产物目录（清旧副本用）。起服时没配就是 None——那时**不猜路径**，
    预演里如实写明"不清 exports 副本"。"""
    return Path(cfg.exports_root) if cfg.exports_root else None


def _split_sids(value) -> list[str]:
    """`sids` 既接列表也接逗号分隔的字符串（命令台给的是文本框）。"""
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = str(value or "").split(",")
    return sorted({s.strip() for s in items if s.strip()})


def _row_counts(db: Path, sids: list[str]) -> dict:
    from harvester.suppress import db_row_counts
    return db_row_counts(db, sids)


def _prune_preview(exports: Path | None, sids: list[str]) -> dict:
    if not exports or not Path(exports).is_dir():
        return {"scanned": 0, "deleted": [], "unreadable": [],
                "manifest_removed": 0, "dry_run": True}
    from harvester.exporter import prune_suppressed
    return prune_suppressed(exports, set(sids), dry_run=True)


def _plan_session_suppress(p: dict, cfg) -> dict:
    from harvester.suppress import list_suppressed
    meta = _suppress_meta(cfg)
    sids = _split_sids(p.get("sids"))
    if not sids:
        raise WriteError("sids 不能为空（库口径 `source:session_id`，即 search 输出的 [sid]）")
    reason = (p.get("reason") or "").strip()
    if not reason:
        raise WriteError("reason 不能为空——日后复查要看得懂为什么把它移出去")
    created_by = (p.get("created_by") or "").strip()
    db = Path(cfg.db)
    counts = _row_counts(db, sids)
    exports = _exports_dir(cfg)
    prune = _prune_preview(exports, sids)
    have = {r["sid"] for r in list_suppressed(meta)}
    already = [s for s in sids if s in have]
    absent = [s for s in sids if not counts[s]["found"] and s not in have]

    human = ["动作：把 %d 个会话从**仓内副本**里抑制掉" % len(sids)]
    for s in sids:
        c = counts[s]
        human.append("· %s%s — 会话行 %d / 消息 %d / 步骤 %d%s"
                     % ((c["title"][:60] + "　") if c["title"] else "", s,
                        c["sessions"], c["messages"], c["steps"],
                        "" if c["found"] else "（库里已经没有这些行）"))
    if exports:
        human.append("将删的 exports 副本：%d 个（扫了 %d 个 json）"
                     % (len(prune.get("deleted") or []), prune.get("scanned", 0)))
        if prune.get("unreadable"):
            human.append("⚠ %d 个 json 读不出 sid，**不会被删**（要看一眼才知道是不是它）"
                         % len(prune["unreadable"]))
    else:
        human.append("⚠ 起服时没配 `--exports-root` → 本次**不清 exports 副本**；"
                     "旧副本还在的话，下次索引仍可能把它收回来（要清请配好再跑）")
    if already:
        human.append("已经抑制过（幂等：只更新理由，不重复计数）：%s"
                     % "、".join(already))
    if absent:
        human.append("⚠ 这些 sid 在库与抑制表里都没有：%s——写下去只会记一条抑制行"
                     % "、".join(absent))
    human.append("**删的是仓内副本，不删采集源**（DSH/WorkBuddy 自己的会话文件"
                 "一字节不动）。此后 sync/update 会在导出步拦住它、不再入库。")
    human.append("恢复：取消抑制**不恢复**已删的行与副本——能不能恢复**只取决于"
                 "语料是否还在**；本工具**不设恢复时限**。")

    row_snapshot = {s: {k: counts[s][k]
                        for k in ("sessions", "messages", "steps")}
                    for s in sids}
    return {
        "target_path": str(meta),
        "before": {"suppressed": sorted(have & set(sids)), "rows": row_snapshot},
        "after": {"suppressed": sorted(have | set(sids))},
        # destructive 必须给"删除前快照"——那是"可回滚"承诺的唯一凭据。
        # **只存数量与标题，不存正文**：正文要能从语料恢复才叫可逆。
        "snapshot": {"sids": sids,
                     "titles": {s: counts[s]["title"] for s in sids},
                     "row_counts": row_snapshot,
                     "exports_to_delete": prune.get("deleted") or [],
                     "note": "只存数量与标题，**不存正文**——正文能否恢复取决于语料是否还在"},
        "limits": [
            "预演报的是「当前状态 + 本动作会做的操作」，不是「执行后保证的结果」",
            "exports 副本的清理只覆盖 exports 目录下的 .json/.md 与 manifest 条目；"
            "目录外的副本本工具看不到",
            "抑制**不影响采集源**：DSH/WorkBuddy 的会话文件不在本工具管辖内，"
            "本工具也不去探测它们还在不在",
        ],
        "human": human,
        "noop": False,
    }


def _exec_session_suppress(p: dict, cfg) -> dict:
    from harvester.suppress import suppress
    meta = _suppress_meta(cfg)
    sids = _split_sids(p.get("sids"))
    r = suppress(meta, Path(cfg.db), sids,
                 reason=(p.get("reason") or "").strip(),
                 created_by=(p.get("created_by") or "").strip(),
                 exports_dir=_exports_dir(cfg))
    purged = r.get("purged") or {}
    pruned = r.get("pruned") or {}
    return {
        "summary": ("抑制 %d 个会话：库内删 会话 %d / 消息 %d / 步骤 %d 行；"
                    "exports 副本删 %d 个"
                    % (len(r["sids"]),
                       sum(v.get("sessions", 0) for v in purged.values()),
                       sum(v.get("messages", 0) for v in purged.values()),
                       sum(v.get("steps", 0) for v in purged.values()),
                       len(pruned.get("deleted") or []))),
        "sids": r["sids"], "added": r["added"], "purged": purged, "pruned": pruned,
    }


def _plan_session_unsuppress(p: dict, cfg) -> dict:
    from harvester.suppress import list_suppressed
    meta = _suppress_meta(cfg)
    sids = _split_sids(p.get("sids"))
    if not sids:
        raise WriteError("sids 不能为空")
    have = {r["sid"]: r for r in list_suppressed(meta)}
    hit = sorted(s for s in sids if s in have)
    human = ["动作：取消 %d 个 sid 的抑制（**只删抑制表行**）" % len(sids)]
    for s in sids:
        r = have.get(s)
        if r:
            human.append("· %s — 抑制于 %s%s" % (
                s, r.get("created") or "—",
                ("，理由：" + r["reason"]) if r.get("reason") else "（未写理由）"))
        else:
            human.append("· %s — **不在抑制表里**（这条取消无效果）" % s)
    human.append("**取消抑制不恢复数据**：已删的库内行与 exports 副本不会回来。"
                 "能不能恢复**只取决于语料是否还在**——语料还在则下次 sync/update "
                 "会重新导出并入库；语料没了就真没了。本工具**不设恢复时限**。")
    return {
        "target_path": str(meta),
        "before": {"suppressed": hit},
        "after": {"suppressed": sorted(set(have) - set(sids))},
        "snapshot": None,     # 非 destructive：after 可由 before 推出，无需快照
        "limits": ["取消抑制不恢复数据——这是上游语义，不是本仓的限制"],
        "human": human,
        "noop": not hit,
    }


def _exec_session_unsuppress(p: dict, cfg) -> dict:
    from harvester.suppress import unsuppress
    meta = _suppress_meta(cfg)
    sids = _split_sids(p.get("sids"))
    n = unsuppress(meta, sids)
    return {"summary": "取消抑制 %d 行（sid=%s）" % (n, "、".join(sids)),
            "removed": n, "sids": sids}


# ---------- 零散登记（P5-⑧，用户 2026-10-16 批准）----------
#
# 与 session_suppress 的本质区别：抑制 = 删仓内副本（destructive）；
# 零散 = 只往 topics_meta 的 sessions_noise 表插幂等行（可逆：删行即撤销），
# 效果是主题候选簇（candidates / keywords）不再收它们。会话数据一行不动。
# 目标库与抑制表同库（topics_meta），但**不同表**——fail loud 用独立小工具，
# 不复用 _suppress_meta 的名字以免语义混淆。


def _noise_meta(cfg) -> Path:
    """零散表所在库 = topics_meta.db（与抑制表同库不同表）。"""
    if not cfg.topics_meta:
        raise WriteError("未配置 topics_meta（起服时加 --topics-meta）")
    return Path(cfg.topics_meta)


def _plan_noise_register(p: dict, cfg) -> dict:
    from harvester.consolidate import list_noise
    meta = _noise_meta(cfg)
    sids = _split_sids(p.get("sids"))
    if not sids:
        raise WriteError("sids 不能为空（要登记零散的 sid，多个用逗号分隔）")
    reason = (p.get("reason") or "").strip()
    have = {r["sid"] for r in list_noise(meta)}
    new = [s for s in sids if s not in have]
    already = [s for s in sids if s in have]
    human = [
        "动作：把 %d 个会话登记为**零散**（不归任何主题）" % len(sids),
        "写入：%s 的 sessions_noise 表（新增 %d 行%s）" % (
            meta, len(new),
            "；已在表 %d 个，幂等跳过" % len(already) if already else ""),
        "效果：这些会话不再进主题候选簇（candidates / keywords）；"
        "会话本身仍在库、仍可检索，一个字节的数据都不删",
        "撤销：从 sessions_noise 删行即恢复（可逆）——与抑制不同，"
        "这不是删除动作",
    ]
    if reason:
        human.append("理由：%s" % reason)
    return {
        "target_path": str(meta),
        "before": {"noise": sorted(have)},
        "after": {"noise": sorted(have | set(sids))},
        "snapshot": None,
        "limits": ["登记零散≠抑制：不删任何数据，只是不再进主题候选"],
        "human": human,
        "noop": not new,
    }


def _exec_noise_register(p: dict, cfg) -> dict:
    from harvester.consolidate import register_noise
    meta = _noise_meta(cfg)
    sids = _split_sids(p.get("sids"))
    reason = (p.get("reason") or "").strip()
    n = register_noise(meta, [{"sid": s, "reason": reason} for s in sids])
    return {"summary": "登记零散 %d 个会话（新增 %d 行，幂等跳过 %d）"
                       % (len(sids), n, len(sids) - n),
            "added": n, "sids": sids}


# ---------- 班规候选与蒸馏包（v0.62：写主库仓内工作文件，不碰库）----------
#
# 用户令（2026-10-14）：建议文案不再只给"复制命令"——命令台要能就地执行。
# 这两个动作写的是**主库仓内的工作文件**（建议池 md / 蒸馏包 md）：
# - 只新增/覆盖这一个目标文件；覆盖前自动备份 .bak-<时间戳>（可逆承诺）；
# - 不碰任何库表、不碰 AGENTS.md（上游纪律：工具绝不代改规矩文件）；
# - 输出路径**服务端自动命名**（registrygen 的 auto_out 语义），页面绝不传
#   路径——让人指路等于给"顺手指向任意文件"留后门；
# - 预演与执行都调上游同一批只读函数（collect_errors_from_db /
#   build_suggestion_entries / build_distill_packet），不做两套算法。

def _repo_root(cfg) -> Path:
    return Path(cfg.db).resolve().parent


def _require_db(cfg) -> Path:
    db = Path(cfg.db)
    if not db.is_file():
        raise WriteError("索引库不存在：%s（先跑采集更新）" % db)
    return db


def _bak_copy(target: Path) -> Path | None:
    """覆盖前备份：存在才备份，返回备份路径（None=首次创建，无需备份）。"""
    if not target.is_file():
        return None
    bak = target.with_name(target.name + ".bak-"
                           + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(target, bak)
    return bak


def _file_human(out: Path, before: dict, what: str) -> list[str]:
    return [
        what,
        "写入：%s%s" % (out, "（已存在 → 先备份 .bak 再覆盖）"
                       if before.get("exists") else "（新文件）"),
    ]


def _plan_suggest_agents(p: dict, cfg) -> dict:
    from harvester.agent_suggest import build_suggestion_entries
    from harvester.errstats import collect_errors_from_db
    from harvester.suggestmeta import load_statuses
    db = _require_db(cfg)
    since = p.get("since")
    try:
        since_days = float(since) if since not in (None, "") else None
    except (TypeError, ValueError):
        raise WriteError("since 需要数字（天数），收到 %r" % (since,))
    min_count = int(p.get("min_count") or 3)
    max_items = int(p.get("max") or 15)
    if min_count < 1:
        raise WriteError("min_count 至少为 1")
    if max_items < 1:
        raise WriteError("max 至少为 1")
    out = _repo_root(cfg) / "agents_suggestions.md"
    errors, meta = collect_errors_from_db(db, since_days=since_days)
    statuses = None
    if cfg.suggestions_meta and Path(cfg.suggestions_meta).is_file():
        statuses = load_statuses(Path(cfg.suggestions_meta))
    built = build_suggestion_entries(errors, min_count=min_count,
                                     max_items=max_items, statuses=statuses)
    n_entries = len(built["entries"])
    before = _fingerprint(out)
    win = ("最近 %g 天" % since_days) if since_days else "全库"
    human = _file_human(out, before,
                        "动作：扫描索引库错误步骤（%s，共 %s 步 / %s 条错误）"
                        "并按模板起草建议"
                        % (win, meta.get("total_steps") or 0,
                           meta.get("error_count") or 0))
    human += [
        "命中：%d 条建议候选（另有 %d 个模式未命中模板，归「待人工归因」）"
        % (n_entries, len(built["leftover"])),
        "纪律：绝不自动改 AGENTS.md——建议池写好后由人工审阅、自行并入",
    ]
    return {"target_path": str(out),
            "before": before, "after": None, "snapshot": None,
            "count": n_entries,
            "limits": ["条数按预演时的库即时统计；执行时库若变了，"
                       "以执行时实况为准"],
            "human": human, "noop": False}


def _exec_suggest_agents(p: dict, cfg) -> dict:
    from harvester.agent_suggest import build_suggestions
    from harvester.errstats import collect_errors_from_db
    from harvester.suggestmeta import load_statuses
    db = _require_db(cfg)
    since = p.get("since")
    since_days = float(since) if since not in (None, "") else None
    min_count = int(p.get("min_count") or 3)
    max_items = int(p.get("max") or 15)
    out = _repo_root(cfg) / "agents_suggestions.md"
    errors, _meta = collect_errors_from_db(db, since_days=since_days)
    statuses = None
    if cfg.suggestions_meta and Path(cfg.suggestions_meta).is_file():
        statuses = load_statuses(Path(cfg.suggestions_meta))
    content = build_suggestions(errors, min_count=min_count,
                                max_items=max_items, statuses=statuses)
    bak = _bak_copy(out)
    out.write_text(content, encoding="utf-8", newline="\n")
    return {"summary": "建议池已写入 %s%s；请人工审阅后自行并入 AGENTS.md"
                       % (out, "（原文件已备份 %s）" % bak if bak else ""),
            "path": str(out), "backup": str(bak) if bak else None}


def _draft_out_path(cfg, sid: str, ctype: str) -> Path:
    # 文件名服务端定：卡型 + sid 指纹前 10 位（同 sid 同型幂等同名，
    # 重跑覆盖自己；不用 sid 原文——它含盘符斜杒，当文件名不安全）。
    digest = hashlib.sha1(sid.encode("utf-8")).hexdigest()[:10]
    return (_repo_root(cfg) / "docs" / "distill_prompts"
            / ("draft-%s-%s.md" % (ctype, digest)))


def _draft_packet(p: dict, cfg) -> tuple[str, Path, str, int]:
    """plan/execute 共用：校验参数 + 生成包（只读）。返回 (packet, out, ctype, max_chars)。"""
    from harvester.drafting import REQUIRED_TYPES, build_distill_packet
    db = _require_db(cfg)
    sid = (p.get("sid") or "").strip()
    if not sid:
        raise WriteError("缺必填参数 sid（会话唯一编号，明细锚点里有）")
    ctype = p.get("type") or "pitfall"
    if ctype not in REQUIRED_TYPES:
        raise WriteError("type=%r 不在 %s（pitfall=坑 / insight=洞见 / "
                         "workflow=流程）" % (ctype, sorted(REQUIRED_TYPES)))
    try:
        max_chars = int(p.get("max_chars") or 24000)
    except (TypeError, ValueError):
        raise WriteError("max_chars 需要整数，收到 %r" % (p.get("max_chars"),))
    if max_chars < 500:
        raise WriteError("max_chars 太小（至少 500，太小包里没原文）")
    try:
        packet = build_distill_packet(db, sid, ctype=ctype,
                                      max_chars=max_chars)
    except KeyError as e:
        raise WriteError(str(e.args[0]) if e.args else str(e))
    except ValueError as e:
        raise WriteError(str(e))
    return packet, _draft_out_path(cfg, sid, ctype), ctype, max_chars


def _plan_draft(p: dict, cfg) -> dict:
    packet, out, ctype, max_chars = _draft_packet(p, cfg)
    sid = (p.get("sid") or "").strip()
    before = _fingerprint(out)
    human = _file_human(out, before,
                        "动作：为会话 %s 生成蒸馏包（%s 卡型，原文截断预算 "
                        "%d 字符）" % (sid, ctype, max_chars))
    human += [
        "内容：自包含 md（指令 + 卡片规范 + 会话原文），本次约 %d 字"
        % len(packet),
        "下一步：把文件交给任意 Agent 起草卡片 → 草稿落 cards_pending/ "
        "→ 卡片校验 → 人工终审",
    ]
    return {"target_path": str(out), "before": before, "after": None,
            "snapshot": None, "bytes": len(packet),
            "limits": ["字数按预演时的库算；执行时以实况为准"],
            "human": human, "noop": False}


def _exec_draft(p: dict, cfg) -> dict:
    from harvester.drafting import write_packet
    packet, out, _ctype, _mc = _draft_packet(p, cfg)
    bak = _bak_copy(out)
    path = write_packet(out, packet)
    return {"summary": "蒸馏包已写入 %s（约 %d 字）%s；交给任意 Agent 起草卡片"
                       % (path, len(packet),
                          "（原文件已备份 %s）" % bak if bak else ""),
            "path": str(path), "backup": str(bak) if bak else None}


# ---------- 卡片脚手架 / 知识库发布（P10：把"只能复制"的两条写命令接进来）----------

def _cards_new_args(p: dict, cfg) -> tuple:
    """预演/执行共用取参（同一真源，不算两套）。

    `root`/`db` 是服务端路径（页面不传）：root 缺省用起服时的 `--cards-root`，
    db 缺省用索引库。sid 必填——给不出"哪次会话"，脚手架就没有锚点。
    """
    db = Path(p.get("db") or cfg.db)
    root = Path(p.get("root") or cfg.cards_root)
    sid = str(p.get("sid") or "").strip()
    if not sid:
        raise WriteError("缺 sid（要哪次会话的卡片脚手架）")
    ctype = str(p.get("type") or "pitfall")
    turn = p.get("turn")
    turn = int(turn) if str(turn or "").strip() not in ("", "None") else None
    return db, root, sid, ctype, p.get("title"), turn


def _plan_cards_new(p: dict, cfg) -> dict:
    """预演：只算"会写到哪个文件"，**连目录都不建**（`scaffold_card(dry_run=True)`）。"""
    from harvester.cards import scaffold_card
    db, root, sid, ctype, title, turn = _cards_new_args(p, cfg)
    try:
        out = scaffold_card(db, sid, root, ctype=ctype, title=title,
                            turn=turn, dry_run=True)
    except (KeyError, ValueError) as e:
        raise WriteError(str(e.args[0]) if e.args else str(e))
    before = _fingerprint(out)
    human = _file_human(out, before,
                        "动作：为会话 %s 生成卡片脚手架（%s 卡型）" % (sid, ctype))
    human += [
        "内容：frontmatter（id/title/type/tags/triggers/anchors/evidence/"
        "confidence/created）+ 正文三节骨架；evidence 与正文留白，待人工补全",
        "不会覆盖任何既有卡片：文件名取当天序号的下一个号",
        "下一步：补全 evidence 与正文 → cards validate",
    ]
    return {"target_path": str(out), "before": before, "after": None,
            "snapshot": None, "bytes": None,
            "limits": ["序号按预演时目录里已有的卡片算；执行时若有人抢先建卡，序号会后移一档"],
            "human": human, "noop": False}


def _exec_cards_new(p: dict, cfg) -> dict:
    from harvester.cards import scaffold_card
    db, root, sid, ctype, title, turn = _cards_new_args(p, cfg)
    try:
        path = scaffold_card(db, sid, root, ctype=ctype, title=title, turn=turn)
    except (KeyError, ValueError) as e:
        raise WriteError(str(e.args[0]) if e.args else str(e))
    return {"summary": "卡片脚手架已生成 %s（%s 卡型）；evidence 与正文待人工补全"
                       % (path, ctype),
            "path": str(path)}


def _kb_root_dest(p: dict, cfg) -> tuple:
    """正本根 = 卡片库的父目录（`knowledge/`）；镜像位 = 服务端定的那个。

    **镜像位不接受页面传参**：让人指路就等于给"把知识库发到任意目录"留后门。
    """
    from harvester.distill import KB_DEFAULT_ROOT
    root = Path(p.get("root") or Path(cfg.cards_root).parent)
    dest = Path(getattr(cfg, "kb_mirror", None) or KB_DEFAULT_ROOT)
    return root, dest


def _plan_kb_publish(p: dict, cfg) -> dict:
    """预演：只数"会复制几个/跳过几个"，**不建目录、不复制**。"""
    from harvester.distill import publish_master
    root, dest = _kb_root_dest(p, cfg)
    try:
        rep = publish_master(root, dest, dry_run=True)
    except FileNotFoundError as e:
        raise WriteError(str(e.args[0]) if e.args else str(e))
    total = sum(rep[s]["copied"] for s in ("cards", "topics") if s in rep)
    skip = sum(rep[s]["skipped"] for s in ("cards", "topics") if s in rep)
    before = _fingerprint(dest)
    human = [
        "动作：把正本 knowledge/ 单向发布到 WorkBuddy 知识库镜像位",
        "方向：正本 → 镜像，只喂不收（镜像里多出来的文件永远不会被删）",
        "范围：只碰 cards/ 与 topics/ 两个子目录",
        "会复制 %d 个文件；%d 个已是最新（按 mtime 判，跳过）"
        % (total, skip),
        "正本根：%s" % root,
        "镜像位：%s%s" % (dest, "（目录还不存在，执行时会建）"
                         if not dest.is_dir() else ""),
    ]
    if rep.get("missing"):
        human.append("正本里没有这些子目录（跳过）：%s"
                     % "、".join(rep["missing"]))
    return {"target_path": str(dest), "before": before, "after": None,
            "snapshot": None, "bytes": None,
            "limits": ["复制按「正本较新才覆盖」判；同一秒内改过的文件靠 mtime 可能判成已最新"],
            "human": human, "noop": total == 0}


def _exec_kb_publish(p: dict, cfg) -> dict:
    from harvester.distill import publish_master
    root, dest = _kb_root_dest(p, cfg)
    try:
        rep = publish_master(root, dest)
    except FileNotFoundError as e:
        raise WriteError(str(e.args[0]) if e.args else str(e))
    parts = ["%s：发布 %d 个（%d 个已最新跳过）"
             % (s, rep[s]["copied"], rep[s]["skipped"])
             for s in ("cards", "topics") if s in rep]
    return {"summary": "已发布到 %s；%s%s"
                       % (dest, "；".join(parts),
                          "；正本缺子目录：" + "、".join(rep["missing"])
                          if rep.get("missing") else ""),
            "path": str(dest), "report": rep}


#: action_id → (plan, execute)。**这张表就是 B 档的"实现白名单"**：
#: `actions_registry.json` 声明了什么，这里必须一一对上（有测试比对）。
# ---------- 卡片复核台账（v0.75，上游 v0.74 的 view 直给）----------

def _cards_reviews_meta(cfg) -> Path:
    if not getattr(cfg, "reviews_meta", None):
        raise WriteError("未配置 reviews_meta（起服时加 --reviews-meta）")
    return Path(cfg.reviews_meta)


def _card_review_keys(p: dict, cfg) -> tuple:
    """预演/执行共用的取键逻辑（同一真源，不算两套）。"""
    root = Path(p.get("root") or cfg.cards_root)
    f = str(p["file"])
    card = root / f
    if not card.is_file():
        raise WriteError(f"卡片文件不存在: {card}")
    # 引文核对必须与展示层（/api/cards）同库同口径：不带索引库时
    # evidence 类警告会退化成「原文不可得」，直给预演取键为空、
    # 接受变 no-op（2026-10-16 真机实测踩中）。默认用本服务的
    # 索引库（cfg.db，_session_content 对缺失库容错），显式传 db 仍可覆盖。
    db = Path(p["db"]) if p.get("db") else Path(cfg.db)
    if not db.is_file():
        db = None
    warns = _cr_current(card, db=db)
    # warn 归一：页面直给传单条字符串（V6），CLI 语义是可多次子串——
    # 不能 tuple() 逐字符化，单串必须视作 [串]（v0.76 V6 修）。
    wv = p.get("warn")
    if wv is None:
        sub: tuple = ()
    elif isinstance(wv, (list, tuple, set)):
        sub = tuple(str(x) for x in wv)
    else:
        sub = (str(wv),)
    if sub:
        warns = [w for w in warns if any(s in w for s in sub)]
    keys = sorted({_cr_key(w) for w in warns})
    return f, keys


def _plan_card_review(p: dict, cfg) -> dict:
    """预演：列出该卡当前警告与将落账的键（只读，无副作用）。"""
    meta = _cards_reviews_meta(cfg)
    f, keys = _card_review_keys(p, cfg)
    verdict = p.get("verdict") or "accepted"
    human = [f"动作：卡片复核落账——{f}",
             f"裁决：**{verdict}**（accepted=接受不再显示；fixed=已按修复处理；"
             "重开用改判 UPSERT）",
             f"目标：{meta}（card_review 表；不碰卡片文件、不碰任何库数据）",
             f"将落账 {len(keys)} 条警告键："]
    human += [f"  - {k[:90]}" for k in keys[:8]]
    if len(keys) > 8:
        human.append(f"  - …另有 {len(keys) - 8} 条")
    if not keys:
        human.append("（该卡当前没有可裁决的警告——执行为 no-op）")
    return {"target_path": str(meta),
            "before": {"card": f},
            "after": {"recorded": len(keys), "verdict": verdict},
            "snapshot": None,
            "limits": ["预演列的是当前警告；执行时若卡片已改，按届时警告取键"
                       "（卡片文件不在两段指纹保护内）"],
            "human": human,
            "noop": not keys}


def _exec_card_review(p: dict, cfg) -> dict:
    meta = _cards_reviews_meta(cfg)
    f, keys = _card_review_keys(p, cfg)
    verdict = p.get("verdict") or "accepted"
    n = _cr_record(meta, f, keys, verdict=verdict,
                   note=p.get("note") or "",
                   reviewed_by=p.get("created_by") or "")
    return {"recorded": n, "card": f, "verdict": verdict,
            "summary": f"已落账 {n} 条裁决（{verdict}）；"
                       "重跑 cards validate / 刷新卡片页即不再显示"}


# ---------- chain 复核台账（v0.76 V7，上游 v0.75 的 view 直给）----------
#: 与 card_review 同一套两段提交：预演列"将落账哪些锚点键"，人确认才写
#: topics_meta.db 的 audit_review 表。键格式是上游 _node_key 的
#: `node:<sid>#turn:<turn>`（同一真源，不自拼格式）。

def _ca_topics_meta(cfg) -> Path:
    if not getattr(cfg, "topics_meta", None):
        raise WriteError("未配置 topics_meta（起服时加 --topics-meta）")
    return Path(cfg.topics_meta)


def _ca_chain_path(p: dict, cfg) -> Path:
    root = getattr(cfg, "chain_root", None)
    if not root:
        raise WriteError("未配置 chain_root（起服时加 --chain-root）")
    f = str(p.get("file") or "")
    if (not f or f.startswith(("/", "\\")) or ".." in f.replace("\\", "/")):
        raise WriteError("file 必须是 chain_root 下的相对路径（不含 ..）")
    path = Path(root) / f
    if not path.is_file():
        raise WriteError(f"chain 文件不存在: {path}")
    return path


def _split_accepts(value) -> list[str]:
    """accepts 参数归一：页面传逗号串或数组都收（惯例同 _split_sids）。"""
    if value is None:
        return []
    items = list(value) if isinstance(value, (list, tuple, set)) \
        else str(value).split(",")
    out = []
    for it in items:
        it = str(it).strip()
        if it:
            out.append(it)
    return out


def _ca_norm_key(k: str) -> str:
    """容忍裸 `sid#turn` 手抄；规范键一律走上游 _node_key。"""
    k = k.strip()
    if k.startswith("node:"):
        return k
    sid, _, turn = k.rpartition("#")
    if sid and turn.isdigit():
        return _ca_node_key(sid, int(turn))
    return k


def _ca_accept_keys(p: dict, cfg) -> tuple:
    """预演/执行共用的取键逻辑（同一真源，不算两套）。

    quotes/coverage 都关——直给只需要锚点可疑行；review_meta 传入使
    已接受裁决的行从可疑集消失（与 CLI 审计同一口径）。"""
    path = _ca_chain_path(p, cfg)
    meta = _ca_topics_meta(cfg)
    r = _ca_audit(path, cfg.db, quotes=False, coverage=False, anchors=True,
                  review_meta=meta, show_reviewed=True)
    suspicious = r["anchors"]["suspicious"]
    cur = {_ca_node_key(s["sid"], s["turn"]): f"{s['sid']}#turn:{s['turn']}"
           for s in suspicious}
    wants = [_ca_norm_key(k) for k in _split_accepts(p.get("accept"))]
    if wants:
        unknown = [k for k in wants if k not in cur]
        if unknown:
            raise WriteError(
                "锚点键不在当前可疑集（可能已被裁决或链已改）："
                + "、".join(unknown[:5]))
        keys = wants
    else:
        keys = sorted(cur)
    return path, keys


def _plan_chain_audit_accept(p: dict, cfg) -> dict:
    """预演：列出该链当前未接受的可疑锚点与将落账的键（只读，无副作用）。"""
    meta = _ca_topics_meta(cfg)
    path, keys = _ca_accept_keys(p, cfg)
    human = [f"动作：chain 复核落账——{path.name}",
             "裁决：**accepted**（这些锚点的疑点判为可接受/已核实，"
             "审计不再报；改判重开用 CLI `--accept` 覆盖）",
             f"目标：{meta}（audit_review 表；不碰 chain 文件、不碰索引库）",
             f"将落账 {len(keys)} 条锚点键："]
    human += [f"  - {k}" for k in keys[:8]]
    if len(keys) > 8:
        human.append(f"  - …另有 {len(keys) - 8} 条")
    if not keys:
        human.append("（该链当前没有未接受的软锚点——执行为 no-op）")
    return {"target_path": str(meta),
            "before": {"chain": path.name},
            "after": {"recorded": len(keys), "verdict": "accepted"},
            "snapshot": None,
            "limits": ["预演列的是当前可疑集；两段之间链文件改了 → "
                       "执行重新取键，缺的直接报错不静默跳过"],
            "human": human,
            "noop": not keys}


def _exec_chain_audit_accept(p: dict, cfg) -> dict:
    meta = _ca_topics_meta(cfg)
    path, keys = _ca_accept_keys(p, cfg)
    n = _ca_record(meta, path.name, keys, verdict="accepted",
                   note=p.get("accept_note") or "")
    return {"recorded": n, "chain": path.name, "verdict": "accepted",
            "summary": f"已落账 {n} 条锚点裁决（accepted）；"
                       "重跑 chain-audit 即不再报"}


# ---------- 归因裁决台账（P6-9 归因台，上游 v0.79 的 attrib-review）----------
#: 与 card_review / chain_audit_accept 同一套两段提交。**只写 attrib_meta.db
#: 的 attribations 表**（`harvester.attrib.record_verdict`，UPSERT 改判=覆盖），
#: 事实库 harvester.db 一字节不动——归因台的"判定槽"落账通道就这一条。

_ATTRIB_VERDICTS = ("accepted", "rejected", "revise")


def _attrib_meta(cfg) -> Path:
    if not getattr(cfg, "attrib_meta", None):
        raise WriteError("未配置 attrib_meta（起服时加 --attrib-meta；"
                         "缺省=<db 同目录>/attrib_meta.db）")
    return Path(cfg.attrib_meta)


def _attrib_verdict_keys(p: dict, cfg) -> tuple:
    """预演/执行共用的取键逻辑（同一真源，不算两套）。"""
    pattern = str(p.get("pattern") or "").strip()
    rule = str(p.get("rule") or "").strip()
    if not pattern or not rule:
        raise WriteError("pattern 与 rule 都必填（照候选表原样抄，别手改）")
    verdict = str(p.get("verdict") or "accepted")
    if verdict not in _ATTRIB_VERDICTS:
        raise WriteError(f"verdict 不支持：{verdict}（可选 "
                         + "|".join(_ATTRIB_VERDICTS) + "）")
    return pattern, rule, verdict


def _plan_attrib_verdict(p: dict, cfg) -> dict:
    """预演：说清"落哪一行、改判还是新增"（只读，无副作用）。"""
    meta = _attrib_meta(cfg)
    pattern, rule, verdict = _attrib_verdict_keys(p, cfg)
    before = None
    if meta.is_file():
        try:
            before = next((r for r in _at_list_verdicts(meta)
                           if r.get("pattern_key") == pattern
                           and r.get("rule_id") == rule), None)
        except Exception:  # noqa: BLE001 - 台账读不出不阻断预演，如实说明
            before = None
    human = [f"动作：归因裁决落账——规则 **{rule}** ← 模式 **{pattern}**",
             f"裁决：**{verdict}**（accepted=接受；rejected=驳回；"
             "revise=方向对但 why/fix 要改）",
             f"目标：{meta}（attribations 表；**不碰 harvester.db**）",
             ("将**改判**已有那一行（UPSERT 覆盖）" if before else
              "将在台账**新增**一行"),
             "落账后重跑 attrib：该候选移出候选区、落到「已裁决」段"
             "（不会静默消失）"]
    if p.get("note"):
        human.append(f"说明：{p['note']}")
    return {"target_path": str(meta),
            "before": ({"verdict": before.get("verdict"),
                        "note": before.get("note") or ""} if before else None),
            "after": {"pattern_key": pattern, "rule_id": rule,
                      "verdict": verdict},
            "snapshot": None,
            "limits": ["预演列的是台账当前状态；两段之间若台账被改，"
                       "服务端按参数指纹拒绝并要求重新预演"],
            "human": human,
            "noop": False}


def _exec_attrib_verdict(p: dict, cfg) -> dict:
    meta = _attrib_meta(cfg)
    pattern, rule, verdict = _attrib_verdict_keys(p, cfg)
    n = _at_record_verdict(meta, pattern, rule,
                           cls=str(p.get("cls") or "unclassified"),
                           verdict=verdict,
                           note=str(p.get("note") or ""),
                           reviewed_by=str(p.get("by") or ""))
    return {"recorded": n, "pattern": pattern, "rule": rule,
            "verdict": verdict,
            "summary": f"已落账 {n} 条归因裁决（{verdict}）：{rule} ← {pattern}；"
                       "重跑 attrib 即不再把该候选列进候选区"}


# ---------- 链长文发布（P11，用户 2026-10-24 裁决）----------
# 链路：A 档 `chain_build` 出创作包（不落盘）→ Agent 按包 §5 写草稿到
# **草稿位**（服务端定路径）→ 本动作两道校验门（结构/锚点 + 引文逐字）
# 全过才把草稿逐字节复制进 knowledge/topics/（知识正本）。
# 红线：页面绝不传路径；路径类参数一律 server 注入。

def chain_draft_path(cfg, topic_id: str) -> Path:
    """链草稿位（服务端定）：`<exports-root>/chains/<topic-id>.md`。

    为什么放 exports 而不是直接写 knowledge/topics/：正本只接受过校验的
    写入。草稿位是工作区，Agent 随便改；发布动作负责"过了门才搬进去"。
    """
    import re as _re
    root = (Path(cfg.exports_root) if cfg.exports_root
            else _repo_root(cfg) / "exports")
    safe = _re.sub(r"[^0-9A-Za-z_.\-]+", "-", str(topic_id)).strip("-")
    return root / "chains" / ("%s.md" % (safe or "topic"))


def _chain_publish_args(p: dict, cfg):
    """plan/execute 共用取参（同一真源）。"""
    err = ensure_harvester_importable()
    if err:
        raise WriteError(err)
    db = _require_db(cfg)
    tid = str(p.get("id") or "").strip()
    if not tid:
        raise WriteError("缺主题 id（要发布哪个主题的链）")
    if cfg.topics_meta is None or not Path(cfg.topics_meta).is_file():
        raise WriteError("未配置 topics_meta（起服时加 --topics-meta）——"
                         "没有主题库就没法核成员与锚点")
    if not cfg.chain_root:
        raise WriteError("未配置 chain_root（起服时加 --chain-root）——"
                         "不知道正本该落哪儿")
    draft = chain_draft_path(cfg, tid)
    if not draft.is_file():
        raise WriteError(
            "草稿不存在：%s｜先点「生成链创作包」，把包交给 Agent，"
            "让它把 chain 长文写到这个路径再来发布" % draft)
    return draft, Path(cfg.chain_root), Path(cfg.topics_meta), Path(db), tid


def _plan_chain_publish(p: dict, cfg) -> dict:
    from harvester.chainbuild import publish
    draft, chain_root, meta, db, tid = _chain_publish_args(p, cfg)
    force = bool(p.get("force"))
    r = publish(draft, chain_root, meta, db, apply=False,
                force=force, allow_audit=bool(p.get("allow_audit")))
    out = Path(r["target"] or "")
    before = _fingerprint(out)
    v, a = r["validate"], r["audit"]
    human = [
        "动作：把链草稿校验后发布进知识正本（主题 %s）" % tid,
        "草稿：%s（%d 字节）" % (draft, draft.stat().st_size),
        "结构校验：%s（stages %s / nodes %s）"
        % ("通过" if v["ok"] else "**未通过**",
           (v.get("stats") or {}).get("stages", 0),
           (v.get("stats") or {}).get("nodes", 0)),
        "引文逐字门：%s（未命中 %d 条）｜锚点语义门：%s（疑似错配 %d 个）"
        % ("通过" if a["ok"] else "**未通过**", a["quotes_unmatched"],
           "通过" if a.get("anchors_suspicious", 0) == 0 else "有告警",
           a.get("anchors_suspicious", 0)),
        "写入：%s%s" % (out, "（已存在 → 发布即覆盖，注意）"
                       if before.get("exists") else "（新文件）"),
    ]
    for b in r["blockers"]:
        human.append("拦下：%s" % b)
    for w in r["warnings"]:
        human.append("警告：%s" % w)
    if r["ok"]:
        human.append("下一步：确认无误后执行；执行即把草稿**逐字节**搬进正本，"
                     "主题页刷新即见新链")
    else:
        human.append("结论：**现在发布不了**——上面「拦下」全清掉再来")
    return {"target_path": str(out), "before": before, "after": None,
            "snapshot": None, "bytes": draft.stat().st_size,
            "limits": ["校验按预演时的草稿与库算；执行时以实况为准"
                       "（草稿若在执行前又被改，执行会用最新内容重算一遍）"],
            "human": human, "noop": False}


def _exec_chain_publish(p: dict, cfg) -> dict:
    from harvester.chainbuild import publish
    draft, chain_root, meta, db, tid = _chain_publish_args(p, cfg)
    r = publish(draft, chain_root, meta, db, apply=True,
                force=bool(p.get("force")),
                allow_audit=bool(p.get("allow_audit")))
    if not r["ok"] or not r["applied"]:
        raise WriteError("发布被拦下：%s" % ("；".join(r["blockers"])
                                            or "未知原因"))
    return {"summary": "链已发布进知识正本：%s（来自草稿 %s）"
                       "——主题页点「chain 演进链」即见" % (r["target"], draft),
            "path": r["target"], "draft": str(draft),
            "stages": (r["validate"].get("stats") or {}).get("stages", 0)}


WRITE_ACTIONS: dict[str, dict] = {
    "session_suppress": {"plan": _plan_session_suppress,
                         "execute": _exec_session_suppress},
    "session_unsuppress": {"plan": _plan_session_unsuppress,
                           "execute": _exec_session_unsuppress},
    "noise_register": {"plan": _plan_noise_register,
                       "execute": _exec_noise_register},
    "suggest_status": {"plan": _plan_suggest_status,
                       "execute": _exec_suggest_status},
    "card_review": {"plan": _plan_card_review,
                    "execute": _exec_card_review},
    "chain_audit_accept": {"plan": _plan_chain_audit_accept,
                           "execute": _exec_chain_audit_accept},
    "attrib_verdict": {"plan": _plan_attrib_verdict,
                       "execute": _exec_attrib_verdict},
    "topic_register": {"plan": _plan_topic_register,
                       "execute": lambda p, c: _exec_topic({**p, "__kind": "register"}, c)},
    "topic_add": {"plan": _plan_topic_add,
                  "execute": lambda p, c: _exec_topic({**p, "__kind": "add"}, c)},
    "topic_remove": {"plan": _plan_topic_remove,
                     "execute": lambda p, c: _exec_topic({**p, "__kind": "remove"}, c)},
    "topic_rename": {"plan": _plan_topic_rename,
                     "execute": lambda p, c: _exec_topic({**p, "__kind": "rename"}, c)},
    "topic_keywords": {"plan": _plan_topic_keywords,
                       "execute": lambda p, c: _exec_topic({**p, "__kind": "keywords"}, c)},
    "topic_merge": {"plan": _plan_topic_merge,
                    "execute": lambda p, c: _exec_topic({**p, "__kind": "merge"}, c)},
    "topic_delete": {"plan": _plan_topic_delete,
                     "execute": lambda p, c: _exec_topic({**p, "__kind": "delete"}, c)},
    "keywords_gc": {"plan": _plan_keywords_gc,
                    "execute": _exec_keywords_gc},
    "suggest_agents": {"plan": _plan_suggest_agents,
                       "execute": _exec_suggest_agents},
    "draft_pack": {"plan": _plan_draft,
                   "execute": _exec_draft},
    "cards_new": {"plan": _plan_cards_new,
                  "execute": _exec_cards_new},
    "kb_publish": {"plan": _plan_kb_publish,
                   "execute": _exec_kb_publish},
    "chain_publish": {"plan": _plan_chain_publish,
                      "execute": _exec_chain_publish},
}


def normalize_params(spec: dict, params: dict, cfg) -> dict:
    """按注册表的参数表规范化：必填校验 + 类型转换 + **丢弃未声明的键**。

    与 A 档 `_clean_params` 同一纪律（服务端自己过滤，不靠前端自觉），
    但 B 档的必填项更多（写错了要落库，代价比读大）。
    """
    out: dict = {}
    for p in spec["params"]:
        name = p["name"]
        server = p.get("server")
        if server:
            # 服务端已定的路径类参数：不取客户端值，由调用方在 execute 前注入
            continue
        val = params.get(name, p.get("default"))
        if p["kind"] == "bool":
            val = bool(val)
        elif p["kind"] == "int" and val not in (None, ""):
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise WriteError("%s 需要整数，收到 %r" % (name, val))
        elif val in (None, ""):
            val = None
        else:
            val = str(val)
        if p["required"] and val in (None, ""):
            raise WriteError("缺必填参数 %s（%s）" % (name, p.get("label") or ""))
        if val is not None:
            out[name] = val
    return out


def plan(action_id: str, spec: dict, params: dict, cfg) -> dict:
    """预演（只读）。返回含 human / before / after / snapshot / target_path。"""
    wa = WRITE_ACTIONS.get(action_id)
    if wa is None:
        raise WriteError("动作 %s 没有实现（注册表与实现漂移了）" % action_id)
    norm = normalize_params(spec, params, cfg)
    prev = wa["plan"](dict(norm), cfg)
    prev["action_id"] = action_id
    prev["cmd"] = spec.get("cmd")
    prev["subaction"] = spec.get("subaction")
    prev["risk"] = spec.get("risk")
    prev["target"] = spec.get("target")
    prev["params"] = norm
    # 目标库指纹：两段提交之间被改过就要重新预览
    prev["target_fp"] = _fingerprint(Path(prev["target_path"]))
    return prev


def execute(action_id: str, spec: dict, params: dict, cfg) -> dict:
    """真执行。调用方负责两段提交与台账（`actionsserve` 做）。"""
    wa = WRITE_ACTIONS.get(action_id)
    if wa is None:
        raise WriteError("动作 %s 没有实现（注册表与实现漂移了）" % action_id)
    return wa["execute"](dict(params), cfg)
