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

function extract(name) {
  // 顶格 "function name(...)" 到下一个顶格 "}"（函数体内部闭括号均有缩进）
  const re = new RegExp("^function " + name + "\\(d,out\\)\\{[\\s\\S]*?^\\}", "m");
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
const repFilter = () => ({
  q: store["rep-q"].value.trim().toLowerCase(),
  only: store["rep-erronly"].checked,
});
const hitQ = (t, q) => !q || String(t || "").toLowerCase().includes(q);
const anchor = (sid, seq, text) => "<a>" + sid + "#" + seq + "</a>";

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

eval(extract("renderTools"));
eval(extract("renderSkills"));
eval(extract("renderCards"));

let failed = 0;
function check(label, cond) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label); }
}

function runTools(q, only) {
  store = freshStore(q, only);
  const out = { innerHTML: "" };
  renderTools(DATA, out);
  return out.innerHTML;
}

/* --- P0-2 renderTools --- */
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
const oldOut = { innerHTML: "" };
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
const skOut = { innerHTML: "" };
renderSkills(SK, skOut);
check("rep-erronly 后 meta 含'已过滤'", skOut2_meta());
function skOut2_meta() { return store["rep-meta"].textContent.includes("已过滤"); }
check("全异常被滤光时空态带计数", (() => {
  const SK0 = { n_invocations: 0, skills: [
    { skill: "aa", calls: 5, sids: 2, ok: 5, err: 0, no_result: 0,
      sources: ["src"], args_samples: [], chains: {}, anchors: [] }] };
  store = freshStore("", true);
  const o = { innerHTML: "" };
  renderSkills(SK0, o);
  return o.innerHTML.includes("已过滤：0/1 个 skill");
})());

const CD = { root: "/cards", summary: { cards: 4, ok: 4, warn: 0, error: 0,
  anchor_checked: 4, anchor_misses: 0 }, cards: [
  { path: "a.md", errors: [], warnings: [] },
  { path: "b.md", errors: [], warnings: [] }] };
store = freshStore("", true);  // 只看错误级：无错误卡 → 空态
const cdOut = { innerHTML: "" };
renderCards(CD, cdOut);
check("G3 rep-erronly 后 meta 含'已过滤'", store["rep-meta"].textContent.includes("已过滤"));
check("G3 过滤态空态带计数", cdOut.innerHTML.includes("已过滤：0/4 张卡"));

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
eval(extract("renderTopics"));
eval(extract("renderChain"));
eval(extractFn("topicPackCmd", "id,level,sid"));
const gSrc = extractFn("gotoTopicAnchor", "sid,turn");

/* renderTopics：任意主题名都渲染（多主题，非写死清单） */
const TOPICS = { topics: [
  { id: "tp-x", name: "主题甲", keywords: "k1, k2", members_count: 7,
    first_activity: "2025-01-01T00:00:00", last_activity: "2025-06-01" },
  { id: "tp-y", name: "主题乙", keywords: "", members_count: 2,
    first_activity: null, last_activity: null }] };
const tOut = { innerHTML: "" };
renderTopics(TOPICS, tOut);
check("主题列表渲染主题甲（数据驱动）", tOut.innerHTML.includes("主题甲"));
check("主题列表渲染主题乙——不假定单一主题", tOut.innerHTML.includes("主题乙"));
check("关键词/成员数随数据渲染", tOut.innerHTML.includes("k1, k2") &&
  tOut.innerHTML.includes("7"));
check("行带 data-tid（点击接线载体，2 行）",
  (tOut.innerHTML.match(/data-tid=/g) || []).length === 2);

const tEmpty = { innerHTML: "" };
renderTopics({ topics: [] }, tEmpty);
check("空注册表空态", tEmpty.innerHTML.includes("尚无注册主题"));

const tHint = { innerHTML: "" };
renderTopics({ topics: [], hint: "topics_meta 未配置" }, tHint);
check("未配置上游显示 hint 且声明动态发现",
  tHint.innerHTML.includes("topics_meta 未配置") &&
  tHint.innerHTML.includes("发现"));

/* renderChain：时间线（阶段/节点/锚点）+ 右栏文档 */
store = freshStore("", false);
store["topic-doc"] = { innerHTML: "" };
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
const chOut = { innerHTML: "" };
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
const deOut = { innerHTML: "" };
renderErrors(DE, deOut);
const deH = deOut.innerHTML;
check("交叉表节渲染", deH.includes("数据源 × model × 错误类别交叉表"));
check("首行=最多坑组合（API 序，dsh×deepseek-flash 在前）",
  deH.indexOf("deepseek-flash") >= 0 &&
  deH.indexOf("deepseek-flash") < deH.indexOf("（未知）"));
check("model（未知）直出与合计列",
  deH.includes("（未知）") && deH.includes("<td class='num'>2</td>"));
const DE_OLD = Object.assign({}, DE); delete DE_OLD.cross;
const deOldOut = { innerHTML: "" };
renderErrors(DE_OLD, deOldOut);
check("旧上游无 cross 降级：不渲染交叉表且不崩（三分类仍在）",
  !deOldOut.innerHTML.includes("交叉表") &&
  deOldOut.innerHTML.includes("三分类分布"));

if (failed) { console.error(failed + " check(s) failed"); process.exit(1); }
console.log("ALL RENDER CHECKS PASSED");
