# harvester-view

会话索引库的**独立只读视图**：给人用的会话检索 / 蒸馏队列 / 报告 / 中台四页单文件前端。
零构建、零第三方依赖（Python stdlib + 原生 JS，无 node_modules、无打包步骤）。

与本套件索引服务**互不留痕、只经 HTTP 接口通信、双向只读**：
本项目不 import 任何上游代码、不知道数据库文件存在，只认识
"返回 `api_version=1` JSON 的 HTTP 服务"（下称**上游**，即主项目的
`python -m harvester api-serve`）。

> 大白话：harvester 负责把会话日志整理成数据库并提供查询接口；
> harvester-view 是架在接口之上的"看板"。视图坏了不影响数据，
> 数据库不升级视图也能照常用（只要接口版本兼容）。

## v0.82 新增（知识图谱，2026-10-19，机械关系图）

主仓零代码改动（提案 docs/PROPOSAL-knowledge-graph-v1.md，用户 2026-10-19
批准；复用 topics_meta / 卡片 anchors / 索引库三个既有真源）：

- **图谱 tab**（词云之后）：A 档动作 `kgraph`（视图侧分析动作，无 CLI
  cmd，同 dualtrack/topic_brief）一次组装 主题×卡片×关键词 三层节点 +
  四类**机械边**：member（主题—成员会话，topics.members 注册时人给证据）/
  anchor（卡片—锚点会话，frontmatter §8 冻结字段）/ topic_of（卡→主题，
  锚点 sid ∈ 主题成员，与 cards validate 同判据）/ keyword（词→主题，
  topics.keywords 人工定稿表）。**零语义臆造**；
- **主题间无边**：现状数据面没有主题层级（成员会话唯一归属），主题树
  （P7-②）缓行不是图坏了——图例固定声明，防误读；
- **会话层懒加载**：基础图只画 主题+卡片+关键词（防节点爆炸）；点主题/
  卡片才按 sids 发第二次 kgraph 动作展开成员/锚点会话（上限 400/次，
  >60 个先确认）；点会话直达回合原文、点关键词灌进会话检索（真检索通路，
  同词云下钻）；未入主题的会话不画节点、bar 如实给计数；
- **确定性力导向布局**：canvas 自写（固定种子 + 固定迭代上限，节点按 id
  排序参与计算——同数据必得同坐标，零第三方依赖）；
- **诚实空态**：topics_meta 未配置 / 卡片目录空 / 展开全 missing 都用
  hints 原样明说，绝不编节点；hint/标签逐字 esc；
- **测试**：先红后绿——test_v66_kgraph.py 10 例（静态账/双层账/机械边
  四类/空态/展开/主题间无边）+ 渲染门 render_kgraph.js 18 断言（作业
  轮询/bar 账/确定性布局/懒加载展开/下钻/收起/空态×2/失败态×2）。

## v0.81 新增（词云导航，2026-10-19，P7-①）

主仓零改动（`/api/keywords` v0.33 就有；启动脚本早已配 `--keywords-meta`）：

- **词云 tab**（会话检索之后）：读上游 keywords_meta 最新 run 的 n-gram
  词频（gram/freq/doc_freq），字号 = doc_freq 分位五档（20/40/60/80
  分位，**纯展示密度不是语义分类**）；二元词/三元词两档切换；bar 标注
  run 编号与日期（数据新鲜度诚实）；
- **点词下钻 = 真检索**：把词灌进会话检索（state.q + f-q + loadSessions），
  复用现有会话→回合三跳——keywords 无词→会话回链，**不臆造归属**，
  下钻结果就是检索真话；
- **诚实空态**：keywords_meta 未生成/无 run/空 rows 三种都明说 + 给
  `python -m harvester keywords` 生成命令，绝不编词；
- **esc 纪律**：词/提示原样转义（注入试探词有渲染门抓）；
- **测试**：先红后绿——test_v65_wordcloud.py 6 例（静态结构/数据通路/
  esc/下钻通路/诚实空态/启动参数）+ 渲染门 render_wordcloud.js
  12 断言（分档/转义/口径/新鲜度/下钻/空态×3/失败态）。
- P7-②（主题树层级）维持缓行：层级语义需主仓数据面先行（parent 字段
  + 候选不裁决），届时单独提案。

## v0.80 新增（卡片触发层，2026-10-19）

主仓 v0.78（P6-卡片触发钩子，借鉴 DSH-agent-procedures 调研）配套：

- **数据面**：`cardlib.parse_card` 透出 `triggers`（主仓 v0.78 卡片可选
  现象词列表；非列表形态=空列表，旧卡零感知——additive 哲学：宁可少给
  不可错给）；`cards_payload` 原样透传（`_cards_payload` 零改动）；
- **渲染面**：卡片报告页每张卡在 evidence/summary 之后、锚点行之前渲染
  触发行（`c-trig`，有才渲染；逐词 `esc` 原样展示，注入试探词转义有门）；
- **测试**：先红后绿——新增 `tests/test_v64_card_triggers.py` 4 例
  （列表逐字 / 非列表空 / 旧卡零感知 / payload 全带键 + 渲染行 esc）+
  渲染门 render_summary.js 第 ⑨ 组 3 断言（c-trig 渲染 / 逐词 esc /
  旧卡零感知）；commandgen 适配版本握手 0.78.0（53→54 命令，新增
  `cards index-md` 归「知识库」卡 A 档）；view 动作面配套：
  actionsserve 新 A 档动作 `cards_index_md`（stdout 版素材直给，存档
  走命令台复制加 --out）+ registrygen READ_ACTIONS 登记（A 档
  34→35），test_v36/v38/v54 三道对账门全过。

## v0.79 新增（报告索引 + 昨日报告卡全文，2026-10-17）

HANDOFF-v0.52-next §三-2 挂账两项收口（用户批准「做完挂账的」）：

- **报告族 md 索引区**：报告页新增「报告索引」按钮与挂点——调上游新端点
  `/api/reports-index` 列出服务端报告目录里实际存在的 `.md` 文件
  （mtime 新→旧，`daily-YYYY-MM-DD.md` 自动标日报日期），每份可
  「看全文」（`/api/report-get?name=`，行内展开、esc 原样展示、
  loaded 守卫防重复拉取）；目录不存在 / 没有 `.md` 是诚实空态照实说；
  **页面不解析 md、不二次聚合**（纪律同四队列：只渲染服务端给的内容）；
- **昨日报告卡「查看全文」**：工作台昨日卡在**确实截断**时
  （`n_chars > md_head` 长度）出现「查看全文」按钮——就地把卡片头部
  `pre` 补成全文（懒加载一次，name 取服务端白名单口径
  `daily-<date>.md`），未截断不出按钮（不多一个撒谎的按钮）；
- **测试**：先红后绿——新增 `tests/test_v63_reports_index.py`
  （静态结构 5 例：端点名逐字 / esc 纪律 / 截断才出按钮 / 按钮接线）
  + 渲染门 `tests/render_reports.js`（7 组：全文懒加载 / 未截断不出 /
  诚实空态 / 恶意文件名 esc 试金石 / loaded 守卫 / 接线）；套件
  330 例全绿（+5），渲染门 12 项全过（含新增 render_reports）；
  commandgen 适配版本握手 0.77.0（53 命令数不变）。

## v0.78 新增（ts 精确锚点一跳原文，2026-10-17）

挂账 §3-方向①「一跳原文」收口（PROPOSAL-faster-data-access.md §3）：
双轨候选的跳转链原来只到**会话**，现在精确到**回合**——

- **数据面**（additive）：`nags.collect` 样例附带 `ts`
  （messages.ts，与回合首条消息 ts 同列同口径）；打磨带/反转点的
  前轮后轮原文（`_fetch_turns`）与 mid 讨论行（上游 topics.timeline）
  本就带 ts，零改动；
- **渲染面**：双轨时间线四处锚点透传 `data-dt-ts`——mid 讨论行 /
  打磨带前轮后轮（「→ 原文」锚）/ 反转点本轮前轮 / 反复叮嘱样例预览；
- **跳转链**：新增 `gotoAnchorTs(sid,ts)`——ts 前 19 位与回合 ts 前缀
  匹配 → `openTurn` 直达该回合全文；扫不到诚实横幅指人工定位（会话
  保持打开，不猜行号）；无 ts 的锚点保持旧口径只开会话；run 守卫与
  gotoAnchor 同款（连点旧链自查放弃）。绑定统一收口
  `bindDualtrackJumps`（真跑与轮询链路同款）；
- **测试**：先红后绿——渲染门 render_dualtrack 新增 ⑨ 组 13 断言
  （透传计数/分发/精确匹配/诚实横幅），test_v42/v44 各钉 sample 带
  ts；套件 325 例全绿，渲染门 11 项全过。

## v0.77 新增（P5 数据获取加速，2026-10-16）

上游提案 `PROPOSAL-faster-data-access.md` §5 全项批准后的视图侧落地：

- **③ 反复叮嘱候选接入双轨时间线**：主题双轨时间线新增
  「反复叮嘱候选」区——**过滤版口径**（调 `analysis/nags.collect`：
  剥 system-reminder 注入 / 剥过短轮 / 剥长正文粘贴 / 逐字模板过滤 /
  相似归并）全库跑一遍再按主题成员过滤；候选**只列不裁决**（诚实说明
  随视图渲染）；组内列出命中成员（hit_sids）与主题外对账，锚点可点直达；
  过滤账目（注入剥块/剥后过短/长粘贴/模板组）随视图渲染，口径透明；
- **⑧ 登记零散直给**：候选组内「登记零散」按钮 → 新 B 档
  `noise_register`（写 topics_meta 的 sessions_noise 表，幂等/可逆，
  **与抑制的本质区别：不删任何数据**）→ 主仓新 CLI `noise list/add`
  （默认 dry-run，--apply 才写）；动作卡归「分诊蒸馏」；
- **⑤ 昨日报告卡**：今日工作台顶部「昨日报告」卡——取数口为新端点
  `/api/report-daily`（主仓 apiserve，文件名白名单
  `daily-YYYY-MM-DD.md`）；found=false 诚实空态并给生成命令；页面只
  渲染服务端给的内容，不二次解析不二次聚合；A 档直给 `report_daily`
  （stdout 捕获，绝不带 --out 落盘）；
- **① 解释层按体检清单收尾**（探针 `_audit/probe_explain_coverage.py`）：
  agg 中台补导语（panels.agg + 挂点）；skills/cards 补结论卡
  （数字全部取自本响应）；glossary 五词条清理——「命令分组」删
  （页面无对应真实用语），「下钻/闭环健康/登记零散/处理单」改为页面
  真实用语（旧全称静态扫描永不命中，tooltip 失效）；
- **② 检索动线收口**（探针 `_audit/probe_search_flow.py`）：会话阅读页
  新增「并入主题」按钮（A 档 topic_add 直给）——读完直接挂进主题，
  不用回列表重新勾选；
- **④ N1 术语标注**：G2 分布表与错误类别 chips 补「本项目自定义分类」
  标注（官方惯例参考 OpenTelemetry error.type）；
- **测试**：325 例全绿（+9）；渲染门 11 项全过；commandgen 53 命令 /
  registrygen A 34+B 16 双向注册表一致。

## v0.34 新增（P1 解释层，2026-10-12）

- **卡片本体**：G3 卡片页现在显示每张卡的结论 / 证据引文 / 锚点 / 置信度，
  与上游 /api/cards 的校验徽章合并（本体由本仓 cardlib.py 只读解析
  frontmatter，**不碰 session-harvester**，工程决策 D9）；
- **本地只读服务（端口 8766，P2 起由 `actions/actionsserve.py` 承担）**：提供
  /view/cards 与 /view/glossary（回环地址绑定、只读、无凭据）；页面跨源取数用 CORS。
  没启动它时页面照常工作，只是卡片页缺本体、解释层不渲染（黄条提示）。
  **注意**：这两个端点在 8766，页面在 8088——前端必须用绝对地址（`ACT_BASE`），
  用相对路径 `fetch("/view/…")` 会打到 8088 的 view.py 并 **404**（P1 遗留缺陷，
  P2 会话起服实测抓到并已修，`tests/render_cmd.js` 有静态门钉住）；
- **解释层 glossary（static/glossary.json）**：术语悬停卡（虚线下划线）、
  「怎么读」可折叠导语、错误模式人话表——全部静态数据，零 LLM；
- **结论卡**：G1/G2/建议池/蒸馏队列头部新增模板结论（主语+数字+判语+
  下一步按钮），数字全部来自当次 API 响应现算；
- **错误类别 chips**：G2 报告页与会话页（勾「只看异常」后）可按
  环境/用法/上下文/未归类过滤；会话页条数与 G2 报告同源对账。

## v0.63–v0.65 新增（研读面与裁决直给，2026-10-16）

- **V3 研读面**（v0.63，`actions/analysis/topicbrief.py`）：主题页新增
  「研读面」聚合层（topic_brief，A 档）——构成/高频问题/错误模式/
  skill 行为/健康缺口/chain 结论层一次看全，每个数字带
  `source/definition`，零语义臆造，缺数诚实空态；
- **V4 C 位重排**（v0.63）：openTopic 默认进研读面，chain 演进链 /
  双轨时间线退为工具栏按钮（按需加载）；
- **V5 卡片认 summary**（v0.64）：主仓 v0.74 拆角色后综述在
  frontmatter `summary`——cardlib 透出、renderCards 双栏
  （evidence=逐字 / summary=综述，insight 综述顶 C 位）；旧卡原样（additive）；
- **V7 审计直给**（v0.65）：chain-audit 报告下渲染可疑锚点逐行 [接受] +
  「全部接受」→ 新 B 档 `chain_audit_accept`（预演列键 / 落账
  topics_meta audit_review，键取上游 `_node_key` 真源）；
- **V6 卡片直给**（v0.65）：警告行 [接受] / 本体 [接受整卡] →
  `card_review` 预演（v0.75 已有的 B 档补前端接线）；
  warn 单串归一修（单条文本不再被逐字符拆散误匹配）；
- **commandgen 收录位置参数**（v0.65 连带）：chain-audit/chain-validate
  的 `file` 与 read/search 位置参数补进命令事实表（真 argparse 对齐）；
- B 档动作规模：v0.36 时 9 → v0.49 时 11 → v0.62 时 13 → **v0.65 时 15**
  （card_review + chain_audit_accept）。

一键启动（含三个进程）：`start-viewv2.cmd`（api-serve 8765 +
**actions-serve 8766** + view 8088；P2 起 8766 由 `actions/actionsserve.py`
承担，P1 的 viewserver.py 已归并进去，仍是**同一个端口、一个进程**）。

## v0.76 直给真机实测三修（2026-10-16）

批次二遗留的「直给按钮真机实测」（浏览器实点 V6/V7 两条 B 档直给链路）
做完了，实测抓出**三个真 bug**（渲染门与单测都盖不住的三类盲区），逐个修：

1. **V6 懒加载时序**（`static/index.html`）：刷新后直奔报告页点警告行
   [接受]，`state.act` 还没拉过 → `actionSpec("card_review")` 必空 →
   直给必早退（黄条「动作面没有 card_review」）。修：handler 先
   `loadCmdData()`（与 `topicGoCmd` 同款懒加载前置），拉取失败仍有
   banner 兜底、不静默。渲染门新增 2 条源码序断言（懒加载必须先于
   actionSpec 查询）钉住；
2. **V6 预演与展示层不同库口径**（`actions/writeactions.py`）：
   `_card_review_keys` 原先不带索引库查当前警告 → evidence 类警告退化成
   「原文不可得」→ 直给传来的完整警告串匹配不上 → 预演「将落账 0 条
   警告键（no-op）」——人确认的是 A、落账的是空。修：默认用本服务索引库
   （cfg.db，与 /api/cards 同库同口径；显式传 db 仍可覆盖）。
   test_v62 新增真库夹具用例（修前必红）；
3. **V7 命令被 B 档劫持**（`actions/actionsserve.py`）：/api/actions 的
   A 档条目从 ACTIONS 代码字典取 cmd——chain_audit 代码字典没写 cmd →
   下发空 → 前端 cmdActionMap 首个声明者胜出 → `chain_audit_accept`
   （B 档，cmd=chain-audit）抢走 chain-audit 命令 → 命令台点 chain-audit
   「执行」直接弹 B 档确认框，**A 档审计从命令台不可达**。修：cmd/subaction
   以注册表 JSON 为准（注册表唯一真相）合并下发。test_v35 新增守卫：
   A 档 cmd 必须与注册表一致 + 同 cmd 多声明者时首位必须是 A 档
   （纯 B 档命令如 cards review 不受影响）。

终验（浏览器真机两轮 + 服务端台账对账）：V6 警告行 [接受] → 自动切命令台
→ 预演「将落账 1 条警告键」逐行可核 → 取消零写入；V7 chain-audit →
真审计报告（引文逐字门）→ 直给区块 7 按钮（6 行级 + 全部接受）→ 行级
[接受] 预演恰列 1 条锚点键 → 取消零写入。台账口径：全程 writes=0，
preview 留痕符合设计。套件 316 OK / 渲染门 16 检查全绿。

## v0.35 新增（P2 打通层，2026-10-12）

- **actions-serve（`actions/actionsserve.py`，8766）**：本仓唯一的动作面，
  也是**唯一** import 兄弟仓库的地方（只 import 调用，不改它）。P1 的
  `/view/cards`、`/view/glossary`、`/view/health` 原样归并（路径/结构不变），
  新增：
  - `GET /api/health`：自检 + **版本握手**（上报 `harvester.__version__`
    与 `commands.json.adapter_version`，不匹配 → 命令台黄条 + 禁 B 档）；
  - `GET /api/commands`：静态命令注册表（43 命令 / 13 张按意图分组的
    命令卡（旧称「动词卡」，本项目自定义待淘汰；官方对照与替换计划见
    `session-harvester/docs/TERMINOLOGY.md` §3-§4）/ 参数数据源）；
  - `GET /api/actions`：动作注册表（v0.65 时点 **A 档 32 + B 档 15**；
    本节写作时为 A4+B9，规模演进见上节）；
  - `POST /api/actions/run` → A 档 `202 {job_id}`（`GET /api/actions/job/<id>`
    轮询），B 档 `200 {status:"needs_confirm", confirm_token, preview}`；
    **进程内线程调 harvester 函数，非 shell**；长任务不阻塞页面；
  - `GET /api/actions/ledger`：动作台账（P3-2；含写记录与破坏性快照）；
  - `GET /api/topic/<id>/timeline?level=`：五档时间线透出（title/coarse/
    mid 直接给；fine/artifact 需 sid）；
  - `GET /api/pattern-sessions?pattern=&days=`：错误模式 → 命中会话清单
    （下钻第二跳；本页下钻的强约束 = 每个聚合数字可点、≤3 跳直达原始证据、
    逐级显式对账——官方原则 details-on-demand/drill-down（Shneiderman 1996），
    旧称「数字皆门」为本项目自定义待淘汰，见 `session-harvester/docs/TERMINOLOGY.md`
    §3-§4。与 G2 报告同源同口径）。
  - `GET /api/workitems?target=<id>`：**处理单**（v0.49）——某个数字**正好那一批**
    待处理记录 + 逐条处理建议 + 命令包 + 逐指标对账。缺参 → `400`（附可选
    target 清单）、未知 target → `404`（附清单）。target id 一律 URL 安全字符
    （`[A-Za-z0-9._:-]`——**不许出现 `#`**，它是 URL 片段符，裸 URL 会被客户端
    截断；本轮为此踩过一次，见 v0.49 一节）。
  - `GET /api/suppressed`：**已抑制会话清单**（v0.49，**只读**）——抑制只作用于
    **仓内副本**（库内三表行 + exports 副本），**不删采集源**；取消抑制本身不恢复
    数据，能不能恢复**只取决于语料是否还在**，**不设恢复时限**。**缺表不建表**
    （读路径不许有副作用）。
  - 参数 `--corpus-watermark` 开启后，timeline 会附带库龄判定
    （`stale`；缺省关，见该参数 help）。
- **命令台（Tab「命令台」）**：13 张动词卡 → 命令 chips → 参数选择器
  （`src=chips` 预设、`src=topics_api/sessions_api` 从库里选、路径类参数
  **不暴露**）→ 命令行预演 → 执行 → 结果抽屉（数字可点，原始 JSON 可展开）。
  A 档直接执行；**B 档两段提交**（预演 → 确认框 → 才写，见 v0.36）；
  C 档只给命令行（红线：命令台不是万能 shell）。
- **数字皆门三跳**：错误模式（可点的次数）→ 命中会话清单（逐会话次数/
  未解决/工具，**逐会话之和与模式总数显式对账**）→ 回合原文
  （复用既有 `gotoAnchor`，按错误原文逐字定位并高亮）。
- **命令注册表由真解析器生成**：`python -m actions.commandgen`
  （`--check` 比对漂移；`tests/test_v35_actions.py` 里逐命令逐参数钉住
  ——上游加参数而注册表没跟上就红）。人手只写两样：参数数据源 `src`、
  三档权限 `tier`。

一键启动（含三个进程）：`start-viewv2.cmd`（api-serve 8765 +
actions-serve 8766 + view 8088）。

## v0.49 新增（处理单 + 配色修复，2026-10-13）

用户本轮实测反馈两条（原话见 `_eng/HANDOFF-view-v2-kickoff.md` §7 会话 6）：

### 一、「队列明细对不上」→ 处理单（Tab「处理单」）

**缺陷本体**：卡片上的数字与跳过去那个页面**各自算了一遍**——「待修补」卡显示 6
（建议条目里 `unresolved_count>=1` 的条数），而跳到的 G2 建议页渲染**全部** 8 条；
更极端的是「待验证 9」跳到了错误报告页（381）。两套人群、两套数字，用户没法对着
手上的数字干活。

**修法（唯一真源）**：新增 `actions/workitems.py`。一个 **target** 拥有一份明细
`items` 与若干条**具名指标** `metrics`；指标值**只由明细派生**：

| `mode` | 值怎么来 | 用在 |
|---|---|---|
| `items` | `len(items)`（**条目数**） | 待修补条目 6 / 已采纳 22 / 注册主题 13 |
| `sum` | `sum(item.metrics[sum_field])`（**次数之和**） | 全库错误 381 / 未解决 31 / chain 3 |

`/api/overview` 与 `/api/workitems` **都只消费这一份**，页面只渲染、不再聚合 →
「数字 == 明细」是**构造保证**。响应里 `reconcile` 把派生过程写成机器可判定的形式
（`{metric, mode, expected, actual, by, ok}`），`reconcile_ok=false` 时页面**响亮
报警并劝阻派活**，不静默截断（超 `MAX_ITEMS` 会让 `ok=false`）。

实测（2026-10-13 真库）：**17 个 target / 25 条指标全部对账通过**。队列明细行
（已裁决 24 / 未解决次数 17 / 未解决模式数 16 / 未解决次数 31 / 旧坑重现 0 /
skill 行为链 31 / 待做卡片 0 / 可评估 9 / 无变化 0）**每一行都指向一个可点 target**；
只有两个数显式标为**聚合数（不派活）**：噪声登记 163、未评估 15——因为"未执行
评估"与"评估了没变化"必须分得开（`$DSH_HOME/AGENTS.md` §五 16）。

**跳转后只显示需处理的记录**（用户需求原文）：新 Tab「处理单」渲染该 target 的
明细（**条数与数字同源**），每条记录带：事实字段、**证据锚点**（点开回原文，走既有
`gotoAnchor` 三跳）、**处理建议**、以及它自己的命令。

**打包处理命令 + 直达命令台**（用户需求原文）：`workRows()` 把「包级命令」与
「每条明细自带的命令」摊平成一份清单：可**一键复制全部命令行**（在仓根目录粘贴
执行），带 `action_id` 的给「**在命令台打开**」——切到命令台、选中那张命令卡、
**预填参数**（不用手抄 `key`），然后照旧走两段提交（预演 → 你确认 → 才写）。
诚实边界：**本视图不代你执行**，命令行是给你核对的。

**闭环健康项同样待遇**（用户需求原文：「当前不能跳转，也无建议」）：三环的每一段
都是 target，因此每一段都能点、都带 `suggestion` 与命令包——与四队列走**同一条
动线、同一批渲染函数**。

**发出去的命令必须真能跑**：新增一道门，把 `workitems` 生成的**每条 CLI** 交给
`harvester.cli.build_parser()` 解析（照 AGENTS.md §四 14「让文档里的命令被测试
真跑」），并要求每个 `action_id` 真在 `actions_registry.json` 里。这道门第一次跑
就抓出两个我自己的错：`cards validate` 漏了 required 的 `--root`；注册表读错了键
（动作分在 `read_actions`/`write_actions` 两张表里，没有 `actions` 这个键）。

### 二、「今日工作台浅底浅色，看得眼花」→ 配色修复

**实测根因（不是猜的）**：页面有 `@media (prefers-color-scheme: dark)`，深色模式下
`--ink` 翻成**浅色字**（`#e6e9ef`）；而工作台组件把底色**写死**成浅色
（`.qcard{background:#fff}`、`.ring{background:#fff}`、`.arm{background:#fafbfc}`
等 11 处）→ 浅字浅底，对比度约 **1.1:1**（WCAG AA 正文要求 ≥4.5:1）。

**同一个毛病还有第二类**：**用了不存在的变量**——13 处写 `var(--sub)`，而 `--sub`
从未定义 → 那些「口径/真源」小字根本没被置灰，整块糊成一片。

**修法**：工作台与命令台的组件底色一律改用主题变量
（`var(--panel)`/`var(--bg)`/`var(--warn-bg)`/`var(--err-bg)`），两种模式都达标；
13 处 `var(--sub)` 改成真实存在的 `var(--ink2)`。

**门（`tests/test_v40_theme_contrast.py`，9 例，会红）**：① 断言页面确有 dark 覆盖
（前提变了要显式裁决）；② `var(--x)` 的 `--x` 必须真被定义过；③ **写死浅底却不钉住
前景**的规则一律红；④ 确实要写死浅底的（悬停卡已钉 `color-scheme:light`）
真算对比度 ≥4.5:1；⑤ 四种字色 × 四种主题底、两种模式全算一遍；
⑥ **选中态 chip 两种模式都要 ≥4.5:1**，且它**不许拿 `--accent` 当底**。

**选中态 chip（用户 2026-10-13 裁决「不改深色，改其他的」）**：`.chip.on` 原来是
`background:var(--accent);color:#fff` —— 浅色 4.8:1 达标、**深色只有 3.2:1**
（深色的 `--accent` 是为深底刻意调亮的浅蓝 `#5b8def`）。现给它**专用底**
`--chip-on-bg`（浅 `#2563eb` / 深 `#1d4ed8`）→ 白字 **5.17:1 / 6.70:1**，
**`--accent` 一个字节没动**（链接/描边/选中态仍用它）。

### 三、会话删除（抑制）的 view 侧（用户批准的清单 ①–⑦、⑧ 不做）

- **不新造写路径**（清单 ⑥）：新增两个 B 档动作 `session_suppress`
  （`destructive`，目标库 `topics_meta` —— 清单 ① 批准抑制表放现有 meta 库，
  不新建）与 `session_unsuppress`（`reversible`）；都走现成的**两段提交 + 台账 +
  destructive 删除前快照**（快照只存数量与标题，**不存正文**——正文能否恢复
  取决于语料）。
- **会话页**：详情面板加「删除（抑制）」按钮（把动作与 sid 装到命令台，由命令台走
  两段提交）；筛选区加「已抑制」勾选 → 显示**服务端全量清单**（`/api/suppressed`，
  **不做客户端分页筛选**——在只有一页的表格上筛选，看起来像「全部就这些」，
  那是 §七 20 那类静默骗人），每条带「恢复」。
- **口径逐字写在页面上**：删的是**仓内副本**、**不删采集源**、能不能恢复
  **只取决于语料是否还在**、**不设恢复时限**。页面上**不许**出现「可恢复」式承诺
  （有测试断言）。
- **B 档动作 9 → 11**（可逆 8、破坏性 3）；破坏性清单与 B 档计数分别由
  `tests/test_v38_stage_gate.py` / `tests/test_v36_write_actions.py` **钉死**，
  一变就必须回来显式复核（本轮两处都按这个流程改，并在注释里写了理由）。
- **门**：`tests/test_v41_suppress_view.py`（17 例）——注册表形状与「写对库」、
  预演零副作用 + 快照不含正文、口径人话逐字、**端到端只在临时库上**（抑制 →
  三表该 sid 0 行 + 抑制表有行 + exports 副本被删；恢复 → 抑制行没了且**数据不回来**）、
  `/api/suppressed` **只读不建表**；`tests/render_cmd.js` 加了 14 条渲染/接线断言。

- **配置要求（v0.49 续批，踩过一次）**：actions-serve **必须传 `--exports-root`**
  （`start-viewv2.cmd` 已补）。不传的话 `cfg.exports_root=None` → 抑制会写表、会删库内行，
  **却清不掉 exports 旧副本**，而旧副本还在就说明**下次 sync 会把这条会话收回来**
  （= 抑制看起来"没生效"）。有一道门读启动脚本断言这一行在，另有一条断言钉住
  "没配时预演必须响亮警告"。

### 本轮自查（变异复核，可重跑）

`docs/reports/mutation-check-v049.py` 把 6 个缺陷**故意放回去**，确认每道门真的会红、
并原样还原：① 卡片底色写死回 `#fff`；② 写回未定义的 `var(--sub)`；③ 对外明细被
悄悄截断一条（数字不变）；④ `sum` 模式忽略具名字段；⑤ target id 改回含 `#`；
⑥ 选中 chip 的专用底调亮（白字退回 3.2:1）。**6/6 全部被抓**（含一次自我纠错：
首版变异「截断 MAX_ITEMS 条」其实抓不到，当时显示"变红"是基线本来有缺陷造成的
假阳性，已改成真会被抓的形态）。

## v0.37 新增（P3-3 今日工作台 + P3-6 阶段门，2026-10-12）

- **今日工作台（默认落地页）**：`GET /api/overview` 现算 **四队列**（待你裁决 /
  待修补 / 待蒸馏 / 待验证）与**闭环健康面板**（三条反馈闭环（closed-loop
  feedback，公开概念）的积压量：错误→班规→效果 / skill→鉴定→修复 /
  主题→chain→卡片；旧称「闭环健康三根环」为本项目自定义待淘汰，
  见 `session-harvester/docs/TERMINOLOGY.md` §3-§4）。**每个数字都带 `source`（哪个上游函数
  算的）与 `definition`（怎么算的）**，页面上逐条显示口径——这是"口径必须可见"
  的验收要求；**每个数字（含闭环健康的每一段）都可点进「处理单」**，
  看到正好那一批待处理记录与处理建议（v0.49 改；旧行为是"跳到另一个页面，
  由那个页面自己再筛一遍"，因而明细对不上，用户 2026-10-13 实测报过）。
  - 实测口径（2026-10-12 真库，随语料变化）：待裁决 **0**（台账 24 条已全裁决）/
    待修补 **6** 条建议（17 次未解决）/ 待蒸馏 **30** 个新错误模式（+31 条 skill
    行为链）/ 待验证 **9** 条「不下结论」（样本不足，≠ 无效）。
  - `GET /api/overview` 实测约 **4.8 s**（现算：`collect_errors_from_db` +
    `collect_triage` + `harvest-eval`），页面会先提示"正在现算"。
  - **页面只渲染服务端给的数，不二次聚合**（两处各算一遍就会出现两套数字）——
    有渲染门钉住。（v0.49 起本页的数字**全部来自 `actions/workitems.py`**，
    与处理单同源；点数字进处理单看**正好这一批**。）
- **P3-6 阶段门的逐卡走查做成了测试**（`tests/test_v38_stage_gate.py`）：
  A 档必须只读且无写库声明；B 档必须有风险等级 / 目标库 / 两段提交声明 /
  必填参数人话 label，且 **destructive 的说明必须写明会丢什么**（实测清单 =
  `topic_delete` / `topic_merge`）；C 档命令不得出现在任何可执行动作里；
  **只有"有动作的命令"才可能被执行**（UI 只有两个 enable 分支，其余一律
  禁用并写明理由）；动作面只有一个写入口（POST /api/actions/run）。

## v0.36 新增（P3 裁决层，2026-10-12）

- **动作注册表冻结**（`actions/actions_registry.json`）：**A 档 4 / B 档 9
  （可逆 7、破坏性 2）/ 13 动词卡**。由 `python -m actions.registrygen` 从
  「`commands.json`（真解析器生成的事实）+ `actions/write_actions.json`（人写的
  判断）」合并生成，**双向检查**：引用了不存在的命令/参数 → 失败；动作声明了它
  **不消费**的参数 → 失败（用户以为填了会生效，是最危险的一类）。执行器只认
  这张表：**表外动作 403**。
- **B 档两段提交**：写动作分两段——① 只**预演**（服务端跑只读预演，返回
  「将写什么」人话 + 目标库 + 风险 + `confirm_token`）；② 带**同一份 params
  + token** 才真写。token 一次性、5 分钟过期，并绑定 `action_id` / 参数指纹 /
  预览指纹：**参数被改、库在两段之间被改、token 复用或过期、串动作 —— 一律
  409 拒绝并要求重新预览**，不会照写。预演**零副作用**（有测试比对字节不变）。
- **动作台账 `actions/actions_meta.db`**（已 gitignore）：`action_runs`
  （**预演也记一行**）/ `write_records`（before/after；**destructive 必带
  删除前快照**——快照是"可回滚"承诺的唯一凭据）/ `confirms`。只读端点
  `GET /api/actions/ledger`。诚实边界：本服务绑 127.0.0.1 且**无鉴权**，
  请求里的 `client` 是调用方**自报**标签（默认 `web-ui`），**不是身份认证**；
  token 是**意图确认**凭据，不是权限凭据。
- **命令台接 B 档**：写动作按 `action_id` 出现在动词卡里（`topic register` /
  `topic delete` …），点开先看参数 → 点「预演」→ 弹**确认框**（逐行渲染服务端
  给的「将写什么」+ 目标库绝对路径 + 风险；破坏性动作红框警告并写明会丢信息）
  → 点「确认执行」才写。破坏性动作的 chips 与可逆动作视觉分开。
- **版本握手黄条（P3-5）**：`/api/health.version_ok === false`（harvester 版本
  ≠ `commands.json.adapter_version`）→ 命令台顶部黄条 + **B 档禁用**，
  **A 档只读不受影响**。判据只有服务端那一处，页面不自己比版本号。

## 快速开始

### 方式一：一键启动（推荐）

提供两个启动器，二选一：

| 启动器 | Python 来源 | 适用场景 |
|---|---|---|
| `start.cmd` | 硬编码 WorkBuddy 自带解释器（非沙箱，只是绝对路径指向 WorkBuddy 安装目录下的 Python） | 本机没装独立 Python、但装有 WorkBuddy |
| `start-system.cmd` | 自动探测系统 Python（先 `py -3` 后 `python`，要求 ≥3.10） | 本机已安装 Python 3.10+ |

两者做同样四件事：

1. 检查 Python 与 `harvester.db` 是否存在；
2. 确保 `../session-harvester/cards_pending/` 目录存在（卡片校验端点需要）；
3. 启动两个控制台窗口：api-serve（:8765，带 `--cards-root`）+ view（:8088）；
4. 自动打开浏览器。

**停止 = 关闭那两个控制台窗口。**

### 方式二：手动启动

```bash
# 1. 起上游（主项目 session-harvester 目录下）
python -m harvester api-serve --db harvester.db \
    --cards-root cards_pending          # 默认 127.0.0.1:8765

# 2. 起本视图（本仓库目录下）
python view.py                          # 默认 127.0.0.1:8088
```

`--cards-root` 指向卡片目录（目录可暂时为空）：不配则报告页的
"卡片校验"会明确报"未配置 --cards-root"。

> ⚠️ **改了代码后必须重启两个服务**：Python 进程启动时加载代码，
> 旧进程不会自动感知新端点/新字段（表现多为"接口报未知路由"）。

### 页面顶部信息条

- 黄条 "本地代理不可达？" → api-serve 没起或端口不对；
- 红条 "上游鉴权失败" → config.json 里 token 与上游启动参数不一致；
- 红条 "API 版本不匹配" → 两边代码版本相差过大，各升级到配套版本。

## 四个页签

### 1️⃣ 会话检索（默认页）

从索引库里找会话，逐层下钻看原文。

| 操作 | 说明 |
|---|---|
| 筛选栏 | 来源 / 模型 / Skill / 起止日期 / 关键字（中文走上游 bigram 全文检索，2 字词可命中）/ **只看异常**（勾选后仅列出含错误步骤的会话，v2.3） |
| 结果表 | 更新时间 / 来源 / 模型 / 标题，命中词 `<mark>` 高亮，按更新时间倒序，每页 50 条；**首列勾选框**支持跨页累计多选（仅含错误的会话可勾） |
| **异常徽章** | 标题旁 "错 N"：该会话含 N 条错误步骤。**橙 = 1–9 条**（值得点开看）；**红 = ≥10 条**（明显异常）。徽章只汇总数量，是否真是问题由人判断 |
| 点行 | 下钻回合列表（回合号 / 角色构成 / 用户侧预览 / 时间） |
| 点回合 | 展开全文，按角色分色：用户（蓝）/ 助手（绿）/ 工具·思考（灰、等宽、超长折叠）/ 系统（橙） |
| 导出 md | 会话详情页右上角。拉取全部回合全文拼成 Markdown 下载（回合多时按钮显示进度）；**纯浏览器本地完成**，不写任何上游状态 |
| **批量导出 md** | 勾选多个会话后（分页条上），合并导出一份 Markdown：每会话一节，标注**会话ID + 异常类型**（归一 pattern）+ 错误步骤清单 + 全文下钻 |
| **批量导出 JSON** | 勾选后导出机器可读 JSON：错误按归一 pattern **跨会话去重聚合**（patterns_dedup，occurrences 带 (sid,seq) 锚点），可直接交给 Agent 分析。需上游 ≥ v0.20（error_steps 字段），旧上游自动降级 |

### 2️⃣ 蒸馏队列

回答"**接下来该沉淀什么**"：机器扫全库排队，人补判断。四个小节：

- **A. 新错误 pattern**——窗口内重复出现的错误（归一聚类），且未见过
  类似卡片 → pitfall 卡候选。每行带锚点样例（点击直通原会话对应位置）；
- **B. 旧坑重现**——已有卡片覆盖的错误又出现 → 检查旧卡是否要更新；
- **C. Skill 行为链候选**——skill 调用后的高频行为链 → workflow 卡素材；
- **D. 高信号会话 Top**——按消息/步骤数排的高频会话 → 泛读排期。

工具条参数：**窗口（天）**（0=全库）、**最少次数**（A 节聚类门槛）、
**会话 Top**（D 节条数）、**异常关键字过滤**（v2.3，本地过滤不重新请求，
命中模式/工具/技能名，按次数倒序）。表头"卡片比对 N 文件"表示已比对的
既有卡片数（未配置 --cards-root 显示"未配置"）。

| 操作 | 说明 |
|---|---|
| 复制命令 | A 节每行"复制命令"→ 剪贴板得到 `python -m harvester cards new --sid <样例会话> --type pitfall`；C 节"深挖命令"→ `python -m harvester report-skill --skill <名>`。**只复制、绝不执行**——请自己开终端粘贴运行 |
| 导出 md | 当前队列整表导出 Markdown（与 CLI `report-triage` 渲染同构） |
| 导出 JSON | 机器可读快照（上游 JSON 原始结构 + 归一去重说明头），可直接交给 Agent 分析 |
| 疑似已有卡 | 黄色 pill，表示该 pattern/关键词命中了已有卡片文本，建卡前先查重 |

### 3️⃣ 报告

回答"**整体长什么样**"：五种全局聚合报告。**切换下拉或切入本页会自动生成**，无需点按钮。

| 选项 | 内容 |
|---|---|
| G1 工具统计 | 工具失败率排行、失败后行为（重试/放弃占比）、按 Agent（数据源）分布 |
| G2 错误三分类 | 错误按"环境 / 用法 / 目标状态"三分类 + 轨迹位置分桶（开场/中途/收尾）+ 模式聚类明细（锚点可点） |
| G4 Skill 行为画像 | 每个 skill 的调用量 / 成功率 / 触发任务抽样 / 调用后行为链 / 锚点 |
| G2 建议条目 | 把高频错误模式写成 AGENTS.md 候选规则句（机器产出，人工审阅后自行并入） |
| G3 卡片校验 | 校验 cards_pending/ 目录下草稿卡的 frontmatter 与锚点真实性，给出"能否并入主库"结论 |

窗口（天）参数改完后点"生成报告"手动重跑。**按钮只在手动点击时给出反馈**：
禁用 + "生成中…" → 成功后闪"✓ 已重新生成"（约 1.5s）→ 恢复；自动渲染
（切入页面 / 切换类型）静默进行、不动按钮。参数未变时重跑结果与之前相同
（数据没变），meta 行的**生成时间与耗时**刷新即代表本次执行真实发生。

**异常过滤**（v2.3）：工具条上的"异常关键字"输入框与"只看异常"勾选
**本地过滤、即时生效**（不重新请求上游）。对 G1 显示按次数倒序的**根因
聚合明细表**（v0.21 P0-2：优先后端 roots 归一聚合，旧上游降级 errors
扁平化；默认视图同样渲染）；G2 过滤模式明细；G4 只看有失败的 skill；
建议池过滤条目与待归因模式；卡片校验过滤问题卡。**过滤态空结果**显示
"已过滤：0/N"计数（过滤前共 N、当前库无匹配异常，v0.21 P0-3）。

"导出 md" 把**当前**报告导出为 Markdown（本地生成）；"导出 JSON" 导出
机器可读快照（上游原始结构 + 归一去重说明头），可直接交给 Agent 分析。

### 4️⃣ 中台

多实例总览（配置了多个上游才有意义，单上游同样可用）。

| 操作 | 说明 |
|---|---|
| 搜索全部上游 | 关键字同时发到每个上游的会话检索，结果按实例分组展示；点结果行 → 切换到该实例并打开该会话。不可达上游只标注 error，不拖垮整体 |
| 刷新 facets 对比 | 各实例的会话时间范围 / 来源 / 模型 / Skill 分布卡片 + 同名求和汇总表 |

## 多上游（config.json）

无 config.json 时默认上游 `http://127.0.0.1:8765`。配多个上游后页面
顶部下拉切换，各上游互不知晓：

```json
{
  "upstreams": [
    {"name": "本机",   "url": "http://127.0.0.1:8765", "token": ""},
    {"name": "工作站", "url": "http://192.168.1.20:8765", "token": "密钥"}
  ]
}
```

- 文件位置：view.py 同目录（可用 `--config 路径` 改）；损坏/形状非法
  直接退出（fail loud），不猜；
- `token` 非空时转发自动携带 `X-Token` 头；
- 上游启动时加 `--token <密钥>` 即开启鉴权。

## 端点对照

本服务（view.py，默认 :8088）：

| 路由 | 作用 |
|---|---|
| `GET /` | 静态单页（static/index.html） |
| `GET /view/upstreams` | 上游列表（页面下拉数据源） |
| `GET /u/<编号>/api/*` | 转发到对应上游（编号 = config 数组下标，0 起） |
| `GET /u/all/facets` | 聚合全部上游 facets（同名求和） |
| `GET /u/all/search?q=…` | 跨实例搜索（逐上游转发 `/api/sessions`，检索语义复用上游） |

上游（api-serve）当前提供（节选，全部只读）：

| 端点 | 作用 |
|---|---|
| `/api/meta` | 库概况 + schema 指纹 + 是否配置 cards_root |
| `/api/sessions` | 会话列表（筛选/分页；v2.1 起每行带 `error_count`） |
| `/api/session/<sid>`、`/api/session/<sid>/turn/<n>` | 会话回合列表 / 回合全文 |
| `/api/facets`、`/api/search` | 分布统计 / 全文检索 |
| `/api/triage` | 蒸馏队列（A–D 四节数据） |
| `/api/reports/tools\|errors\|skills\|agents` | 四种全局报告 |
| `/api/cards` | 卡片校验（需 --cards-root；未配 400 / 目录缺失 404） |

## 安全模型

- 双向只读：本服务仅转发 GET（非 GET 一律 405）；仅转发 `/api/*` 子路径；
  上游只开只读连接（SQLite authorizer 白名单 + ro URI）；
- 默认绑 `127.0.0.1`，不对外网监听；
- 本视图**不执行任何命令**："复制命令"按钮只写剪贴板，执行由人完成；
  导出文件由浏览器 Blob 本地生成；
- 版本协商：上游 200 响应携带 `api_version` 且 major ≠ 1 时明确报错，
  不做静默猜测。

### 本机路径处理（2026-10-12 用户要求）

**展示面一律不印本机绝对路径**：页面上出现目录/文件位置时，用 `~` 缩写用户目录
（`C:\Users\<你>\.workbuddy\knowledge\cards` → `~/.workbuddy/knowledge/cards`），
完整路径放进元素的 `title`（悬停可见）。前端由 `displayPath()` 统一处理，
缩写的真源是 `/api/health.home`（服务端只报"用户目录是哪个"，不报凭据）。

**台账/执行面保留真实路径**：`actions_meta.db` 的 `target_path` 记的是真实绝对路径
——那是**本机审计**（"这次写的是哪个文件"）需要的，且只落本机 sqlite 文件、
不进版本库。两类面的区别是**给谁看**：给人看的板子要能分享，给机器审计的账要精确。

## FAQ

**Q：提示"api-serve 未配置 --cards-root"？**
A：上游启动时没带 `--cards-root <目录>`。用本仓库 start.cmd 启动即可
（已自动带上）；或手动启动时补参数。

**Q：点"生成报告"好像没反应？**
A：切入报告页或切换报告类型时会**自动生成**，不用点按钮。按钮是手动
重跑用的：改了"窗口（天）"参数、或想刷新数据时才需要点。参数没改再点，
结果和之前长得一样（meta 行时间戳会刷新，成功时按钮闪"✓ 已重新生成"）。
若停留"生成中…"超过十几秒，见下一问。

**Q：第一次点"生成队列"很慢？**
A：大库首次查询要把数据从磁盘读进 SQLite 页缓存（冷启动），之后通常
亚秒级。这是正常现象，不是卡死。

**Q：页面功能对不上文档 / 报"未知路由"？**
A：九成是**旧进程还在跑**。关掉 api-serve 与 view 两个窗口，重新
start.cmd。

**Q：导出/复制没反应？**
A：导出走浏览器下载（看下载栏）；复制依赖剪贴板权限，被拒时按钮无
"已复制"反馈，可手动选中文本复制。

## 测试

```bash
# 全套件（跑之前先设 PYTHONPATH 指向 session-harvester/scripts/sandbox）
python -m unittest discover -s tests -v

# 渲染门（node 逐文件列全；缺 node 时上面套件里转 skip，不是"通过"）
node tests/render_smoke.js      # P0-2/P0-3 报告渲染
node tests/render_cmd.js        # P2 命令台 + 数字皆门三跳 + 端点地址静态门
node tests/render_home.js       # P3-3 今日工作台（四队列 + 闭环健康）
node tests/boot_sequence.js     # 启动序列真跑（"永远加载中"那个真缺陷的回归门）
node tests/render_work.js       # v0.49 处理单（对账报警 + 建议 + 命令包 + 送命令台）
node tests/render_summary.js    # v0.74 卡片解释层 + v0.79 触发层（c-trig）
node tests/render_dualtrack.js  # v0.77 双轨时间线 + ts 锚点
node tests/render_reports.js    # v0.79 报告索引 + 昨日卡全文
node tests/render_wordcloud.js  # v0.81 词云导航（分档/下钻/空态）
node tests/render_kgraph.js     # v0.82 知识图谱（确定性布局/懒加载/诚实空态）

# v0.49 自查：把 5 个缺陷故意放回去，确认每道门都会红（末行打印 5/5 被抓）
python docs/reports/mutation-check-v049.py

# 跨仓库真实载荷门（真库取数喂真渲染函数；**退出码 3 = 未执行，不是通过**）
python tests/check_real_payload.py
```

覆盖：静态页服务、转发保真（query/token）、版本不匹配拒绝、
不可达与超时降级、非 GET 405、多上游切换、`/u/all/facets` 聚合降级、
`/u/all/search` 跨实例搜索与降级、config 加载 fail loud、
卡片本体解析、本地服务三端点、**actions-serve（白名单 403 / 参数过滤 /
契约版本 409 / 作业轮询 / timeline 五档 / pattern-sessions 数字对账 /
regress 语料漂移必须变红）**、**命令注册表与真解析器逐参数比对（漂移即红）**、
命令台渲染与三跳接线、**v0.49 处理单（数字↔明细对账 / 每条记录带建议 /
命令包摊平与可复制 / action_id 真在注册表里 / 生成的 CLI 真能被真解析器解析 /
target id URL 安全）、配色对比度（写死浅底与未定义变量一律红）**。

### 跨仓库集成门：真实载荷渲染（`tests/render_real.js`）

`render_smoke.js` 用的是**构造夹具**，证明不了"后端真的会发这个字段名"。这道门
用**真库现算的载荷**（兄弟仓库 `session-harvester` 的 `harvester.db` +
`topics_meta.db`）喂给从真实 `index.html` 提取的 `renderTopics` / `renderChain`：

```bash
python tests/check_real_payload.py            # 取数 → node 断言（退出码 0/1/3）
# 退出码 3 = **未执行**（后端仓库或库不在；测试里会 skip 并写明理由，
#            不当成通过——"未执行/不适用/失败"必须能分开看）
```

断言**全部从载荷推导**（主题数、链数、首阶段文本、锚点节点数、首个关键词），
不写死真实数字——写死的话数据一变这道门就假红。真正"不适用"的项（例如载荷里
没有多链主题）单独计 `n/a` 并打印理由，不混进 ok。

原为后端仓库 `docs/reports/` 下的一次性脚本（`check-view-real.py` +
`view-real-render-check.js`），v0.33 移进本仓库并接入上面的套件。
