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

import json
import time
from pathlib import Path

from . import ensure_harvester_importable

_ERR = ensure_harvester_importable()
if _ERR is None:  # pragma: no cover - 缺上游时由调用方报 503
    from harvester import suggestmeta as _sm
    from harvester import topics as _tp
else:  # pragma: no cover
    _sm = _tp = None


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
    if status == "adopted":
        after["adopted_at"] = adopted_at or before.get("adopted_at") \
            or time.strftime("%Y-%m-%d %H:%M:%S")
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
                     % after["adopted_at"])
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


#: action_id → (plan, execute)。**这张表就是 B 档的"实现白名单"**：
#: `actions_registry.json` 声明了什么，这里必须一一对上（有测试比对）。
WRITE_ACTIONS: dict[str, dict] = {
    "suggest_status": {"plan": _plan_suggest_status,
                       "execute": _exec_suggest_status},
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
