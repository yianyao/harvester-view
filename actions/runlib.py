# -*- coding: utf-8 -*-
"""runlib -- 动作台账（`actions_meta.db`）+ B 档两段提交（SOP P3-2 / D5 / §7.3）。

三个职责，都是**确定性**的：

1. **台账**：每次动作执行写一行 `action_runs`（谁/何时/动作/参数/耗时/结果
   摘要），B 档额外一行 `write_records`（写哪个库、改了什么、前后计数、
   destructive 时存**删除前快照**——快照是"可回滚"承诺的唯一凭据）。
2. **两段提交**：B 档第一次 POST 只**预演**（dry-run：跑一遍但不提交，
   拿真数字生成"将写什么"摘要）+ 发一次性 `confirm_token`；带 token 的第二次
   POST 才真执行。token 绑定 `action_id + 参数指纹 + 预览指纹`，默认 5 分钟过期。
3. **并发保护**：同一动作的"预览 → 执行"之间若 meta 库被别人改过，执行会被
   拒绝并要求重新预览——否则用户确认的和实际写的可能不是一回事。

**安全口径（诚实说明，不夸大）**：
- 本服务绑 127.0.0.1、无鉴权，`client` 字段是**调用方自报**的标签
  （默认 `web-ui`），**不是身份认证**。它的用途是"台账里能分辨是谁点的"，
  不是"防冒充"；
- token 不是权限凭据，是**意图确认**凭据（防误触 / 防脚本盲调）。
  跨机器暴露一律拒绝启动（沿用上游 api-serve 的 `_check_host` 口径）。
- 所有写只落 meta 库；`harvester.db` 全程 `mode=ro`（D7）。

写 SQL 一律走 sqlite3 默认连接（meta 库本就是可写的）；台账文件默认
`harvester-view/actions/actions_meta.db`，已进 .gitignore。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path

#: token 默认有效期（秒）。短到能防"预览页放着不管，两天后误点执行"，
#: 长到够人读一遍"将写什么"。
TOKEN_TTL_SEC = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_runs (
    run_id       TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    action_id    TEXT NOT NULL,
    tier         TEXT NOT NULL,
    client       TEXT,
    params_json  TEXT,
    elapsed_ms   INTEGER,
    status       TEXT NOT NULL,
    result_summary TEXT,
    error        TEXT,
    job_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_runs_ts ON action_runs(ts);
CREATE INDEX IF NOT EXISTS idx_action_runs_action ON action_runs(action_id);

CREATE TABLE IF NOT EXISTS write_records (
    run_id       TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    action_id    TEXT NOT NULL,
    target       TEXT NOT NULL,
    target_path  TEXT,
    risk         TEXT,
    cmd          TEXT,
    summary      TEXT,
    before_json  TEXT,
    after_json   TEXT,
    snapshot_json TEXT,
    client       TEXT
);
CREATE INDEX IF NOT EXISTS idx_write_records_ts ON write_records(ts);

CREATE TABLE IF NOT EXISTS confirms (
    token        TEXT PRIMARY KEY,
    action_id    TEXT NOT NULL,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    params_json  TEXT NOT NULL,
    preview_json TEXT NOT NULL,
    client       TEXT,
    consumed_at  TEXT,
    run_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_confirms_action ON confirms(action_id);
"""


def connect(path: Path) -> sqlite3.Connection:
    """打开台账库（建表，幂等）。台账是本仓自己的文件，不是只读对象。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    return con


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class ConfirmError(Exception):
    """token 相关失败（缺失/过期/已用/参数或预览被改）。"""


def params_fingerprint(action_id: str, params: dict) -> str:
    """参数指纹：动作 id + **排序后的**参数（值用 JSON 规范化）。
    排序是必需的——dict 顺序不该影响"是不是同一份请求"。"""
    blob = json.dumps({"action_id": action_id, "params": params},
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def preview_fingerprint(preview: dict) -> str:
    """预览指纹：把"将写什么"整份冻结进指纹。库在两段之间被改过 →
    预览数字变了 → 指纹不匹配 → 拒绝执行（要求重新预览）。"""
    blob = json.dumps(preview, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def issue_confirm(meta_db: Path, action_id: str, params: dict, preview: dict,
                  client: str, ttl: int = TOKEN_TTL_SEC) -> dict:
    """发一次性 token。返回 {token, expires_in, params_fp, preview_fp}。"""
    token = secrets.token_urlsafe(24)
    now = time.time()
    con = connect(meta_db)
    try:
        # 顺手清掉过期未用的（台账是本地小库，不需要定时任务）
        con.execute("DELETE FROM confirms WHERE expires_at < ? AND consumed_at "
                    "IS NULL", (now,))
        con.execute(
            "INSERT INTO confirms (token, action_id, created_at, expires_at, "
            "params_json, preview_json, client) VALUES (?,?,?,?,?,?,?)",
            (token, action_id, now, now + ttl,
             json.dumps(params, ensure_ascii=False, sort_keys=True, default=str),
             json.dumps(preview, ensure_ascii=False, sort_keys=True, default=str),
             client))
        con.commit()
    finally:
        con.close()
    return {"token": token, "expires_in": ttl,
            "params_fp": params_fingerprint(action_id, params),
            "preview_fp": preview_fingerprint(preview)}


def consume_confirm(meta_db: Path, token: str, action_id: str,
                    params: dict, preview: dict) -> None:
    """校验并**消费** token。任何不匹配都抛 ConfirmError（带人话原因）。"""
    if not token:
        raise ConfirmError("缺 confirm_token：B 档动作必须两段提交，"
                           "先用同一参数请求一次预览拿 token")
    con = connect(meta_db)
    try:
        row = con.execute("SELECT * FROM confirms WHERE token=?",
                          (token,)).fetchone()
        if row is None:
            raise ConfirmError("confirm_token 不存在（可能来自另一次服务进程，"
                              "或已被清理）——请重新预览")
        if row["action_id"] != action_id:
            raise ConfirmError("confirm_token 属于动作 %r，不是 %r"
                              % (row["action_id"], action_id))
        if row["consumed_at"]:
            raise ConfirmError("confirm_token 已用过（一次性凭据）——请重新预览")
        if float(row["expires_at"]) < time.time():
            raise ConfirmError("confirm_token 已过期（有效期 %d 秒）——请重新预览"
                              % TOKEN_TTL_SEC)
        want_p = params_fingerprint(action_id, params)
        got_p = params_fingerprint(action_id, json.loads(row["params_json"]))
        if want_p != got_p:
            raise ConfirmError("参数与预览时不一致——预览的是别的参数。"
                              "请用当前参数重新预览")
        want_v = preview_fingerprint(preview)
        got_v = preview_fingerprint(json.loads(row["preview_json"]))
        if want_v != got_v:
            raise ConfirmError(
                "库在两段之间变了（预览指纹不匹配：确认时看到的数字已经不等于"
                "现在的数字）——请**重新预览**再确认")
        con.execute("UPDATE confirms SET consumed_at=? WHERE token=?",
                    (_now(), token))
        con.commit()
    finally:
        con.close()


def record_run(meta_db: Path, *, run_id: str, action_id: str, tier: str,
               client: str, params: dict, elapsed_ms: int, status: str,
               result_summary: str = "", error: str = "",
               job_id: str = "") -> None:
    con = connect(meta_db)
    try:
        con.execute(
            "INSERT OR REPLACE INTO action_runs (run_id, ts, action_id, tier, "
            "client, params_json, elapsed_ms, status, result_summary, error, "
            "job_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, _now(), action_id, tier, client,
             json.dumps(params, ensure_ascii=False, sort_keys=True, default=str),
             int(elapsed_ms), status, result_summary[:2000], error[:2000],
             job_id))
        con.commit()
    finally:
        con.close()


def record_write(meta_db: Path, *, run_id: str, action_id: str, target: str,
                 target_path: str, risk: str, cmd: str, summary: str,
                 before: dict, after: dict, snapshot=None,
                 client: str = "") -> None:
    con = connect(meta_db)
    try:
        con.execute(
            "INSERT OR REPLACE INTO write_records (run_id, ts, action_id, "
            "target, target_path, risk, cmd, summary, before_json, after_json, "
            "snapshot_json, client) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, _now(), action_id, target, target_path, risk, cmd,
             summary[:2000],
             json.dumps(before, ensure_ascii=False, default=str),
             json.dumps(after, ensure_ascii=False, default=str),
             (json.dumps(snapshot, ensure_ascii=False, default=str)
              if snapshot is not None else None),
             client))
        con.commit()
    finally:
        con.close()


# ---------- 查询（给 /api/actions/ledger 与工作台用） ----------

def recent_runs(meta_db: Path, limit: int = 50,
                action_id: str | None = None) -> list[dict]:
    con = connect(meta_db)
    try:
        sql = ("SELECT run_id, ts, action_id, tier, client, params_json, "
               "elapsed_ms, status, result_summary, error, job_id "
               "FROM action_runs")
        args: tuple = ()
        if action_id:
            sql += " WHERE action_id=?"
            args = (action_id,)
        sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"
        rows = [dict(r) for r in con.execute(sql, args + (int(limit),))]
    finally:
        con.close()
    for r in rows:
        try:
            r["params"] = json.loads(r.pop("params_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            r["params"] = {}
    return rows


def recent_writes(meta_db: Path, limit: int = 50) -> list[dict]:
    con = connect(meta_db)
    try:
        rows = []
        for r in con.execute(
                "SELECT run_id, ts, action_id, target, target_path, risk, cmd, "
                "summary, before_json, after_json, snapshot_json, client "
                "FROM write_records ORDER BY ts DESC, rowid DESC LIMIT ?",
                (int(limit),)):
            d = dict(r)
            for key in ("before_json", "after_json", "snapshot_json"):
                try:
                    d[key[:-5]] = json.loads(d.pop(key) or "null")
                except (json.JSONDecodeError, TypeError):
                    d[key[:-5]] = None
            rows.append(d)
    finally:
        con.close()
    return rows


def ledger_stats(meta_db: Path) -> dict:
    """台账总览（工作台的"闭环健康"要用）。库不存在/无表 → 0，不抛。"""
    if not Path(meta_db).is_file():
        return {"runs": 0, "writes": 0, "failed": 0, "destructive": 0,
                "last_run": None, "last_write": None, "clients": []}
    con = connect(meta_db)
    try:
        runs = con.execute("SELECT COUNT(*) FROM action_runs").fetchone()[0]
        failed = con.execute("SELECT COUNT(*) FROM action_runs "
                             "WHERE status='failed'").fetchone()[0]
        writes = con.execute("SELECT COUNT(*) FROM write_records").fetchone()[0]
        destr = con.execute("SELECT COUNT(*) FROM write_records "
                            "WHERE risk='destructive'").fetchone()[0]
        last_run = con.execute("SELECT MAX(ts) FROM action_runs").fetchone()[0]
        last_write = con.execute("SELECT MAX(ts) FROM write_records").fetchone()[0]
        clients = [r[0] for r in con.execute(
            "SELECT DISTINCT client FROM action_runs WHERE client IS NOT NULL "
            "ORDER BY client")]
    finally:
        con.close()
    return {"runs": runs, "writes": writes, "failed": failed,
            "destructive": destr, "last_run": last_run,
            "last_write": last_write, "clients": clients}
