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

if (failed) { console.error(failed + " check(s) failed"); process.exit(1); }
console.log("ALL RENDER CHECKS PASSED");
