/* 真实载荷渲染检查（**跨仓库集成门**）：把真实 API 载荷喂给真实 index.html 里
 * 提取出来的 renderTopics / renderChain，验证"后端字段名 ↔ 前端期望"真的对得上。
 *
 * 与 render_smoke.js 的分工：
 *   - render_smoke.js 用**构造夹具**：快、无外部依赖，随每次 `unittest` 跑；
 *   - 本脚本用**真库现算的载荷**：慢、依赖兄弟仓库的 harvester.db，每改一次 view
 *     渲染都该跑（原为 docs/reports 下的一次性脚本，v0.33 移进本仓库）。
 *
 * 用法：node tests/render_real.js [payload.json] [index.html]
 *   载荷由同目录 check_real_payload.py 生成（它负责真库取数与选主题）。
 *
 * ⚠ 断言一律**从载荷推导**，不写死数字：写死"324 成员 / 50 节点 / 2 条链"这类
 *   真实数字，数据一变这个门就**假红**（它当初是一次性脚本，那时写死是合理的；
 *   变成每轮都跑的门就必须自洽）。
 *   "载荷里根本没有这种主题"（如一条链都没有）时输出 `n/a` 并**单列计数**，
 *   不混进 ok（项目 AGENTS.md §五 16：未执行/不适用要能分开看）。
 */
const fs = require("fs");
const path = require("path");

const HERE = __dirname;
const payloadPath = process.argv[2] || path.join(HERE, "_tmp", "view-payload.json");
const htmlPath = process.argv[3] || path.join(HERE, "..", "static", "index.html");

const html = fs.readFileSync(htmlPath, "utf8");
const P = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

let ok = 0, na = 0, fails = 0;
function check(name, cond) {
  console.log((cond ? "ok  - " : "FAIL- ") + name);
  cond ? ok++ : fails++;
}
function note(name, why) {
  console.log("n/a - " + name + "（" + why + "）");
  na++;
}

function extractFn(name, sig) {
  const re = new RegExp("^(?:async )?function " + name + "\\(" + sig +
    "\\)\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name + "(" + sig + ")");
  return m[0];
}

/* 渲染函数依赖的桩：store/$/esc 与 index.html 里的全局一致。
   P4-5 起 renderChain 还读 state.skJoin/skJoinQuery（挂轨道）——门里给
   null 桩（未挂轨道形态）；挂轨道形态由 render_skilljoin.js 门专管。 */
const store = { "topic-doc": { innerHTML: "" }, "rep-meta": { textContent: "" } };
function mkOut() { return { innerHTML: "", querySelectorAll: () => [] }; }
const $ = id => store[id];
const esc = s => String(s == null ? "" : s);
const state = { skJoin: null, skJoinQuery: null, chainIdx: 0,
                topicCur: null, anchorRun: 0 };
eval(extractFn("kwText", "k"));
eval(extractFn("renderTopics", "d,out"));
eval(extractFn("renderChain", "d,out,idx"));
eval(extractFn("bindChainSwitch", "root,d"));
eval(extractFn("bindTopicNodes", "root"));
eval(extractFn("renderErrors", "d,out"));

const topics = (P.topics && P.topics.topics) || [];
const kwOf = t => {
  const k = t.keywords;
  if (Array.isArray(k)) return k;
  try { return JSON.parse(k || "[]"); } catch (e) { return []; }
};
const stageOf = c => (c.fm && c.fm.anchors && c.fm.anchors[0] &&
  c.fm.anchors[0].stage) || "";
const nodeCountOf = c => ((c.fm && c.fm.anchors) || [])
  .reduce((n, a) => n + ((a.nodes || []).length), 0);

/* ① 主题列表：真实 /api/topics 载荷 */
const tOut = { innerHTML: "" };
renderTopics(P.topics, tOut);
check("主题行数 = 载荷主题数 " + topics.length,
  (tOut.innerHTML.match(/data-tid=/g) || []).length === topics.length);
check("每个主题都带 chains_count 字段",
  topics.every(t => typeof t.chains_count === "number"));
check("主题列表不渲染零散会话字段",
  !tOut.innerHTML.includes("sessions_noise"));

const withChain = topics.filter(t => (t.chains_count || 0) > 0);
if (withChain.length === 0) {
  note("链列渲染", "载荷里没有带链的主题");
} else {
  check("链列有表头", tOut.innerHTML.includes(">链<"));
  const names = withChain[0].chain_names || [];
  check("带链主题的链名出现在列表里（" + withChain.length + " 个带链）",
    names.length === 0 || names.some(n => tOut.innerHTML.includes(n)));
}

const zero = topics.filter(t => t.members_count === 0);
if (zero.length === 0) {
  note("0 成员类目灰显", "载荷里没有 0 成员类目");
} else {
  check("0 成员类目灰显（" + zero.length + " 个）",
    tOut.innerHTML.includes("opacity:.55"));
}

const withKw = topics.filter(t => kwOf(t).length > 0);
if (withKw.length === 0) {
  note("关键词人读形态", "载荷里没有带关键词的主题");
} else {
  const k0 = kwOf(withKw[0])[0];
  check("关键词渲染成人读形态（首个关键词「" + k0 + "」可见）",
    tOut.innerHTML.includes(k0));
}

/* ② 多链主题：真实 /api/topic/<id>/chain 载荷（Python 侧选链最多的主题） */
const mc = P.multichain || {};
const chains = mc.chains || [];
check("多链载荷 chain_count 与 chains[] 一致",
  Array.isArray(mc.chains) && mc.chain_count === chains.length);
if (chains.length < 2) {
  note("切换条与换链断言",
    "载荷主题只有 " + chains.length + " 条链（多链行为无从验证）");
} else {
  const mOut = mkOut();   /* P4-5 起 renderChain 末尾绑 sk 按钮，需 querySelectorAll */
  renderChain(mc, mOut, 0);
  check("切换条按钮数 = chain_count（" + chains.length + "）",
    (mOut.innerHTML.match(/data-chain-idx=/g) || []).length === chains.length);

  const s0 = stageOf(chains[0]), s1 = stageOf(chains[1]);
  if (s0 && s1 && s0 !== s1) {
    check("渲染第 1 条时时间线是它自己的阶段（" + s0 + "）",
      mOut.innerHTML.includes(s0) && !mOut.innerHTML.includes(s1));
  } else {
    note("阶段互斥断言", "两条链的首阶段文本相同或为空");
  }

  store["topic-doc"] = { innerHTML: "" };
  renderChain(mc, mOut, 1);
  const n1 = nodeCountOf(chains[1]);
  check("第 2 条锚点节点数 = 载荷推导值（" + n1 + "）",
    (mOut.innerHTML.match(/data-sid=/g) || []).length === n1);
  const nameOf = c => c.name || "";
  if (nameOf(chains[1])) {
    check("换链后右栏正文真的换了（" + nameOf(chains[1]) + "）",
      store["topic-doc"].innerHTML.includes(nameOf(chains[1])) &&
      (!nameOf(chains[0]) || !store["topic-doc"].innerHTML.includes(nameOf(chains[0]))));
  } else {
    note("右栏换链断言", "载荷里的链没有 name（无法判断是否换了）");
  }
}

/* ③ 真实 /api/reports/errors 载荷（v0.34 P1）：chips 渲染 + 对账断言 */
const ER = P.errors || {};
const bc = ER.by_class || {};
const bcSum = Object.values(bc).reduce((a, b) => a + b, 0);
const pats = ER.patterns || [];
const patSum = pats.reduce((a, x) => a + x.count, 0);
/* 桩：renderErrors 依赖 state.errChip 与 patternPlain/clsLabel（冒烟同款） */
const stateStub = { errChip: "" };
const clsLabel = c => ({ env: "环境类", tool_interface: "用法类",
  context: "上下文类" }[c] || c);
const patternPlain = () => "";
const repFilter = () => ({ q: "", only: false });
const hitQ = (t, q) => !q || String(t || "").toLowerCase().includes(q);
const anchor = (sid, seq, text) => "<a>" + sid + "#" + seq + "</a>";
if (bcSum === 0 || pats.length === 0) {
  note("errors chips 对账", "真库窗口内无错误（无数据可断言）");
} else {
  const erOut = mkOut();
  renderErrors(ER, erOut);
  check("chips 行数 = 类别数 + 全部（真库 " + Object.keys(bc).length + " 类）",
    (erOut.innerHTML.match(/data-errchip=/g) || []).length ===
    Object.keys(bc).length + 1);
  check("对账①：chips「全部」数字 = by_class 求和 = " + bcSum,
    erOut.innerHTML.includes("全部 " + bcSum));
  /* P1-5 验收口径：模式明细条数求和 ≡ by_class 求和（同库同源） */
  check("对账②：patterns 求和 " + patSum + " = by_class 求和 " + bcSum,
    patSum === bcSum);
  /* 对账③：渲染总数必须等于**库指纹**里的错误数（同库、**两条独立算路**）。
     历史写法把库快照写死在这里（2026-10-12=381 / 2026-10-14=362）——
     每次刷库必假红一次：2026-10-22 刷库后真库变 532，这条就红了。
     改为取 `db_fingerprint.errors`（`dbmeta` 从库直接计数，与
     `by_class` 的聚合是**两条独立算路**），故这条**仍然会红**
     （口径漂移 / 聚合漏类 / 指纹与报告不同源都能抓到），
     但不再把"库快照"这种会变的东西钉进测试。 */
  const fpTotal = (ER.db_fingerprint || {}).errors;
  if (typeof fpTotal === "number") {
    check("对账③：by_class 求和 " + bcSum + " = 库指纹 errors " + fpTotal,
      bcSum === fpTotal);
  } else {
    note("对账③", "载荷无 db_fingerprint.errors（无法与库计数交叉核对）");
  }

  /* ── P6-5 归因进报告页：渲染门 ────────────────────────────────────
     P6-5 的验收是「每个模式行带 why/fix + 置信；无归因的行显式标未归因」。
     这里三条**会红**的检查（不是只看字符串存不存在那种）：
       ④ 渲染出的「未归因」格数 = 载荷里被渲染那批（表只渲染前 60 行）
          中 attrib 为 null 的模式数 —— 逐条对账，少一格多一格都红；
       ⑤ 渲染出的规则号**必须全部来自载荷** —— 防止页面自己编规则；
       ⑥ 至少一条行同时有「根因」与「应对」 —— 这是 P6-5 的门禁原文。 */
  if (ER.attrib_status !== "ok") {
    note("P6-5 归因渲染", "载荷 attrib_status=" + ER.attrib_status +
      "（归因层不可用，本条不适用；页面此时应显示「不含归因」）");
    /* 不可用时页面必须明说，而不是静默不出列 */
    check("P6-5 不可用时页面明说「不含归因」",
      erOut.innerHTML.includes("本报告不含归因"));
  } else {
    /* 表只渲染前 60 行（按 count 降序，与页面同序） */
    const shown = [...pats].sort((a, b) => b.count - a.count).slice(0, 60);
    const expectUn = shown.filter(x => !x.attrib).length;
    const expectAtt = shown.length - expectUn;
    const gotUn = (erOut.innerHTML.match(/>未归因</g) || []).length;
    const ruleIds = [...new Set(shown.filter(x => x.attrib)
      .map(x => x.attrib.rule_id))];
    const gotIds = [...new Set(
      [...erOut.innerHTML.matchAll(/class='pill known'[^>]*>([^<]+)</g)]
        .map(m => m[1]))];
    const invented = gotIds.filter(id => ruleIds.indexOf(id) < 0);
    check("对账④：渲染「未归因」格 " + gotUn + " = 载荷未归因模式 " + expectUn +
      "（渲染 " + shown.length + " 行）", gotUn === expectUn);
    check("对账⑤：页面出现的规则号全来自载荷（未编造）" +
      (invented.length ? "：多出 " + invented.join(",") : ""),
      invented.length === 0 && gotIds.length === ruleIds.length);
    check("P6-5 门禁：≥1 行同时带「根因」与「应对」",
      erOut.innerHTML.includes("<b>根因</b>") &&
      erOut.innerHTML.includes("<b>应对</b>") && expectAtt > 0);
  }

  /* P6-7 可评审分级：页面必须**如实显示载荷给的等级**，且 R1 及以下
     不许出现裸的「可评审」。两条都会红：等级被页面改高、或没显示等级。 */
  const rv = ER.reviewable;
  if (!rv || !rv.level) {
    note("P6-7 可评审分级", "载荷无 reviewable（旧上游），页面不显示等级，本条不适用");
    check("P6-7 无 reviewable 时页面不编造等级",
      !/本面等级/.test(erOut.innerHTML));
  } else {
    const shown = erOut.innerHTML;
    check("P6-7 页面显示载荷给的等级 R=" + rv.level,
      shown.includes("本面等级 " + rv.level));
    const claimsRev = /(?<!不)可评审/.test(shown);
    check("P6-7 等级与「可评审」口径一致（can_review=" + !!rv.can_review + "）",
      claimsRev === !!rv.can_review);
  }
}

console.log("\nok " + ok + " / n/a " + na + " / FAIL " + fails);
console.log(fails ? ("真实载荷渲染检查失败 " + fails + " 项")
                  : "ALL REAL RENDER CHECKS PASSED" +
                    (na ? "（另有 " + na + " 项不适用）" : ""));
process.exit(fails ? 1 : 0);
