/* 报告读取面渲染门（node 运行，退出码 0=全过）。
 *
 * 覆盖 P5 后续两件事（用户 2026-10-17 批准「做完挂账的」）：
 *   ① 「昨日报告」卡截断时的「查看全文」懒加载；
 *   ② 报告页「报告索引」区（/api/reports-index 列目录 + 看全文）。
 *
 * 纪律与 render_home 同源：「只渲染服务端给的内容」——文件名/全文/hint
 * 必须 esc 原样展示（页面不解析 md、不二次聚合）；恶意文件名、超长全文
 * 都是 esc 的试金石。空态（目录不存在/没有 .md）照实说，不装数据。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

function extract(name, sig) {
  const re = new RegExp("^(?:async )?function " + name + "\\(" + sig +
    "\\)\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name + "(" + sig + ")");
  return m[0];
}

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));

/* 元素桩：支持 querySelector（按选择器惰性建子桩）与预注册的
   querySelectorAll 结果（渲染门要在渲染后手动驱动 onclick）。 */
function mkEl(id) {
  const el = { id, innerHTML: "", textContent: "", style: {}, disabled: false,
    dataset: {}, className: "", _q: {}, _qa: {},
    querySelector(sel) {
      if (!el._q[sel]) el._q[sel] = mkEl(id + ">" + sel);
      return el._q[sel];
    },
    querySelectorAll(sel) { return el._qa[sel] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }

/* api 桩：apiNext 队列逐个吐响应；调用本身进 apiCalls 供对账 */
const apiCalls = [];
let apiNext = [];
async function api(url) {
  apiCalls.push(url);
  if (!apiNext.length) throw new Error("api 桩没有下一个响应：" + url);
  const d = apiNext.shift();
  if (d instanceof Error) throw d;
  return d;
}
const banners = [];
function showBanner(k, m) { banners.push([k, m]); }
function errorBox(m) { return "<div class='empty'>" + esc(m) + "</div>"; }
const glossary = { loaded: false };
function applyTermTooltips() {}

for (const [n, sig] of [
  ["loadDailyCard", ""], ["loadReportsIndex", ""],
  ["fetchReportFile", "name"],
]) {
  eval(extract(n, sig));
}

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

(async () => {
  /* ① 昨日卡：截断 → 有「查看全文」；点击 → report-get → 全文进 pre */
  const dc = $("home-daily");
  apiNext = [{ found: true, date: "2026-10-16", n_chars: 9000,
    md_head: "# 头部", path: "X:\\docs\\reports\\daily-2026-10-16.md" }];
  await loadDailyCard();
  check("昨日卡渲染头部（esc）", dc.innerHTML.includes(esc("# 头部")));
  check("截断时出现「查看全文」", dc.innerHTML.includes("查看全文"));
  check("全文按钮带白名单文件名 daily-<date>.md",
    dc.innerHTML.includes("data-dfull=") &&
    dc.innerHTML.includes(esc("daily-2026-10-16.md")),
    "name 必须取服务端白名单口径，前端不得自己拼路径");
  const fb = dc.querySelector("[data-dfull]");
  fb.dataset.dfull = "daily-2026-10-16.md"; /* 桩不解析 HTML，浏览器 DOM 会
    把 data-dfull 反序列化进 dataset——这里手动补上等价状态 */
  apiNext = [{ found: true, name: "daily-2026-10-16.md", n_chars: 9000,
    md: "# 头部\n全文正文" }];
  await fb.onclick();
  check("全文走 /api/report-get（name 白名单原样）",
    apiCalls.includes("/api/report-get?name=" +
      encodeURIComponent("daily-2026-10-16.md")));
  check("全文进 pre 且原样（textContent 不解析）",
    dc.querySelector("pre").textContent.includes("全文正文"));
  check("载入后按钮禁用（防重复拉取）", fb.disabled === true);

  /* ② 未截断 → 不出全文按钮（多余按钮=撒谎） */
  apiNext = [{ found: true, date: "2026-10-16", n_chars: 4,
    md_head: "# 短文", path: "p" }];
  await loadDailyCard();
  check("未截断不出「查看全文」",
    !$("home-daily").innerHTML.includes("查看全文"));

  /* ③ 诚实空态：目录不存在照实说，不装数据 */
  apiNext = [{ found: false, hint: "日报目录不存在" }];
  await loadDailyCard();
  const h3 = $("home-daily").innerHTML;
  check("空态照实说（hint esc 展示）",
    h3.includes("还没有") && h3.includes(esc("日报目录不存在")));
  check("空态不出「查看全文」", !h3.includes("查看全文"));

  /* ④ 报告索引：列文件 + esc 试金石（恶意文件名） */
  const idx = $("rep-index");
  const fbody = mkEl("fbody");
  const fbtn = mkEl("fbtn");
  fbtn.parentElement = { querySelector: s => s === ".rep-fbody" ? fbody : null };
  idx._qa["[data-rfull]"] = [fbtn];
  apiNext = [{ found: true, dir: "D:\\r", count: 2, files: [
    { name: "daily-2026-10-16.md", date: "2026-10-16", bytes: 9000,
      mtime: "2026-10-16 23:00:00" },
    { name: "<img>.md", date: null, bytes: 5, mtime: "2026-10-15 10:00:00" },
  ] }];
  await loadReportsIndex();
  const ih = idx.innerHTML;
  check("索引列出文件名", ih.includes(esc("daily-2026-10-16.md")));
  check("恶意文件名被 esc（不解析）",
    ih.includes(esc("<img>.md")) && !ih.includes("<img>.md"));
  check("日报抽出的日期展示", ih.includes("2026-10-16"));
  check("目录路径照实展示", ih.includes(esc("D:\\r")));
  check("每份文件都有「看全文」",
    (ih.match(/data-rfull=/g) || []).length === 2);

  /* ⑤ 看全文：走 report-get；二次点击不再拉取（loaded 守卫） */
  fbtn.dataset.rfull = "daily-2026-10-16.md"; /* 同上：桩补 dataset */
  apiNext = [{ found: true, name: "daily-2026-10-16.md", md: "全文字" }];
  const n0 = apiCalls.filter(u => u.startsWith("/api/report-get")).length;
  await fbtn.onclick();
  const n1 = apiCalls.filter(u => u.startsWith("/api/report-get")).length;
  check("看全文走 /api/report-get", n1 === n0 + 1);
  check("全文渲染进行内 pre（esc）", fbody.innerHTML.includes(esc("全文字")));
  await fbtn.onclick();
  check("重复点击不再拉取（loaded 守卫）",
    apiCalls.filter(u => u.startsWith("/api/report-get")).length === n1,
    "没守卫=每次点都打一遍服务端，且 apiNext 空了会露馅");

  /* ⑥ 索引诚实空态：目录不存在 / 目录里没有 .md */
  apiNext = [{ found: false, hint: "报告目录不存在" }];
  await loadReportsIndex();
  check("目录不存在照实说",
    idx.innerHTML.includes(esc("报告目录不存在")));
  apiNext = [{ found: true, dir: "D:\\r", count: 0, files: [] }];
  await loadReportsIndex();
  check("空目录照实说",
    idx.innerHTML.includes("还没有 .md 文件") && idx.innerHTML.includes(esc("D:\\r")));

  /* ⑦ 静态接线：按钮/挂点存在且已绑定；页签首进即拉索引 */
  check("报告页有索引按钮与挂点",
    html.includes('id="btn-rep-index"') && html.includes('id="rep-index"'));
  check("索引按钮已接线",
    /btn-rep-index"\)\.onclick=\(\)=>loadReportsIndex\(\)/.test(html));
  check("报告页签首次进入即拉索引",
    /name==="reports"\)\{ loadReport\(\)\.catch\(\(\)=>\{\}\); ?loadReportsIndex\(\)\.catch\(\(\)=>\{\}\); \}/
      .test(html));
  check("fetchReportFile 对 name 做 encodeURIComponent",
    /encodeURIComponent\(name\)/.test(extract("fetchReportFile", "name")));

  console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
    "\nALL REPORTS-INDEX CHECKS PASSED");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("GATE ERROR:", e); process.exit(1); });
