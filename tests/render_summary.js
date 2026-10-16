/* v0.76 V5 渲染门：renderCards 认 summary（evidence/summary 拆角色双栏）。
 *
 * 主仓 v0.74 拆角色后综述在 frontmatter summary；view 不认 → 综述丢失、
 * insight 卡证据栏空白。本门从 index.html 提取 renderCards + esc 源文本，
 * 注入最小 DOM stub 后真实执行，断言：
 *  1. insight 卡（summary-only）：综述顶 C 位（紧跟结论），无证据块；
 *  2. pitfall 卡（evidence+summary）：证据在前、综述在后，两块都在；
 *  3. 非 insight summary-only：综述单独成块；
 *  4. 旧卡（evidence-only）：原样渲染，无综述块（additive 不打扰旧数据）；
 *  5. esc 注入守卫：summary 含 <script> 必须被转义；
 *  6. 节标题更新为「证据·综述」。
 */
const fs = require("fs");
const path = require("path");

const htmlPath = path.join(__dirname, "..", "static", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");

function extract(name, sig) {
  const s = sig || "d,out";
  const re = new RegExp("^function " + name + "\\(" + s + "\\)\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name);
  return m[0];
}

/* 注意：不得声明 "use strict"——eval 内函数声明需外泄 */
let store = {};
const $ = id => store[id];
function mkOut() { return { innerHTML: "", querySelectorAll: () => [] }; }
const repFilter = () => ({ q: "", only: false });
const hitQ = () => true;
/* esc 桩：与页面 esc（index.html:665）同语义五字符转义。
   不直接 extract——esc 闭括号在行中，^} 锚点会吞到远处的顶层函数。
   页面 esc 的转义集由下方源码级断言钉住。 */
const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));
const shortSid = sid => String(sid || "").slice(0, 12);
const bindCardAnchors = () => {};
function pTag(){return "";}
const state = { cardsBodies: [] };
eval(extract("renderCards"));

let failed = 0;
function check(label, cond) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label); }
}

const bodies = {
  insight: { path: "i.md", parsed: true, id: "kc-i", title: "综述型卡",
    type: "insight", confidence: 0.7, evidence: "",
    summary: "机器核它核得了的，语义裁决还给人",
    conclusion: "结论 i", anchors: [{ sid: "dsh:i", turn: 1 }] },
  both: { path: "b.md", parsed: true, id: "kc-b", title: "双字段卡",
    type: "pitfall", confidence: 0.9, evidence: "Error: old_string not found",
    summary: "先 Read 再 Edit", conclusion: "结论 b",
    anchors: [{ sid: "wc:b", turn: 2 }] },
  sumonly: { path: "s.md", parsed: true, id: "kc-s", title: "非insight综述卡",
    type: "workflow", confidence: 0.6, evidence: "",
    summary: "综述 s", conclusion: "结论 s",
    anchors: [{ sid: "wc:s", turn: 3 }] },
  legacy: { path: "l.md", parsed: true, id: "kc-l", title: "旧卡",
    type: "pitfall", confidence: 0.5, evidence: "旧证据",
    summary: "", conclusion: "旧结论", anchors: [] },
  xss: { path: "x.md", parsed: true, id: "kc-x", title: "注入尝试卡",
    type: "insight", confidence: 0.5, evidence: "",
    summary: "<script>alert(1)</script>", conclusion: "结论 x", anchors: [] },
  /* v0.76 V6：带警告卡——断言警告行 [接受] 与本体 [接受整卡] 按钮 */
  warncard: { path: "w.md", parsed: true, id: "kc-w", title: "带警告卡",
    type: "pitfall", confidence: 0.5, evidence: "证据 w",
    summary: "", conclusion: "结论 w", anchors: [],
    warnings: ["警告 WA：锚点未命中索引库（dsh:gone/x.v4.jsonl.zstd#turn:1）"] },
};
function run(keys) {
  state.cardsBodies = keys.map(k => bodies[k]);
  const d = { root: "/cards", summary: { cards: keys.length, ok: keys.length,
    warn: 0, error: 0, anchor_checked: 0, anchor_misses: 0 },
    cards: keys.map(k => ({ path: bodies[k].path,
      errors: bodies[k].errors || [], warnings: bodies[k].warnings || [] })) };
  store = { "rep-meta": { innerHTML: "", textContent: "" } };
  const out = mkOut();
  renderCards(d, out);
  return out.innerHTML;
}

/* 1. insight summary-only：综述顶 C 位（结论之后、无证据块） */
const hi = run(["insight"]);
check("insight 综述渲染（C 位块 c-sum）",
  hi.includes("机器核它核得了的") && hi.includes("c-sum"));
check("insight 综述紧跟结论（结论在综述前）",
  hi.indexOf("结论 i") < hi.indexOf("机器核它核得了的"));
check("insight 无逐字证据块（summary-only 不装作有证据）",
  !hi.includes("c-ev"));
check("综述块带「综述」标注", hi.includes("综述"));

/* 2. pitfall evidence+summary：证据在前、综述在后 */
const hb = run(["both"]);
check("双字段卡两块都渲染",
  hb.includes("Error: old_string not found") && hb.includes("先 Read 再 Edit"));
check("双字段卡顺序：逐字证据（c-ev）在前、综述（c-sum）在后",
  hb.indexOf("c-ev") < hb.indexOf("c-sum") &&
  hb.indexOf("old_string") < hb.indexOf("先 Read 再 Edit"));

/* 3. 非 insight summary-only */
const hs = run(["sumonly"]);
check("非 insight 综述单独成块",
  hs.includes("综述 s") && hs.includes("c-sum") && !hs.includes("c-ev"));

/* 4. 旧卡 additive 不打扰 */
const hl = run(["legacy"]);
check("旧卡（evidence-only）原样渲染、无综述块",
  hl.includes("旧证据") && hl.includes("c-ev") && !hl.includes("c-sum"));

/* 5. 注入守卫：esc 真实转义 */
const hx = run(["xss"]);
check("summary 注入被转义（无裸 <script>）",
  !hx.includes("<script>alert(1)</script>") &&
  hx.includes("&lt;script&gt;"));
/* 页面 esc 源码级钉住：五字符转义表必须原样在位（桩与页面同语义的前提） */
check("页面 esc 五字符转义表在位",
  html.includes('"&":"&amp;"') && html.includes('"<":"&lt;"') &&
  html.includes('">":"&gt;"') && html.includes('"&quot;"') &&
  html.includes("&#39;"));

/* 6. 节标题 */
check("本体节标题含「证据·综述」",
  run(["insight"]).indexOf("证据·综述") > -1);

/* 7. V6 直给按钮：警告行 [接受] + 本体 [接受整卡]（属性用单引号） */
const hw = run(["warncard"]);
check("警告行带 data-cracc 接受按钮（完整警告文本）",
  hw.includes("data-cracc='警告 WA：锚点未命中索引库") &&
  hw.includes("data-crfile='w.md'"));
check("本体带整卡一键（__all__）",
  hw.includes("data-cracc='__all__'") && hw.includes("接受整卡"));
check("直给按钮恰好 2 个（行级 + 整卡，无重复注入）",
  hw.split("data-cracc=").length - 1 === 2);

/* 8. V6 直给守卫（2026-10-16 真机实测踩中）：注册表懒加载必须先于
   actionSpec 查询——刷新后直奔报告页点 [接受] 时 state.act 尚未拉取，
   先查必空、直给必早退（渲染门盖不住加载顺序，用源码序断言钉住）。 */
const bd = extract("bindCardDirect", "root");
const iLoad = bd.indexOf("loadCmdData()");
const iSpec = bd.indexOf('actionSpec("card_review")');
check("bindCardDirect 懒加载注册表先于 actionSpec 查询",
  iLoad > -1 && iSpec > -1 && iLoad < iSpec);
check("bindCardDirect 懒加载失败仍有 banner 兜底（不静默）",
  iSpec > -1 && bd.includes("if(!spec){showBanner"));

if (failed) { console.error(failed + " check(s) failed"); process.exit(1); }
console.log("ALL SUMMARY CHECKS PASSED");
