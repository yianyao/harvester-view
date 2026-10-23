# -*- coding: utf-8 -*-
"""commandgen -- 从**真实 argparse** 生成命令注册表（SOP §5.4/§5.5 的机器形态）。

为什么由解析器生成而不是手抄：手抄一份 40 命令 × 参数表，第一次上游加参数
就漂移且**没人会发现**。这里用 `harvester.cli.build_parser()`（v0.47 特意
抽出来的纯函数）走一遍真解析器 → 命令名/参数名/类型/默认值全部是**事实**；
人手只负责两件事：

1. **参数数据源标注**（`SRC` 表：哪个参数该渲染成选择器、选择器数据从哪来）
   ——这是产品判断，不是事实，必须人写；
2. **动词卡归并**（`CARDS` 表：40 命令 → 13 张卡的意图归并）与三档权限。

产出 `actions/commands.json`（**已提交**：frontend 的 `index.html` 消费它，
是功能性数据而非文档——铁律 1.3 白名单）。

用法：
    python -m actions.commandgen            # 写 commands.json 并打印摘要
    python -m actions.commandgen --check    # 只比对，不写（CI/测试用；有漂移=1）
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import ensure_harvester_importable  # `upstream_root` 不用（已清）

OUT = Path(__file__).resolve().parent / "commands.json"

#: 动词卡（13 张，SOP §5.4 总表照录）。tier 是卡的默认档，命令可覆写。
CARDS: list[dict] = [
    {"card_id": "collect", "label": "采集更新", "tier": "B",
     "order": 1,
     "desc": "把新会话收进库：日常用 update（增量），sync 是全量对账（重活）。"
             "反过来的动作也在这里：suppress 把没营养的会话从**仓内副本**里"
             "抑制掉（不删采集源）。",
     "writes": "harvester.db（采集库，钦定写入路径）+ topics_meta.db 的抑制表"},
    {"card_id": "browse", "label": "浏览检索", "tier": "A",
     "order": 2,
     "desc": "看数据源状态、扫纲要、全文检索、按序号读会话。",
     "writes": ""},
    {"card_id": "export", "label": "导出交接", "tier": "A",
     "order": 3,
     "desc": "导出/聚合/打包/起草蒸馏包——产物落 exports/，供 Agent 消费。",
     "writes": ""},
    {"card_id": "tools", "label": "工具体检", "tier": "A",
     "order": 4,
     "desc": "G1 工具调用与失败率；trace 口径另有一份。",
     "writes": ""},
    {"card_id": "errors", "label": "错误验尸", "tier": "A",
     "order": 5,
     "desc": "G2 错误三分类与失败翼报告。",
     "writes": ""},
    {"card_id": "skill", "label": "skill 鉴定", "tier": "A",
     "order": 6,
     "desc": "G4 Skill 行为画像；join 把 chain 锚点与 skill 调用点交叉。",
     "writes": ""},
    {"card_id": "triage", "label": "分诊蒸馏", "tier": "A",
     "order": 7,
     "desc": "蒸馏队列与主题梳理流水线（含深度分诊；实测约 9 分钟，标重活）。",
     "writes": ""},
    {"card_id": "ledger", "label": "班规裁决", "tier": "A",
     "order": 8,
     "desc": "建议池生成与状态落库、人账同步、A/B 效果评估。",
     "writes": "suggestions_meta.db（B 档动作）"},
    {"card_id": "topic", "label": "主题档案", "tier": "A",
     "order": 9,
     "desc": "主题注册表全家族 + 五档时间线 + chain 导出与校验审计。",
     "writes": "topics_meta.db（register/add/merge 属 B 档）"},
    {"card_id": "artifacts", "label": "产物回收", "tier": "A",
     "order": 10,
     "desc": "从 Write/Edit 参数回源提取产物版本；查看用 show。",
     "writes": "artifacts_meta.db（extract 属 B 档）"},
    {"card_id": "kb", "label": "知识库", "tier": "A",
     "order": 11,
     "desc": "卡片校验/脚手架、知识库骨架与盘点。",
     "writes": "卡片目录 / 知识库骨架（kb-init、cards new 属 B 档）"},
    {"card_id": "keywords", "label": "词频账本", "tier": "A",
     "order": 12,
     "desc": "n-gram 关键词统计与 keywords_meta.db 回收。",
     "writes": "keywords_meta.db（gc 属 B 档）"},
    {"card_id": "service", "label": "长驻服务", "tier": "C",
     "order": 13,
     "desc": "要占端口常驻或要人亲手登录的动作——**只生成命令**，页面永不执行。",
     "writes": "占用端口 / 浏览器登录态"},
]

#: 命令 → 动词卡。**必须覆盖全部子命令**（`--check` 与测试会红）。
#: 嵌套子命令写成 `父 子`（如 `cards validate`）。
COMMAND_CARD: dict[str, str] = {
    "update": "collect", "sync": "collect", "probe": "collect",
    # v0.49 会话抑制（后端线，2026-10-13）：键名必须与真解析器的**叶子命令名**
    # 逐字一致（`--check` 会红）。归到「采集更新」卡：它是采集的反向动作，
    # 且同属"仓内副本"的进出。
    "suppress list": "collect", "suppress add": "collect",
    "suppress remove": "collect", "suppress prune-exports": "collect",
    # v0.52 候选清单（只读）：抑制的去 sid 化入口，同属"仓内副本"进出
    "suppress candidates": "collect",
    # P5-⑧ 零散登记（2026-10-16 批准）：分诊裁决的 meta 侧出口——
    # 判「不归任何主题」的会话不再进候选簇，归「分诊蒸馏」卡
    "noise list": "triage", "noise add": "triage",
    "adapters": "browse", "scan": "browse", "search": "browse",
    "read": "browse", "index": "browse",
    "export": "export", "aggregate": "export", "pack": "export",
    "draft": "export", "export-analysis": "export",
    "report-tools": "tools", "report-traces": "tools",
    "report-errors": "errors", "report-chains": "errors",
    # P6 归因层（v0.79，用户 2026-10-22 批准后端改动）：attrib 只读 harvester.db
    # → 归「错误分析」卡（它就是错误分析的延伸：把计数变成根因候选）；
    # attrib-review 写 attrib_meta.db 台账 → 同卡，档位 B（与 cards review 同模式）
    "attrib": "errors", "attrib-review": "errors",
    # P6-6 评审包（v0.79）：只读，四类 kind 都是"把数据面打成可评审包"，
    # 其中 errors/attrib 两类就是错误分析的出口 → 归「错误分析」卡；
    # skills/chains 两类同命令产出，按**命令**归卡（不按 kind 拆卡），
    # 否则同一命令会出现在两张卡里、注册表按命令名去重会丢一张。
    "evidence-pack": "errors",
    # 第④b步 llm-pack（2026-10-23 用户批准）：只读，把"现在判不了的东西"连证据
    # 打成 llm-pack/1 交 LLM 复核 → 与 evidence-pack 同属错误/归因分析出口，
    # 归「错误分析」卡（按**命令**归卡，不按 face 拆）。
    "llm-pack": "errors",
    # P5-⑤ 每日速览（2026-10-16 批准）：四队列+三报告聚合，只读；
    # 归「错误分析」卡（它的三段报告全在这卡里，队列数字是附加项）
    "report-daily": "errors",
    "report-skill": "skill", "report-skill-join": "skill",
    "triage": "triage", "topic-candidates": "triage",
    "topic-consolidate": "triage",
    "suggest-agents": "ledger", "suggest-status": "ledger",
    "ledger-sync": "ledger", "harvest-eval": "ledger",
    "topic": "topic", "chain-validate": "topic", "chain-audit": "topic",
    "artifacts": "artifacts",
    "cards": "kb", "cards validate": "kb", "cards new": "kb",
    # v0.74 卡片复核台账（上游）：人裁过的警告不再显示——写 cards_meta.db → B
    "cards review": "kb",
    # v0.78 触发词消歧素材（P6-卡片触发钩子）：只读只列不裁决 → A
    "cards index-md": "kb",
    "kb-init": "kb", "kb-stats": "kb",
    # v0.66 正本→镜像发布（用户裁决：正本随项目走，WorkBuddy 位只作镜像）。
    # 档位在 TIER_OVERRIDE 定为 B（写镜像位文件，仓外写→两段提交；幂等不删）。
    "kb-publish": "kb",
    "keywords": "keywords", "keywords-gc": "keywords",
    "deadcode-scan": "service", "regress": "service",
    "mcp-serve": "service", "api-serve": "service",
    # 注意：裸 `weblogin` **不是叶子命令**（必须选 check/init-config/prepare），
    # 故这里三行才是全部事实——不要为了"看起来齐全"凭空补一条 weblogin。
    "weblogin check": "service",
    "weblogin init-config": "service", "weblogin prepare": "service",
}

#: 命令级用途（v0.90 P9，用户 2026-10-23 第 2 条：悬停提示 7 条一模一样）。
#: **每条一句、按真实行为写**——它是命令台悬停与参数区标题的唯一来源；
#: `--check` 会要求「每条命令都有、且互不重复」，宁可红也不回落成卡说明。
#: 判据：这句话能不能让人决定"点不点它"？（不能 → 重写）
PURPOSE: dict[str, str] = {
    "update": "增量收新会话进 harvester.db（日常用这一条）",
    "sync": "全量对账重收一遍（重活，慢）",
    "probe": "探测各采集源是否可用（不写库）",
    "suppress list": "列已抑制的会话",
    "suppress add": "把会话加进抑制名单（不删采集源）",
    "suppress remove": "把会话移出抑制名单",
    "suppress prune-exports": "清掉被抑制会话在 exports 的旧副本",
    "suppress candidates": "从库里挑疑似没营养的会话，供你决定抑制",
    "adapters": "列出已适配的采集源及其路径",
    "scan": "扫 exports 目录，生成会话纲要清单",
    "search": "全文检索会话消息（FTS，中文按 bigram）",
    "read": "按序号读一个会话的原文回合",
    "index": "把 exports 重建进索引库（重活）",
    "export": "把会话导出成 Markdown 交接材料",
    "aggregate": "跨实例聚合多份库",
    "pack": "把主题相关会话打包成蒸馏输入",
    "draft": "按主题起草 chain 草稿",
    "export-analysis": "导出分析数据（errors/tools/skills/triage）",
    "report-tools": "工具调用次数与失败率（G1）",
    "report-traces": "按 trace 口径再看一遍工具统计",
    "report-errors": "错误三分类与失败翼（G2）",
    "report-chains": "工具链失败模式：长回合/连击/循环/空转",
    "report-daily": "四队列 + 三段报告的每日速览",
    "report-skill": "单个 skill 的行为画像（G4）",
    "report-skill-join": "chain 锚点与 skill 调用点交叉",
    "attrib": "错误模式 → 根因候选（候选，不是裁决）",
    "attrib-review": "把人的 why/fix 裁决记进 attribations 台账",
    "evidence-pack": "把数据面打成可评审包（人读 + 机读）",
    "llm-pack": "打包给 LLM：事实 + 归因候选 + 判定槽",
    "triage": "蒸馏队列分诊（含深挖，约 9 分钟）",
    "topic-candidates": "列主题候选簇",
    "topic-consolidate": "把候选簇合并/登记成主题",
    "topic": "主题注册表：list/add/merge/md/…",
    "chain-validate": "校验 chain 结构：frontmatter/阶段/锚点",
    "chain-audit": "审计 chain 的引文与锚点（可落裁决）",
    "suggest-agents": "生成 AGENTS.md 候选条目",
    "suggest-status": "把建议状态落库（B 档）",
    "ledger-sync": "人账与库内建议对账",
    "harvest-eval": "A/B 建议采纳效果评估",
    "artifacts": "从 Write/Edit 参数回源提取产物版本",
    "cards validate": "校验卡片本体与触发词（只读）",
    "cards new": "按 sid 起草一张新卡片（写文件）",
    "cards review": "记卡片警告的人工裁决（写台账）",
    "cards index-md": "列卡片在 Markdown 里的引用情况（只读）",
    "kb-init": "初始化知识库骨架",
    "kb-stats": "知识库盘点：数量/分类/最近更新",
    "kb-publish": "把正本发布到镜像位（B 档）",
    "keywords": "n-gram 关键词统计并落账",
    "keywords-gc": "回收 keywords_meta.db 的失效行",
    "deadcode-scan": "AST 扫未用 import/函数/常量",
    "regress": "跑回归基线并与上次对比",
    "mcp-serve": "起 MCP 服务（常驻，占端口）",
    "api-serve": "起只读 API 服务（常驻，占 8765）",
    "weblogin check": "检查登录态是否可用",
    "weblogin init-config": "写登录配置",
    "weblogin prepare": "打开浏览器准备登录",
    "noise list": "列已登记的「不归任何主题」会话",
    "noise add": "把会话登记为噪声（不再进候选簇）",
}

#: 命令 → 三档覆写（缺省=取所在卡的档）。依据提案 §5.4/§5.6：
#: A 只读直执；B 写 meta/采集库（两段提交）；C 只生成命令永不执行。
TIER_OVERRIDE: dict[str, str] = {
    # 写采集库或写 meta → B
    "update": "B", "sync": "B", "export": "B", "aggregate": "B",
    "scan": "B", "probe": "B", "index": "B", "draft": "B",
    "keywords": "B", "keywords-gc": "B",
    "suggest-agents": "B", "suggest-status": "B", "ledger-sync": "B",
    "topic": "B", "topic-consolidate": "B", "artifacts": "B",
    "kb-init": "B", "cards new": "B",
    # v0.74 卡片复核落账：写 cards_meta.db → B（UPSERT 可改判，非破坏性）
    "cards review": "B",
    # P6 归因台账（v0.79）：写 attrib_meta.db → B（UPSERT 可改判，非破坏性）。
    # attrib 本身只读 → A（走末尾缺省，无需在这里写）。
    "attrib-review": "B",
    # v0.78 index-md：只读清单（stdout/--out 素材）→ A；--out 写的是
    # 用户指定的新素材文件，不碰任何库/既有文件
    "cards index-md": "A",
    # v0.66 kb-publish：写镜像位文件（仓外写）→ B；幂等、不删镜像文件
    "kb-publish": "B",
    # 长驻/需人亲手登录 → C（永不执行）
    "mcp-serve": "C", "api-serve": "C",
    # v0.49 会话抑制：查抑制表是只读 → A；其余三个写库/删副本 → B
    # （卡默认就是 B，这里显式写出来是为了让"这条为什么能写"有据可查）。
    # v0.52 候选清单同为只读 → A。
    "suppress list": "A", "suppress candidates": "A",
    "suppress add": "B", "suppress remove": "B",
    "suppress prune-exports": "B",
    # P5-⑧ 零散登记：list 只读 → A；add 写 topics_meta 的幂等表 → B
    "noise list": "A", "noise add": "B",
    "weblogin check": "C", "weblogin init-config": "C",
    "weblogin prepare": "C",
    # service 卡里两个**不该被连坐**的例外：它们只是打印/在临时目录里造数据，
    # 既不占端口也不要人登录，归 C 会让"零复制粘贴"验收凭空少两项。
    #   deadcode-scan 只读源码树；regress 全程只用临时库（真库一字节不动）
    "deadcode-scan": "A", "regress": "A",
    # 其余（report-*/search/read/list/timeline/validate/audit/deadcode/regress）
    # 取 A：只读 harvester.db，或只在临时库/临时目录里造数据
}

#: 少数命令带**位置式子动作**（`topic list|show|register…`），子动作的三档
#: 归属不同：读的归 A，写的归 B。命令级 tier 取**最宽**的子动作档（这里
#: 有写动作 → B），另在 subactions 里逐项标注，页面据此渲染逐项权限。
#: 不提这个区分，"topic 到底能不能直接在页面跑"就没法回答。
#: 注意（实测 2026-10-12）：CLI 的 `topic` 子动作只有 15 个，**没有
#: `timeline`**——五档时间线是库函数 `topics.timeline`，由本仓
#: `/api/topic/<id>/timeline` 端点透出，不是一条 CLI 命令。
SUBACTION_CHOICES: dict[str, str] = {"topic": "cmd"}
SUBACTION_TIER: dict[str, dict[str, str]] = {
    "topic": {
        "list": "A", "show": "A", "turns": "A", "dedupe": "A",
        "export": "A", "md": "A", "chain": "A", "pack": "A",
        "keywords": "B", "register": "B", "add": "B", "remove": "B",
        "merge": "B", "rename": "B", "delete": "B",
    },
}

#: 参数 → 数据源标注（SOP §5.5 的机器形态）。`none` = 自由文本/开关，
#: 页面渲染成输入框或复选框；`server` = 起服时已定，**页面不暴露**
#: （SOP §5 红线：命令台不暴露路径类参数）；`chips` = 预设 chips；
#: 其余 = 有对应端点，必须渲染成选择器而不是手输框。
SRC: dict[str, str] = {
    # —— 数据源端点（可选项来自库，不许手输）——
    "topic_id": "topics_api",
    "sid": "sessions_api",
    "sids": "sessions_api",
    "chain": "cards_api",
    "input": "sessions_api",
    "from_dir": "exports_dir",
    # —— 时间窗 / 枚举 / 数值 ——
    "days": "chips",
    "since": "chips",
    "until": "chips",
    "level": "chips",
    "top": "none",
    "min_count": "none",
    "min_calls": "none",
    "keep_runs": "none",
    "keywords_keep_runs": "none",
    "top_n": "none",
    "turn": "none",
    "no": "none",
    "limit": "none",
    "batch": "none",
    "all": "none",
    "deep": "none",
    "erronly": "none",
    "errors_only": "none",
    "samples": "none",
    "kind": "none",
    "format": "none",
    "with_notes": "none",
    # —— 自由文本 ——
    "q": "none",
    "name": "none",
    "text": "none",
    "pattern": "none",
    "note": "none",
    "key": "none",
    "status": "none",
    "reason": "none",
    "owner": "none",
    "impact": "none",
    "adopted_at": "none",
    "product": "none",
    "action": "none",
    "cmd": "none",
    "select": "none",
    "sources": "none",
    "file": "none",
    "path": "none",
    "id": "none",
    "id_": "topics_api",
    "from_": "topics_api",
    "evidence": "none",
    "query": "none",
    "prompt": "none",
    "title": "none",
    "keywords": "none",
    # —— 输出 / 预算 / 枚举：不接受自由文本（选择器或自动命名）——
    "out": "auto_out",
    "plan_out": "auto_out",
    "coverage_out": "auto_out",
    "seed_out": "auto_out",
    "triage_out": "auto_out",
    "triage_brief_out": "auto_out",
    "max_chars": "chips",
    "cap": "chips",
    "role": "chips",
    "samples": "chips",
    "json": "chips",
    "list_limit": "chips",
    "triage_max_turns": "chips",
    "brief_chars": "chips",
    "noise_max_chars": "chips",
    "craft_topic": "topics_api",
    "topic": "topics_api",
    "skill": "chips",
    "type": "chips",
    "source": "chips",
    # 服务端已定、**但值本身是可选项**（页面给选择器而不是输入框）：
    # `--corpus` 只有 default/drift 两种，选错没意义
    "corpus": "chips",
    # 服务端已定、且**页面绝不暴露**（regress 的临时目录父目录：让人指路
    # 就等于给"顺手指向真库"留后门；接受客户端传值同样不行）
    "temp_base": "server",
    # —— 服务端已定（页面不暴露）——
    "root": "server",
    "db": "server",
    "inbox": "server",
    "exports": "server",
    "topics_meta": "server",
    "keywords_meta": "server",
    "suggestions_meta": "server",
    "cards_root": "server",
    "chain_root": "server",
    "artifacts_meta": "server",
    "artifact_meta": "server",
    "queue": "server",
    "plan": "none",
    "apply": "none",
    "force": "none",
    "vacuum": "none",
    "prune_keywords": "none",
    "no_prune_keywords": "none",
    "dry_run": "none",
    "verbose": "none",
}

#: 其余以「库/文件路径」形态出现、**只有起服时才知道**的参数名（dest）。
#: 一律归 `server`：页面不暴露路径类参数（SOP §5 红线：meta 库路径起服务
#: 时已定）。按 dest 精确列举而不用后缀猜测——猜测会把 `--craft-topic`
#: （自由文本）之类误标成路径。
#: **刻意不收 `sources`**：`--sources` 是用户自己的数据源声明文件名（相对
#: 项目根，换一份配置就换一个名），属自由文本而非"服务端已定的库路径"。
#: `test_sources_stays_free_text` 钉住这个判断，免得被顺手"统一"掉。
SERVER_DESTS: frozenset[str] = frozenset({
    "artifacts_meta", "brief_chars", "cards_meta", "chain_meta", "corpus_path",
    "coverage", "coverage_out", "evidence_file", "export_file", "from_dir",
    "judgment", "keywords_meta_path", "meta", "plan_out", "plan_seed",
    "report_root", "roots", "seed_out", "snap_dir", "stopwords",
    "triage_brief", "triage_brief_out", "triage_json",
    "triage_out", "inputs", "artifact_meta",
})

#: **机读专用**参数（P9/G2）：`--json PATH` 是给程序/页面消费的出口，不是
#: 人要填的字段。页面既不渲染输入框，也不算"路径类参数由服务端注入"——
#: 打包/导出这类动作由 handler 自己决定要不要带，人不该看见这一格。
#: （2026-10-23 用户批注「该是什么写什么」：别把机器参数塞进人读表单。）
HIDDEN_DESTS: frozenset[str] = frozenset({"json"})

#: 少数参数的默认值语义要人话解释（页面徽章用）
NOTE: dict[str, str] = {
    "days": "缺省=全库；0 不是全库而是「0 天窗口」（上游实测差异，勿踩）",
    "since": "只统计最近 N 天（按 steps.ts，近似口径）",
    "level": "title 极粗 ~2K / coarse ~10K / mid ~30K / fine·artifact 按需档",
    "out": "缺省打印到 stdout；命令台自动命名 reports/<动词>-<日期>.md",
    "max_chars": "覆盖该档默认字符预算（不填=用档位预算）",
    "role": "keywords：user 只统计用户话 / all 全部角色",
    "corpus": "regress：default 内置语料 / drift 故意漂移（验证断言会红）",
}

_HELP_MAX = 300


def _parser():
    err = ensure_harvester_importable()
    if err:
        raise SystemExit("[commandgen] %s" % err)
    from harvester.cli import build_parser
    return build_parser()


def _leaf_commands(parser) -> list[tuple[str, object]]:
    """展开解析器 → [(点分命令名, 子解析器)]，递归处理嵌套子命令。

    `weblogin check` / `cards validate` 这类嵌套项各算一条命令（它们的
    参数与行为不同，合成一条会让参数表失去意义）。
    """
    import argparse as _ap
    out: list[tuple[str, object]] = []

    def walk(p, prefix: str):
        for act in p._actions:  # noqa: SLF001 - argparse 无公开枚举接口
            if not isinstance(act, _ap._SubParsersAction):  # noqa: SLF001
                continue
            for name, sp in act.choices.items():
                full = ("%s %s" % (prefix, name)).strip()
                if any(isinstance(a, _ap._SubParsersAction)  # noqa: SLF001
                       for a in sp._actions):  # noqa: SLF001
                    walk(sp, full)
                else:
                    out.append((full, sp))

    walk(parser, "")
    return out


def _arg_kind(act) -> str:
    import argparse as _ap
    if isinstance(act, _ap.BooleanOptionalAction):
        return "bool"
    if isinstance(act, (_ap._StoreTrueAction, _ap._StoreFalseAction)):  # noqa: SLF001
        return "switch"
    if act.choices:
        return "enum"
    t = act.type
    if t is int:
        return "int"
    if t is float:
        return "float"
    return "str"

def _jsonable(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        # 字符串也可能已经是 import 期 str(Path.home()/...) 的产物
        # （如 cli.py report-traces/kb-* 的 default）——同样归一成 ~ 形式。
        if isinstance(v, str):
            try:
                home = str(Path.home())
                if v.startswith(home):
                    rest = v[len(home):].lstrip("\\/").replace("\\", "/")
                    return "~/" + rest if rest else "~"
            except Exception:
                pass
        return v
    # 可移植性（用户 2026-10-13 三问①）：argparse 的 default 是 import 期
    # 求值的 Path.home()/... 绝对路径——直接 str() 会把"本机用户名"烤进
    # 生成物（commands.json 曾出现 C:\Users\<某人>\...，换机即错）。
    # 宿主目录下的路径一律序列化成 "~" 前缀的机器无关形式；
    # 前端预填会跳过 "~" 开头的 default（留空 → argparse 的 home 相对
    # default 在运行端自行解析，显示层用 ~ 形式保证可读）。
    if isinstance(v, Path):
        try:
            home = Path.home()
            r = v.expanduser().resolve().relative_to(home)
            return "~/" + r.as_posix()
        except ValueError:
            pass
    return str(v)


def extract() -> dict:
    """走真解析器 → 注册表事实（命令/参数/类型/默认值/help）+ 人写的标注。"""
    parser = _parser()
    from harvester import __version__

    cmds = []
    for name, sp in _leaf_commands(parser):
        card_id = COMMAND_CARD.get(name)
        card = next((c for c in CARDS if c["card_id"] == card_id), None)
        if card is None:
            raise SystemExit("[commandgen] 命令 %r 没有归并到任何动词卡"
                             "（COMMAND_CARD 漏了？）" % name)
        args = []
        subactions = None
        for act in sp._actions:  # noqa: SLF001
            if (act.dest in SUBACTION_CHOICES.values()
                    and act.choices and not act.option_strings):
                tiers = SUBACTION_TIER.get(name, {})
                subactions = [{"name": str(c), "tier": tiers.get(str(c), "B")}
                              for c in act.choices]
                continue
            opts = [o for o in act.option_strings if o.startswith("--")]
            if not opts or "--help" in opts:
                # 位置参数收录（v0.76 V7 连带：chain-audit/chain-validate 的
                # file 是位置参数——不收录则 B 档写动作没法声明 file 参数，
                # 页面永远传不了文件名）。help 与位置式子动作选择器除外。
                if opts or act.dest in ("help",) or not act.dest \
                        or act.choices:
                    continue
                dest = act.dest
                src = SRC.get(dest)
                if src is None:
                    src = ("hidden" if dest in HIDDEN_DESTS else
                       "server" if dest in SERVER_DESTS else "none")
                args.append({
                    "name": dest,
                    "dest": dest,
                    "kind": _arg_kind(act),
                    "default": _jsonable(act.default),
                    "required": True,
                    "choices": None,
                    "src": src,
                    "negates": None,
                    "help": (act.help or "").strip()[:_HELP_MAX] or None,
                    "note": NOTE.get(dest),
                })
                continue
            dest = act.dest
            neg = [o for o in opts if o.startswith("--no-")]
            src = SRC.get(dest)
            if src is None:
                src = ("hidden" if dest in HIDDEN_DESTS else
                       "server" if dest in SERVER_DESTS else "none")
            args.append({
                "name": opts[0],
                "dest": dest,
                "kind": _arg_kind(act),
                "default": _jsonable(act.default),
                "required": bool(getattr(act, "required", False)),
                "choices": ([str(c) for c in act.choices]
                            if act.choices else None),
                "src": src,
                "negates": (neg[0] if neg else None),
                "help": (act.help or "").strip()[:_HELP_MAX] or None,
                "note": NOTE.get(dest),
            })
        sub = [c["dest"] for c in sp._actions  # noqa: SLF001
               if c.dest == "action"]
        cmds.append({
            "cmd": name,
            "card_id": card_id,
            "tier": TIER_OVERRIDE.get(name, card["tier"]),
            "args": args,
            "subactions": subactions,
            "help": (sp.description or "").strip().split("\n")[0][:_HELP_MAX]
                    or None,
            # 用途：人写的一句（PURPOSE）；缺失即 `--check` 红，不许回落成卡说明
            "purpose": PURPOSE.get(name),
            "nested": bool(sub),
        })
    cmds.sort(key=lambda c: c["cmd"])
    return {
        "schema": 1,
        "adapter_version": __version__,
        "kind": "harvester-view 命令注册表（静态；由真实 argparse 生成）",
        "generated_by": "actions/commandgen.py（python -m actions.commandgen）",
        "hint": "命令名/参数/默认值是事实（走 harvester.cli.build_parser 生成）；"
                "src=参数数据源、tier=三档权限是人写的产品判断。"
                "页面凡 src 指向端点者一律渲染成选择器，不给人输框。",
        "cards": CARDS,
        "commands": cmds,
        "tiers": {"A": "只读直接执行", "B": "写 meta/采集库（两段提交）",
                  "C": "只生成命令，永不执行"},
    }


def check(path: Path = OUT) -> list[str]:
    """比对磁盘上的 commands.json 与真解析器。返回漂移清单（空=一致）。"""
    want = extract()
    if not path.exists():
        return ["%s 不存在" % path]
    got = json.loads(path.read_text(encoding="utf-8"))
    diffs: list[str] = []
    if got.get("adapter_version") != want["adapter_version"]:
        diffs.append("adapter_version: %r → %r"
                     % (got.get("adapter_version"), want["adapter_version"]))
    gc = {c["cmd"]: c for c in got.get("commands", [])}
    wc = {c["cmd"]: c for c in want["commands"]}
    for name in sorted(set(wc) - set(gc)):
        diffs.append("缺命令 %s" % name)
    for name in sorted(set(gc) - set(wc)):
        diffs.append("多命令 %s" % name)
    for name in sorted(set(gc) & set(wc)):
        if [s["name"] for s in (gc[name].get("subactions") or [])] != \
                [s["name"] for s in (wc[name].get("subactions") or [])]:
            diffs.append("%s 子动作清单不一致: %r → %r"
                         % (name, gc[name].get("subactions"),
                            wc[name].get("subactions")))
        ga = {a["name"]: a for a in gc[name]["args"]}
        wa = {a["name"]: a for a in wc[name]["args"]}
        for an in sorted(set(wa) - set(ga)):
            diffs.append("%s 缺参数 %s" % (name, an))
        for an in sorted(set(ga) - set(wa)):
            diffs.append("%s 多参数 %s" % (name, an))
        for an in sorted(set(ga) & set(wa)):
            if ga[an]["src"] != wa[an]["src"]:
                diffs.append("%s %s src: %r → %r"
                             % (name, an, ga[an]["src"], wa[an]["src"]))
    # 用途（P9）：每条命令必须有**不重复**的一句；缺/重复都红——
    # 否则页面悬停又会回落成同一句卡说明（2026-10-23 用户实测 7 条一样）。
    missing = sorted(n for n, c in wc.items() if not (c.get("purpose") or "").strip())
    if missing:
        diffs.append("缺用途（PURPOSE）: %s" % "、".join(missing))
    seen: dict[str, list[str]] = {}
    for n, c in sorted(wc.items()):
        p = (c.get("purpose") or "").strip()
        if p:
            seen.setdefault(p, []).append(n)
    for p, names in sorted(seen.items()):
        if len(names) > 1:
            diffs.append("用途重复（%s 共用同一句）: %s"
                         % ("、".join(names), p))
    return diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="actions.commandgen")
    ap.add_argument("--check", action="store_true",
                    help="只比对不写（有漂移退出码 1）")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    path = Path(args.out)
    if args.check:
        diffs = check(path)
        if diffs:
            print("[commandgen] 注册表已漂移（%d 处）：" % len(diffs))
            for d in diffs[:40]:
                print("  - " + d)
            return 1
        print("[commandgen] OK：%s 与真实解析器一致" % path.name)
        return 0
    obj = extract()
    # newline="\n"：.gitattributes 钉了 *.json eol=lf，Windows 下按默认写法
    # 会产出 CRLF → 每次生成都报"CRLF will be replaced by LF"并让 diff 抖动
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    n_args = sum(len(c["args"]) for c in obj["commands"])
    n_src = sum(1 for c in obj["commands"] for a in c["args"]
                if a["src"] != "none")
    print("[commandgen] 写入 %s：%d 命令 / %d 参数（其中 %d 有数据源标注）"
          "/ %d 动词卡 / 适配版本 %s"
          % (path, len(obj["commands"]), n_args, n_src, len(obj["cards"]),
             obj["adapter_version"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
