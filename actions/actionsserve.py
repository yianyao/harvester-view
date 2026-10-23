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
- `GET /api/envprobe`         本机执行环境探测（命令前缀用 uv/python/绝对路径
                              哪个，及仓根位置——2026-10-15，复制到终端的命令
                              必须能在用户终端直接跑）；
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
from urllib.parse import parse_qs, unquote, urlparse

from . import ensure_harvester_importable, upstream_root
from . import envprobe
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
                 actions_meta: Path | None = None,
                 reviews_meta: Path | None = None,
                 attrib_meta: Path | None = None):
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
        self.reviews_meta = (Path(reviews_meta) if reviews_meta
                             else None)
        #: v0.79 P6 归因台账（attrib_meta.db）。缺省 None = **零感知**：
        #: 不过滤任何归因候选，行为与"没有台账"完全一致（同 cards review 口径）。
        self.attrib_meta = (Path(attrib_meta) if attrib_meta else None)
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
                "attrib_meta": self.attrib_meta,
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


def _action_dualtrack(cfg: Config, params: dict) -> dict:
    """A 档：主题双轨时间线（P4-4）。上轨讨论流（timeline）+ 下轨产物流
    （artifacts）+ 打磨带/反转点候选（消费 P4-3，两轮原文对比自带）。

    为什么它是 A 档且走作业通道：全程只读三库（harvester.db / topics_meta /
    artifacts_meta），产物返回页面不落盘；但**大主题的 timeline 是逐会话
    查询**（FTS 虚表 sid UNINDEXED → N 次全表扫，实测 411 成员 ~86s），
    同步 GET 会把请求吊死在 timeline 计算上——故页面点按钮走
    POST /api/actions/run 入队 + 轮询（铁律 6.5：长任务不阻塞页面）。
    小主题/脚本可用同步 GET /api/topic/<id>/dualtrack。"""
    from . import dualtrack
    topic_id = str(params.get("id") or "").strip()
    level = str(params.get("level") or "coarse")
    if not topic_id:
        raise _BadRequest("缺主题 id（--id）——回主题页选一个主题")
    if level not in dualtrack.LEVELS:
        raise _BadRequest("level=%r 不在 %s（fine/artifact 为按需档，"
                          "不在双轨范围）" % (level, list(dualtrack.LEVELS)))
    if cfg.topics_meta is None:
        raise RuntimeError("未配置 topics_meta（起服时加 --topics-meta）")
    tl = topic_timeline(cfg, topic_id, level, None)
    return dualtrack.build(cfg, topic_id, level, tl)


def _action_topic_brief(cfg: Config, params: dict) -> dict:
    """A 档：主题研读面（v0.76 V3）。确定性聚合层——构成/活跃/高频问题/
    错误模式/skill 行为/重复会话/健康 + 已发布 chain 的结论与未决。
    全部只读、零语义臆造；大主题走作业通道（同 dualtrack 理由），
    小主题/脚本可用同步 GET /api/topic/<id>/brief。"""
    from . import topicbrief
    topic_id = str(params.get("id") or "").strip()
    level = str(params.get("level") or "coarse")
    if not topic_id:
        raise _BadRequest("缺主题 id（--id）——回主题页选一个主题")
    if cfg.topics_meta is None:
        raise RuntimeError("未配置 topics_meta（起服时加 --topics-meta）")
    tl = topic_timeline(cfg, topic_id, level, None)
    return topicbrief.build(cfg, topic_id, level, tl)


def _action_kgraph(cfg: Config, params: dict) -> dict:
    """A 档：知识图谱（v0.86，大类总览+搜索子图）。默认态只画大类
    （显隐前端）；params.q 非空=搜索模式：正文 FTS（messages 索引，
    检索框同判据）命中会话+邻域子图——标题不含但正文含关键字的会话
    也能命中。params.topic 非空（v0.89 主题态，用户批注①②）：该主题
    成员会话 ∪ FTS 命中——主题点击灌成员子图（label 整串 FTS 几乎必
    0 命中，纯 FTS 必孤点）。其余：只取用户先发起、机械聚类、机械边、
    账目全进 meta。只读三库与卡片目录；诚实空态走 hints。"""
    from .analysis import kgraph
    return kgraph.build(cfg, query=str(params.get("q") or ""),
                        topic=str(params.get("topic") or ""))


# ---------- A 档通用执行器（v0.63：白名单补全，用户 2026-10-14「都加上」） ----------

def _cli_report(argv: list[str]) -> dict:
    """A 档通用执行器：进程内跑**真 CLI**（argv 由各动作处理器拼好），捕获输出。

    为什么走 CLI 而不是另调一层函数：这些 report/校验命令的「数字真源」就是
    CLI 背后的那批 collect/render 函数——进程内跑 CLI 与终端里跑**同一代码
    路径**，页面结果与手敲命令逐字一致，不存在第二套口径。
    只读纪律由各动作处理器保证：**绝不拼进 --out / --apply 类参数**
    （--out 是 auto_out 参数，页面与动作都不传，产物一律打印捕获）。
    argparse 用法错误（SystemExit 2）转成 ValueError → 作业层显式失败，
    不让 usage 文本混进 stdout 冒充报告。
    """
    import contextlib
    import io
    if _HARVESTER_IMPORT_ERROR is not None:  # pragma: no cover
        raise RuntimeError(_HARVESTER_IMPORT_ERROR or "上游 harvester 不可用")
    from harvester import cli as _hcli
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ns = _hcli.build_parser().parse_args(argv)
            code = ns.func(ns)
    except SystemExit as e:  # argparse 用法错误 / 提前退出
        raise ValueError("命令参数不合法（argparse 退出码 %s）：%s\n%s"
                         % (e.code, " ".join(argv),
                            err.getvalue()[:300])) from None
    return {"view_actions_api": VIEW_ACTIONS_API, "argv": argv,
            "exit_code": int(code or 0), "stdout": out.getvalue(),
            "stderr": err.getvalue()}


def _sources_path(cfg: Config) -> str:
    """采集源配置（sources.json）：与库同目录（这就是主仓根，CLI 的惯例 cwd）。"""
    p = cfg.db.parent / "sources.json"
    if not p.is_file():
        raise RuntimeError("采集源配置不存在：%s（adapters/read/pack 要用它"
                           "扫采集源；先在主仓根跑 probe）" % p)
    return str(p)


def _chain_file(cfg: Config, name) -> Path:
    """把页面传来的 chain 文件名解析到 chain_root 下（拒绝任何路径成分）。

    页面只允许传**文件名**（主题页/卡片页列出来的那份），解析、拼路径都在
    服务端——不收路径就不存在「顺手指向任意文件」的读取后门。"""
    if cfg.chain_root is None:
        raise RuntimeError("未配置 chain-root（起服时加 --chain-root）")
    text = str(name or "").strip()
    if not text or Path(text).name != text or text in (".", ".."):
        raise _BadRequest("chain 文件名不合法：%r（只传 chain-root 下的文件名，"
                          "不带目录）" % (name,))
    p = cfg.chain_root / text
    if not p.is_file():
        raise _NotFound("chain 文件不存在：%s（chain-root=%s）"
                        % (text, cfg.chain_root))
    return p


def _require_topics_meta(cfg: Config) -> Path:
    if cfg.topics_meta is None or not cfg.topics_meta.is_file():
        raise RuntimeError("未配置 topics_meta（起服时加 --topics-meta）")
    return cfg.topics_meta


def _action_adapters(cfg: Config, params: dict) -> dict:
    """A 档：数据源探测状态（`adapters`，只打印不写）。"""
    return _cli_report(["adapters", "--sources", _sources_path(cfg)])


def _action_search(cfg: Config, params: dict) -> dict:
    """A 档：全文检索（`search`；与 /api/sessions 的 FTS 同库不同路——
    这里走 CLI 原口径，带 role 片段）。"""
    query = str(params.get("query") or "").strip()
    if not query:
        raise _BadRequest("缺检索词（query）")
    argv = ["search", query, "--db", str(cfg.db)]
    if params.get("limit") is not None:
        argv += ["--limit", str(int(params["limit"]))]
    if params.get("source"):
        argv += ["--source", str(params["source"])]
    return _cli_report(argv)


def _action_read(cfg: Config, params: dict) -> dict:
    """A 档：按纲要序号读会话原文（`read`；序号来自 scan 纲要，采集源实时读）。"""
    no = str(params.get("no") or "").strip()
    if not no:
        raise _BadRequest("缺纲要序号（no）——序号见 scan 生成的纲要")
    argv = ["read", no, "--sources", _sources_path(cfg),
            "--turn", str(params.get("turn") or "1")]
    return _cli_report(argv)


def _action_pack(cfg: Config, params: dict) -> dict:
    """A 档：交接包（`pack` 的进程内版）——**产物返回页面不落盘**。

    CLI 版默认写 context_pack.md（写文件）；动作版走 topic_pack 同款纪律：
    build_pack 只读采集源，内容直接回页面，人自己决定存到哪。"""
    from harvester.adapters import load_sources
    from harvester.exporter import parse_selection
    from harvester.outline import scan_all
    from harvester.pack import build_pack
    sel = str(params.get("select") or "").strip()
    if not sel:
        raise _BadRequest("缺会话选择（select，如 3 或 1,4-5；序号见纲要）")
    adapters_list, items, _ = scan_all(sources=load_sources(_sources_path(cfg)))
    adapters = {ad.id: ad for ad in adapters_list}
    try:
        nos, dropped = parse_selection(sel, len(items))
    except ValueError as e:
        raise _BadRequest("序号不合法：%s" % e)
    recs = []
    for n in nos:
        it = next(x for x in items if x["no"] == n)
        try:
            recs.append((adapters[it["adapter"]].load_session(it["session_id"]), n))
        except Exception as e:  # noqa: BLE001 - 单条失败跳过（与 CLI 同款）
            print("[warn] 序号 %s 加载失败已跳过: %s" % (n, e))
    if not recs:
        raise _BadRequest("所选会话全部加载失败（纲要过期？重新 scan）")
    content = build_pack(recs, total_budget=int(params.get("tokens") or 2000),
                         question=params.get("question") or None)
    return {"view_actions_api": VIEW_ACTIONS_API, "sessions": len(recs),
            "chars": len(content), "content": content,
            "note": ("越界序号已忽略：%s" % dropped) if dropped else ""}


def _action_cards_validate(cfg: Config, params: dict) -> dict:
    """A 档：卡片校验（`cards validate`；root/db/注册表全部服务端定）。"""
    argv = ["cards", "validate", "--root", str(cfg.cards_root),
            "--db", str(cfg.db)]
    if cfg.topics_meta and cfg.topics_meta.is_file():
        argv += ["--topics-meta", str(cfg.topics_meta)]
    return _cli_report(argv)


def _action_cards_index_md(cfg: Config, params: dict) -> dict:
    """A 档：触发词消歧素材（v0.78 `cards index-md`；stdout 版，不落盘）。

    只读只列不裁决；人要把素材存档时在命令台复制命令加 --out 自行跑。
    """
    return _cli_report(["cards", "index-md", "--root", str(cfg.cards_root)])


def _action_chain_validate(cfg: Config, params: dict) -> dict:
    """A 档：chain 长文结构校验（`chain-validate`；文件限 chain_root 内）。"""
    p = _chain_file(cfg, params.get("file"))
    return _cli_report(["chain-validate", str(p),
                        "--meta", str(_require_topics_meta(cfg)),
                        "--db", str(cfg.db)])


def _action_chain_audit(cfg: Config, params: dict) -> dict:
    """A 档：chain 内容审计（`chain-audit`：引文逐字门+锚点语义门+覆盖）。

    v0.76 V7 直给：文本报告照旧走真 CLI（逐字一致）；另附
    `audit_anchors`（可疑锚点结构化块）供页面渲染 [接受] 直给按钮——
    用同一上游 audit_chain（quotes/coverage 关，省一半算力），
    review_meta 传台账使已裁决行从可疑集消失（与 CLI 口径一致）。"""
    p = _chain_file(cfg, params.get("file"))
    argv = ["chain-audit", str(p), "--db", str(cfg.db)]
    for flag, key in (("--no-quotes", "no_quotes"),
                      ("--no-anchors", "no_anchors"),
                      ("--no-coverage", "no_coverage"),
                      ("--quotes-ascii", "quotes_ascii"),
                      ("--strict", "strict")):
        if params.get(key):
            argv.append(flag)
    if params.get("list_limit") is not None:
        argv += ["--list-limit", str(int(params["list_limit"]))]
    r = _cli_report(argv)
    if not params.get("no_anchors"):
        try:
            from harvester.chainaudit import audit_chain as _ac
            meta = _require_topics_meta(cfg)
            a = _ac(p, cfg.db, quotes=False, coverage=False, anchors=True,
                    review_meta=meta, show_reviewed=True)
            r["audit_anchors"] = {
                "file": p.name,
                "suspicious": [{"sid": s["sid"], "turn": s["turn"]}
                               for s in a["anchors"]["suspicious"]],
                "reviewed_accepted": a["anchors"].get("reviewed_accepted", 0),
            }
        except Exception:  # noqa: BLE001 - 结构化块失败不影响文本报告
            pass
    return r


def _action_deadcode_scan(cfg: Config, params: dict) -> dict:
    """A 档：死代码扫描（`deadcode-scan`；base=主仓根，兄弟仓自动覆盖）。

    不给 --fail-on-found（那是 CI 门的用法）；unstable 单列（并发编辑的
    结论不可信口径与上游一致）。"""
    from harvester.deadcode import (DEFAULT_ROOTS, found_total, render_deadcode,
                                    scan, unstable_total)
    report = scan(roots=DEFAULT_ROOTS, base=cfg.db.parent)
    text = render_deadcode(report)
    u = unstable_total(report)
    return {"view_actions_api": VIEW_ACTIONS_API, "stdout": text,
            "exit_code": 0, "found": found_total(report),
            "unstable": u,
            "stderr": ("[deadcode-scan] %d 个文件扫描期间被改动 → %d 条发现"
                       "不可信，请等写入结束重跑" % (len(report["unstable"]), u))
                      if u else ""}


def _action_export_analysis(cfg: Config, params: dict) -> dict:
    """A 档：统一分析导出（`export-analysis`；产物打印捕获，不落盘）。"""
    kind = str(params.get("kind") or "").strip()
    if kind not in ("sessions", "tools", "errors", "skills", "triage"):
        raise _BadRequest("kind 只支持 sessions/tools/errors/skills/triage，"
                          "收到 %r" % kind)
    fmt = str(params.get("format") or "md")
    if fmt not in ("md", "json"):
        raise _BadRequest("format 只支持 md/json，收到 %r" % fmt)
    argv = ["export-analysis", "--kind", kind, "--format", fmt,
            "--db", str(cfg.db)]
    if params.get("days") is not None:
        argv += ["--days", str(params["days"])]
    if params.get("sids"):
        argv += ["--sids", str(params["sids"])]
    if params.get("min_count") is not None:
        argv += ["--min-count", str(int(params["min_count"]))]
    if params.get("top") is not None:
        argv += ["--top", str(int(params["top"]))]
    if cfg.cards_root and Path(cfg.cards_root).is_dir():
        argv += ["--cards-root", str(cfg.cards_root)]
    return _cli_report(argv)


def _action_harvest_eval(cfg: Config, params: dict) -> dict:
    """A 档：效果评估（`harvest-eval`；只读采集库，报告打印捕获）。"""
    if cfg.suggestions_meta is None:
        raise RuntimeError("未配置 suggestions_meta（起服时加 --suggestions-meta）")
    argv = ["harvest-eval", "--db", str(cfg.db),
            "--meta", str(cfg.suggestions_meta)]
    if params.get("since") is not None:
        argv += ["--since", str(params["since"])]
    for key, flag in (("window_days", "--window-days"),
                      ("min_per_window", "--min-per-window"),
                      ("min_total", "--min-total")):
        if params.get(key) is not None:
            argv += [flag, str(int(params[key]))]
    if params.get("threshold") is not None:
        argv += ["--threshold", str(params["threshold"])]
    return _cli_report(argv)


def _action_kb_stats(cfg: Config, params: dict) -> dict:
    """A 档：知识库盘点（`kb-stats`；root=上游缺省 ~/.workbuddy/knowledge）。"""
    from harvester.distill import KB_DEFAULT_ROOT
    return _cli_report(["kb-stats", "--root", str(KB_DEFAULT_ROOT)])


def _action_report_chains(cfg: Config, params: dict) -> dict:
    """A 档：工具链失败模式报告（`report-chains`）。"""
    argv = ["report-chains", "--db", str(cfg.db)]
    if params.get("since") is not None:
        argv += ["--since", str(params["since"])]
    if params.get("top") is not None:
        argv += ["--top", str(int(params["top"]))]
    return _cli_report(argv)


def _action_attrib(cfg: Config, params: dict) -> dict:
    """A 档：归因候选（`attrib`，v0.79 P6）。

    **只用 `--brief`（人读出口）**：页面给人看，就该拿人读形态——
    机读出口（`--json`）是给程序消费的，两出口**不共用实现**是本工程的
    冻结决策（SOP §3.3）。直给版**绝不带 --out**（stdout 捕获即结果）。
    只读 harvester.db；台账过滤走 `attrib_meta.db`（缺省零感知）。
    """
    argv = ["attrib", "--db", str(cfg.db), "--brief", "-"]
    if params.get("since") is not None:
        argv += ["--since", str(params["since"])]
    if cfg.attrib_meta:
        argv += ["--meta", str(cfg.attrib_meta)]
    rep = _cli_report(argv)
    # P6-9 归因台：**同一次调用的机读面**。命令台看 `stdout`（人读 brief），
    # 归因台看 `data`（`attrib --json` 的解析结果）——两个出口在 CLI 里本就
    # 不共用实现，这里只是把两面一起带回去，**页面不再自己算覆盖度/等级**。
    jargv = ["attrib", "--db", str(cfg.db), "--json", "-"]
    if params.get("since") is not None:
        jargv += ["--since", str(params["since"])]
    if cfg.attrib_meta:
        jargv += ["--meta", str(cfg.attrib_meta)]
    jrep = _cli_report(jargv)
    if int(jrep.get("exit_code") or 0) == 0:
        import json as _json
        try:
            rep["data"] = _json.loads(jrep.get("stdout") or "{}")
        except ValueError as e:            # 机读面坏了要说出来，别静默缺键
            rep["data_error"] = f"机读出口不是合法 JSON：{e}"
    else:
        rep["data_error"] = (jrep.get("stderr") or "").strip()[:300] or "机读出口失败"
    return rep


def _action_evidence_pack(cfg: Config, params: dict) -> dict:
    """A 档：评审包（`evidence-pack`，P6-6）。

    走**人读出口**（Markdown 打到 stdout，**绝不带 `--out`**）：
    页面要的是结果本身，不在服务端落盘（与 `_action_attrib` 同纪律）。

    包内三段原样返回（判据头/证据体/**判定槽**）——**页面不做二次加工**：
    判定槽的价值就在于"能整段贴给任意 Agent 裁决再回填台账"，
    前端一改格式就不再是同一个包了。
    """
    kind = str(params.get("kind") or "errors")
    if kind not in ("errors", "skills", "chains", "attrib"):
        raise ValueError(f"kind 不支持：{kind}（可选 errors|skills|chains|attrib）")
    argv = ["evidence-pack", "--kind", kind, "--db", str(cfg.db)]
    if params.get("since") is not None:
        argv += ["--since", str(params["since"])]
    if params.get("top") is not None:
        argv += ["--top", str(int(params["top"]))]
    if getattr(cfg, "attrib_meta", None):
        argv += ["--meta", str(cfg.attrib_meta)]
    return _cli_report(argv)


def _action_llm_pack(cfg: Config, params: dict) -> dict:
    """A 档：LLM 包（`llm-pack`，第④b步）。

    走**机读出口**（JSON 打到 stdout，绝不带 `--json PATH`）：包本身就是给
    LLM 的输入，页面要原样拿走，服务端不落盘（同 `_action_evidence_pack` 纪律）。
    """
    faces = str(params.get("faces") or "errors")
    argv = ["llm-pack", "--faces", faces, "--db", str(cfg.db)]
    if params.get("limit") is not None:
        argv += ["--limit", str(int(params["limit"]))]
    return _cli_report(argv)


def _action_report_daily(cfg: Config, params: dict) -> dict:
    """A 档：每日数据速览（`report-daily`，P5-⑤）。

    只读聚合；直给版**绝不带 --out**（stdout 捕获即结果，不在服务端
    落第二个文件——要落盘走 CLI 或 schedule 配方）。"""
    argv = ["report-daily", "--db", str(cfg.db)]
    if cfg.suggestions_meta:
        argv += ["--meta", str(cfg.suggestions_meta)]
    if params.get("since") is not None:
        argv += ["--since", str(params["since"])]
    return _cli_report(argv)


def _action_report_skill(cfg: Config, params: dict) -> dict:
    """A 档：skill 行为画像（`report-skill`）。"""
    argv = ["report-skill", "--db", str(cfg.db)]
    if params.get("skill"):
        argv += ["--skill", str(params["skill"])]
    if params.get("min_calls") is not None:
        argv += ["--min-calls", str(int(params["min_calls"]))]
    return _cli_report(argv)


def _action_report_skill_join(cfg: Config, params: dict) -> dict:
    """A 档：skill 进化 join 交叉表（`report-skill-join`；chain 限 root 内）。"""
    p = _chain_file(cfg, params.get("chain"))
    argv = ["report-skill-join", "--chain", str(p), "--db", str(cfg.db)]
    if params.get("skill"):
        argv += ["--skill", str(params["skill"])]
    if params.get("margin_days") is not None:
        argv += ["--margin-days", str(params["margin_days"])]
    return _cli_report(argv)


def _action_report_traces(cfg: Config, params: dict) -> dict:
    """A 档：OTel trace 统计（`report-traces`；root=~/.workbuddy/traces）。"""
    return _cli_report(["report-traces",
                        "--root", str(Path.home() / ".workbuddy" / "traces")])


def _action_suppress_list(cfg: Config, params: dict) -> dict:
    """A 档：抑制表清单（`suppress list`；抑制表在 topics_meta.db）。"""
    return _cli_report(["suppress", "list",
                        "--meta", str(_require_topics_meta(cfg))])


def _action_noise_list(cfg: Config, params: dict) -> dict:
    """A 档：零散登记清单（`noise list`；sessions_noise 在 topics_meta.db）。

    P5-⑧（用户 2026-10-16 批准）的只读面：列出已判零散的会话（何时/为什么），
    与抑制清单同款走进程内真 CLI、输出捕获。"""
    return _cli_report(["noise", "list",
                        "--meta", str(_require_topics_meta(cfg))])


def _action_suppress_candidates(cfg: Config, params: dict) -> dict:
    """A 档：抑制候选清单（`suppress candidates`，只读扫描）。

    min_jaccard 先验对齐 CLI：0~1 之外一律拒绝（不静默当没给）。"""
    argv = ["suppress", "candidates", "--db", str(cfg.db),
            "--meta", str(_require_topics_meta(cfg))]
    if params.get("min_jaccard") is not None:
        mj = float(params["min_jaccard"])
        if not (0.0 < mj <= 1.0):
            raise _BadRequest("min_jaccard 需在 0~1 之间（收到 %s）" % mj)
        argv += ["--min-jaccard", str(mj)]
    return _cli_report(argv)


def _action_topic_candidates(cfg: Config, params: dict) -> dict:
    """A 档：主题聚类候选（`topic-candidates`；只产候选不改注册表）。"""
    argv = ["topic-candidates", "--db", str(cfg.db)]
    if cfg.topics_meta and cfg.topics_meta.is_file():
        argv += ["--topics-meta", str(cfg.topics_meta)]
    for key, flag in (("min_sim", "--min-sim"), ("min_size", "--min-size"),
                      ("top_tools", "--top-tools")):
        if params.get(key) is not None:
            argv += [flag, str(params[key])]
    return _cli_report(argv)


def _action_triage(cfg: Config, params: dict) -> dict:
    """A 档：蒸馏队列（`triage`；与工作台「待蒸馏」同源同口径）。"""
    argv = ["triage", "--db", str(cfg.db)]
    if params.get("since") is not None:
        argv += ["--since", str(params["since"])]
    if cfg.cards_root and Path(cfg.cards_root).is_dir():
        argv += ["--cards-root", str(cfg.cards_root)]
    for key, flag in (("min_count", "--min-count"), ("top", "--top")):
        if params.get(key) is not None:
            argv += [flag, str(int(params[key]))]
    return _cli_report(argv)


def _topic_sub_argv(cfg: Config, sub: str, params: dict,
                    *, db: bool = False, chain_root: bool = False) -> list[str]:
    """`topic <子动作>` 的公共 argv（meta 服务端定；id 从 params.topic_id 取）。"""
    argv = ["topic", sub, "--id", str(params.get("topic_id") or "").strip(),
            "--meta", str(_require_topics_meta(cfg))]
    if not argv[3]:
        raise _BadRequest("缺主题 id（--id）——回主题页选一个主题")
    if db:
        argv += ["--db", str(cfg.db)]
    if chain_root and cfg.chain_root:
        argv += ["--chain-root", str(cfg.chain_root)]
    return argv


def _action_topic_show(cfg: Config, params: dict) -> dict:
    return _cli_report(_topic_sub_argv(cfg, "show", params))


def _action_topic_dedupe(cfg: Config, params: dict) -> dict:
    return _cli_report(_topic_sub_argv(cfg, "dedupe", params, db=True))


def _action_topic_export(cfg: Config, params: dict) -> dict:
    return _cli_report(_topic_sub_argv(cfg, "export", params, db=True,
                                       chain_root=True))


def _action_topic_md(cfg: Config, params: dict) -> dict:
    return _cli_report(_topic_sub_argv(cfg, "md", params, db=True,
                                       chain_root=True))


def _action_topic_turns(cfg: Config, params: dict) -> dict:
    argv = _topic_sub_argv(cfg, "turns", params, db=True)
    if params.get("cap") is not None:
        argv += ["--cap", str(int(params["cap"]))]
    if params.get("full"):
        argv.append("--full")
    return _cli_report(argv)


def _action_topic_chain(cfg: Config, params: dict) -> dict:
    argv = _topic_sub_argv(cfg, "chain", params, db=True)
    level = str(params.get("level") or "title")
    if level not in ("title", "coarse", "mid", "fine", "artifact"):
        raise _BadRequest("level=%r 不在 title/coarse/mid/fine/artifact" % level)
    argv += ["--level", level]
    if params.get("sid"):
        argv += ["--sid", str(params["sid"])]
    if params.get("max_chars") is not None:
        argv += ["--max-chars", str(int(params["max_chars"]))]
    if level == "artifact" and not cfg.artifacts_meta:
        raise _BadRequest("level=artifact 需要 --artifacts-meta（起服时已传则"
                          "自动带上）")
    if cfg.artifacts_meta and cfg.artifacts_meta.is_file():
        argv += ["--artifacts-meta", str(cfg.artifacts_meta)]
    return _cli_report(argv)


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
    "dualtrack": {
        "tier": "A",
        "label": "主题双轨时间线（讨论流+产物流）",
        "desc": "上轨讨论流（timeline）+ 下轨产物版本（artifacts）+ "
                "打磨带/反转点候选（消费 P4-3，点击看两轮原文对比）。"
                "大主题（数百成员）实测 ~90s，作业后台跑、页面轮询。",
        "params": {"id": {"type": "topic_id", "src": "topics_api",
                           "default": None, "label": "主题 id"},
                   "level": {"type": "level", "src": "chips",
                             "default": "coarse",
                             "choices": ["coarse", "mid"],
                             "label": "讨论流分层"}},
        "est_sec": 30,
        "reversible": "幂等（只读三库，产物返回页面不落盘）",
        "writes": "",
        "handler": _action_dualtrack,
    },
    "topic_brief": {
        "tier": "A",
        "label": "主题研读面（聚合分析）",
        "desc": "确定性聚合层：成员构成/活跃月度/高频问题/错误模式/"
                "skill 行为/重复会话/健康 + 已发布 chain 的结论与未决。"
                "每个数字带来源与口径；语义结论只来自 chain，没有就明说。",
        "params": {"id": {"type": "topic_id", "src": "topics_api",
                           "default": None, "label": "主题 id"},
                   "level": {"type": "level", "src": "chips",
                             "default": "coarse",
                             "choices": ["coarse", "mid"],
                             "label": "讨论流分层"}},
        "est_sec": 30,
        "reversible": "幂等（只读三库与 chain 文件，产物返回页面不落盘）",
        "writes": "",
        "handler": _action_topic_brief,
    },
    "kgraph": {
        "tier": "A",
        "label": "知识图谱（大类总览+搜索子图）",
        "desc": "默认=大类总览（主题/卡片，显隐前端）；q 参数=搜索模式："
                "正文 FTS（messages 索引，检索框同判据）命中会话+其邻域"
                "子图。只取用户先发起；机械聚类一簇一个代表；五类边来自"
                "既有真源，零语义臆造。只读，不落盘。",
        "params": {"q": {"type": "text", "required": False,
                          "desc": "搜索关键字（正文 FTS，空=大类总览）"},
                   "topic": {"type": "text", "required": False,
                             "desc": "主题 id（v0.89 主题态：成员子图 ∪ "
                                     "FTS 命中；点主题时与 q 同传）"}},
        "est_sec": 6,
        "reversible": "幂等（只读三库与卡片目录，产物返回页面不落盘）",
        "writes": "",
        "handler": _action_kgraph,
    },
    # ---- v0.63 白名单补全（用户 2026-10-14「还有哪些没加的？都加上」）----
    # 覆盖 commands.json 里**全部剩余 A 档命令**；执行一律走进程内真 CLI
    # （_cli_report），输出捕获返回页面，绝不落盘（--out 一律不传）。
    "adapters": {
        "tier": "A",
        "label": "数据源探测状态",
        "desc": "显示本机各采集源的探测状态（adapters；读 sources.json）。",
        "params": {},
        "est_sec": 8,
        "reversible": "幂等（只读采集源配置）",
        "writes": "",
        "handler": _action_adapters,
    },
    "search": {
        "tier": "A",
        "label": "全文检索",
        "desc": "FTS 全文检索历史会话（search；结果带 role 与片段）。",
        "params": {"query": {"type": "text", "src": "chips", "default": None,
                             "label": "检索词"},
                   "limit": {"type": "int", "src": "chips", "default": 20,
                             "label": "最多几条"},
                   "source": {"type": "text", "src": "chips", "default": None,
                              "label": "只看某来源（可空）"}},
        "est_sec": 2,
        "reversible": "幂等（只读索引库）",
        "writes": "",
        "handler": _action_search,
    },
    "read": {
        "tier": "A",
        "label": "读会话原文",
        "desc": "按纲要序号读采集源里的会话原文（read；序号见 scan 纲要）。",
        "params": {"no": {"type": "text", "src": "chips", "default": None,
                          "label": "纲要序号"},
                   "turn": {"type": "text", "src": "chips", "default": "1",
                            "label": "回合（1..N / last / all）"}},
        "est_sec": 10,
        "reversible": "幂等（只读采集源）",
        "writes": "",
        "handler": _action_read,
    },
    "pack": {
        "tier": "A",
        "label": "交接包（给另一个 Agent）",
        "desc": "把所选会话打包成接续上下文（pack）。CLI 版写文件；动作版"
                "产物直接回页面，不落盘。",
        "params": {"select": {"type": "text", "src": "chips", "default": None,
                              "label": "会话序号（如 3 或 1,4-5）"},
                   "tokens": {"type": "int", "src": "chips", "default": 2000,
                              "label": "token 预算"},
                   "question": {"type": "text", "src": "chips", "default": None,
                                "label": "带着什么问题交接（可空）"}},
        "est_sec": 15,
        "reversible": "幂等（只读采集源，产物返回页面不落盘）",
        "writes": "",
        "handler": _action_pack,
    },
    "cards_validate": {
        "tier": "A",
        "label": "卡片校验",
        "desc": "校验卡片目录（cards validate；root/db/注册表服务端定，"
                "错误与警告分列）。",
        "params": {},
        "est_sec": 3,
        "reversible": "幂等（只读卡片目录与库）",
        "writes": "",
        "handler": _action_cards_validate,
    },
    "cards_index_md": {
        "tier": "A",
        "label": "触发词素材",
        "desc": "触发词消歧素材（cards index-md；触发行+疑似易混对+"
                "AGENTS.md 钩子模板；stdout 版，存档请复制命令加 --out）。",
        "params": {},
        "est_sec": 2,
        "reversible": "幂等（只读卡片目录）",
        "writes": "",
        "handler": _action_cards_index_md,
    },
    "chain_validate": {
        "tier": "A",
        "label": "chain 结构校验",
        "desc": "校验主题长文结构（chain-validate：sid 在成员内、turn 越界）。",
        "params": {"file": {"type": "text", "src": "chips", "default": None,
                            "label": "chain 文件名（chain-root 下）"}},
        "est_sec": 2,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_chain_validate,
    },
    "chain_audit": {
        "tier": "A",
        "label": "chain 内容审计",
        "desc": "审计主题长文内容保真（chain-audit：引文逐字门+锚点语义门"
                "+证据覆盖）。",
        "params": {"file": {"type": "text", "src": "chips", "default": None,
                            "label": "chain 文件名（chain-root 下）"},
                   "strict": {"type": "text", "src": "chips", "default": None,
                              "label": "锚点告警也判失败（strict）"},
                   "list_limit": {"type": "int", "src": "chips", "default": 20,
                                  "label": "逐条清单最多列几条"}},
        "est_sec": 5,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_chain_audit,
    },
    "deadcode_scan": {
        "tier": "A",
        "label": "死代码扫描",
        "desc": "扫主仓+兄弟仓源码树（deadcode-scan）；启发式，只提示不判死。",
        "params": {},
        "est_sec": 10,
        "reversible": "幂等（只读源码树）",
        "writes": "",
        "handler": _action_deadcode_scan,
    },
    "export_analysis": {
        "tier": "A",
        "label": "统一分析导出",
        "desc": "五类分析产物（export-analysis：sessions/tools/errors/"
                "skills/triage），md/JSON 同源；产物回页面不落盘。",
        "params": {"kind": {"type": "text", "src": "chips", "default": "errors",
                            "label": "类别（sessions/tools/errors/skills/triage）"},
                   "format": {"type": "text", "src": "chips", "default": "md",
                              "label": "格式（md/json）"},
                   "days": {"type": "days", "src": "chips", "default": None,
                            "label": "时间窗"},
                   "min_count": {"type": "int", "src": "chips", "default": None,
                                 "label": "至少出现几次"},
                   "top": {"type": "int", "src": "chips", "default": None,
                           "label": "Top N"}},
        "est_sec": 3,
        "reversible": "幂等（只读库，产物返回页面）",
        "writes": "",
        "handler": _action_export_analysis,
    },
    "harvest_eval": {
        "tier": "A",
        "label": "班规效果评估",
        "desc": "采纳规则的 A/B 三值判定（harvest-eval：改善/没变化/不下结论）。",
        "params": {"since": {"type": "days", "src": "chips", "default": None,
                             "label": "只看最近 N 天"},
                   "window_days": {"type": "int", "src": "chips", "default": None,
                                   "label": "前后窗口天数（默认 7）"},
                   "min_per_window": {"type": "int", "src": "chips",
                                      "default": None,
                                      "label": "每窗口样本门槛（默认 5）"},
                   "min_total": {"type": "int", "src": "chips", "default": None,
                                 "label": "合计样本门槛（默认 20）"},
                   "threshold": {"type": "days", "src": "chips", "default": None,
                                 "label": "判定阈值（默认 0.1）"}},
        "est_sec": 5,
        "reversible": "幂等（只读两库）",
        "writes": "",
        "handler": _action_harvest_eval,
    },
    "kb_stats": {
        "tier": "A",
        "label": "知识库盘点",
        "desc": "知识库根目录盘点（kb-stats：话题文件数/INDEX 一致性/台账条数）。",
        "params": {},
        "est_sec": 2,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_kb_stats,
    },
    "report_chains": {
        "tier": "A",
        "label": "工具链失败模式",
        "desc": "长回合/连击/循环/空转/高步 Top N（report-chains）。",
        "params": {"since": {"type": "days", "src": "chips", "default": None,
                             "label": "时间窗"},
                   "top": {"type": "int", "src": "chips", "default": 10,
                           "label": "Top N"}},
        "est_sec": 3,
        "reversible": "幂等（只读库）",
        "writes": "",
        "handler": _action_report_chains,
    },
    "attrib": {
        "tier": "A",
        "label": "归因候选",
        "desc": "把错误计数变成**根因候选 + 应对候选**（attrib，v0.79 P6）。"
                "归因是候选（机器判断，会错），裁决走 CLI `attrib-review` 回填"
                "attribations 台账；人裁过的候选不再列出。规则表是数据文件"
                "（harvester/data/attrib-rules.yaml）。",
        "params": {"since": {"type": "days", "src": "chips", "default": None,
                             "label": "时间窗"}},
        "est_sec": 3,
        "reversible": "幂等（只读库；不写任何库）",
        "writes": "",
        "handler": _action_attrib,
    },
    "evidence_pack": {
        "tier": "A",
        "label": "评审包",
        "desc": "把一个数据面打成**可评审**的包（evidence-pack，v0.79 P6-6）："
                "① 判据头（口径/时间窗/库快照含 STALE/R 等级/覆盖度/边界）"
                "② 证据体（计数 + 原文锚点 `sid#seq` + 归因候选 why/fix）"
                "③ **判定槽**（pattern_key 已填、裁决四列留空）。"
                "**没有判定槽的包不是评审包、是报告**；等级 R0–R3 由内容算"
                "（skills/chains 目前是 R1：能计数、评不出根因）。",
        "params": {"kind": {"type": "text", "src": "chips", "default": "errors",
                            "choices": ["errors", "skills", "chains", "attrib"],
                            "label": "包类型"},
                   "since": {"type": "days", "src": "chips", "default": None,
                             "label": "时间窗"},
                   "top": {"type": "int", "src": "chips", "default": 20,
                           "label": "每类条数"}},
        "est_sec": 3,
        "reversible": "幂等（只读库；包打到 stdout，不落盘）",
        "writes": "",
        "handler": _action_evidence_pack,
    },
    "llm_pack": {
        "tier": "A",
        "label": "LLM 包",
        "desc": "把「现在判不了的东西」连证据打成 llm-pack/1 交 LLM 复核"
                "（第④b步）：每条 = 事实（锚点/工具名）+ 现有归因候选 + "
                "**判定槽**。只读 harvester.db；回填仍走 attrib-review 台账。",
        "params": {"faces": {"type": "text", "src": "chips", "default": "errors",
                             "choices": ["errors", "tools", "errors,tools"],
                             "label": "面"},
                   "limit": {"type": "int", "src": "chips", "default": 40,
                             "label": "每面条数"}},
        "est_sec": 3,
        "reversible": "幂等（只读库；包打到 stdout，不落盘）",
        "writes": "",
        "handler": _action_llm_pack,
    },
    "report_skill": {
        "tier": "A",
        "label": "skill 行为画像",
        "desc": "按 skill 聚合调用/行为链/触发任务（report-skill；可精确到单个）。",
        "params": {"skill": {"type": "text", "src": "chips", "default": None,
                             "label": "只看某个 skill（可空）"},
                   "min_calls": {"type": "int", "src": "chips", "default": 0,
                                 "label": "低于该调用次数标低样本"}},
        "est_sec": 3,
        "reversible": "幂等（只读库）",
        "writes": "",
        "handler": _action_report_skill,
    },
    "report_skill_join": {
        "tier": "A",
        "label": "skill 进化交叉表",
        "desc": "chain 时间线锚点 × skill 调用锚点交叉（report-skill-join）。",
        "params": {"chain": {"type": "text", "src": "chips", "default": None,
                             "label": "chain 文件名（chain-root 下）"},
                   "skill": {"type": "text", "src": "chips", "default": None,
                             "label": "只看某个 skill（可空）"},
                   "margin_days": {"type": "days", "src": "chips",
                                   "default": 90,
                                   "label": "认可窗（天）"}},
        "est_sec": 3,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_report_skill_join,
    },
    "report_traces": {
        "tier": "A",
        "label": "trace 工具统计",
        "desc": "OTel trace 的耗时/失败率/取消（report-traces；读本地 traces）。",
        "params": {},
        "est_sec": 3,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_report_traces,
    },
    "suppress_list": {
        "tier": "A",
        "label": "抑制表清单",
        "desc": "列出已抑制会话（suppress list：谁/何时/为什么）。",
        "params": {},
        "est_sec": 1,
        "reversible": "幂等（只读抑制表）",
        "writes": "",
        "handler": _action_suppress_list,
    },
    "noise_list": {
        "tier": "A",
        "label": "零散登记清单",
        "desc": "列出已登记零散的会话（noise list：谁/何时/为什么）；"
                "登记≠抑制——只写 meta 表，会话数据一行不动。",
        "params": {},
        "est_sec": 1,
        "reversible": "幂等（只读零散表）",
        "writes": "",
        "handler": _action_noise_list,
    },
    "report_daily": {
        "tier": "A",
        "label": "每日数据速览",
        "desc": "四队列+tools/errors/chains 五段聚合（report-daily，只读）；"
                "四队列与工作台同源，快照口径。",
        "params": {"since": {"type": "days", "src": "chips", "default": 1.0,
                             "label": "tools/errors/chains 窗口（天）"}},
        "est_sec": 8,
        "reversible": "幂等（只读聚合）",
        "writes": "",
        "handler": _action_report_daily,
    },
    "suppress_candidates": {
        "tier": "A",
        "label": "抑制候选清单",
        "desc": "扫描重复簇与零轮次会话（suppress candidates，只读）；"
                "可按 J 下限过滤重复簇候选。",
        "params": {"min_jaccard": {"type": "days", "src": "chips",
                                   "default": None,
                                   "label": "只列重复度 J≥此值的候选（0~1）"}},
        "est_sec": 30,
        "reversible": "幂等（只读扫描）",
        "writes": "",
        "handler": _action_suppress_candidates,
    },
    "topic_candidates": {
        "tier": "A",
        "label": "主题聚类候选",
        "desc": "标题 n-gram+任务签名产候选主题（topic-candidates；"
                "只产候选不改注册表）。",
        "params": {"min_sim": {"type": "days", "src": "chips", "default": 0.35,
                               "label": "相似度下限"},
                   "min_size": {"type": "int", "src": "chips", "default": 2,
                                "label": "最小簇大小"},
                   "top_tools": {"type": "int", "src": "chips", "default": 3,
                                 "label": "每簇列几个标志工具"}},
        "est_sec": 10,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_topic_candidates,
    },
    "triage": {
        "tier": "A",
        "label": "蒸馏队列",
        "desc": "重复踩坑/旧坑重现/skill 待鉴定初筛（triage；与工作台"
                "「待蒸馏」同源）。",
        "params": {"since": {"type": "days", "src": "chips", "default": 7,
                             "label": "时间窗（天）"},
                   "min_count": {"type": "int", "src": "chips", "default": 2,
                                 "label": "至少出现几次才列"},
                   "top": {"type": "int", "src": "chips", "default": 10,
                           "label": "Top N"}},
        "est_sec": 5,
        "reversible": "幂等（只读库与卡片目录）",
        "writes": "",
        "handler": _action_triage,
    },
    "topic_show": {
        "tier": "A",
        "label": "主题详情",
        "desc": "看一个主题的成员清单与关键词（topic show）。",
        "params": {"topic_id": {"type": "topic_id", "src": "topics_api",
                                "default": None, "label": "主题 id"}},
        "est_sec": 1,
        "reversible": "幂等（只读注册表）",
        "writes": "",
        "handler": _action_topic_show,
    },
    "topic_turns": {
        "tier": "A",
        "label": "主题回合索引",
        "desc": "主题成员的回合索引/raw 全文（topic turns；chain 锚点的来源）。",
        "params": {"topic_id": {"type": "topic_id", "src": "topics_api",
                                "default": None, "label": "主题 id"},
                   "cap": {"type": "int", "src": "chips", "default": 200,
                           "label": "每会话最多几个回合"},
                   "full": {"type": "text", "src": "chips", "default": None,
                            "label": "raw 全文（true=全文，默认预览）"}},
        "est_sec": 5,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_topic_turns,
    },
    "topic_dedupe": {
        "tier": "A",
        "label": "主题去重报告",
        "desc": "会话级去重（topic dedupe：成员 → 代表，重复列出）。",
        "params": {"topic_id": {"type": "topic_id", "src": "topics_api",
                                "default": None, "label": "主题 id"}},
        "est_sec": 5,
        "reversible": "幂等（只读）",
        "writes": "",
        "handler": _action_topic_dedupe,
    },
    "topic_export": {
        "tier": "A",
        "label": "主题结构化导出",
        "desc": "给机器读的 topic JSON（topic export）；产物回页面不落盘。",
        "params": {"topic_id": {"type": "topic_id", "src": "topics_api",
                                "default": None, "label": "主题 id"}},
        "est_sec": 3,
        "reversible": "幂等（只读，产物返回页面）",
        "writes": "",
        "handler": _action_topic_export,
    },
    "topic_md": {
        "tier": "A",
        "label": "主题人读速览",
        "desc": "一页速览（topic md：是什么/跨多久/关键转折/结论与未决）。",
        "params": {"topic_id": {"type": "topic_id", "src": "topics_api",
                                "default": None, "label": "主题 id"}},
        "est_sec": 3,
        "reversible": "幂等（只读，产物返回页面）",
        "writes": "",
        "handler": _action_topic_md,
    },
    "topic_chain": {
        "tier": "A",
        "label": "主题链时间线",
        "desc": "主题链分层时间线（topic chain：title/coarse/mid/fine/artifact）。",
        "params": {"topic_id": {"type": "topic_id", "src": "topics_api",
                                "default": None, "label": "主题 id"},
                   "level": {"type": "level", "src": "chips", "default": "title",
                             "label": "分层"},
                   "sid": {"type": "sid", "src": "sessions_api", "default": None,
                           "label": "成员 sid（fine/artifact 档必填）"},
                   "max_chars": {"type": "int", "src": "chips", "default": None,
                                 "label": "字符预算（可空）"}},
        "est_sec": 3,
        "reversible": "幂等（只读，产物返回页面）",
        "writes": "",
        "handler": _action_topic_chain,
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
                if path == "/api/envprobe":
                    self._json(envprobe.probe())
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
                m = re.fullmatch(r"/api/topic/(.+)/dualtrack", path)
                if m:
                    self._dualtrack(unquote(m.group(1)), qs)
                    return
                m = re.fullmatch(r"/api/topic/(.+)/brief", path)
                if m:
                    self._topic_brief(unquote(m.group(1)), qs)
                    return
                m = re.fullmatch(r"/api/topic/(.+)/skilljoin", path)
                if m:
                    self._skilljoin(unquote(m.group(1)), qs)
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
                            "/api/topic/<id>/dualtrack?level=coarse|mid",
                            "/api/topic/<id>/skilljoin?skill=&margin=",
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
                payload = load_commands(cfg.commands_json)
                # 命令前缀与仓根随包透出（envprobe）：页面据此渲染可复制、
                # 在用户终端能直接跑的命令形，并提示粘贴位置。
                env = envprobe.probe()
                payload["cli_prefix"] = env["cli_prefix"]
                payload["repo_root"] = env["repo_root"]
                self._json(payload)
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
            # cmd/subaction 以注册表为准（注册表唯一真相）：ACTIONS 代码
            # 字典缺这两个键时，/api/actions 曾发空值 → 前端 cmdActionMap
            # 首个声明者胜出 → B 档同名 cmd（chain_audit_accept 的
            # cmd=chain-audit）劫持命令 chip，A 档审计从命令台不可达
            # （2026-10-16 真机实测踩中：点 chain-audit「执行」直接弹
            # B 档确认框）。A 档在列表前段，首个声明者恒为 A 档。
            reg_a = {a["action_id"]: a
                     for a in reg.get("read_actions") or []}
            for aid, spec in sorted(ACTIONS.items()):
                ra = reg_a.get(aid) or {}
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
                              "cmd": ra.get("cmd")
                              if ra.get("cmd") is not None else spec.get("cmd"),
                              "subaction": ra.get("subaction")
                              if ra.get("subaction") is not None
                              else spec.get("subaction")})
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

        def _dualtrack(self, topic_id: str, qs: dict):
            """P4-4 双轨时间线（主题工作台）：上轨讨论流（复用 topic_timeline，
            已含库指纹/库龄叠加）+ 下轨产物流 + 打磨带/反转点候选（消费 P4-3，
            两轮原文对比随候选自带）。level 只认 coarse/mid（fine/artifact
            是按需档；title 与 chain 视图重复）。"""
            from . import dualtrack
            level = str(qs.get("level") or "coarse")
            if level not in dualtrack.LEVELS:
                raise _BadRequest("level=%r 不在 %s（fine/artifact 为按需档，"
                                  "不在双轨范围）" % (level,
                                                  list(dualtrack.LEVELS)))
            if cfg.topics_meta is None:
                raise RuntimeError("未配置 topics_meta（起服时加 --topics-meta）")
            tl = topic_timeline(cfg, topic_id, level, None)
            self._json(dualtrack.build(cfg, topic_id, level, tl))

        def _topic_brief(self, topic_id: str, qs: dict):
            """v0.76 V3 主题研读面（同步 GET，小主题/脚本用）：确定性聚合层
            （构成/活跃/高频问题/错误模式/skill 行为/重复会话/健康）+ 已发布
            chain 的结论与未决。大主题请走 A 档动作 topic_brief 作业通道。"""
            from . import topicbrief
            level = str((qs.get("level") or ["coarse"])[0])
            if cfg.topics_meta is None:
                raise RuntimeError("未配置 topics_meta（起服时加 --topics-meta）")
            tl = topic_timeline(cfg, topic_id, level, None)
            self._json(topicbrief.build(cfg, topic_id, level, tl))

        def _skilljoin(self, topic_id: str, qs: dict):
            """P4-5 skill 调用点挂轨道：chain 时间线锚点 × skill 调用锚点
            交叉表（T4 确定性层）。**上游实现直通**——直接调
            `harvester.skilljoin.build_cross`，不重算 join（成员内节点
            精确归组带 turn / 时间窗归组 / margin 只向后放宽三通道全部
            来自上游口径，本仓不复制逻辑）。chain 文件按 frontmatter
            topic_id 在 chain-root 下定位（workitems._chain_paths 同口径，
            文件名不可信——显示名会被截断改写）。skill 缺省=全部；
            margin 缺省 0（叙事节奏 chain 的认可口径是 90，见 TODO 裁决
            记录，由调用方传，不写死）。只读。"""
            from harvester.skilljoin import build_cross
            from .workitems import _chain_paths
            if cfg.chain_root is None:
                raise _BadRequest("未配置 chain-root（起服加 --chain-root）")
            if not cfg.db:
                raise RuntimeError("未配置索引库（--db）")
            skill = str(qs.get("skill") or "").strip() or None
            try:
                margin = float(str(qs.get("margin") or 0))
            except ValueError:
                raise _BadRequest("margin=%r 不是数字（向后放宽天数，≥0）"
                                  % qs.get("margin"))
            if margin < 0:
                raise _BadRequest("margin 只向后放宽，须 ≥0（得 %s）" % margin)
            paths = _chain_paths(cfg).get(topic_id) or []
            if not paths:
                raise _NotFound("该主题没有 chain 长文（chain-root 下按 "
                                "frontmatter.topic_id 扫不到 %s）——先由人跑 "
                                "topic pack 蒸馏产出 chain-*.md" % topic_id)
            d = build_cross(Path(paths[0]), cfg.db, skill=skill,
                            margin_days=margin)
            d["chain_path"] = paths[0]
            if len(paths) > 1:
                d["chain_paths_extra"] = paths[1:]
            d["view_actions_api"] = VIEW_ACTIONS_API
            d["topic_id"] = topic_id
            self._json(d)

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
                payload = _wi.build_workitems(cfg, tid)
                # 处理单命令包的粘贴位置提示用（envprobe 现探测，v0.68）
                payload["repo_root"] = envprobe.probe()["repo_root"]
                self._json(payload)
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
    ap.add_argument("--reviews-meta", default=None,
                    help="卡片复核台账（B 档动作 card_review 写它；"
                         "缺省=<db 同目录>/cards_meta.db）")
    ap.add_argument("--attrib-meta", default=None,
                    help="归因台账（v0.79 P6；CLI attrib-review 写它。"
                         "缺省=<db 同目录>/attrib_meta.db；库/表不存在=零感知，"
                         "人已裁决的归因候选不再列出）")
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
                  actions_meta=Path(args.actions_meta),
                  reviews_meta=(Path(args.reviews_meta)
                               if getattr(args, "reviews_meta", None)
                               else (db.parent / "cards_meta.db")),
                  attrib_meta=(Path(args.attrib_meta)
                               if getattr(args, "attrib_meta", None)
                               else (db.parent / "attrib_meta.db")))


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
