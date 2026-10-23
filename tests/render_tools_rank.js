/* 渲染门（2026-10-23，用户批准的第①步）：工具表必须分成「排行 / 样本不足」两段。
 *
 * 钉住四件事（改回去就会红）：
 *   1. 样本不足段（ranked=false）**排在**排行段之后，且包在 <details> 里；
 *   2. 1 次调用 1 次失败的工具**不在排行段里**（真库曾是榜首）；
 *   3. 排行段里按后端给的顺序（页面不重排：排序口径只有 tool_rows 一处）；
 *   4. 每个工具**只渲染一次**（不重复、不丢行）；旧载荷无 ranked 字段时降级为全排行。
 *
 * 断言一律**先切出目标分片再查**（AGENTS.md §三 15：整段 innerHTML 查找会被
 * 别处的载荷回声蒙混过关）。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "static", "index.html"), "utf8");

function extract(name) {
  // 顶格 "function name(d,out){" 到下一个顶格 "}"（函数体内部闭括号均有缩进）
  const m = html.match(new RegExp("^function " + name + "\\(d,out\\)\\{[\\s\\S]*?^\\}", "m"));
  if (!m) throw new Error("extract fail: " + name);
  return m[0];
}

let store;
function fresh() { store = { "rep-meta": { textContent: "" } }; }
const $ = id => store[id];
const esc = s => String(s == null ? "" : s);
const mkOut = () => ({ innerHTML: "", querySelectorAll: () => [] });
const repFilter = () => ({ q: "", only: false });
const hitQ = () => true;
const anchor = (sid, seq) => "<a>" + sid + "#" + seq + "</a>";
const clsLabel = c => c;
const patternPlain = () => "";

eval(extract("renderTools"));

let fail = 0;
const ok = (cond, msg) => {
  if (cond) { console.log("ok - " + msg); } else { fail++; console.log("FAIL - " + msg); }
};

function row(tool, calls, error, ranked) {
  return { tool, calls, success: calls - error, error,
           fail_rate: calls ? error / calls : 0, given_up: 0, retried: 0,
           raw_tools: [tool], low_sample: false, ranked, errors: [] };
}

function render(data) {
  fresh();
  const out = mkOut();
  renderTools(data, out);
  return out.innerHTML;
}

/* 分片：排行段 = 第一个 <h3> 到「样本不足」details 之前；样本段 = 那个 details 整体 */
function split(htmlOut) {
  const detStart = htmlOut.indexOf("<details><summary class='sub2'>样本不足");
  const head = htmlOut.slice(0, detStart < 0 ? htmlOut.length : detStart);
  if (detStart < 0) return { head, small: "" };
  const detEnd = htmlOut.indexOf("</details>", detStart);
  return { head, small: htmlOut.slice(detStart, detEnd + "</details>".length) };
}

/* ---------- 真库形状：275 次失败的 Edit vs 1 次调用的 memory_source ---------- */
const DATA = {
  rank_min_calls: 20,
  flow: null,
  by_source: {},
  tools: [
    row("Edit", 6505, 275, true),
    row("web_fetch", 286, 52, true),
    row("skill_run_asset", 2, 2, false),
    row("memory_source", 1, 1, false),
  ],
};
const h1 = render(DATA);
const s1 = split(h1);

ok(s1.head.indexOf("Edit") >= 0 && s1.head.indexOf("web_fetch") >= 0,
   "排行段含样本充足的工具");
ok(s1.head.indexOf("Edit") < s1.head.indexOf("web_fetch"),
   "排行段保持后端顺序（Edit 275 次失败在 web_fetch 52 次之前）");
ok(s1.head.indexOf("memory_source") < 0,
   "1 次调用 1 次失败**不在排行段**（真库曾霸榜）");
ok(s1.small.indexOf("memory_source") >= 0 && s1.small.indexOf("skill_run_asset") >= 0,
   "样本不足的两个工具都在 details 段里（可见、不静默丢弃）");
ok(h1.indexOf("样本 ≥20 次调用") >= 0,
   "标题写明排行门槛（20 次调用）");
ok(/样本不足（2 个工具/.test(s1.small),
   "样本段摘要给出数量（2 个工具）");
ok((h1.match(/memory_source/g) || []).length === 1,
   "memory_source 只渲染一次（不重复）");
ok((h1.match(/Edit/g) || []).length === 1, "Edit 只渲染一次（不重复）");

/* ---------- 旧载荷兼容：没有 ranked / rank_min_calls ---------- */
const OLD = {
  flow: null, by_source: {},
  tools: [{ tool: "Edit", calls: 20, success: 13, error: 7, fail_rate: 0.35,
            given_up: 2, retried: 1, raw_tools: ["Edit"], errors: [] }],
};
const h2 = render(OLD);
ok(h2.indexOf("样本不足") < 0, "旧载荷（无 ranked）不造出样本不足段");
ok(h2.indexOf("Edit") >= 0, "旧载荷的工具照常渲染");
ok(h2.indexOf("20 次调用") >= 0, "缺 rank_min_calls 时用默认 20 写进标题");

/* ---------- 全部样本不足：排行段空态要说明白，而不是空白 ---------- */
const ALL_SMALL = {
  rank_min_calls: 20, flow: null, by_source: {},
  tools: [row("a", 2, 1, false), row("b", 1, 1, false)],
};
const h3 = render(ALL_SMALL);
const s3 = split(h3);
ok(/样本充足的工具里没有失败记录/.test(s3.head),
   "排行段空时给空态文案（不是一片空白）");
ok(/样本不足（2 个工具/.test(s3.small), "两个工具都进样本段");

console.log(fail ? ("FAILED: " + fail) : "ALL TOOL-RANK RENDER CHECKS PASSED");
process.exit(fail ? 1 : 0);