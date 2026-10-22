/* 端到端等价验证：把**当前 8765 正在返回的真实 JSON** 喂给**当前 8088 正在
 * 服务的真实 index.html** 里的 renderErrors，看"浏览器此刻会看到什么"。
 *
 * 与 render_real.js 的区别：那个用 DB 现算的载荷（不经过 HTTP 与页面服务），
 * 这个走 **HTTP 实拉**——专门用来回答"我改了页面，为什么打开还是原样"：
 * 如果这里能渲染出「根因 / 应对」列，那服务端与页面代码就是对的，
 * 看不到只可能是浏览器用了旧标签页/缓存。
 *
 * 用法：node tests/live_render_probe.js [apiBase] [pageBase]
 */
const http = require("http");

const API = process.argv[2] || "http://127.0.0.1:8765";
const PAGE = process.argv[3] || "http://127.0.0.1:8088";

function get(url) {
  return new Promise((res, rej) => {
    http.get(url, r => {
      let b = "";
      r.setEncoding("utf8");
      r.on("data", c => (b += c));
      r.on("end", () => res({ status: r.statusCode, body: b }));
    }).on("error", rej);
  });
}

let ok = 0, fails = 0;
function check(name, cond) {
  console.log((cond ? "ok  - " : "FAIL- ") + name);
  cond ? ok++ : fails++;
}

(async () => {
  const page = await get(PAGE + "/");
  check("页面服务可达且返回 200", page.status === 200);
  check("页面里含「根因 / 应对」表头", page.body.includes("根因 / 应对</th>"));
  check("页面里含「本报告不含归因」分支",
    page.body.includes("本报告不含归因"));

  /* 窗口用 30 天（= 页面 `rep-days` 的默认值）。
     ⚠ 不要手写 `days=0`：页面在窗口为 0 时**省略 days 参数**（后端据此判为全库），
     而裸 API 的 `days=0` 会被算成「截止到此刻」→ 返回**空报告**。
     这两者不是同一件事，探针若手写 days=0 会得到"渲染 0 行"的假结论。 */
  const api = await get(API + "/api/reports/errors?days=30");
  check("API 可达且返回 200", api.status === 200);
  const D = JSON.parse(api.body);
  check("API 载荷 attrib_status = ok", D.attrib_status === "ok");
  check("API 载荷带 attrib_summary 三计数",
    D.attrib_summary && typeof D.attrib_summary.patterns_total === "number");

  /* 从**正在服务的页面**里取渲染函数（不是从磁盘读，避免"改的是磁盘、
     服务的是旧文件"这种混淆） */
  const html = page.body;
  const re = /^(?:async )?function renderErrors\(d,out\)\{[\s\S]*?^\}/m;
  const m = html.match(re);
  if (!m) {
    console.log("FAIL- 从服务中的页面里提取不到 renderErrors");
    process.exit(1);
  }
  const esc = s => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  const $ = () => ({ textContent: "" });
  const state = { errChip: "" };
  const repFilter = () => ({ q: "" });
  const hitQ = () => true;
  const clsLabel = x => x;
  const patternPlain = () => "";
  const anchor = () => "<a></a>";
  const bindPatternDrill = () => {};
  const showPatternSessions = () => {};
  const glossary = { loaded: false };
  const applyTermTooltips = () => {};
  const bindAnchors = () => {};
  const rerenderReport = () => {};
  const switchTab = () => {};

  const out = { innerHTML: "", querySelectorAll: () => [] };
  // eslint-disable-next-line no-eval
  eval(m[0]);
  renderErrors(D, out);
  const H = out.innerHTML;

  const shown = [...(D.patterns || [])].sort((a, b) => b.count - a.count)
    .slice(0, 60);
  const expectUn = shown.filter(x => !x.attrib).length;
  const gotUn = (H.match(/>未归因</g) || []).length;
  check("渲染出「未归因」格 " + gotUn + " = 载荷未归因 " + expectUn +
    "（渲染 " + shown.length + " 行）", gotUn === expectUn);
  check("渲染出「根因」与「应对」字样",
    H.includes("<b>根因</b>") && H.includes("<b>应对</b>"));
  check("渲染出规则号 pill（来自载荷）", H.includes("pill known"));
  check("未归因文案写明「不是「没问题」」", H.includes("不是「没问题」"));
  /* 对账口径必须在**同一窗口**内比：30 天窗口的 by_class 求和 == meta.error_count。
     不能拿全库的 db_fingerprint.errors 比——那是另一个窗口的数，比了必假红。
     （render_real.js 的"对账③"用 db_fingerprint 是对的，因为它的载荷就是全库。） */
  check("三分类表数字仍是事实层口径（by_class 求和 = 同窗口 meta.error_count）",
    Object.values(D.by_class || {}).reduce((a, b) => a + b, 0) ===
    (D.meta || {}).error_count);

  console.log("\n" + (fails ? "FAIL" : "ok") + " " + ok + " / FAIL " + fails);
  process.exit(fails ? 1 : 0);
})().catch(e => { console.log("探针异常：" + e.message); process.exit(1); });