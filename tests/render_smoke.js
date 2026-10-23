/* v0.21 P0-2/P0-3 render 层冒烟断言（node 运行，退出码 0=全过）。
 *
 * H22 教训：过滤器类改动必须断言"过滤真的能滤"（行数必须变化）。
 * 做法：从 index.html 提取 renderTools/renderSkills/renderCards 源文本，
 * 注入最小 DOM stub 后真实执行，断言：
 *  P0-2: roots 优先渲染（输出含根因模式、类别列；计数累加而非行数）；
 *        关键字过滤后根因行数变化；旧上游无 roots 时降级 errors 扁平化。
 *  P0-3: 勾选 rep-erronly 后 meta 行含"已过滤"，空态文案含计数。
 */
/* 注意：不得声明 "use strict"——严格模式下 eval 内的函数声明不外泄 */
const fs = require("fs");
const path = require("path");

const htmlPath = path.join(__dirname, "..", "static", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");

function extract(name, sig) {
  // 顶格 "function name(...)" 到下一个顶格 "}"（函数体内部闭括号均有缩进）
  const s = sig || "d,out";
  const re = new RegExp("^function " + name + "\\(" + s + "\\)\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name);
  return m[0];
}

let store;
function freshStore(q, only) {
  return {
    "rep-q": { value: q || "" },
    "rep-erronly": { checked: !!only },
    "rep-meta": { textContent: "" },
  };
}
const $ = id => store[id];
const esc = s => String(s == null ? "" : s);
/* v0.34：渲染目标 out 需要 querySelectorAll（chips/锚点接线），桩统一供给 */
function mkOut() { return { innerHTML: "", querySelectorAll: () => [] }; }
const repFilter = () => ({
  q: store["rep-q"].value.trim().toLowerCase(),
  only: store["rep-erronly"].checked,
});
const hitQ = (t, q) => !q || String(t || "").toLowerCase().includes(q);
const anchor = (sid, seq, text) => "<a>" + sid + "#" + seq + "</a>";
/* v0.34 P1 桩：renderCards 现在依赖 shortSid/bindCardAnchors（D9 本体合并），
   冒烟环境用最简桩——断言"渲染不崩 + 本体节出现"；接线细节由
   test_v34_local_data + 真实载荷门覆盖。 */
const shortSid = sid => String(sid || "").slice(0, 12);
const bindCardAnchors = root => {};
const clsLabel = c => ({ env: "环境类", tool_interface: "用法类",
  context: "上下文类" }[c] || c);
const patternPlain = () => "";
const state = { cardsBodies: [], errChip: "" };

/* 数据夹具：Edit 带 roots（2 根因，count 5+2=7），Bash 无 roots（降级
   errors，count 3，pattern 与 Edit 的 P1 同文本 → 跨工具合并 5+3=8） */
const DATA = {
  tools: [
    { tool: "Edit", calls: 20, success: 13, error: 7, fail_rate: 0.35,
      given_up: 2, retried: 1, raw_tools: ["Edit"], low_sample: false,
      roots: [
        { pattern: "P1-file-changed", "class": "tool_interface", count: 5,
          sample: "sample one" },
        { pattern: "P2-not-found", "class": "tool_interface", count: 2,
          sample: "sample two" },
      ],
      errors: [{ detail: "RAW-E1", count: 5 }, { detail: "RAW-E2", count: 2 }] },
    { tool: "Bash", calls: 9, success: 6, error: 3, fail_rate: 0.333,
      given_up: 0, retried: 0, raw_tools: ["Bash"], low_sample: false,
      roots: [],
      errors: [{ detail: "P1-file-changed", count: 3 }] },
  ],
  flow: { errors: 10, retried: 1, given_up: 2 },
  by_source: {},
};

/* 第③步：renderTools 里的 errTable 现在调模块级阈值辅助函数
   （repMinOf/repGate/repRemain/repMinChips/wireRepThresh）。extract 只提单个
   函数，这里把**整块**提出来（从注释头到 wireRepThresh 的收尾 `}`），并把
   `const REP_MIN_DEFAULT` 改成 `var`——eval 里的 const 不外泄，会报未定义。 */
function extractBlock(firstMarker, lastFn) {
  const i = html.indexOf(firstMarker);
  const fnAt = html.indexOf("function " + lastFn + "(");
  const j = html.indexOf("\n}\n", fnAt);
  if (i < 0 || fnAt < 0 || j < 0) throw new Error("extractBlock fail: " + firstMarker);
  return html.slice(i, j + 3).replace("const REP_MIN_DEFAULT = 3;",
                                      "var REP_MIN_DEFAULT = 3;");
}
eval(extractBlock("/* 报告页「次数阈值」", "wireRepThresh"));
eval(extract("renderTools"));
eval(extract("renderSkills"));
function pTag(){return "";} /* P5-2 桩：身份徽章不参与卡片渲染断言 */
eval(extract("renderCards"));

let failed = 0;
function check(label, cond) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label); }
}

function runTools(q, only) {
  store = freshStore(q, only);
  const out = mkOut();
  renderTools(DATA, out);
  return out.innerHTML;
}

/* --- P0-2 renderTools --- */
/* 第③步起根因表默认按次数阈值（≥3）过滤；本节验的是"渲染/过滤/proots"，
   故把阈值显式调到 1（全部）。阈值本身由 render_threshold.js 专测。 */
state.repMin = 1;
let h0 = runTools("", false);
check("默认视图渲染根因表", h0.includes("异常根因明细"));
check("roots 优先：模式占位符名出现", h0.includes("P1-file-changed"));
check("roots 优先：原始 errors 明细 RAW-E1 不再直吐", !h0.includes("RAW-E1"));
check("跨工具合并计数 8（5+3 累加，非行数 2）", h0.includes("<td class='num'>8</td>"));
check("类别列存在", h0.includes("tool_interface"));

let hq = runTools("p2", false);
check("关键字过滤后行数变化（仅 P2）", hq.includes("P2-not-found") && !hq.includes("P1-file-changed"));

let hnone = runTools("不存在关键字xyz", false);
check("无命中空态", hnone.includes("无命中"));

/* 降级：去掉 roots 字段（旧上游）→ errors 扁平化仍可用 */
const OLD = { tools: DATA.tools.map(t => {
  const c = Object.assign({}, t); delete c.roots; return c;
}) };
store = freshStore("", false);
const oldOut = mkOut();
renderTools(OLD, oldOut);
check("无 roots 降级 errors 扁平化", oldOut.innerHTML.includes("RAW-E1"));

/* --- P0-3 renderSkills / renderCards --- */
const SK = { n_invocations: 9, skills: [
  { skill: "aa", calls: 5, sids: 2, ok: 5, err: 0, no_result: 0,
    sources: ["src"], args_samples: [], chains: {}, anchors: [] },
  { skill: "bb", calls: 4, sids: 1, ok: 3, err: 1, no_result: 0,
    sources: ["src"], args_samples: [], chains: {}, anchors: [] },
] };
store = freshStore("", true);  // 勾选 rep-erronly：aa（无异常）被滤掉
const skOut = mkOut();
renderSkills(SK, skOut);
check("rep-erronly 后 meta 含'已过滤'", skOut2_meta());
function skOut2_meta() { return store["rep-meta"].textContent.includes("已过滤"); }
check("全异常被滤光时空态带计数", (() => {
  const SK0 = { n_invocations: 0, skills: [
    { skill: "aa", calls: 5, sids: 2, ok: 5, err: 0, no_result: 0,
      sources: ["src"], args_samples: [], chains: {}, anchors: [] }] };
  store = freshStore("", true);
  const o = mkOut();
  renderSkills(SK0, o);
  return o.innerHTML.includes("已过滤：0/1 个 skill");
})());

const CD = { root: "/cards", summary: { cards: 4, ok: 4, warn: 0, error: 0,
  anchor_checked: 4, anchor_misses: 0 }, cards: [
  { path: "a.md", errors: [], warnings: [] },
  { path: "b.md", errors: [], warnings: [] }] };
store = freshStore("", true);  // 只看错误级：无错误卡 → 空态
const cdOut = mkOut();
renderCards(CD, cdOut);
/* 卡片页的 meta 现在用 innerHTML（要在 title 里放完整路径、正文放缩写路径，
   用户 2026-10-12 要求展示面不印本机绝对路径），故这里读 innerHTML；
   同时钉住"正文不出现绝对路径"（title 里允许有，那是悬停可见的定位信息）。 */
check("G3 rep-erronly 后 meta 含'已过滤'",
  store["rep-meta"].innerHTML.includes("已过滤"));
check("G3 meta 正文字不印本机绝对路径（用户 2026-10-12）",
  !/>[A-Za-z]:[\\/]/.test(store["rep-meta"].innerHTML) &&
  !store["rep-meta"].innerHTML.includes("C:\\Users"));
check("G3 过滤态空态带计数", cdOut.innerHTML.includes("已过滤：0/4 张卡"));

/* --- v0.34 P1-3 卡片本体合并（D9）--- */
const CDB = { root: "/cards", summary: { cards: 2, ok: 2, warn: 0, error: 0,
  anchor_checked: 1, anchor_misses: 0 }, cards: [
  { path: "a.md", errors: [], warnings: [] },
  { path: "ghost.md", errors: ["锚点 x 不在索引库"], warnings: [] }] };
state.cardsBodies = [
  { path: "a.md", parsed: true, id: "kc-1", title: "标题甲",
    type: "pitfall", confidence: 0.9, evidence: "证据引文甲",
    conclusion: "结论甲", anchors: [{ sid: "dsh:abc", turn: 5 }] }];
store = freshStore("", false);
const cdbOut = mkOut();
renderCards(CDB, cdbOut);
check("P1-3 本体节渲染（标题出现）", cdbOut.innerHTML.includes("标题甲"));
check("P1-3 校验徽章与本体合并（校验通过 pill）",
  cdbOut.innerHTML.includes("校验通过"));
check("P1-3 证据片段渲染", cdbOut.innerHTML.includes("证据引文甲"));
check("P1-3 锚点渲染（sid#turn）", cdbOut.innerHTML.includes("#5"));
check("P1-3 无本体校验卡显式标注（ghost）",
  cdbOut.innerHTML.includes("ghost.md") &&
  cdbOut.innerHTML.includes("无本体"));
state.cardsBodies = [];
store = freshStore("", false);
const cdb0 = mkOut();
renderCards(CDB, cdb0);
check("P1-3 本体未加载时给运行 viewserver 提示（不冒充空柜）",
  cdb0.innerHTML.includes("viewserver"));

/* --- v0.34 P1-1/P1-5 glossary/chips 纪律断言（源码级） --- */
check("模式人话只在收录时显示（patternPlain 未收录返回空）",
  html.includes("function patternPlain"));
check("chips 有 class 过滤接线（data-errchip）",
  html.includes("data-errchip"));
check("结论卡数字全部来自 API 现算（renderRepConcl 存在）",
  html.includes("function renderRepConcl"));
check("导语来自 glossary 而非散落文案（panelIntro 消费 glossary）",
  html.includes("function panelIntro"));
/* --- v0.64（用户 2026-10-14 批注）：报告页结论卡「在命令台打开」真接线 --- */
check("结论卡「在命令台打开」不再是 P2 占位（占位文案清零）",
  !html.includes("P2 接入（占位）"));
check("三张结论卡真接线到动作面（tools/errors=A 档直执，agents=B 档两段提交）",
  html.includes('openInCmd("report_tools",{})') &&
  html.includes('openInCmd("report_errors",{})') &&
  html.includes('openInCmd("suggest_agents",{})'));
check("报告页四个标签共用窗口口径声明（与工作台全库口径对账）",
  html.includes("口径=近 ") && html.includes("全库口径见工作台") &&
  html.includes("两边数字不同是口径不同不是账错"));
check("处理单页常驻「数据都在哪」路径速查（v0.66 正本/镜像架构）",
  html.includes("数据都在哪") && html.includes("不是运行时探测的") &&
  html.includes("知识正本") && html.includes("发布镜像") &&
  html.includes("kb-publish"));

/* --- v0.22 T3-5 主题 tab（H22：动态性 + 接线断言） ---
 * 主题必须数据驱动：渲染 /api/topics 返回什么就显示什么，绝无写死主题名。
 * 锚点节点必须带 data-sid/data-turn 并真正接线到会话下钻。 */
function extractFn(name, sig) {
  const re = new RegExp("^(?:async )?function " + name + "\\(" + sig +
    "\\)\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name + "(" + sig + ")");
  return m[0];
}
eval(extractFn("kwText", "k"));
eval(extract("renderTopics"));
eval(extract("renderChain", "d,out,idx"));
eval(extractFn("topicPackCmd", "id,level,sid"));
eval(extractFn("bindChainSwitch", "root,d"));
eval(extractFn("bindTopicNodes", "root"));   // 切换后会重绑节点，需一并提取
const gSrc = extractFn("gotoTopicAnchor", "sid,turn");

/* renderTopics：任意主题名都渲染（多主题，非写死清单） */
const TOPICS = { topics: [
  { id: "tp-x", name: "主题甲", keywords: "[\"k1\",\"k2\"]", members_count: 7,
    first_activity: "2025-01-01T00:00:00", last_activity: "2025-06-01",
    chains_count: 2, chain_names: ["思维链：叙事节奏", "思维链：吾好梦中救人"] },
  { id: "tp-y", name: "主题乙", keywords: "", members_count: 2,
    first_activity: null, last_activity: null, chains_count: 0,
    chain_names: [] },
  { id: "tp-z", name: "空类目", keywords: "不是JSON", members_count: 0,
    first_activity: null, last_activity: null, chains_count: 0,
    chain_names: [] }] };
const tOut = mkOut();
renderTopics(TOPICS, tOut);
check("主题列表渲染主题甲（数据驱动）", tOut.innerHTML.includes("主题甲"));
check("主题列表渲染主题乙——不假定单一主题", tOut.innerHTML.includes("主题乙"));
check("关键词按人读形态渲染（JSON → 顿号，不留引号/方括号）",
  tOut.innerHTML.includes("k1、k2") && !tOut.innerHTML.includes("[&quot;k1&quot;"));
check("关键词解析失败时原样显示，不吞内容",
  tOut.innerHTML.includes("不是JSON"));
check("成员数随数据渲染", tOut.innerHTML.includes("7"));
check("行带 data-tid（点击接线载体，3 行）",
  (tOut.innerHTML.match(/data-tid=/g) || []).length === 3);
/* v0.25：一个主题可以有多条 chain，列表要看得见"有几条链" */
check("链列显示条数与链名", tOut.innerHTML.includes(">链<") &&
  tOut.innerHTML.includes("2 <span class='sub2'>思维链：叙事节奏") &&
  tOut.innerHTML.includes("思维链：吾好梦中救人"));
check("无链主题显示占位符而非空白", tOut.innerHTML.includes("—"));
check("0 成员类目灰显（自己类目还没挂成员）",
  tOut.innerHTML.includes("opacity:.55"));

const tEmpty = mkOut();
renderTopics({ topics: [] }, tEmpty);
check("空注册表空态", tEmpty.innerHTML.includes("尚无注册主题"));

const tHint = mkOut();
renderTopics({ topics: [], hint: "topics_meta 未配置" }, tHint);
check("未配置上游显示 hint 且声明动态发现",
  tHint.innerHTML.includes("topics_meta 未配置") &&
  tHint.innerHTML.includes("发现"));

/* renderChain：时间线（阶段/节点/锚点）+ 右栏文档 */
store = freshStore("", false);
store["topic-doc"] = mkOut();
const CH = { topic_id: "tp-x", chain_path: "topics/chain-主题甲.md",
  body: "## 阶段一\n正文内容示例",
  fm: { topic: "主题甲", members: ["s1", "s2"],
    db_fingerprint: { sessions: 296 },
    anchors: [
      { stage: "阶段一·诊断", span: "2025-01 ~ 2025-02", nodes: [
        { sid: "deepseek-export:a", turn: 1, note: "设定提交" },
        { sid: "yuanbao-raw:b", turn: 4, note: "跨模型交叉验证" }] },
      { stage: "阶段二·迭代", span: "2025-06", nodes: [
        { sid: "deepseek-export:a", turn: 9, note: "数十轮打磨" }] }] } };
const chOut = mkOut();
renderChain(CH, chOut);
check("阶段标题渲染", chOut.innerHTML.includes("阶段一·诊断") &&
  chOut.innerHTML.includes("阶段二·迭代"));
check("节点 note 渲染", chOut.innerHTML.includes("设定提交") &&
  chOut.innerHTML.includes("数十轮打磨"));
check("节点锚点带 data-sid+data-turn（3 个，含 turn=4）",
  (chOut.innerHTML.match(/data-sid=/g) || []).length === 3 &&
  chOut.innerHTML.includes('data-turn="4"'));
check("右栏文档收到 body 与成员数",
  store["topic-doc"].innerHTML.includes("正文内容示例") &&
  store["topic-doc"].innerHTML.includes("2"));
check("单链（旧上游）不渲染切换条",
  !chOut.innerHTML.includes("data-chain-idx"));

/* v0.25 多链：一个主题两条 chain —— 切换条 + 按 idx 渲染对应那条 + 接线 */
const CH2 = { topic_id: "tp-x", chain_count: 2,
  chains: [
    { name: "思维链：叙事节奏（技法主线）", chain_path: "topics/chain-叙事节奏.md",
      body: "叙事节奏正文", stages: 2, nodes: 3,
      fm: { topic: "主题甲", members: ["s1"], anchors: [
        { stage: "技法一", span: "2025-01", nodes: [
          { sid: "deepseek-export:a", turn: 2, note: "节奏对比" }] }] } },
    { name: "思维链：吾好梦中救人（作品本体主线）",
      chain_path: "topics/chain-吾好梦中救人.md", body: "作品本体正文",
      stages: 1, nodes: 1,
      fm: { topic: "主题甲", members: ["s1", "s2"], anchors: [
        { stage: "本体一", span: "2025-02", nodes: [
          { sid: "yuanbao-raw:b", turn: 7, note: "设定补全" }] }] } } ] };
store["topic-doc"] = mkOut();
const ch2Out = mkOut();
renderChain(CH2, ch2Out, 0);
check("多链第一条：切换条两个按钮 + 标注 1/2",
  (ch2Out.innerHTML.match(/data-chain-idx=/g) || []).length === 2 &&
  ch2Out.innerHTML.includes("技法一") && !ch2Out.innerHTML.includes("本体一"));
store["topic-doc"] = mkOut();
renderChain(CH2, ch2Out, 1);
check("按 idx 渲染第二条（阶段与正文都换）",
  ch2Out.innerHTML.includes("本体一") &&
  store["topic-doc"].innerHTML.includes("作品本体正文") &&
  !store["topic-doc"].innerHTML.includes("叙事节奏正文"));
check("右栏标出当前是第几条链",
  store["topic-doc"].innerHTML.includes("作品本体主线"));
const cBtn = { dataset: { chainIdx: "0" }, onclick: null };
const fakeRoot = { querySelectorAll: s =>
  s === "[data-chain-idx]" ? [cBtn] : [], innerHTML: "" };
store["topic-doc"] = mkOut();
bindChainSwitch(fakeRoot, CH2);
check("链切换按钮已接线（onclick 是函数）", typeof cBtn.onclick === "function");
cBtn.onclick();
check("点按钮真的换链（切回第一条）",
  fakeRoot.innerHTML.includes("技法一") &&
  store["topic-doc"].innerHTML.includes("叙事节奏正文"));

/* topicPackCmd：纯文本复制命令，绝无执行语义（红线 3） */
check("命令含 topic pack 与 --id/--level",
  topicPackCmd("tp-x", "coarse").includes("topic pack --id tp-x --level coarse"));
check("fine/artifact 附 --sid 占位", topicPackCmd("tp-x", "artifact").includes("--sid"));
check("命令串不含执行语义", !/\b(fetch|exec|spawn|eval)\(/.test(topicPackCmd("tp-x", "mid")));

/* gotoTopicAnchor 接线：必须真正 openSession+openTurn（H22 接线断言） */
check("锚点跳转接线 openSession+openTurn",
  gSrc.includes("openSession(") && gSrc.includes("openTurn("));
check("跳转先切回会话 tab", gSrc.includes('switchTab("sessions")'));

/* --- v0.22 P1-4 统一分析导出（view 端只做下载，SOP-P1-4） --- */
check("exportAnalysis 走 /api/export-analysis 端点",
  html.includes("/api/export-analysis"));
check("apiText 消费 text/markdown 出口",
  html.includes("async function apiText"));
check("六按钮全部接线 exportAnalysis（定义+sessions/triage/报告 md|json）",
  (html.match(/exportAnalysis\(/g) || []).length >= 7);
check("旧本地拼装已移除（exportAbnormal*/mdTriage）",
  !html.includes("exportAbnormalMd") &&
  !html.includes("exportAbnormalJson") &&
  !html.includes("function mdTriage"));
check("agents/cards 报告仍可本地导出（端点未覆盖，mdReport 保留）",
  html.includes("mdReport("));

/* --- v0.51 banner 增强 + 锚点/下钻并发守卫（用户 2026-10-13 报） ---
 * 门钉不变式：①提示条可关可跳；②api 成功不清粘性提示；③切页签必关；
 * ④锚点链/下钻渲染带代号守卫（连点不串台）；⑤无原文+回合号明确直开回合。 */
check("banner 有常驻关闭按钮与可点动作（showBannerRich）",
  html.includes("function showBannerRich") &&
  html.includes("class='banner-x'") &&
  html.includes("class='banner-act'"));
check("api 成功走软清除（err/锚点粘性提示不被 loadAll 冲掉）",
  html.includes("function softClearBanner") &&
  (html.match(/softClearBanner\(\)/g) || []).length >= 3 &&
  !/if\(res\.ok\)\{\s*hideBanner\(\)/.test(html));
check("切页签无条件关提示条",
  /function switchTab\(name\)\{\s*hideBanner\(\);/.test(html));
check("锚点链带 anchorRun 守卫（连点不串台）",
  /async function gotoAnchor\(sid,seq,errText\)\{\s*const run=\+\+state\.anchorRun;/
    .test(html) &&
  /async function gotoTopicAnchor\(sid,turn\)\{\s*const run=\+\+state\.anchorRun;/
    .test(html));
check("下钻渲染带 drillTk 代号（晚发赢家，早发弃渲染）",
  /async function openSession\(sid\)\{\s*const tk=\+\+state\.drillTk;/.test(html) &&
  /async function openTurn\(sid,no,hitIdx\)\{\s*const tk=\+\+state\.drillTk;/.test(html));
check("无错误原文+回合号明确 → 直开回合（不再「自行定位」）",
  html.includes("await openTurn(sid,seq)"));

/* --- v0.22 P1-3 交叉表（G2 renderErrors 消费 additive cross 字段） ---
 * H22：渲染断言必须演示会红；旧上游无 cross 字段时降级不渲染不崩。 */
eval(extract("renderErrors"));
const DE = {
  meta: { error_count: 3, total_steps: 100, sessions: 2 },
  by_class: { env: 1, tool_interface: 2 },
  by_bucket: { "开场": 3 },
  by_source: { dsh: { tool_interface: 2 }, wc: { env: 1 } },
  patterns: [],
  cross: [
    { source: "dsh", model: "deepseek-flash", env: 0, tool_interface: 2,
      context: 0, unclassified: 0, total: 2 },
    { source: "wc", model: "（未知）", env: 1, tool_interface: 0,
      context: 0, unclassified: 0, total: 1 },
  ],
};
const deOut = mkOut();
renderErrors(DE, deOut);
const deH = deOut.innerHTML;
check("交叉表节渲染", deH.includes("数据源 × model × 错误类别交叉表"));
check("首行=最多坑组合（API 序，dsh×deepseek-flash 在前）",
  deH.indexOf("deepseek-flash") >= 0 &&
  deH.indexOf("deepseek-flash") < deH.indexOf("（未知）"));
check("model（未知）直出与合计列",
  deH.includes("（未知）") && deH.includes("<td class='num'>2</td>"));
const DE_OLD = Object.assign({}, DE); delete DE_OLD.cross;
const deOldOut = mkOut();
renderErrors(DE_OLD, deOldOut);
check("旧上游无 cross 降级：不渲染交叉表且不崩（三分类仍在）",
  !deOldOut.innerHTML.includes("交叉表") &&
  deOldOut.innerHTML.includes("三分类分布"));

/* --- v0.34 P1-5：errors 类别 chips 渲染 + 点击过滤真的能滤（H22）---
   DE.patterns 为空（上面交叉表夹具），chips 断言用带 3 模式的专用夹具：
   env×1 + tool_interface×2（x2+x3 两行）。 */
const DEP = Object.assign({}, DE, { patterns: [
  { pattern: "PAT-ENV", "class": "env", count: 1, tools: ["t1"], samples: [] },
  { pattern: "PAT-TI-A", "class": "tool_interface", count: 2, tools: ["t2"],
    samples: [] },
  { pattern: "PAT-TI-B", "class": "tool_interface", count: 3, tools: ["t3"],
    samples: [] }] });
/* 第③步起模式表默认按次数阈值（≥3）过滤；本节只验 chips 口径，
   故先显式把阈值调成 1（"全部"）——阈值本身由 render_threshold.js 专测。 */
state.repMin = 1;
state.errChip = "";
const depAll = mkOut();
renderErrors(DEP, depAll);
check("chips 行渲染（全部 + 2 类别）",
  (depAll.innerHTML.match(/data-errchip=/g) || []).length === 3 &&
  depAll.innerHTML.includes("用法类 5"));
check("chips 数字=事件条数（全部 6 = 1+2+3），而模式行仍是 3 行",
  depAll.innerHTML.includes("全部 6") &&
  /* P2-5 起每格是"可点的门"（data-pat2 锚点），故断言门与次数文本，
     而不是旧的 `x1</td>` 字面形态——含义不变：三种模式各一行、次数正确 */
  (depAll.innerHTML.match(/data-pat2=/g) || []).length === 6 &&
  depAll.innerHTML.includes(">x1</a>") &&
  depAll.innerHTML.includes(">x2</a>") &&
  depAll.innerHTML.includes(">x3</a>"));
state.errChip = "env";
const depEnv = mkOut();
renderErrors(DEP, depEnv);
check("chips 选中 env 后过滤真的能滤（只剩 PAT-ENV 一行）",
  depEnv.innerHTML.includes("PAT-ENV") &&
  !depEnv.innerHTML.includes("PAT-TI-A") &&
  !depEnv.innerHTML.includes("PAT-TI-B"));
state.errChip = "tool_interface";
const depTI = mkOut();
renderErrors(DEP, depTI);
check("chips 选中用法类（两行合计 x5）",
  depTI.innerHTML.includes(">x2</a>") &&
  depTI.innerHTML.includes(">x3</a>") &&
  !depTI.innerHTML.includes("PAT-ENV"));
state.errChip = "";

/* ===== 列表页总纪律（2026-10-23 用户口径）：**全量渲染，或说清藏了几条** =====
   任何列表页都不许静默截断。这里给每页塞 30 条，两个可接受结局：
   ① 30 条名字都在（没截断）；② 页面写明「另有 N / 共 N / 前 N 条」（截断但报数）。
   两个都不满足 = 静默丢行 → 红。 */
const N30 = 30;
const mk30 = (f) => Array.from({ length: N30 }, (_, i) => f(i));
const EXPLAINED = /另有\s*\d+|共\s*\d+\s*(条|个)|前\s*\d+\s*条|已过滤/;
const lostRows = (html, names) => names.filter(n => html.indexOf(n) < 0).length;

/* skills */
const SK30 = { n_invocations: 300, min_calls: 1, skills: mk30(i => ({
  skill: "sk-" + String(i).padStart(2, "0"), calls: 30 - i, sids: 1, ok: 30 - i,
  err: 0, no_result: 0, sources: ["src"], args_samples: [], chains: {},
  anchors: [] })) };
store = freshStore("", false);
const sk30Out = mkOut();
renderSkills(SK30, sk30Out);
{
  const names = mk30(i => "sk-" + String(i).padStart(2, "0"));
  check("skills 30 条：全渲染或报数（不静默丢行）",
    lostRows(sk30Out.innerHTML, names) === 0 || EXPLAINED.test(sk30Out.innerHTML));
}

/* cards */
/* cards：卡片页的"清单"是**校验异常清单**（无问题的卡不进表，这是设计），
   所以夹具必须每张都带一个问题，才能验"30 条异常全在或报数"。 */
const CD30 = { root: "/cards", summary: { cards: N30, ok: 0, warn: 0, error: N30,
  anchor_checked: N30, anchor_misses: 0 },
  cards: mk30(i => ({ path: "card-" + String(i).padStart(2, "0") + ".md",
                      errors: ["锚点缺失 #" + i], warnings: [] })) };
store = freshStore("", false);
const cd30Out = mkOut();
renderCards(CD30, cd30Out);
{
  const names = mk30(i => "card-" + String(i).padStart(2, "0") + ".md");
  check("cards 30 张：全渲染或报数（不静默丢行）",
    lostRows(cd30Out.innerHTML, names) === 0 || EXPLAINED.test(cd30Out.innerHTML));
}

/* topics */
const TP30 = { topics: mk30(i => ({ id: "tp-" + i,
  name: "主题-" + String(i).padStart(2, "0"), keywords: "", members_count: 1,
  first_activity: null, last_activity: null, chains_count: 0, chain_names: [] })) };
const tp30Out = mkOut();
renderTopics(TP30, tp30Out);
{
  const names = mk30(i => "主题-" + String(i).padStart(2, "0"));
  check("topics 30 个：全渲染或报数（不静默丢行）",
    lostRows(tp30Out.innerHTML, names) === 0 || EXPLAINED.test(tp30Out.innerHTML));
}

if (failed) { console.error(failed + " check(s) failed"); process.exit(1); }
console.log("ALL RENDER CHECKS PASSED");
