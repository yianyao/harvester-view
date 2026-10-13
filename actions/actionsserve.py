# -*- coding: utf-8 -*-
"""actionsserve -- harvester-view 动作面（8766）+ 本地只读数据面。

工程依据：SOP §0 冻结决策 D1/D3（三进程架构；actions-serve 落在
`harvester-view/actions/actionsserve.py`；8766 归本服务）、D7（只读铁律：
harvester.db 一律 mode=ro；写只落 meta 库）、D9（P1 的 viewserver 在此归并）。

**归并（P2-1）**：P1 的 `viewserver.py` 端点（`/view/cards` /
`/view/glossary` / `/view/health`）原样并入本服务——路径、响应结构、
CORS 头、POST 405 全部不变（additive）；顶层 `viewserver.py` 退化为
**转发薄壳**（起服函数直接指过来），既有测试与 `start-viewv2.cmd` 不动。

本服务新增（P2）：
- `GET /api/health`           自检 + **版本握手**（上报 harvester.__version__
                              与 commands.json 的适配版本，D4）；
- `GET /api/commands`         静态命令注册表（§5.4 动词卡 + §5.5 参数数据源）；
- `GET /api/actions`          A 档动作注册表（冻结白名单，非万能 shell，§5.6）；
- `POST /api/actions/run`     **入队**执行一个白名单动作（进程内线程调
                              harvester 函数，非 shell），立刻返回 job_id；
- `GET /api/actions/job/<id>` 轮询作业状态/进度/结果（长任务不阻塞页面）；
- `GET /api/topic/<id>/timeline?level=`  五档时间线透出（P2-3）；
- `GET /api/pattern-sessions` 错误模式 → 会话清单 + 逐条 `resolved`
                              （P2-5 数字皆门的第二跳，与 G2 报告同源同库）。

只读纪律：所有 SQL 走 `_open_ro`（`mode=ro` + 内核级 authorizer 双保险，
与上游 apiserve 同款）；本模块对 session-harvester 只 import 不改。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import traceback
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from . import ensure_harvester_importable, upstream_root
from . import runlib
from . import writeactions as wa

# 上游 import 必须在放进 sys.path 之后（缺上游时保持 None，端点诚实降级）
_HARVESTER_IMPORT_ERROR = ensure_harvester_importable()
if _HARVESTER_IMPORT_ERROR is None:  # pragma: no branch - 缺上游时走 else
    import harvester  # noqa: E402
    from harvester import apiserve as _hapi  # noqa: E402
    from harvester import errstats as _herr  # noqa: E402
    from harvester import topics as _htopics  # noqa: E402
    from harvester.dbmeta import db_fingerprint  # noqa: E402
else:  # pragma: no cover - 只在没有兄弟仓库的环境触发
    harvester = None
    _hapi = _herr = _htopics = None
    db_fingerprint = None

#: 本服务端点契约版本（view 前端据此握手；与 harvester 的 api_version 无关）
VIEW_ACTIONS_API = 1
#: 冻结端口（SOP §0 D1）：8765=apiserve，8766=本服务
DEFAULT_PORT = 8766
_HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = _HERE.parent
DEFAULT_COMMANDS_JSON = _HERE / "commands.json"
DEFAULT_REGISTRY_JSON = _HERE / "actions_registry.json"
DEFAULT_ACTIONS_META = _HERE / "actions_meta.db"
DEFAULT_CARDS_ROOT = Path.home() / ".workbuddy" / "knowledge" / "cards"

#: 动作执行线程池上限：命令台是人点的，不会并发几十个；超限直接拒绝而不是排队
MAX_JOBS = 32

# ---------- 只读数据库（口径与上游 apiserve 同款，双保险） ----------

_SQLITE_OK = getattr(sqlite3, "SQLITE_OK", 0)
_SQLITE_SELECT = getattr(sqlite3, "SQLITE_SELECT", 21)
_SQLITE_READ = getattr(sqlite3, "SQLITE_READ", 20)
_SQLITE_FUNCTION = getattr(sqlite3, "SQLITE_FUNCTION", 31)
_SQLITE_PRAGMA = getattr(sqlite3, "SQLITE_PRAGMA", 19)
_SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 34)


def _readonly_authorizer(action, arg1, arg2, db_name, trigger):
    """内核级只读白名单：SELECT/READ/函数/递归子查询放行；PRAGMA 仅放行
    sqlite3 驱动自用的 data_version，其余（含一切写动作）一律 deny。"""
    if action in (_SQLITE_SELECT, _SQLITE_READ, _SQLITE_FUNCTION,
                  _SQLITE_RECURSIVE):
        return _SQLITE_OK
    if action == _SQLITE_PRAGMA and arg1 == "data_version":
        return _SQLITE_OK
    return sqlite3.SQLITE_DENY


def open_ro(db: Path) -> sqlite3.Connection:
    """物理只读连接（mode=ro + authorizer）。写尝试会在内核层被拒。"""
    con = sqlite3.connect("file:%s?mode=ro" % Path(db).as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    con.set_authorizer(_readonly_authorizer)
    return con


# ---------- 运行期配置 ----------

class Config:
    """本服务运行期配置（起服时定死；命令台参数区**不暴露**任何路径类参数，
    SOP §5 红线）。"""

    def __init__(self, db: Path, cards_root: Path, glossary: Path,
                 topics_meta: Path | None = None,
                 artifacts_meta: Path | None = None,
                 chain_root: Path | None = None,
                 keywords_meta: Path | None = None,
                 suggestions_meta: Path | None = None,
                 exports_root: Path | None = None,
                 commands_json: Path | None = None,
                 corpus_watermark: bool = False,
                 registry_json: Path | None = None,
                 actions_meta: Path | None = None):
        self.db = Path(db)
        self.cards_root = Path(cards_root)
        self.glossary = Path(glossary)
        self.topics_meta = Path(topics_meta) if topics_meta else None
        self.artifacts_meta = (Path(artifacts_meta) if artifacts_meta
                               else None)
        self.chain_root = Path(chain_root) if chain_root else None
        self.keywords_meta = (Path(keywords_meta) if keywords_meta else None)
        self.suggestions_meta = (Path(suggestions_meta) if suggestions_meta
                                 else None)
        self.exports_root = Path(exports_root) if exports_root else None
        self.commands_json = Path(commands_json or DEFAULT_COMMANDS_JSON)
        #: 动作注册表（P3-1）与动作台账库（P3-2）
        self.registry_json = Path(registry_json or DEFAULT_REGISTRY_JSON)
        self.actions_meta = Path(actions_meta or DEFAULT_ACTIONS_META)
        #: 是否在 timeline 端点里附带库龄判定（`stale`）。缺省 **关**：
        #: 它要扫一遍语料盘（实测 0.08 s，但盘在网络上/语料巨大时会变慢），
        #: P2-3 的验收是"无 chain 主题也能看时间线"，不该被库龄判定拖住。
        self.want_watermark = bool(corpus_watermark)
        self._corpus_mtime: float | None = None
        self._wm_done = False

    def corpus_mtime(self) -> float | None:
        """当前语料最新 mtime（惰性、只算一次）。**基准是"这次的盘"**，
        与库里记录的"上次索引看到的"（`corpus_indexed_mtime`）对比才得到
        `stale`——这正是上游 H104 纠正过的口径（库文件 mtime 会给出假 STALE）。"""
        if not self.want_watermark:
            return None
        if not self._wm_done:
            self._wm_done = True
            try:
                from harvester.outline import corpus_watermark
                self._corpus_mtime = corpus_watermark().get("mtime")
            except Exception:  # noqa: BLE001 - 库里判定失败不该拖垮时间线
                self._corpus_mtime = None
        return self._corpus_mtime

    def default_for(self, name: str) -> Path | None:
        """参数缺省值的数据源（服务端已定，故页面不暴露路径类参数）。"""
        return {"db": self.db,
                "cards_root": self.cards_root,
                "topics_meta": self.topics_meta,
                "artifacts_meta": self.artifacts_meta,
                "chain_root": self.chain_root,
                "keywords_meta": self.keywords_meta,
                "suggestions_meta": self.suggestions_meta,
                "exports_root": self.exports_root}.get(name)


# ---------- 动作注册表（A 档，冻结白名单） ----------

def _action_report_tools(cfg: Config, params: dict) -> dict:
    """A 档：G1 工具统计（进程内调 apiserve.api_reports_tools，与 /api/reports
    /tools 同源同口径——页面与动作面不可能算出两个数）。"""
    days = _num(params.get("days"))
    con = open_ro(cfg.db)
    try:
        return _hapi.api_reports_tools(con, cfg.db, _days_params(days))
    finally:
        con.close()


def _action_report_errors(cfg: Config, params: dict) -> dict:
    """A 档：G2 错误三分类（同理，与 /api/reports/errors 同源）。"""
    days = _num(params.get("days"))
    con = open_ro(cfg.db)
    try:
        return _hapi.api_reports_errors(con, cfg.db, _days_params(days))
    finally:
        con.close()


def _action_topic_list(cfg: Config, params: dict) -> dict:
    """A 档：主题注册表清单（走 topics_meta.db 只读；无 meta 时诚实空表）。"""
    if cfg.topics_meta is None or not cfg.topics_meta.is_file():
        return {"view_actions_api": VIEW_ACTIONS_API, "topics": [],
                "hint": "topics_meta 未配置或不存在：起服时加 --topics-meta"}
    con = open_ro(cfg.topics_meta)
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT id, name, keywords, members, created FROM topics ORDER BY id")]
    finally:
        con.close()
    topics = []
    for r in rows:
        try:
            members = json.loads(r["members"] or "[]")
        except (json.JSONDecodeError, TypeError):
            members = []
        topics.append({"id": r["id"], "name": r["name"],
                       "keywords": r["keywords"] or "",
                       "members_count": len(members),
                       "created": r["created"]})
    return {"view_actions_api": VIEW_ACTIONS_API, "topics": topics}


def _action_topic_pack(cfg: Config, params: dict) -> dict:
    """A 档：主题蒸馏包（`topic pack` 的进程内版，2026-10-13 用户要求：
    主题页分层选择后直接跳命令台执行，不用手抄命令）。

    只读口径：`build_topic_packet` 只读 harvester.db / topics_meta.db /
    artifacts_meta.db，产物**返回给页面**（不落盘、不给 --out）——
    与 regress 同款「重活也是只读」的 A 档判据。参数校验对齐
    `topic_timeline`（400=调用姿势 / 503=环境缺配，两类不混淆）。"""
    if _htopics is None:
        raise RuntimeError(_HARVESTER_IMPORT_ERROR or "上游 harvester 不可用")
    topic_id = str(params.get("id") or "").strip()
    level = str(params.get("level") or "title")
    sid = str(params.get("sid") or "").strip() or None
    max_chars = params.get("max_chars")
    if not topic_id:
        raise _BadRequest("缺主题 id（--id）——回主题页点一个主题再跳过来")
    if level not in _htopics.LEVELS:
        raise _BadRequest("level=%r 不在 %s" % (level, list(_htopics.LEVELS)))
    if level in ("fine", "artifact") and not sid:
        raise _BadRequest("level=%s 为按需档：必须给 sid（成员会话 id）"
                          % level)
    if cfg.topics_meta is None:
        raise RuntimeError("未配置 topics_meta（起服时加 --topics-meta）")
    if level == "artifact" and not cfg.artifacts_meta:
        raise _BadRequest("level=artifact 需要 --artifacts-meta"
                          "（先跑 artifacts extract --sid <sid>）")
    try:
        packet = _htopics.build_topic_packet(
            cfg.db, cfg.topics_meta, topic_id, level=level, sid=sid,
            artifacts_db=cfg.artifacts_meta,
            max_chars=int(max_chars) if max_chars else None)
    except KeyError as e:  # show_topic 未命中 → 主题不存在
        raise _NotFound("主题 %s 不存在（KeyError %s）" % (topic_id, e))
    return {"view_actions_api": VIEW_ACTIONS_API, "topic_id": topic_id,
            "level": level, "sid": sid, "chars": len(packet),
            "packet": packet}


#: `regress` 允许的语料变体（冻结：与上游 `regress.CORPUS_VARIANTS` 同集合，
#: 见 `_action_regress` 的用法）。写在这里是为了让"允许值"在本仓可见可测。
_REGRESS_CORPORA = frozenset({"default", "drift"})


def _rm_tree(path: Path) -> None:
    """删掉动作自己造的临时目录（失败不抛：清理是收尾动作，不该盖住结果）。"""
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001 - 见 docstring
        pass


def _action_regress(cfg: Config, params: dict) -> dict:
    """A 档：端到端回归（**唯一的重活**，实测 ~26 s）。

    为什么它算 A 档（只读）而不是 B 档：`regress` 全程只在临时目录里用
    `indexing.SCHEMA` 造库（上游 docstring 明说"真 harvester.db /
    topics_meta.db 一字节不动"），既不写采集库也不写 meta 库。命令台把它
    跑起来，等于替人省掉一次"复制命令 + 开终端 + 看退出码"。"""
    from harvester.regress import (EXIT_FAIL, EXIT_NOT_RUN, EXIT_OK,
                                   corpus_by_name, run_regress)
    corpus = str(params.get("corpus") or "default")
    if corpus not in _REGRESS_CORPORA:
        raise _BadRequest("corpus 只支持 %s，收到 %r"
                          % (" / ".join(sorted(_REGRESS_CORPORA)), corpus))
    # temp_base 归 server：临时目录父目录由服务端定，页面不指路。**不接受
    # 客户端传路径**——那正是"顺手指向真库"的后门（上游 cmd_regress 特意
    # 不给 --db/--meta 就是为了堵它）。用 mkdtemp：CWD 可能是只读仓库。
    import tempfile
    base = Path(tempfile.mkdtemp(prefix="view-action-regress-"))
    try:
        r = run_regress(base, corpus=corpus_by_name(corpus),
                        keep_temp=bool(params.get("keep_temp")))
    finally:
        if not params.get("keep_temp"):
            _rm_tree(base)
    # 步骤字典的键是 **step**（`Step.to_dict`），不是 name——认错键的后果是
    # 页面上 8 个步骤全显示空名（首版实测如此，已改正）
    steps = r.get("steps") or []
    failed = [s for s in steps if s.get("status") == "fail"]
    code = (EXIT_OK if not failed and r.get("failed_assertions", 0) == 0
            else EXIT_FAIL)
    return {"view_actions_api": VIEW_ACTIONS_API,
            "corpus": corpus,
            "exit_code": code,
            "assertions": r.get("assertions"),
            "failed_assertions": r.get("failed_assertions"),
            "steps_total": len(steps),
            "steps_failed": len(failed),
            "steps_not_run": len([s for s in steps
                                  if s.get("status") == "not_run"]),
            "steps": [{"step": s.get("step"), "what": s.get("what"),
                       "status": s.get("status"), "why": s.get("why"),
                       "failed_checks": s.get("failed") or []}
                      for s in steps],
            "exit_codes": {"ok": EXIT_OK, "fail": EXIT_FAIL,
                           "not_run": EXIT_NOT_RUN},
            "summary": r.get("summary")}


#: A 档动作白名单。**冻结**：执行器只认这张表（SOP §5.3 红线——新增的不是
#: 任意执行，而是一张冻结的动作注册表）。
ACTIONS: dict[str, dict] = {
    "report_tools": {
        "tier": "A",
        "label": "工具体检（G1 工具统计）",
        "desc": "工具调用数/失败率/重试与放弃，与报告页 G1 同源同口径。",
        "params": {"days": {"type": "days", "src": "chips", "default": None,
                            "label": "时间窗"}},
        "est_sec": 2,
        "reversible": "幂等（可重跑覆盖，不写任何库）",
        "writes": "",
        "handler": _action_report_tools,
    },
    "report_errors": {
        "tier": "A",
        "label": "错误验尸（G2 错误三分类）",
        "desc": "错误三分类 + 归一模式聚类 + 轨迹分桶，与报告页 G2 同源同口径。",
        "params": {"days": {"type": "days", "src": "chips", "default": None,
                            "label": "时间窗"}},
        "est_sec": 3,
        "reversible": "幂等（可重跑覆盖，不写任何库）",
        "writes": "",
        "handler": _action_report_errors,
    },
    "topic_list": {
        "tier": "A",
        "label": "主题档案（注册表清单）",
        "desc": "主题注册表：成员数/关键词/创建时间。",
        "params": {},
        "est_sec": 1,
        "reversible": "幂等（只读 topics_meta.db）",
        "writes": "",
        "handler": _action_topic_list,
    },
    "topic_pack": {
        "tier": "A",
        "cmd": "topic",
        "subaction": "pack",
        "label": "主题蒸馏包（topic pack）",
        "desc": "按分层档生成主题蒸馏包（title/coarse/mid/fine/artifact），"
                "进程内只读生成、不落盘。fine/artifact 档需给成员 sid。",
        "params": {"id": {"type": "topic_id", "src": "topics_api",
                          "default": None, "label": "主题 id"},
                   "level": {"type": "level", "src": "chips",
                             "default": "title",
                             "choices": ["title", "coarse", "mid", "fine",
                                         "artifact"],
                             "label": "分层"},
                   "sid": {"type": "sid", "src": "sessions_api",
                           "default": None,
                           "label": "成员 sid（fine/artifact 档必填）"},
                   "max_chars": {"type": "int", "src": "chips",
                                 "default": None,
                                 "label": "覆盖字符预算（可选）"}},
        "est_sec": 3,
        "reversible": "幂等（只读三库，产物返回页面不落盘）",
        "writes": "",
        "handler": _action_topic_pack,
    },
    "regress": {
        "tier": "A",
        "label": "端到端回归（重活）",
        "desc": "从空库跑完整链路并逐项核对数字；只用临时库，真库一字节不动。",
        "params": {"corpus": {"type": "text", "src": "chips",
                              "default": "default",
                              "choices": ["default", "drift"],
                              "label": "语料变体"},
                   "temp_base": {"type": "path", "src": "server",
                                 "default": None,
                                 "label": "临时目录父目录（服务端）"}},
        "est_sec": 3,
        "reversible": "幂等（全程临时目录，跑完即删）",
        "writes": "",
        "handler": _action_regress,
    },
}


def _num(v) -> float | None:
    if v in (None, "", "null"):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("不是数字：%r" % (v,))
    return f


def _days_params(days: float | None) -> dict:
    """days → 上游 apiserve 的 params 形状。

    口径提醒（P1 踩过的坑）：上游**缺省 = 全库**，`days=0` 是「0 天窗口」
    ≠ 全库。故 None 时**不传 key**，而不是传 0。
    """
    return {} if days is None else {"days": str(days)}


def _clean_params(action: dict, raw: dict, cfg: Config | None = None) -> dict:
    """按动作声明的 params 过滤 + 类型校验（**只认声明过的键**，多余键丢弃
    ——命令台传来的任何东西都不会变成 argparse 风格的自由参数）。

    src="server_choice" 的参数：值由**服务端**决定（页面只读展示，不给输入
    框），客户端传什么都被忽略——否则"页面不暴露路径"就只是前端自觉。
    """
    out: dict = {}
    for name, spec in (action.get("params") or {}).items():
        if spec.get("src") == "server_choice":
            out[name] = spec.get("default")
            continue
        if name not in raw:
            out[name] = spec.get("default")
            continue
        val = raw[name]
        kind = spec.get("type")
        if kind == "days":
            out[name] = _num(val)
        elif kind == "int":
            try:
                out[name] = int(val)
            except (TypeError, ValueError):
                raise ValueError("%s 需要整数，收到 %r" % (name, val))
        elif kind in ("text", "sid", "topic_id", "level", "path"):
            out[name] = str(val)
        else:  # pragma: no cover - 注册表写错时 fail loud
            raise ValueError("动作参数类型未定义：%s=%r" % (name, kind))
    return out


# ---------- 作业（长任务不阻塞页面：SOP §5 红线） ----------

class JobStore:
    """进程内作业台账（线程安全）。**不落盘**：动作面自身的历史台账
    （actions_meta.db）属 P3-2，本阶段只保证"页面可关、回来还能查"。

    收尾口径（诚实）：线程无法被安全杀死，`cancel()` 只把状态标记为
    cancelled，**底层查询仍会跑完**——所以界面不提供"取消"，只提供
    "关掉页面"（后台继续，回来看结果）。
    """

    def __init__(self, max_jobs: int = MAX_JOBS):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._max = max_jobs

    def submit(self, fn, label: str) -> dict:
        with self._lock:
            if len(self._jobs) >= self._max:
                done = [k for k, j in self._jobs.items()
                        if j["status"] in ("done", "failed")]
                for k in done[:max(1, len(done) // 2)]:
                    self._jobs.pop(k, None)
                if len(self._jobs) >= self._max:
                    raise RuntimeError("作业过多（上限 %d）：先等正在跑的完成"
                                       % self._max)
            jid = uuid.uuid4().hex[:12]
            job = {"job_id": jid, "label": label, "status": "queued",
                   "started_at": None, "ended_at": None, "elapsed_ms": None,
                   "result": None, "error": None, "progress": ""}
            self._jobs[jid] = job

        def _run():
            t0 = _now()
            self._patch(jid, status="running", started_at=_iso(),
                        progress="正在执行（进程内调用 harvester，非 shell）")
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - 作业边界：任何异常都要可见
                self._patch(jid, status="failed", error=str(e),
                            traceback=traceback.format_exc()[-2000:],
                            ended_at=_iso(), elapsed_ms=int((_now() - t0) * 1000))
                return
            self._patch(jid, status="done", result=res, ended_at=_iso(),
                        progress="", elapsed_ms=int((_now() - t0) * 1000))

        threading.Thread(target=_run, name="action-" + jid,
                         daemon=True).start()
        return dict(job)

    def _patch(self, jid: str, **kw) -> None:
        with self._lock:
            if jid in self._jobs:
                self._jobs[jid].update(kw)

    def get(self, jid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(jid)
            return dict(j) if j else None

    def listing(self) -> list[dict]:
        with self._lock:
            return [{"job_id": j["job_id"], "label": j["label"],
                     "status": j["status"], "started_at": j["started_at"],
                     "elapsed_ms": j["elapsed_ms"]}
                    for j in self._jobs.values()]


def _now() -> float:
    """单调-ish 的墙上时间（秒）。只用于算耗时差，不做时间戳。"""
    import time
    return time.time()


def _now_ms() -> int:
    return int(_now() * 1000)


def _iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------- /api/health：版本握手（D4） ----------

def load_commands(path: Path) -> dict:
    """读静态命令注册表。损坏 → 抛错（fail loud，由 health/commands 端点
    渲染成黄条，绝不静默给一个空表）。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_registry(path: Path) -> dict:
    """读动作注册表（P3-1 生成物）。损坏 → 抛错，由端点渲染成 500。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def registry_specs(cfg: Config) -> dict[str, dict]:
    """注册表 → {action_id: 条目}（A 与 B 合并）。**执行器只认这里**：
    注册表里没有的动作一律 403（SOP §5.3：不是万能 shell）。"""
    reg = load_registry(cfg.registry_json)
    out: dict[str, dict] = {}
    for a in reg.get("read_actions") or []:
        out[a["action_id"]] = {**a, "tier": a.get("tier") or "A"}
    for a in reg.get("write_actions") or []:
        out[a["action_id"]] = {**a, "tier": a.get("tier") or "B"}
    return out


def check_registry_consistency(reg: dict) -> list[str]:
    """注册表 ↔ 实现的一致性（起服时自检 + 套件里有同样的断言）。

    三类漂移都会让"页面显示能做、实际做不到"：
    1. 注册表声明的 B 档动作在 `writeactions.WRITE_ACTIONS` 里没有实现；
    2. 实现了但注册表没声明（页面上根本不出现，白写）；
    3. 同一个 action_id 同时出现在 A/B 两档（权限归属自相矛盾）。
    """
    problems: list[str] = []
    a_ids = {a["action_id"] for a in reg.get("read_actions") or []}
    b_ids = {a["action_id"] for a in reg.get("write_actions") or []}
    impl = set(wa.WRITE_ACTIONS)
    for aid in sorted(b_ids - impl):
        problems.append("注册表声明了 B 档动作 %s，但 writeactions 里没有实现"
                        % aid)
    for aid in sorted(impl - b_ids):
        problems.append("writeactions 实现了 %s，但注册表没声明（页面上不会出现）"
                        % aid)
    for aid in sorted(a_ids - set(ACTIONS)):
        problems.append("注册表声明了 A 档动作 %s，但 actionsserve 里没有实现"
                        % aid)
    for aid in sorted(set(ACTIONS) - a_ids):
        problems.append("actionsserve 实现了 A 档动作 %s，但注册表没声明" % aid)
    for aid in sorted(a_ids & b_ids):
        problems.append("动作 %s 同时被声明为 A 与 B 档（权限自相矛盾）" % aid)
    return problems


def health(cfg: Config) -> dict:
    """自检 + 版本握手。`version_ok` 是**唯一**的握手判据：
    harvester.__version__ == commands.json.adapter_version（D4：不匹配 →
    命令台黄条 + 禁用 B 档）。"""
    adapter_version = None
    cmd_error = None
    try:
        adapter_version = load_commands(cfg.commands_json).get("adapter_version")
    except (OSError, json.JSONDecodeError, ValueError) as e:
        cmd_error = "commands.json 不可读/损坏（fail loud）: %s" % e
    reg_error = None
    reg_counts = {"read_actions": 0, "write_actions": 0}
    reg_problems: list[str] = []
    try:
        reg = load_registry(cfg.registry_json)
        reg_counts = {"read_actions": len(reg.get("read_actions") or []),
                      "write_actions": len(reg.get("write_actions") or [])}
        reg_problems = check_registry_consistency(reg)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        reg_error = "actions_registry.json 不可读/损坏（fail loud）: %s" % e
    hv = getattr(harvester, "__version__", None) if harvester else None
    version_ok = bool(hv and adapter_version and hv == adapter_version)
    return {
        "ok": (_HARVESTER_IMPORT_ERROR is None and cmd_error is None
               and reg_error is None and not reg_problems),
        "view_actions_api": VIEW_ACTIONS_API,
        "service": "harvester-view 动作面 + 本地只读数据面",
        # 归并自 P1 viewserver 的三项（既有字段一个不少）：
        "cards_root": str(cfg.cards_root),
        "cards_root_exists": cfg.cards_root.is_dir(),
        "glossary": str(cfg.glossary),
        "glossary_exists": cfg.glossary.exists(),
        # P2 新增（additive）：
        "harvester_version": hv,
        "commands_adapter_version": adapter_version,
        "version_ok": version_ok,
        "upstream_root": str(upstream_root()),
        "upstream_error": _HARVESTER_IMPORT_ERROR,
        "commands_error": cmd_error,
        "db": str(cfg.db),
        "db_exists": cfg.db.is_file(),
        "topics_meta": str(cfg.topics_meta) if cfg.topics_meta else None,
        "artifacts_meta": (str(cfg.artifacts_meta)
                           if cfg.artifacts_meta else None),
        "port": DEFAULT_PORT,
        # P3 新增（additive）：动作注册表与台账
        "registry": str(cfg.registry_json),
        "registry_error": reg_error,
        "registry_counts": reg_counts,
        "registry_problems": reg_problems,
        "actions_meta": str(cfg.actions_meta),
        "actions_meta_ledger": (runlib.ledger_stats(cfg.actions_meta)
                                if cfg.actions_meta else None),
        # 用户目录：**给页面做路径缩写用**（展示面不印本机绝对路径，用户 2026-10-12
        # 要求；前端把 home 前缀换成 `~`）。这是路径不是凭据，回环服务本就可信。
        "home": str(Path.home()),
    }


# ---------- 时序：P2-3 timeline 透出 ----------

def topic_timeline(cfg: Config, topic_id: str, level: str,
                   sid: str | None) -> dict:
    # 参数校验放最前：非法参数是**调用姿势**问题（400），不能因为环境缺
    # topics_meta（503）而被吞掉——否则"参数写错了"会伪装成"服务缺能力"。
    if _htopics is None:
        raise RuntimeError(_HARVESTER_IMPORT_ERROR or "上游 harvester 不可用")
    if level not in _htopics.LEVELS:
        raise _BadRequest("level=%r 不在 %s"
                          % (level, list(_htopics.LEVELS)))
    if level in ("fine", "artifact") and not sid:
        raise _BadRequest("level=%s 为按需档：必须给 sid" % level)
    if cfg.topics_meta is None:
        raise RuntimeError("未配置 topics_meta（起服时加 --topics-meta）")
    if level == "artifact" and not cfg.artifacts_meta:
        raise _BadRequest("level=artifact 需要 --artifacts-meta"
                          "（先跑 artifacts extract --sid <sid>）")
    try:
        d = _htopics.timeline(cfg.topics_meta, topic_id, db=cfg.db,
                              level=level, sid=sid,
                              artifacts_db=cfg.artifacts_meta)
    except KeyError as e:  # show_topic 未命中 → 主题不存在
        raise _NotFound("主题 %s 不存在（KeyError %s）" % (topic_id, e))
    out = dict(d)
    # 库龄（可选）：上游 timeline 内部调 db_fingerprint 时默认不判库龄
    # （stale=None）。要看库龄就得在**外圈**补算并覆写——只动本仓的响应，
    # 不碰上游函数签名。
    cm = cfg.corpus_mtime()
    if cm is not None and isinstance(out.get("db_fingerprint"), dict):
        fp = dict(out["db_fingerprint"])
        water = fp.get("corpus_indexed_mtime")
        fp["corpus_mtime"] = _fmt_mtime(cm)
        fp["stale"] = None
        if water:
            fp["stale"] = _fmt_mtime(cm) > water  # 字符串比较=时间序（同口径）
        out["db_fingerprint"] = fp
    out["view_actions_api"] = VIEW_ACTIONS_API
    out["topic_id"] = topic_id
    return out


def _fmt_mtime(epoch: float) -> str:
    """epoch 秒 → 'YYYY-MM-DD HH:MM:SS'（与 dbmeta 同格式，便于字符串比较）。"""
    from datetime import datetime
    return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")


class _BadRequest(Exception):
    """参数/输入问题 → 400（与 500 区分：前者是调用姿势，后者是服务坏了）。"""


class _NotFound(Exception):
    """目标不存在 → 404。"""


# ---------- P2-5：错误模式 → 会话清单（三跳的第二跳，数字可对账） ----------

def pattern_sessions(cfg: Config, pattern: str,
                     days: float | None = None) -> dict:
    """给定归一模式，列出**全部**命中会话与逐条 resolved。

    数字口径（P2-5 验收：沿途数字逐级对账一致）：
    - `count` 与 `occurrences` 均为该模式在全库（或时间窗内）的错误条数，
      算法与 `/api/reports/errors` 的 patterns[].count **同源**：
      都是 `errstats.normalize_error` 归一后计数 → 必须相纸。
    - `resolved` 与 `given_up` 复刻上游 errstats 的口径（"该错误之后同会话
      同工具是否再次被调用"），不新发明第二套判据。
    """
    if pattern is None or pattern == "":
        raise _BadRequest("缺 pattern 参数")
    con = open_ro(cfg.db)
    try:
        errors, meta = _herr.collect_errors_from_db(
            cfg.db, since_days=days, con=con)
        calls: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(list))
        for r in con.execute("SELECT sid, tool, seq FROM steps "
                             "WHERE phase='call' ORDER BY sid, seq"):
            calls[r["sid"]][r["tool"]].append(r["seq"])
    finally:
        con.close()

    def _resolved(sid: str, tool: str, seq: int) -> bool:
        seqs = calls.get(sid, {}).get(tool, [])
        return any(s > seq for s in seqs)

    hits = [e for e in errors if e.get("pattern") == pattern]
    by_sid: dict[str, dict] = {}
    for e in hits:
        row = by_sid.setdefault(e["sid"], {
            "sid": e["sid"], "title": "", "occurrences": 0,
            "given_up": 0, "first_seq": e["seq"], "sample_seq": e["seq"],
            "sample_error": e.get("error") or "",
            "tools": set(), "skipped_no_turn": 0})
        row["occurrences"] += 1
        row["tools"].add(e["tool"])
        if not _resolved(e["sid"], e["tool"], e["seq"]):
            row["given_up"] += 1
    # 会话标题（一次批量取，避免 N 次查询）
    if by_sid:
        con = open_ro(cfg.db)
        try:
            for sid, row in by_sid.items():
                r = con.execute("SELECT title, source, updated_at FROM sessions "
                                "WHERE sid=?", (sid,)).fetchone()
                if r is not None:
                    row["title"] = r["title"] or ""
                    row["source"] = r["source"] or ""
                    row["updated_at"] = r["updated_at"] or ""
        finally:
            con.close()

    sessions = []
    for row in by_sid.values():
        row["tools"] = sorted(row["tools"])
        sessions.append(row)
    sessions.sort(key=lambda r: (-r["occurrences"], r["sid"]))
    occurrence_total = sum(r["occurrences"] for r in sessions)
    return {
        "view_actions_api": VIEW_ACTIONS_API,
        "pattern": pattern,
        "days": days,
        "count": occurrence_total,
        "occurrences": occurrence_total,
        "sessions_count": len(sessions),
        "given_up": sum(r["given_up"] for r in sessions),
        "sessions": sessions,
        "meta": meta,
    }


# ---------- HTTP ----------

def make_handler(cfg: Config):
    """生成 Handler 类（闭包携带配置，测试用随机端口起停）。"""
    jobs = JobStore()

    class ActionsHandler(BaseHTTPRequestHandler):
        server_version = "harvester-view-actions/1"

        def log_message(self, fmt, *args):  # noqa: N802
            print("[actionsserve] %s %s" % (self.address_string(), fmt % args),
                  file=sys.stderr)

        # -- 响应工具（CORS 同 P1 viewserver：页面 8088 → 本服务 8766 跨源） --
        def _headers(self, status: int, length: int, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _json(self, obj, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._headers(status, len(body),
                          "application/json; charset=utf-8")
            self.wfile.write(body)

        def do_OPTIONS(self):  # noqa: N802（跨源预检）
            self._headers(204, 0, "text/plain")

        # -- GET --
        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            path = u.path
            try:
                if path == "/view/cards":
                    self._json(_cards_payload(cfg))
                    return
                if path == "/view/glossary":
                    self._glossary()
                    return
                if path in ("/view/health", "/api/health"):
                    self._json(health(cfg))
                    return
                if path == "/api/commands":
                    self._commands()
                    return
                if path == "/api/actions":
                    self._json(self._registry_payload())
                    return
                if path == "/api/actions/ledger":
                    self._ledger(qs)
                    return
                m = re.fullmatch(r"/api/actions/job/([0-9a-f]+)", path)
                if m:
                    job = jobs.get(m.group(1))
                    if job is None:
                        self._json({"error": "作业 %s 不存在（进程重启会清空；"
                                             "动作面不落盘）" % m.group(1)}, 404)
                    else:
                        self._json(job)
                    return
                m = re.fullmatch(r"/api/topic/(.+)/timeline", path)
                if m:
                    self._timeline(unquote(m.group(1)), qs)
                    return
                if path == "/api/pattern-sessions":
                    self._pattern(qs)
                    return
                if path == "/api/overview":
                    self._overview()
                    return
                if path == "/api/suppressed":
                    self._suppressed()
                    return
                if path == "/api/workitems":
                    self._workitems(qs)
                    return
                if path == "/":
                    self._json({
                        "service": "harvester-view 动作面 + 本地只读数据面",
                        "view_actions_api": VIEW_ACTIONS_API,
                        "endpoints": [
                            "/view/cards", "/view/glossary", "/view/health",
                            "/api/health", "/api/commands", "/api/actions",
                            "/api/actions/ledger",
                            "POST /api/actions/run",
                            "/api/actions/job/<id>",
                            "/api/topic/<id>/timeline?level=",
                            "/api/pattern-sessions?pattern=&days=",
                            "/api/overview",
                            "/api/workitems?target=",
                            "/api/suppressed"]})
                    return
                self._json({"error": "未知路由 %s" % path}, 404)
            except _BadRequest as e:
                self._json({"error": str(e)}, 400)
            except _NotFound as e:
                self._json({"error": str(e)}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
            except RuntimeError as e:
                self._json({"error": str(e)}, 503)
            except sqlite3.DatabaseError as e:
                self._json({"error": "数据库错误: %s" % e}, 500)
            except Exception as e:  # noqa: BLE001 - HTTP 边界：不弹裸 traceback
                self._json({"error": "内部错误: %s" % e}, 500)

        # -- POST：唯一可写入口 = 动作入队（白名单校验在执行器内） --
        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            if u.path != "/api/actions/run":
                self._json({"error": "本服务只有 POST /api/actions/run 一个入口"
                                     "（A 档白名单动作；其余一律只读）"}, 405)
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            raw = self.rfile.read(n) if n > 0 else b"{}"
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                self._json({"error": "请求体不是合法 JSON: %s" % e}, 400)
                return
            if not isinstance(req, dict):
                self._json({"error": "请求体必须是 JSON 对象"}, 400)
                return
            action_id = req.get("action_id")
            pv = req.get("view_actions_api")
            if pv is not None and pv != VIEW_ACTIONS_API:
                self._json({"error": "页面动作面契约版本 %r ≠ 服务端 %d，请刷新"
                                     "页面或升级对应侧"
                                     % (pv, VIEW_ACTIONS_API)}, 409)
                return
            # **执行器只认注册表**（P3-1/D5）：表外动作一律 403
            try:
                specs = registry_specs(cfg)
            except (OSError, json.JSONDecodeError, ValueError) as e:
                self._json({"error": "动作注册表不可读/损坏（fail loud）: %s" % e},
                           500)
                return
            rspec = specs.get(action_id)
            if rspec is None:
                self._json({"error": "action_id=%r 不在动作注册表内（本服务不是"
                                     "万能 shell：只执行冻结白名单里的动作）"
                                     % action_id}, 403)
                return
            if rspec["tier"] == "C":
                self._json({"error": "动作 %s 是 C 档：只生成命令，页面永不执行"
                                     "（红线 3）" % action_id}, 403)
                return
            client = str(req.get("client") or self.headers.get("X-View-Client")
                         or "web-ui")[:80]
            if rspec["tier"] == "B":
                self._run_write(rspec, req, client)
                return
            spec = ACTIONS.get(action_id)
            if spec is None:   # 注册表声明了但没实现（health 里也会报）
                self._json({"error": "动作 %s 在注册表里，但服务里没有对应实现"
                                     "（注册表与实现漂移，见 /api/health 的 "
                                     "registry_problems）" % action_id}, 500)
                return
            try:
                params = _clean_params(spec, req.get("params") or {}, cfg)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
                return
            try:
                job = jobs.submit(lambda: spec["handler"](cfg, params),
                                  label=spec["label"])
            except RuntimeError as e:
                self._json({"error": str(e)}, 429)
                return
            self._json({"job_id": job["job_id"], "status": job["status"],
                        "action_id": action_id, "tier": "A", "params": params,
                        "est_sec": spec.get("est_sec"),
                        "poll": "/api/actions/job/" + job["job_id"]}, 202)

        do_PUT = do_DELETE = do_PATCH = do_POST

        # -- 端点实现 --
        def _glossary(self):
            try:
                text = cfg.glossary.read_text(encoding="utf-8")
            except OSError as e:
                self._json({"error": "glossary 文件不可读: %s" % e}, 500)
                return
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as e:
                self._json({"error": "glossary JSON 损坏（fail loud）: %s" % e},
                           500)
                return
            self._json(obj)

        def _commands(self):
            try:
                self._json(load_commands(cfg.commands_json))
            except (OSError, json.JSONDecodeError, ValueError) as e:
                self._json({"error": "commands.json 不可读/损坏（fail loud）: %s"
                                     % e}, 500)

        def _registry_payload(self) -> dict:
            """动作注册表（P3-1 生成物）——页面据此渲染动作卡与参数选择器。
            **注册表就是唯一真相**：页面不另写一份，A/B 档同源。"""
            hv = getattr(harvester, "__version__", None) if harvester else None
            try:
                reg = load_registry(cfg.registry_json)
            except (OSError, json.JSONDecodeError, ValueError) as e:
                self._json({"error": "动作注册表不可读/损坏（fail loud）: %s" % e},
                           500)
                return
            a_out = []
            for aid, spec in sorted(ACTIONS.items()):
                a_out.append({"action_id": aid, "tier": "A",
                              "label": spec["label"], "desc": spec.get("desc"),
                              "params": spec.get("params") or {},
                              "est_sec": spec.get("est_sec"),
                              "reversible": spec.get("reversible"),
                              "writes": spec.get("writes") or "",
                              "risk": None,
                              # 2026-10-13 additive：A 档也带命令映射（
                              # topic_pack 这类子动作级动作靠它上页面映射表；
                              # 旧字段一个不少，旧前端多收两键无害）。
                              "cmd": spec.get("cmd"),
                              "subaction": spec.get("subaction")})
            b_out = [{"action_id": a["action_id"], "tier": a.get("tier", "B"),
                      "label": a.get("summary"), "desc": a.get("detail"),
                      "cmd": a.get("cmd"), "subaction": a.get("subaction"),
                      "risk": a.get("risk"), "target": a.get("target"),
                      "target_path": str(cfg.default_for(a.get("target")) or ""),
                      "reversible": a.get("reversible"),
                      "writes": a.get("writes") or "",
                      "est_sec": 1,
                      "params": {p["name"]: {
                          "type": p["kind"], "src": p["src"],
                          "default": p["default"], "label": p["label"],
                          "required": p["required"],
                          "choices": p["choices"], "note": p["note"],
                          "server": p["server"]}
                          for p in a["params"]}}
                     for a in reg.get("write_actions") or []]
            self._json({"view_actions_api": VIEW_ACTIONS_API,
                        "harvester_version": hv,
                        "actions": a_out + b_out,
                        "tiers": reg.get("tiers") or {},
                        "write_targets": reg.get("write_targets") or {},
                        "registry_schema": reg.get("schema"),
                        "counts": reg.get("counts") or {}})

        # ---- B 档：两段提交（P3-2） ----
        def _run_write(self, rspec: dict, req: dict, client: str) -> None:
            """B 档调度：无 token → 预演 + 发 token；带 token → 校验后真写。

            三段留痕（都进 `actions_meta.db`）：① 预演也记一行 run（status=
            `preview`）——"有人点了什么但没确认"同样是有用的事实；② 执行记
            `done`/`failed`；③ 真写成功再记一行 `write_records`（含 before/
            after 与 destructive 快照）。
            """
            aid = rspec["action_id"]
            params_in = req.get("params") or {}
            token = req.get("confirm_token")
            try:
                norm = wa.normalize_params(rspec, params_in, cfg)
            except wa.WriteError as e:
                self._json({"error": str(e)}, 400)
                return
            try:
                prev = wa.plan(aid, rspec, norm, cfg)
            except wa.WriteError as e:
                self._json({"error": str(e)}, 400)
                return
            except KeyError as e:
                self._json({"error": "目标不存在：%s" % e}, 404)
                return
            except Exception as e:  # noqa: BLE001 - HTTP 边界
                self._json({"error": "预演失败：%s" % e}, 500)
                return

            if not token:
                run_id = uuid.uuid4().hex[:12]
                try:
                    conf = runlib.issue_confirm(cfg.actions_meta, aid, norm,
                                                prev, client)
                except sqlite3.DatabaseError as e:
                    self._json({"error": "台账库不可写：%s" % e}, 500)
                    return
                runlib.record_run(
                    cfg.actions_meta, run_id=run_id, action_id=aid, tier="B",
                    client=client, params=norm, elapsed_ms=0, status="preview",
                    result_summary="；".join(prev.get("human") or [])[:2000])
                self._json({
                    "status": "needs_confirm", "action_id": aid, "tier": "B",
                    "risk": prev.get("risk"), "target": prev.get("target"),
                    "target_path": prev.get("target_path"),
                    "preview": prev, "human": prev.get("human") or [],
                    "confirm_token": conf["token"],
                    "expires_in": conf["expires_in"],
                    "preview_run_id": run_id,
                    "hint": "把 confirm_token 连同**同一份 params** 再 POST 一次"
                            "即执行；参数或库状态变了会被拒并要求重新预览",
                }, 200)
                return

            # 第二段：先校验 token（含参数指纹与预览指纹），再执行
            run_id = uuid.uuid4().hex[:12]
            t0 = _now_ms()
            try:
                runlib.consume_confirm(cfg.actions_meta, token, aid, norm, prev)
            except runlib.ConfirmError as e:
                runlib.record_run(cfg.actions_meta, run_id=run_id, action_id=aid,
                                  tier="B", client=client, params=norm,
                                  elapsed_ms=0, status="failed",
                                  error=str(e))
                self._json({"error": str(e), "retry": "preview"}, 409)
                return
            except sqlite3.DatabaseError as e:
                self._json({"error": "台账库不可读：%s" % e}, 500)
                return
            try:
                result = wa.execute(aid, rspec, norm, cfg)
            except wa.WriteError as e:
                ms = _now_ms() - t0
                runlib.record_run(cfg.actions_meta, run_id=run_id, action_id=aid,
                                  tier="B", client=client, params=norm,
                                  elapsed_ms=ms, status="failed", error=str(e))
                self._json({"error": str(e)}, 400)
                return
            except (KeyError, ValueError) as e:
                ms = _now_ms() - t0
                runlib.record_run(cfg.actions_meta, run_id=run_id, action_id=aid,
                                  tier="B", client=client, params=norm,
                                  elapsed_ms=ms, status="failed", error=str(e))
                self._json({"error": "上游拒绝：%s" % e}, 400)
                return
            except Exception as e:  # noqa: BLE001 - HTTP 边界
                ms = _now_ms() - t0
                runlib.record_run(cfg.actions_meta, run_id=run_id, action_id=aid,
                                  tier="B", client=client, params=norm,
                                  elapsed_ms=ms, status="failed", error=str(e))
                self._json({"error": "执行失败：%s" % e}, 500)
                return
            ms = _now_ms() - t0
            summary = result.get("summary") or "；".join(prev.get("human") or [])
            try:
                runlib.record_run(cfg.actions_meta, run_id=run_id, action_id=aid,
                                  tier="B", client=client, params=norm,
                                  elapsed_ms=ms, status="done",
                                  result_summary=summary)
                runlib.record_write(
                    cfg.actions_meta, run_id=run_id, action_id=aid,
                    target=prev.get("target") or "",
                    target_path=prev.get("target_path") or "",
                    risk=prev.get("risk") or "", cmd=prev.get("cmd") or "",
                    summary=summary, before=prev.get("before") or {},
                    after=prev.get("after") or {},
                    snapshot=prev.get("snapshot"), client=client)
            except sqlite3.DatabaseError as e:
                # 写成功了但台账没记上——**必须显式告诉调用方**，
                # 否则"台账=全量留痕"的承诺被静默破坏
                self._json({"ok": True, "result": result, "summary": summary,
                            "run_id": run_id, "elapsed_ms": ms,
                            "warning": "动作已执行，但台账写入失败：%s" % e}, 200)
                return
            self._json({"status": "done", "action_id": aid, "tier": "B",
                        "run_id": run_id, "elapsed_ms": ms, "result": result,
                        "summary": summary}, 200)

        def _ledger(self, qs: dict):
            limit = 50
            try:
                limit = max(1, min(500, int(qs.get("limit") or 50)))
            except (TypeError, ValueError):
                pass
            try:
                self._json({
                    "view_actions_api": VIEW_ACTIONS_API,
                    "stats": runlib.ledger_stats(cfg.actions_meta),
                    "runs": runlib.recent_runs(cfg.actions_meta, limit=limit,
                                               action_id=qs.get("action_id")),
                    "writes": runlib.recent_writes(cfg.actions_meta, limit=limit),
                    "db": str(cfg.actions_meta)})
            except sqlite3.DatabaseError as e:
                self._json({"error": "台账库错误：%s" % e}, 500)

        def _timeline(self, topic_id: str, qs: dict):
            level = str(qs.get("level") or "title")
            sid = qs.get("sid") or None
            try:
                self._json(topic_timeline(cfg, topic_id, level, sid))
            except _BadRequest as e:
                self._json({"error": str(e)}, 400)
            except _NotFound as e:
                self._json({"error": str(e)}, 404)

        def _pattern(self, qs: dict):
            pattern = qs.get("pattern")
            days = _num(qs.get("days"))
            self._json(pattern_sessions(cfg, pattern, days))

        def _suppressed(self):
            """已抑制会话清单（v0.49，**只读**）。UI 的「已抑制」页签与「恢复」用。

            纪律：走上游 `suppress.list_suppressed`（默认 `create=False`，**不建表**
            ——读一下就顺手建表会让只读消费方产生副作用）。缺配置/缺库/缺表一律
            返回空表 + `configured` 说明，**不报错也不猜**（与 timeline 的
            `stale:null` 同款：不知道就说不知道）。
            """
            from harvester.suppress import list_suppressed
            if not cfg.topics_meta:
                self._json({"view_suppress_api": 1, "configured": False,
                            "meta": None, "count": 0, "rows": [],
                            "hint": "起服时未配 --topics-meta，抑制表无处可查"})
                return
            meta = Path(cfg.topics_meta)
            rows = list_suppressed(meta)
            self._json({"view_suppress_api": 1, "configured": True,
                        "meta": str(meta), "meta_exists": meta.is_file(),
                        "count": len(rows), "rows": rows,
                        "honest": ("抑制只作用于**仓内副本**（库内三表行 + exports "
                                   "副本），**不删采集源**；取消抑制本身不恢复数据"
                                   "——能不能恢复只取决于语料是否还在，"
                                   "**不设恢复时限**")})

        def _overview(self):
            """今日工作台数据面（P3-3）：四队列 + 三环，每个数字带口径与真源。"""
            from . import overview as _ov
            try:
                self._json(_ov.build_overview(cfg))
            except RuntimeError as e:
                self._json({"error": str(e)}, 503)
            except sqlite3.DatabaseError as e:
                self._json({"error": "数据库错误: %s" % e}, 500)

        def _workitems(self, qs: dict):
            """处理单（v0.49）：某个数字**正好那一批**待处理记录 + 处理建议 + 命令包。

            参数 `target`：工作台上数字的 target id（如 `queue:repair`、
            `ring:errors#0`）。缺参 → 400（**不猜**，猜出来的清单就是"对不上"的
            那个毛病本身）；未知 target → 404 并把可选值列出来。
            """
            from . import workitems as _wi
            tid = (qs.get("target") or "").strip()
            if not tid:
                try:
                    g = _wi.collect(cfg)
                    ids = _wi.target_ids(g)
                except RuntimeError as e:
                    self._json({"error": str(e)}, 503)
                    return
                self._json({"error": "缺参数 target",
                            "available": ids}, 400)
                return
            try:
                self._json(_wi.build_workitems(cfg, tid))
            except KeyError:
                g = _wi.collect(cfg)
                self._json({"error": "未知 target：%s" % tid,
                            "available": _wi.target_ids(g)}, 404)
            except RuntimeError as e:
                self._json({"error": str(e)}, 503)
            except sqlite3.DatabaseError as e:
                self._json({"error": "数据库错误: %s" % e}, 500)

    return ActionsHandler


def _cards_payload(cfg: Config) -> dict:
    """卡片本体（P1 的 cardlib 原样复用：本仓库根目录的 cardlib.py）。

    P1 的 cardlib 已是纯函数模块且被 `tests/test_v34_local_data.py` 钉住，
    故**不搬家**（搬家＝改测试触点＋改 git 历史，收益为零）；这里按需把
    仓库根补进 sys.path 后 import，归并只发生在服务层。
    """
    try:
        import cardlib
    except ImportError:  # `python -m actions.actionsserve` 时仓库根不在 path
        p = str(PACKAGE_ROOT)
        if p not in sys.path:
            sys.path.insert(0, p)
        import cardlib
    return cardlib.cards_payload(cfg.cards_root)


# ---------- 启动入口 ----------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="actionsserve",
        description="harvester-view 动作面 + 本地只读数据面（8766）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=None,
                    help="索引库（一律只读打开）；缺省取兄弟仓库的 "
                         "harvester.db")
    ap.add_argument("--cards-root", default=str(DEFAULT_CARDS_ROOT))
    ap.add_argument("--glossary",
                    default=str(PACKAGE_ROOT / "static" / "glossary.json"))
    ap.add_argument("--topics-meta", default=None)
    ap.add_argument("--artifacts-meta", default=None)
    ap.add_argument("--chain-root", default=None)
    ap.add_argument("--keywords-meta", default=None)
    ap.add_argument("--suggestions-meta", default=None)
    ap.add_argument("--exports-root", default=None)
    ap.add_argument("--commands-json", default=str(DEFAULT_COMMANDS_JSON))
    ap.add_argument("--registry-json", default=str(DEFAULT_REGISTRY_JSON),
                    help="动作注册表（P3-1 生成物；执行器只认它）")
    ap.add_argument("--actions-meta", default=str(DEFAULT_ACTIONS_META),
                    help="动作台账库（P3-2）：action_runs/write_records/confirms")
    ap.add_argument("--corpus-watermark", action="store_true",
                    help="在 timeline 端点附带库龄判定（要扫一遍语料盘；"
                         "缺省关，见 Config.want_watermark 注释）")
    return ap


def config_from_args(args) -> Config:
    db = Path(args.db) if args.db else (upstream_root() / "harvester.db")
    return Config(db=db, cards_root=Path(args.cards_root),
                  glossary=Path(args.glossary),
                  topics_meta=Path(args.topics_meta) if args.topics_meta else None,
                  artifacts_meta=(Path(args.artifacts_meta)
                                  if args.artifacts_meta else None),
                  chain_root=Path(args.chain_root) if args.chain_root else None,
                  keywords_meta=(Path(args.keywords_meta)
                                 if args.keywords_meta else None),
                  suggestions_meta=(Path(args.suggestions_meta)
                                    if args.suggestions_meta else None),
                  exports_root=(Path(args.exports_root)
                                if args.exports_root else None),
                  commands_json=Path(args.commands_json),
                  corpus_watermark=getattr(args, "corpus_watermark", False),
                  registry_json=Path(args.registry_json),
                  actions_meta=Path(args.actions_meta))


def serve(cfg: Config, host: str = "127.0.0.1",
          port: int = DEFAULT_PORT) -> None:
    """起服（阻塞）。`viewserver.serve` 与本函数是同一个实现（归并薄壳）。"""
    if _HARVESTER_IMPORT_ERROR:
        print("[actionsserve] 警告: 上游不可用（%s）——数据面端点照常，"
              "依赖 harvester 的端点会诚实报 503" % _HARVESTER_IMPORT_ERROR,
              file=sys.stderr)
    if not cfg.cards_root.is_dir():
        print("[actionsserve] 警告: 卡片目录不存在: %s（/view/cards 返回空列"
              "表，不阻断启动）" % cfg.cards_root, file=sys.stderr)
    if not cfg.glossary.exists():
        print("[actionsserve] 警告: glossary 不存在: %s（/view/glossary 返回"
              " 500）" % cfg.glossary, file=sys.stderr)
    # P3：注册表 ↔ 实现一致性自检（**只警告不阻断**——不一致时页面会黄条，
    # 但 A 档读动作仍然可用，没必要让整个服务起不来）
    try:
        probs = check_registry_consistency(load_registry(cfg.registry_json))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        probs = ["注册表不可读/损坏: %s" % e]
    for p in probs:
        print("[actionsserve] ⚠ 注册表不一致: %s" % p, file=sys.stderr)
    srv = ThreadingHTTPServer((host, port), make_handler(cfg))
    real = srv.socket.getsockname()[1]
    print("[actionsserve] 已启动 http://%s:%d/  （db=%s, cards=%s）"
          % (host, real, cfg.db.name, cfg.cards_root.name), file=sys.stderr)
    print("[actionsserve] Ctrl+C 停止", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[actionsserve] 已停止", file=sys.stderr)
    finally:
        srv.server_close()


def run(argv=None) -> int:
    args = build_parser().parse_args(argv)
    serve(config_from_args(args), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
