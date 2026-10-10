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

/* 渲染函数依赖的桩：store/$/esc 与 index.html 里的全局一致 */
const store = { "topic-doc": { innerHTML: "" } };
const $ = id => store[id];
const esc = s => String(s == null ? "" : s);
eval(extractFn("kwText", "k"));
eval(extractFn("renderTopics", "d,out"));
eval(extractFn("renderChain", "d,out,idx"));
eval(extractFn("bindChainSwitch", "root,d"));
eval(extractFn("bindTopicNodes", "root"));

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
  const mOut = { innerHTML: "" };
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

console.log("\nok " + ok + " / n/a " + na + " / FAIL " + fails);
console.log(fails ? ("真实载荷渲染检查失败 " + fails + " 项")
                  : "ALL REAL RENDER CHECKS PASSED" +
                    (na ? "（另有 " + na + " 项不适用）" : ""));
process.exit(fails ? 1 : 0);
