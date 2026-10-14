# -*- coding: utf-8 -*-
"""workitems -- 工作项层：把工作台上每个数字变成「一串可处理的记录」。

为什么需要这一层（用户 2026-10-13 原话：「四队列中点击（去处理）即跳转到相关
页面，这点很好。但**队列明细对不上**」）：

    根因是**卡片上的数字和跳过去的页面各自算了一遍**：数字取自「建议条目里
    unresolved_count>=1 的条数」（6），而跳到的 G2 建议页渲染的是**全部**条目（8）；
    更极端的例子是「待验证 9」跳到了错误报告页（381）——两套人群、两套数字，
    用户没法对着手上的数字干活。

本模块的判据（**本项目自定义，非行业术语**）：一个「工作项（work item，
敏捷/项目管理领域通用词）」= **一条需要人处理的记录**（一条待裁决的建议句 /
一个反复失败的错误模式 / 一张待蒸馏的模式候选 …），至少带三样东西：

    ① 锚点：回得到原文（sid + 回合序号，走既有的 `anchor()` 三跳）；
    ② 处理建议：这条该怎么处理（人话，写给不熟悉本仓的人）；
    ③ 命令：把处理动作打包成**可复制的 CLI 行**，或**可直接在命令台执行**的
       B 档动作（`action_id` + `params`，命令台按 action_id 找到卡并预填参数）。

**唯一真源纪律**（这是本模块存在的全部理由）

    一个 target 拥有一份明细 `items`，以及若干条**具名指标** `metrics`。
    指标的取值**只由明细派生**，两种派生方式（`mode`）：

        - `items`：值 = `len(items)`（条目数）
        - `sum`  ：值 = `sum(item["metrics"][sum_field])`（次数之和）

    工作台上显示的每个数字都必须是某个 target 的某条 metric；`/api/overview`
    与 `/api/workitems` 都只消费这里的结果，页面只渲染、不再聚合。于是
    「数字 == 明细」是**构造保证**而不是巧合。

    `mode` 必须显式声明：本仓在 P2-5 为「条目数与次数和混用」吃过一次亏
    （样例数 ≠ 会话数），所以这里不允许有隐含口径。`reconcile` 把每条指标的
    派生过程写成机器可判定的形式，`tests/test_v40_workitems.py` 逐条钉住。

明细上限 `MAX_ITEMS`：**超限不是静默截断**——`reconcile.ok` 会因此变 false
（响亮地报「数字与明细对不上」），宁可红也不许页面拿一个和列表不一样大的数字
去派活（照 `$DSH_HOME/AGENTS.md` §七 20「给人看的摘要不能当机器输入」办）。
"""
from __future__ import annotations

from pathlib import Path

from . import ensure_harvester_importable

_ERR = ensure_harvester_importable()
if _ERR is None:  # pragma: no cover - 缺上游时由端点报 503
    from harvester import apiserve as _hapi
    from harvester import harvesteval as _he
    from harvester import suggestmeta as _sm
    from harvester.errstats import (collect_errors_from_db, normalize_error,
                                    pattern_stats)
    from harvester.triage import collect_triage
else:  # pragma: no cover
    _hapi = _he = _sm = None
    collect_errors_from_db = pattern_stats = normalize_error = None
    collect_triage = None

VIEW_WORKITEMS_API = 1

#: 明细条数上限；超限会让 `reconcile.ok=false`（响亮），不静默截断。
MAX_ITEMS = 200

WHERE_REPORTS_AGENTS = "报告页 → 建议条目"
WHERE_REPORTS_ERRORS = "报告页 → 错误三分类"
WHERE_REPORTS_SKILL = "报告页 → Skill 行为画像"
WHERE_TRIAGE = "蒸馏队列页"
WHERE_TOPICS = "主题页"
WHERE_CARDS = "卡片页 → 卡片校验"


# ---------------------------------------------------------------- 小工具

def _rel(path, cfg) -> str | None:
    """给人复制的 CLI 里，**同仓的库用文件名**，不印本机绝对路径。

    用户的 N3 要求是「报告页别印本机绝对路径」（噪声与隐私）；而命令又必须能直接
    粘贴执行。两者兼得的办法：库与 `harvester.db` 同目录时（这就是仓库根，也正是
    命令行的惯例 cwd）只写文件名；不同目录才写全路径（那时不写全路径没法执行）。
    """
    if not path:
        return None
    p = Path(path)
    try:
        if cfg.db and p.parent == Path(cfg.db).parent:
            return p.name
    except OSError:  # pragma: no cover - 路径比较失败不该拖垮页面
        pass
    return str(p)


def _q(value) -> str:
    """命令行里给值加引号（只在需要时）。按 shlex 的 posix 口径转义，
    保证 `test_v40_workitems` 那道「命令能真解析」的门能把它拆回**一个**参数。"""
    text = str(value)
    if text and all(ch.isalnum() or ch in "-_./:=," for ch in text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _cli(parts) -> str:
    return "python -m harvester " + " ".join(parts)


def _cmd(label, note, cli=None, action_id=None, params=None, tier=None) -> dict:
    """命令包的一行。`action_id` 非空 = 命令台能**直接执行**（A 只读 / B 两段提交）；
    `cli` = 可复制的命令行。两者都给最理想；只有其一时页面会说清缺哪个。"""
    return {"label": label, "note": note, "cli": cli,
            "action_id": action_id, "params": dict(params or {}), "tier": tier}


def _anchor(sid, seq, probe="") -> dict | None:
    if not sid:
        return None
    return {"sid": str(sid), "seq": int(seq or 0), "probe": str(probe or "")}


def _sample(s):
    """锚点样例归一。

    **同一份数据有两种形状**：`triage.collect_triage` 在**进程内**返回的 samples 是
    元组 `(sid, seq, error)`，而经 apiserve 的 HTTP 序列化后变成 dict
    `{"sid","seq","error"}`（`/api/triage` 实测就是 dict）。工作项层两个来源都吃，
    所以两种形状都收——不假设调用方是谁。
    """
    if s is None:
        return None
    if isinstance(s, dict):
        return s
    if isinstance(s, (tuple, list)):
        return {"sid": s[0] if len(s) > 0 else None,
                "seq": s[1] if len(s) > 1 else 0,
                "error": s[2] if len(s) > 2 else ""}
    return None


def _item(item_id, title, *, metrics=None, facts=None, anchor=None, detail="",
          suggestion="", commands=None, level="warn", owner="") -> dict:
    m = {"count": 1}
    m.update(metrics or {})
    return {"item_id": str(item_id), "title": str(title),
            "metrics": {k: int(v or 0) for k, v in m.items()},
            "facts": [list(f) for f in (facts or [])], "anchor": anchor,
            "detail": str(detail), "suggestion": str(suggestion),
            "commands": list(commands or []), "level": level,
            "owner": str(owner)}


def _metric(name, label, mode, sum_field="count", unit=None, source=None) -> dict:
    """一条具名指标。`unit` 缺省时用 target 的单位——**同一 target 的两条指标单位
    可能不同**（如「未解决模式数 16 个」与「未解决次数 31 次」），故必须能各自声明。
    `source` 同理：一条指标可以来自与 target 主数字**不同的**上游函数
    （如「可评估的已采纳」来自 `run_eval.adopted_total`），必须能各自交代。
    """
    return {"name": name, "label": label, "mode": mode,
            "sum_field": sum_field, "unit": unit, "source": source}


# ---------------------------------------------------------------- 现算上游

def collect(cfg) -> dict:
    """把这一屏要用的上游材料**现算一次**（四队列 / 三环 / 各 target 共用）。

    不缓存的理由与 overview 相同：数量级都小（13 主题 / 24 台账 / 57 模式），
    而缓存会立刻制造「两套数字」的风险——正是本轮要修的那个毛病。
    """
    if _hapi is None:
        raise RuntimeError(_ERR or "上游 harvester 不可用")
    from . import actionsserve as _as  # 延迟导入，避免循环依赖

    con = _as.open_ro(cfg.db)
    try:
        errs, err_meta = collect_errors_from_db(cfg.db, con=con)
        g = {
            "pstats": pattern_stats(errs),
            "err_meta": err_meta,
            "errs_rep": _hapi.api_reports_errors(con, cfg.db, {}),
            "agents": _hapi.api_reports_agents(con, cfg.db, {},
                                               meta_db=cfg.suggestions_meta),
            "skills_rep": _hapi.api_reports_skills(con, cfg.db, {}),
            "topics": _hapi.api_topics(con, cfg.db, cfg.topics_meta,
                                       cfg.chain_root),
            "triage": collect_triage(cfg.db, cards_root=cfg.cards_root,
                                     min_count=2, top_n=10, con=con),
            "cards": None,
        }
        if cfg.cards_root and Path(cfg.cards_root).is_dir():
            try:
                g["cards"] = _hapi.api_cards(con, cfg.db, cfg.cards_root, {})
            except (ValueError, FileNotFoundError):
                g["cards"] = None
    finally:
        con.close()
    g["records"] = _sm.load_records(cfg.suggestions_meta)
    g["registry"] = _sm.registry_counts(cfg.topics_meta)
    g["ev"] = (_he.run_eval(cfg.db, cfg.suggestions_meta)
               if cfg.suggestions_meta
               else {"counts": {}, "adopted_total": 0, "skipped": 0,
                     "skipped_topic": 0, "entries": []})
    g["cards_pending"] = []
    if cfg.cards_root:
        pdir = Path(cfg.cards_root).parent / "cards_pending"
        if pdir.is_dir():
            g["cards_pending"] = sorted(pdir.glob("*.md"))
    g["cfg"] = cfg
    return g


# ---------------------------------------------------------------- 明细取材

def _ledger_items(g, predicate) -> list[dict]:
    items = []
    for key in sorted(g["records"]):
        r = g["records"][key]
        if not predicate(key, r):
            continue
        is_topic = key.startswith(_sm.TOPIC_PREFIX)
        items.append(_item(
            "ledger:" + key, key,
            facts=[("台账状态", r["status"][:24]),
                   ("命名空间", "主题登记（topic:）" if is_topic else "建议句"),
                   ("采纳时刻", r.get("adopted_at") or "—"),
                   ("更新时间", r.get("updated_at") or "—")],
            detail=r.get("note") or "",
            suggestion=_ledger_suggestion(r, is_topic),
            commands=_decide_commands(g, key) if r["status"] == "pending" else
                     ([] if is_topic else _verify_commands(g)),
            level="warn" if r["status"] == "pending" else "ok",
            owner="人"))
    return items


def _ledger_suggestion(r, is_topic) -> str:
    if r["status"] == "pending":
        return ("逐条拍板：写进 AGENTS.md 给 AI 当规矩（=采纳），或者驳回。"
                "工具只记录你的决定，**绝不自动改 AGENTS.md**。")
    if r["status"] == "rejected":
        return "已驳回：不再参与效果评估。若判断变了，用下面的命令改回 pending。"
    if is_topic:
        return ("这是**主题登记**（从主题注册表同步来的另一本账），"
                "不是给 AI 的规矩句，也不参加效果评估。")
    return ("已采纳。有没有用要看效果评估的三种结论：判『变差』才回滚，"
            "判『没变化』建议退休，判『看不出结论』是样本不够。")


def _decide_commands(g, key) -> list[dict]:
    cfg = g["cfg"]
    meta = _rel(cfg.suggestions_meta, cfg)
    if not meta:
        return [_cmd("无法给出命令",
                     "服务端未配置 --suggestions-meta，命令里的库路径无从确定"
                     "（本层不猜路径）。")]
    return [
        _cmd("换成命令台执行（采纳）",
             "两步提交：先预演看清要写什么，确认后才真正写。备注里带一行 "
             "`pattern: 错误名`，效果评估才认得这条规矩。",
             cli=_cli(["suggest-status", "--meta", _q(meta), "--key", _q(key),
                       "--status", "adopted", "--note", _q("pattern: <错误模式>")]),
             action_id="suggest_status",
             params={"key": key, "status": "adopted"}, tier="B"),
        _cmd("换成命令台执行（驳回）",
             "驳回后，效果评估就不再看它了。",
             cli=_cli(["suggest-status", "--meta", _q(meta), "--key", _q(key),
                       "--status", "rejected"]),
             action_id="suggest_status",
             params={"key": key, "status": "rejected"}, tier="B"),
    ]


def _pending_items(g) -> list[dict]:
    return _ledger_items(g, lambda k, r: r["status"] == "pending")


def _done_items(g) -> list[dict]:
    return _ledger_items(g, lambda k, r: r["status"] != "pending")


def _adopted_items(g) -> list[dict]:
    return _ledger_items(g, lambda k, r: r["status"] == "adopted")


def _unresolved_entries(g) -> list[dict]:
    return [e for e in g["agents"]["entries"]
            if (e.get("unresolved_count") or 0) >= 1]


def _repair_items(g) -> list[dict]:
    items = []
    for e in _unresolved_entries(g):
        samples = e.get("samples") or []
        first = _sample(samples[0]) if samples else None
        pattern = ""
        if first and normalize_error:
            pattern = normalize_error(first.get("error") or "")
        un = e.get("unresolved_count") or 0
        items.append(_item(
            "agent:" + e["title"], e["title"],
            metrics={"count": un, "unresolved": un},
            facts=[("实测次数", "x%d" % (e.get("total") or 0)),
                   ("未解决次数", str(un)),
                   ("责任方", e.get("owner") or "—"),
                   ("台账状态", e.get("status") or "—")],
            anchor=_anchor(first and first.get("sid"), first and first.get("seq"),
                           first and first.get("error")),
            detail=e.get("body") or "",
            suggestion="坑还在：先点锚点回原文确认，再看 AGENTS.md 里那条规矩写得够不够清楚。"
                       "要改内容请人工改 AGENTS.md；工具只负责补一行 "
                       "`pattern: 错误名` 的批注，让效果评估能把规矩和错误对上号。",
            commands=_repair_commands(g, e["title"], pattern, first),
            level="bad", owner=e.get("owner") or ""))
    return items


def _repair_commands(g, title, pattern, sample) -> list[dict]:
    cfg = g["cfg"]
    meta = _rel(cfg.suggestions_meta, cfg)
    out = []
    if meta:
        note = ("pattern: " + pattern) if pattern else "pattern: <错误模式>"
        out.append(_cmd(
            "在命令台补 pattern 批注（采纳并留痕）",
            "备注里带一行 `pattern: 错误名`，效果评估才能把这条规矩与"
            "错误对上号；不补这一行，评估会一直显示『未评估』。",
            cli=_cli(["suggest-status", "--meta", _q(meta), "--key", _q(title),
                      "--status", "adopted", "--note", _q(note)]),
            action_id="suggest_status",
            params={"key": title, "status": "adopted", "note": note}, tier="B"))
    if sample and sample.get("sid"):
        out.append(_cmd(
            "打包这一条命中的会话原文（蒸馏包）",
            "自包含 md：会话原文 + 卡片规范 + 指令，直接喂 Agent 起草修订。",
            cli=_cli(["draft", "--sid", _q(sample["sid"]), "--type", "pitfall"])))
    return out


def _distill_items(g) -> list[dict]:
    items = []
    for p in g["triage"].get("new_patterns") or []:
        samples = p.get("samples") or []
        first = _sample(samples[0]) if samples else None
        items.append(_item(
            "pattern:" + str(p.get("pattern")), str(p.get("pattern")),
            metrics={"count": p.get("count") or 0},
            facts=[("次数", "x%d" % (p.get("count") or 0)),
                   ("类别（本项目自定义分类）", p.get("class") or "—"),
                   ("工具", "、".join(p.get("tools") or []) or "—"),
                   ("首次出现", p.get("first_seen") or "—"),
                   ("疑似已有卡", "是" if p.get("known_card") else "否")],
            anchor=_anchor(first and first.get("sid"), first and first.get("seq"),
                           first and first.get("error")),
            detail="这个坑最近反复出现，但建议池里还没有对应条目——"
                   "也就是还没登记过怎么处置它。",
            suggestion="两条路都已备好，点下面命令包里的「在命令台打开」即可："
                       "① 「生成班规候选」把它起草成给 AI 的规则条目（预演确认后"
                       "写建议池文件，再人工定稿）；② 「生成蒸馏包」从出坑的这次"
                       "会话导出打包材料，交给 AI 写知识卡片。工具只出草稿，"
                       "不会自动改你的规则文件（AGENTS.md）。",
            commands=_draft_command(first),
            owner="Agent（起草）+ 人（定稿）"))
    return items


def _draft_command(sample) -> list[dict]:
    if not (sample and sample.get("sid")):
        return []
    return [_cmd(
        "打包这一条的会话原文（蒸馏包）",
        "B 档动作：点了在命令台预填好 sid，先预演、确认才写；"
        "产物是自包含 md（会话原文+卡片规范+指令），直接喂 Agent 就能起草。",
        cli=_cli(["draft", "--sid", _q(sample["sid"]), "--type", "pitfall"]),
        action_id="draft_pack",
        params={"sid": sample["sid"], "type": "pitfall"}, tier="B")]


def _old_pattern_items(g) -> list[dict]:
    items = []
    for p in g["triage"].get("old_patterns") or []:
        samples = p.get("samples") or []
        first = _sample(samples[0]) if samples else None
        items.append(_item(
            "oldpattern:" + str(p.get("pattern")), str(p.get("pattern")),
            metrics={"count": p.get("count") or 0},
            facts=[("次数", "x%d" % (p.get("count") or 0)),
                   ("类别", p.get("class") or "—")],
            anchor=_anchor(first and first.get("sid"), first and first.get("seq"),
                           first and first.get("error")),
            detail="旧坑重现：曾是建议/卡片，又在新窗口里出现。",
            suggestion="坑回来了：说明上一版班规没拦住它。先看锚点，再改班规措辞"
                       "（不是再加一条新规则）。",
            commands=_draft_command(first), level="bad"))
    return items


def _cards_pending_items(g) -> list[dict]:
    items = []
    for p in g["cards_pending"]:
        items.append(_item(
            "cardpending:" + Path(p).name, Path(p).name,
            facts=[("路径", str(p.parent))],
            detail="已生成脚手架、还没定稿入卡片仓。",
            suggestion="按卡片规范补齐锚点（出处标记）和证据原文，跑一次下面的卡片校验，过了就能入库。",
            commands=[_cmd("校验卡片目录",
                           "含刚定稿的这张；错误与警告分列。",
                           cli=_cards_cli(g))]))
    return items


def _inconclusive_items(g) -> list[dict]:
    return _eval_items(g, _he.VERDICT_INCONCLUSIVE)


def _evaluable_items(g) -> list[dict]:
    """**可评估**的已采纳条目（不过滤判语）。

    `harvesteval.run_eval` 里 `adopted_total = len(entries)`（源码 406 行），
    所以「可评估条数」与这份明细是同一个集合——这里正是靠这一点让数字有明细可点。
    """
    return _eval_items(g, None)


def _unchanged_items(g) -> list[dict]:
    return _eval_items(g, _he.VERDICT_UNCHANGED)


def _eval_items(g, verdict) -> list[dict]:
    items = []
    for e in g["ev"].get("entries") or []:
        if verdict is not None and e.get("verdict") != verdict:
            continue
        b = e.get("before") or {}
        a = e.get("after") or {}
        items.append(_item(
            "eval:" + str(e.get("key")), str(e.get("key")),
            metrics={"count": min(int(b.get("errors") or 0),
                                  int(a.get("errors") or 0))},
            facts=[("判定", e.get("verdict") or "—"),
                   ("采纳时刻", e.get("adopted_at") or "—"),
                   ("分界来源", e.get("boundary_source") or "—"),
                   ("前窗口错误", str(b.get("errors", "—"))),
                   ("后窗口错误", str(a.get("errors", "—"))),
                   ("匹配来源", e.get("match_source") or "—")],
            detail="匹配到的错误模式：" + "；".join(e.get("targets") or [])[:300],
            suggestion=_eval_suggestion(verdict),
            commands=_verify_commands(g),
            level="ok", owner="人（决定等还是放宽）"))
    return items


def _eval_suggestion(verdict) -> str:
    if verdict == _he.VERDICT_UNCHANGED:
        return ("『没变化』：采纳前后没差别。工具建议让这条规矩退休，"
                "但由你决定（工具只是把结论摆出来，绝不自动改 AGENTS.md）。")
    return ("『看不出结论』= 样本不够，**不是**『没用』，更不是『变差』。"
            "据此回滚会误判。要么再攒一阵数据，要么用下面的命令放宽时间窗和"
            "样本门槛重跑；还不够就只能先挂着。")


def _verify_commands(g) -> list[dict]:
    cfg = g["cfg"]
    meta = _rel(cfg.suggestions_meta, cfg)
    db = _rel(cfg.db, cfg)
    if not (meta and db):
        return []
    return [_cmd(
        "放宽窗口与样本门槛重跑评估",
        "窗口 14 → 30 天、每窗口门槛 5 → 3、合计 20 → 10：样本更宽，"
        "但放宽后**仍要两指标（未解决率与复发率）都不升**才算改善。",
        cli=_cli(["harvest-eval", "--db", _q(db), "--meta", _q(meta),
                  "--window-days", "30", "--min-per-window", "3",
                  "--min-total", "10"]))]


def _pattern_items(g) -> list[dict]:
    items = []
    for p in g["errs_rep"].get("patterns") or []:
        st = g["pstats"].get(p["pattern"]) or {}
        samples = p.get("samples") or []
        first = _sample(samples[0]) if samples else None
        gu = st.get("given_up") or 0
        items.append(_item(
            "pattern:" + str(p["pattern"]), str(p["pattern"]),
            metrics={"count": p.get("count") or 0, "given_up": gu},
            facts=[("次数", "x%d" % (p.get("count") or 0)),
                   ("类别（本项目自定义分类）", p.get("class") or "—"),
                   ("未解决", str(gu)),
                   ("工具", "、".join(p.get("tools") or []) or "—")],
            anchor=_anchor(first and first.get("sid"), first and first.get("seq"),
                           first and first.get("error")),
            detail="未解决 = 该错误之后，同会话内这个工具再没被调用过。",
            suggestion="按次数从大到小处理头部；`未解决 > 0` 说明坑还在，优先看它。"
                       "点锚点能回到原文，别只看模式名。",
            level="bad" if gu else "warn"))
    return items


def _givenup_items(g) -> list[dict]:
    keep = {k for k, v in g["pstats"].items() if v.get("given_up")}
    return [i for i in _pattern_items(g)
            if i["item_id"][len("pattern:"):] in keep]


def _skill_report_items(g) -> list[dict]:
    items = []
    for s in g["skills_rep"].get("skills") or []:
        items.append(_item(
            "skill:" + str(s.get("skill")), str(s.get("skill")),
            metrics={"count": s.get("calls") or 0},
            facts=[("调用", "x%d" % (s.get("calls") or 0)),
                   ("成功", str(s.get("ok") or 0)),
                   ("失败", str(s.get("err") or 0)),
                   ("无结果", str(s.get("no_result") or 0)),
                   ("会话数", str(s.get("sids") or 0)),
                   ("来源", "、".join(s.get("sources") or []) or "—"),
                   ("低样本", "是（不下结论）" if s.get("low_sample") else "否")],
            detail="行为链：" + "；".join(list((s.get("chains") or {}).keys())[:3]),
            suggestion="失败多或低样本的先去 G4 看行为链；低样本只记录、不下结论。"
                       "疑点落到具体工具就转 G1 工具体检。",
            commands=_skill_command(s.get("skill")),
            level="bad" if (s.get("err") or 0) else "ok"))
    return items


def _skill_command(skill) -> list[dict]:
    return [_cmd(
        "看这一个 skill 的全量调用清单",
        "与 G4 同源；--skill 精确到单个 skill。",
        cli=_cli(["report-skill", "--skill", _q(skill)]))]


def _skill_chain_items(g) -> list[dict]:
    items = []
    for s in g["triage"].get("skills") or []:
        items.append(_item(
            "skillchain:" + str(s.get("skill")), str(s.get("skill")),
            metrics={"count": s.get("n_calls") or 0},
            facts=[("调用", "x%d" % (s.get("n_calls") or 0)),
                   ("成功", str(s.get("n_ok") or 0)),
                   ("来源", "、".join(s.get("sources") or []) or "—"),
                   ("疑似已有卡", "是" if s.get("known_card") else "否"),
                   ("行为链", str(len(s.get("chain") or [])) + " 步")],
            detail="最近用过、但还没鉴定好坏的 skill（蒸馏队列页的口径）。",
            suggestion="这就是「待鉴定」：行为画像还没定稿。先看全量调用清单，"
                       "确认是好用法还是坑，再决定做卡还是记进班规。",
            commands=_skill_command(s.get("skill")),
            level="ok" if s.get("known_card") else "warn"))
    return items


def _topic_items(g) -> list[dict]:
    items = []
    for t in g["topics"].get("topics") or []:
        n = int(t.get("members_count") or 0)
        items.append(_item(
            "topic:" + str(t.get("id")), str(t.get("name")),
            metrics={"count": n, "chains": int(t.get("chains_count") or 0)},
            facts=[("id", t.get("id") or "—"),
                   ("成员", str(n)),
                   ("chain", str(t.get("chains_count") or 0)),
                   ("最后活动", (t.get("last_activity") or "—")[:19])],
            detail="关键词：" + str(t.get("keywords") or "")[:200],
            suggestion="没有成员的主题要么补成员要么合并；"
                       "成员多但还没写长文的，是该动笔的信号。",
            level="ok" if n else "bad"))
    return items


def _chain_paths(cfg) -> dict:
    """`topic_id -> [chain 长文路径]`。

    chain 文件**只能靠内容定位**：文件名是 `chain-<显示名>.md`，而显示名会被截断或
    改写（实测 `chain-Skill自学习进化.md` ⟷ 主题名「Skill 自学习进化与跨平台设计」），
    用文件名拼必然错。frontmatter 里有 `topic_id`，与上游 `api_topics` 的
    `chain_index` 同一口径（`apiserve.py:529-537`：扫 `chain-*.md` + `load_chain`）。
    """
    out: dict = {}
    root = getattr(cfg, "chain_root", None)
    if not root or not Path(root).is_dir():
        return out
    try:
        from harvester.topicchain import load_chain
    except ImportError:  # pragma: no cover - 上游缺模块时不给命令，不猜路径
        return out
    for p in sorted(Path(root).glob("chain-*.md")):
        try:
            fm = (load_chain(p) or {}).get("fm")
        except (ValueError, RuntimeError, OSError):
            continue
        tid = fm.get("topic_id") if isinstance(fm, dict) else None
        if tid:
            out.setdefault(str(tid), []).append(str(p))
    return out


def _topic_chain_items(g) -> list[dict]:
    """有 chain 的主题。

    每条明细带**真实的 chain 文件路径**——`chain-validate` 是位置参数
    （真解析器实测：`chain-validate [-h] [--meta] [--db] [--out] file`），
    只给命令名会得到一个 argparse 退 2（本轮就是这么被自己的门抓到的）。
    """
    paths = _chain_paths(g["cfg"])
    items = []
    for i in _topic_items(g):
        if i["metrics"]["chains"] < 1:
            continue
        tid = i["item_id"][len("topic:"):]
        files = paths.get(tid) or []
        if files:
            i["commands"] = [
                _cmd("校验这条 chain（frontmatter + 锚点可回溯）",
                     "只读；引文逐字与锚点语义一并核对。",
                     cli=_cli(["chain-validate", _q(f)])) for f in files]
            i["facts"] = list(i["facts"]) + [["文件", Path(f).name]
                                             for f in files]
        else:
            i["commands"] = [_cmd(
                "校验 chain 长文",
                "本主题的 chain 文件没在 chain-root 下按 frontmatter.topic_id "
                "扫到——请把 <chain 长文路径> 换成实际文件（chain-root 下的 "
                "chain-*.md）。",
                cli=_cli(["chain-validate", _q("<chain 长文路径>")]))]
        items.append(i)
    return items


def _cards_cli(g) -> str:
    """`cards validate` **必须带 `--root`**。

    真解析器实测它是 required：漏了只会得到一个 argparse 退 2，用户粘上去才发现
    写错。卡片库通常不在仓内（`_rel` 就返回全路径），故这里给的是服务端起服时定
    的真实路径——命令要能跑，就不能拿占位符糊过去。
    """
    cfg = g["cfg"]
    parts = ["cards", "validate", "--root", _q(_rel(cfg.cards_root, cfg))]
    db = _rel(cfg.db, cfg)
    if db:
        parts += ["--db", _q(db)]
    return _cli(parts)


def _card_items(g) -> list[dict]:
    items = []
    for c in (g["cards"] or {}).get("cards") or []:
        errs = c.get("errors") or []
        warns = c.get("warnings") or []
        if not (errs or warns):
            continue
        items.append(_item(
            "card:" + str(c.get("path")), str(c.get("path")),
            facts=[("错误", str(len(errs))), ("警告", str(len(warns)))],
            detail="；".join(errs + warns)[:400],
            suggestion="警告里最常见的是「证据行未在锚点原文中找到」——要么改证据"
                       "引用，要么接受它是改写并人工复核。校验器只报警，不改卡片。",
            commands=[_cmd("校验卡片目录",
                           "只读；错误与警告分列。",
                           cli=_cards_cli(g))]))
    return items


# ---------------------------------------------------------------- target 定义

def _targets_all(g) -> list[dict]:
    """全部 target。`counts` 由 `targets()` 从明细派生，这里只声明**口径**。"""
    cfg = g["cfg"]
    db = _rel(cfg.db, cfg)
    meta = _rel(cfg.suggestions_meta, cfg)
    topics_meta = _rel(cfg.topics_meta, cfg)
    triage_cli = (_cli(["triage", "--db", _q(db), "--since", "7"]) if db else None)
    coverage_cli = (_cli(["suggest-agents", "--meta", _q(meta), "--coverage"])
                    if meta else None)
    eval_cli = (_cli(["harvest-eval", "--db", _q(db), "--meta", _q(meta)])
                if (db and meta) else None)
    pending = _pending_items(g)
    done = _done_items(g)
    repair = _repair_items(g)
    distill = _distill_items(g)
    oldpat = _old_pattern_items(g)
    cardsp = _cards_pending_items(g)
    verify = _inconclusive_items(g)
    evaluable = _evaluable_items(g)
    unchanged = _unchanged_items(g)
    adopted = _adopted_items(g)
    patterns = _pattern_items(g)
    givenup = _givenup_items(g)
    skills_rep = _skill_report_items(g)
    skills_tri = _skill_chain_items(g)
    topics = _topic_items(g)
    chains = _topic_chain_items(g)
    cards = _card_items(g)

    return [
        # ---- 四队列（卡片上的主数字） ----
        {"target_id": "queue:decide", "group": "queue", "queue_id": "decide",
         "label": "待你裁决", "question": "有裁决等着我做吗？", "unit": "条",
         "source": "suggestmeta.load_records[status=pending]",
         "definition": "还没拍板的建议句条数：建议已写好，但你还没决定采纳还是驳回。"
                       "已拍板的不算；本数只数建议句，主题登记另算一本账。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人",
         "suggestion": "这一队是「要我拍板」的：每条要么采纳、要么驳回，"
                       "工具不会替你决定。裁决完记得把采纳的句子人工并进 AGENTS.md。",
         "package": [c for c in [_cmd(
             "先看建议池与台账的对账",
             "列出待裁决、台账陈旧、已采纳未生效三类；只对账不代改。",
             cli=coverage_cli)] if c],
         "metrics": [_metric("pending", "待裁决建议句", "items")],
         "primary": "pending", "items": pending},
        {"target_id": "queue:repair", "group": "queue", "queue_id": "repair",
         "label": "待修补", "question": "哪里有坑该填？", "unit": "条",
         "source": "api_reports_agents.entries[unresolved_count>=1]",
         "definition": "『规矩写了但坑还在』的建议条数。坑还在 = 采纳这条建议之后"
                       "同类错误又出现过，且失败之后同会话里这个工具再没被调用过"
                       "（次数合计见下一行）。与错误报告页的『未解决次数』同源、"
                       "颗粒不同：这里是**条目数**。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人（改班规）+ 工具（补 pattern 桥）",
         "suggestion": "每条都是『规矩写了但坑还在』：先点锚点回原文，"
                       "再决定是改规矩措辞，还是补 pattern 批注让评估盯住它。",
         "package": [c for c in [_cmd(
             "跑一次效果评估（看采纳后有没有真的好）",
             "未解决率与复发率**都要不升**才算改善；任一显著变差即变差。",
             cli=eval_cli)] if c],
         "metrics": [_metric("entries", "待修补条目", "items", unit="条"),
                     _metric("unresolved", "未解决次数合计", "sum", "unresolved",
                             unit="次")],
         "primary": "entries", "items": repair},
        {"target_id": "queue:distill", "group": "queue", "queue_id": "distill",
         "label": "待蒸馏", "question": "有什么素材值得做成卡片/主题？", "unit": "个",
         "source": "triage.collect_triage.new_patterns",
         "definition": "还没变成知识的重复踩坑数：这些错误模式最近反复出现，"
                       "但建议池里还没登记处置办法。另有 skill 行为链与待做卡片，"
                       "见下方明细。",
         "where": WHERE_TRIAGE, "owner": "Agent（起草）+ 人（定稿）",
         "suggestion": "这些都是「还没变成知识的重复劳动」。挑次数高的先做，"
                       "一条模式只做一次，别批量生成一堆没人看的卡片。",
         "package": [c for c in [
             _cmd("按模板出建议池候选（人工再审）",
                  "B 档动作：点了在命令台预填好，先预演看清将写什么，确认才写。"
                  "只产候选、只写建议池文件，不改 AGENTS.md。",
                  cli=coverage_cli,
                  action_id="suggest_agents",
                  params={"min_count": 3, "max": 15}, tier="B"),
             _cmd("重跑分诊队列，输出 JSON 供后续消费",
                  "队列是现算的；这个命令把同一批数据落盘。",
                  cli=triage_cli)] if c],
         "metrics": [_metric("patterns", "新错误模式", "items", unit="个"),
                     _metric("hits", "出现次数合计", "sum", "count", unit="次")],
         "primary": "patterns", "items": distill},
        {"target_id": "queue:verify", "group": "queue", "queue_id": "verify",
         "label": "待验证", "question": "采纳的班规真的有效吗？", "unit": "条",
         "source": "harvesteval.run_eval.counts[不下结论]",
         "definition": "已采纳、但采纳前后对比的样本太少、**看不出有没有效果**的条数。"
                       "是『还看不出来』，不是『没用』——更不能混进『变差』。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人（决定等还是放宽）",
         "suggestion": "「不下结论」只能等样本或放宽门槛，**不能**当成没用去回滚。",
         "package": _verify_commands(g),
         "metrics": [_metric("entries", "不下结论的条数", "items")],
         "primary": "entries", "items": verify},

        # ---- 队列明细（卡片下面的每一行数字，也都是可点的 target） ----
        {"target_id": "queue:decide.done", "group": "detail", "queue_id": "decide",
         "label": "已裁决（可复核）", "unit": "条",
         "source": "suggestmeta.load_records[status!=pending]",
         "definition": "已拍板的建议条数（采纳 + 驳回，含主题登记那本账的条目）。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人",
         "suggestion": "已裁决不等于做对了：复核一下驳回的是不是真该驳回、"
                       "采纳的有没有按 harvest-eval 的结果退休。",
         "package": [c for c in [_cmd(
             "跑一次效果评估", "看已采纳的班规到底有没有效果。",
             cli=eval_cli)] if c],
         "metrics": [_metric("entries", "已裁决条数", "items")],
         "primary": "entries", "items": done},
        {"target_id": "queue:distill.old", "group": "detail",
         "queue_id": "distill", "label": "旧坑重现", "unit": "个",
         "source": "triage.collect_triage.old_patterns",
         "definition": "老坑又冒出来了：以前登记过建议或卡片，最近又出现的踩坑模式。",
         "where": WHERE_TRIAGE, "owner": "人 + Agent",
         "suggestion": "坑回来了说明上一版班规没拦住；改措辞，别再加一条新规则。",
         "package": [c for c in [_cmd(
             "重跑分诊队列", "本队列的数据源。", cli=triage_cli)] if c],
         "metrics": [_metric("patterns", "旧坑重现模式数", "items")],
         "primary": "patterns", "items": oldpat},
        {"target_id": "queue:distill.cards", "group": "detail",
         "queue_id": "distill", "label": "待做卡片", "unit": "张",
         "source": "目录扫描 cards_pending/*.md",
         "definition": "草稿卡片数：架子已搭好，还没定稿进卡片库。",
         "where": WHERE_CARDS, "owner": "Agent + 人",
         "suggestion": "补齐 frontmatter 的锚点与证据，校验过后入库。",
         "package": [c for c in [_cmd(
             "校验卡片目录", "只读；错误与警告分列。",
             cli=_cards_cli(g))] if c],
         "metrics": [_metric("cards", "待做卡片数", "items")],
         "primary": "cards", "items": cardsp},
        {"target_id": "queue:verify.evaluable", "group": "detail",
         "queue_id": "verify", "label": "可评估的已采纳", "unit": "条",
         "source": "harvesteval.run_eval.adopted_total",
         "definition": "能做效果对比的已采纳条数（主题登记的不算）。"
                       "这个数就等于下面明细的行数——数字与清单天然一致。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人",
         "suggestion": "这份清单之外的已采纳条目没法评估——多半是没写 `pattern:` "
                       "批注，或属于主题登记。想让评估覆盖更多，先补批注。",
         "package": [c for c in [_cmd("跑一次效果评估",
                                      "三值判定 + 样本门槛。",
                                      cli=eval_cli)] if c],
         "metrics": [_metric("entries", "可评估条数", "items",
                             source="harvesteval.run_eval.adopted_total")],
         "primary": "entries", "items": evaluable},
        {"target_id": "queue:verify.unchanged", "group": "detail",
         "queue_id": "verify", "label": "无变化（建议退休）", "unit": "条",
         "source": "harvesteval.run_eval.counts[无变化]",
         "definition": "采纳前后对比判『没变化』的条数。工具的建议是让这条规矩退休，"
                       "但不代你做主（不会自动改 AGENTS.md）。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人（决定退休）",
         "suggestion": "采纳前后没差别 → 这条班规大概是白占地方。退休要人决定。",
         "package": [c for c in [_cmd("跑一次效果评估", "看还有没有别的判语。",
                                      cli=eval_cli)] if c],
         "metrics": [_metric("entries", "无变化条数", "items",
                             source="harvesteval.run_eval.counts[无变化]")],
         "primary": "entries", "items": unchanged},

        # ---- 三环各段 ----
        {"target_id": "ring:errors.0", "group": "ring", "ring_id": "errors",
         "label": "错误 → 班规 → 效果 · 有料（全库错误）", "unit": "条",
         "source": "errstats.collect_errors_from_db",
         "definition": "全库错误步骤总数。按踩坑模式归堆统计：次数 = 各模式次数之和，"
                       "明细就是模式清单（数字与明细同源）。「模式」= 把同一种坑归成一堆"
                       "（按出错形状归并），不是『不同方式』——「错误模式 N 个」就是 N 种不同的坑，"
                       "全部错误按坑归并而来。",
         "where": WHERE_REPORTS_ERRORS, "owner": "人",
         "suggestion": "按次数从大到小处理头部；`未解决 > 0` 的模式说明坑还在。",
         "package": [_cmd("在命令台跑错误三分类报告",
                          "A 档只读动作，与报告页同源同值。",
                          action_id="report_errors", params={}, tier="A")],
         "metrics": [_metric("errors", "全库错误步骤数", "sum", "count", unit="条"),
                     _metric("patterns", "错误模式数", "items", unit="个")],
         "primary": "errors", "items": patterns},
        {"target_id": "ring:errors.1", "group": "ring", "ring_id": "errors",
         "label": "错误 → 班规 → 效果 · 已行动（已采纳）", "unit": "条",
         "source": "suggestmeta.load_records[status=adopted]",
         "definition": "已经采纳成 AI 规矩的条数（含主题登记）。注意这本账与第一段"
                       "是**两本账**：第一段数的是错误次数，这里数的是规矩条数——"
                       "不是从第一段里挑出来的一批，不互为子集。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人",
         "suggestion": "采纳了不等于有用：效果由评估命令判，"
                       "『没变化』建议退休、『变差』才回滚。",
         "package": _verify_commands(g),
         "metrics": [_metric("entries", "已采纳条数", "items")],
         "primary": "entries", "items": adopted},
        {"target_id": "ring:errors.2", "group": "ring", "ring_id": "errors",
         "label": "错误 → 班规 → 效果 · 待验证（不下结论）", "unit": "条",
         "source": "harvesteval.run_eval.counts[不下结论]",
         "definition": "已采纳条目里，采纳前后对比的样本太少、**看不出有没有效果**的"
                       "条数——是上一段「已采纳」的子集（评估只对已采纳的做）。",
         "where": WHERE_REPORTS_AGENTS, "owner": "人",
         "suggestion": "只能等样本或放宽门槛；不许把「看不出来」当成「没用」。",
         "package": _verify_commands(g),
         "metrics": [_metric("entries", "不下结论条数", "items")],
         "primary": "entries", "items": verify},
        {"target_id": "ring:errors.givenup", "group": "ring", "ring_id": "errors",
         "label": "错误 → 班规 → 效果 · 未解决（模式维度）", "unit": "次",
         "source": "errstats.pattern_stats.given_up",
         "definition": "踩坑模式维度的未解决：有至少 1 次『失败后工具再没被调用』的"
                       "模式个数（模式数）与次数合计（次数）——同一批明细数出两条数字，"
                       "模式数与次数各算各的。",
         "where": WHERE_REPORTS_ERRORS, "owner": "人",
         "suggestion": "这些模式是「放弃了但没解决」：优先给它们补班规或改工具，"
                       "别只盯着总次数。",
         "package": [_cmd("在命令台跑错误三分类报告",
                          "A 档只读。", action_id="report_errors",
                          params={}, tier="A")],
         "metrics": [_metric("given_up", "未解决次数合计", "sum", "given_up",
                             unit="次"),
                     _metric("patterns", "未解决模式数", "items", unit="个")],
         "primary": "given_up", "items": givenup},
        {"target_id": "ring:skill.0", "group": "ring", "ring_id": "skill",
         "label": "skill → 鉴定 → 修复 · 有料（行为画像）", "unit": "个",
         "source": "api_reports_skills.skills",
         "definition": "行为画像里登记的 skill 个数（每个 skill 一行）。",
         "where": WHERE_REPORTS_SKILL, "owner": "人 + Agent",
         "suggestion": "先看失败多与低样本的；低样本只记录、不下结论。",
         "package": [c for c in [_cmd(
             "跑一次行为画像，把低样本标出来",
             "--min-calls 5：调用不到 5 次的标 low-sample，页面不该拿它下结论。",
             cli=(_cli(["report-skill", "--min-calls", "5"]) if db else None))] if c],
         "metrics": [_metric("skills", "行为画像 skill 数", "items", unit="个"),
                     _metric("calls", "调用次数合计", "sum", "count", unit="次")],
         "primary": "skills", "items": skills_rep},
        {"target_id": "ring:skill.1", "group": "ring", "ring_id": "skill",
         "label": "skill → 鉴定 → 修复 · 待鉴定（行为链）", "unit": "条",
         "source": "triage.collect_triage.skills",
         "definition": "还没鉴定好坏的 skill 行为链条数（最近用过、但没有定论是好用法还是坑）。",
         "where": WHERE_TRIAGE, "owner": "人 + Agent",
         "suggestion": "有调用、没定稿：确认是好用法还是坑，再决定做卡还是记班规。",
         "package": [c for c in [_cmd("重跑分诊队列（本队列的数据源）",
                                      "队列是现算的；这个命令把同一批数据落盘。",
                                      cli=triage_cli)] if c],
         "metrics": [_metric("skills", "待鉴定 skill 数", "items", unit="个"),
                     _metric("calls", "调用次数合计", "sum", "count", unit="次")],
         "primary": "skills", "items": skills_tri},
        {"target_id": "ring:topics.0", "group": "ring", "ring_id": "topics",
         "label": "主题 → chain → 卡片 · 有料（注册主题）", "unit": "个",
         "source": "api_topics.topics",
         "definition": "已注册的主题个数（每个主题一行）。",
         "where": WHERE_TOPICS, "owner": "人",
         "suggestion": "成员 0 的要补成员或合并；成员多的看要不要出 chain。",
         "package": [c for c in [_cmd(
             "列出主题注册表", "只读；--full 会带上更多字段。",
             cli=(_cli(["topic", "list", "--meta", _q(topics_meta)])
                  if topics_meta else None))] if c],
         "metrics": [_metric("topics", "注册主题数", "items", unit="个"),
                     _metric("members", "成员总数", "sum", "count", unit="条")],
         "primary": "topics", "items": topics},
        {"target_id": "ring:topics.1", "group": "ring", "ring_id": "topics",
         "label": "主题 → chain → 卡片 · 已行动（已发布 chain）", "unit": "条",
         "source": "api_topics.topics[chains_count]",
         "definition": "已发布的主题长文条数。一条主题可能有多篇长文，"
                       "所以条数 = 明细里各主题长文数之和，明细本身是『有长文的主题』。",
         "where": WHERE_TOPICS, "owner": "人",
         "suggestion": "改过 chain 就要重跑校验与审计（引文逐字 + 锚点语义）。",
         #: 命令包刻意留空：chain-validate 是**按文件**跑的（位置参数），
         #: 逐文件命令挂在下面每条明细上；`workRows()` 会把两处摊平成一份清单，
         #: 在这里再放一遍只会造成重复。
         "package": [],
         "metrics": [_metric("chains", "已发布 chain 数", "sum", "chains",
                             unit="条"),
                     _metric("topics", "有 chain 的主题数", "items", unit="个")],
         "primary": "chains", "items": chains},
        {"target_id": "ring:topics.2", "group": "ring", "ring_id": "topics",
         "label": "主题 → chain → 卡片 · 待确认（卡片警告/错误）", "unit": "张",
         "source": "api_cards.summary[error+warn]",
         "definition": "卡片库里带警告/错误的卡片数"
                       + ("" if g["cards"] is not None else
                          "（未配置卡片目录，本项按 0 计）"),
         "where": WHERE_CARDS, "owner": "人 + Agent",
         "suggestion": "警告多是「证据行与锚点原文对不上」：改引用，"
                       "或人工复核后接受。",
         "package": [_cmd("校验卡片目录", "只读；错误与警告分列。",
                          cli=_cards_cli(g))],
         "metrics": [_metric("cards", "待确认卡片数", "items")],
         "primary": "cards", "items": cards},
    ]


def targets(g) -> list[dict]:
    """全部 target：`counts` 由明细**派生**（故不可能与明细不一致）。"""
    out = _targets_all(g)
    for t in out:
        items = t["items"]
        counts = {}
        for m in t["metrics"]:
            if m["mode"] == "items":
                counts[m["name"]] = len(items)
            else:
                counts[m["name"]] = sum(int(i["metrics"].get(m["sum_field"]) or 0)
                                        for i in items)
        t["counts"] = counts
        t["count"] = int(counts.get(t["primary"], 0))
        t["level"] = "ok" if t["count"] == 0 else "warn"
    return out


#: 队列明细行 → (target_id, metric 名)。**工作台上卡片下面的每一行数字都在这里**：
#: 想加一个数字，就必须同时说清"它是哪个 target 的哪条指标"，否则页面会拿不到明细
#: （`build_queue_details` 会抛 KeyError，测试会红）。`None` = 聚合数、不派活。
DETAIL_MAP = {
    "decide": [
        ("已裁决（可复核）", "queue:decide.done", "entries"),
        ("噪声登记（不属于任何主题）", None, None),
    ],
    "repair": [
        ("未解决次数合计", "queue:repair", "unresolved"),
        ("未解决模式数", "ring:errors.givenup", "patterns"),
        ("未解决次数（模式维度）", "ring:errors.givenup", "given_up"),
    ],
    "distill": [
        ("旧坑重现", "queue:distill.old", "patterns"),
        ("skill 行为链", "ring:skill.1", "skills"),
        ("待做卡片", "queue:distill.cards", "cards"),
    ],
    "verify": [
        ("可评估的已采纳", "queue:verify.evaluable", "entries"),
        ("无变化（建议退休）", "queue:verify.unchanged", "entries"),
        ("未评估（pending/rejected 与主题登记）", None, None),
    ],
}

#: 聚合数（不派活）的口径与当前值来源——页面要显式标"这是聚合数，不是待处理清单"。
_AGGREGATE = {
    ("decide", "噪声登记（不属于任何主题）"): {
        "unit": "条", "source": "suggestmeta.registry_counts[sessions_noise]",
        "definition": "已判定『不属于任何主题』的散会话登记条数——**聚合数，"
                      "不是待处理清单**（想逐条判定归属，去主题页），"
                      "所以这里不配明细、也不给命令。",
        "where": WHERE_TOPICS},
    ("verify", "未评估（pending/rejected 与主题登记）"): {
        "unit": "条", "source": "harvesteval.run_eval.skipped(_topic)",
        "definition": "**聚合数**：还没做效果评估的条数（没拍板的、已驳回的、"
                      "主题登记的）。『没评估』与『评估了没变化』是两回事，"
                      "必须分开数；这里只报数、不给明细——它们不是等你处理的活。",
        "where": "—"},
}


def build_queue_details(g, idx) -> dict:
    """`queue_id -> [明细行]`。每行**引用**某个 target 的某条指标（或显式声明是聚合数）。

    这是「队列明细对不上」的正面修法：明细行不再自己算数，而是**指向**那条指标的
    真源；页面点它就直接打开那份明细。
    """
    by_id = {t["target_id"]: t for t in idx}
    agg = _aggregate_values(g)
    out = {}
    for qid, rows in DETAIL_MAP.items():
        lst = []
        for label, tid, metric in rows:
            if tid is None:
                a = _AGGREGATE[(qid, label)]
                lst.append({"label": label, "value": agg[(qid, label)],
                            "unit": a["unit"], "source": a["source"],
                            "definition": a["definition"],
                            "where": a["where"], "target": None,
                            "metric": None, "aggregate": True})
                continue
            t = by_id[tid]
            m = next(x for x in t["metrics"] if x["name"] == metric)
            lst.append({"label": label, "value": m["value"],
                        "unit": m.get("unit") or t["unit"],
                        "source": m.get("source") or t["source"],
                        "definition": m["label"] + "。" + t["definition"],
                        "where": t["where"], "target": tid, "metric": metric,
                        "aggregate": False})
        out[qid] = lst
    return out


def _aggregate_values(g) -> dict:
    """聚合数的当前值（不派活的那两行）。口径与判据写在 `_AGGREGATE`。"""
    return {("decide", "噪声登记（不属于任何主题）"): g["registry"].get("noise", 0),
            ("verify", "未评估（pending/rejected 与主题登记）"):
                g["ev"].get("skipped", 0) + g["ev"].get("skipped_topic", 0)}


# ---------------------------------------------------------------- 对外形状

def _reconcile(items, m, shown) -> dict:
    if m["mode"] == "items":
        return {"metric": m["name"], "mode": "items", "expected": len(items),
                "actual": len(shown), "by": "len(items)",
                "items_total": len(items), "shown": len(shown),
                "truncated": len(shown) < len(items),
                "ok": bool(len(shown) == len(items))}
    total = sum(int(i["metrics"].get(m["sum_field"]) or 0) for i in items)
    actual = sum(int(i["metrics"].get(m["sum_field"]) or 0) for i in shown)
    return {"metric": m["name"], "mode": "sum", "expected": total,
            "actual": actual, "by": "sum(item.metrics.%s)" % m["sum_field"],
            "items_total": len(items), "shown": len(shown),
            "truncated": len(shown) < len(items),
            "ok": bool(total == actual and len(shown) == len(items))}


def _public_item(i: dict) -> dict:
    return {"item_id": i["item_id"], "title": i["title"],
            "metrics": dict(i["metrics"]), "facts": i["facts"],
            "anchor": i["anchor"], "detail": i["detail"],
            "suggestion": i["suggestion"], "commands": i["commands"],
            "level": i["level"], "owner": i["owner"]}


def _public_target(t: dict, *, with_items: bool) -> dict:
    shown = t["items"][:MAX_ITEMS]
    rec = [_reconcile(t["items"], m, shown) for m in t["metrics"]]
    d = {"target_id": t["target_id"], "group": t["group"],
         "label": t["label"], "unit": t["unit"], "count": t["count"],
         "level": t["level"], "source": t["source"],
         "definition": t["definition"], "where": t["where"],
         "owner": t.get("owner", ""), "suggestion": t.get("suggestion", ""),
         "primary": t["primary"],
         "metrics": [dict(m, value=t["counts"][m["name"]]) for m in t["metrics"]],
         "package": t.get("package") or [], "reconcile": rec,
         "reconcile_ok": bool(rec) and all(r["ok"] for r in rec)}
    if t["group"] == "queue":
        d["queue_id"] = t.get("queue_id")
        d["question"] = t.get("question", "")
    elif t["group"] == "detail":
        d["queue_id"] = t.get("queue_id")
    else:
        d["ring_id"] = t.get("ring_id")
    if with_items:
        d["items"] = [_public_item(i) for i in shown]
    return d


def build_index(g) -> list[dict]:
    """全部 target 的索引（带各条指标值与对账结论，不带明细）——给 `/api/overview`。"""
    return [_public_target(t, with_items=False) for t in targets(g)]


def build_workitems(cfg, target_id: str) -> dict:
    """一个 target 的**处理单**：明细 + 处理建议 + 命令包 + 逐指标对账。"""
    g = collect(cfg)
    found = [t for t in targets(g) if t["target_id"] == target_id]
    if not found:
        raise KeyError(target_id)
    d = _public_target(found[0], with_items=True)
    d["view_workitems_api"] = VIEW_WORKITEMS_API
    d["db_fingerprint"] = g["errs_rep"].get("db_fingerprint")
    d["hint"] = ("明细与数字**同源**：每条指标都由下面这份明细按 mode 派生。"
                 "任一条 `reconcile_ok=false` 就说明清单不完整，先别照它派活。")
    return d


def target_ids(g) -> list[str]:
    return [t["target_id"] for t in targets(g)]
