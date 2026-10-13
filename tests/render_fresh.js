/* P5-2 三身份视觉 + 数据新鲜度常驻条（node 运行，退出码 0=全过）。
 *
 * 验收原文（TODO P5-2）：
 *  ① 机械事实 / AI 判断 / 人的裁决三种块**肉眼可分**；
 *  ② 新鲜度条随**指纹轮询**变色（灰→黄）。
 *
 * 断言：
 *  - pTag 三种徽章齐全，未知 kind 诚实返回空串（不造第三种身份）；
 *  - 挂点齐全：结论卡/蒸馏结论=机械事实，建议池条目=AI 判断，
 *    卡片本体=AI 判断，建议条目的 adopted/rejected 状态=人的裁决，
 *    动作台账=人的裁决；
 *  - 三身份 CSS 三种边线互不相同（肉眼可分的物理基础）；
 *  - renderFreshBar 真执行：无指纹隐藏 / 指纹一致=灰 / 指纹变更=黄（库已
 *    更新）/ 语料比库新=黄；「以此为新基线」复灰；
 *  - pollFreshBar 真执行：拉 /api/meta 指纹、首拉建基线、再拉对比变色；
 *  - 红线：新鲜度条与三身份标签只渲染不执行（切片无 api/actions /
 *    copyText / runCmdAction）；
 *  - 动态值（db_mtime）进 innerHTML 必须 esc。
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

/* 源码切片 */
const secMark = "===== P5-2 三身份与新鲜度（provenance+freshbar） =====";
const iSec = html.indexOf(secMark);
if (iSec < 0) throw new Error("找不到 P5-2 section 注释");
const iSecEnd = html.indexOf("===== P5-2 provenance end =====", iSec);
if (iSecEnd < 0) throw new Error("找不到 P5-2 section 结束注释");
const secSrc = html.slice(iSec, iSecEnd);

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));
function mkEl() {
  return { innerHTML: "", textContent: "", style: {}, disabled: false,
    value: "", dataset: {}, className: "", tagName: "DIV",
    querySelectorAll: () => [], addEventListener: () => {},
    classList: { toggle: () => {} } };
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl(); return els[id]; }
const state = { fresh: null }; /* 页面 state 桩（P5-2 只用 fresh 字段） */
/* api 桩必须放**模块顶层**：pollFreshBar 是在模块作用域 eval 的，
   它引的 api 沿闭包链找到的是这里，不是 async IIFE 内部 */
const api = async () => global.__apiStub();

for (const [n, sig] of [
  ["pTag", "kind"], ["freshKey", "fp"],
  ["renderFreshBar", ""], ["pollFreshBar", ""],
]) {
  eval(extract(n, sig));
}

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

(async () => {

/* ① pTag 三种身份 + 未知 kind 诚实空串 */
check("pTag(fact) = 「机械事实」徽章",
  pTag("fact").includes("机械事实") && pTag("fact").includes("t-fact"));
check("pTag(ai) = 「AI 判断」徽章",
  pTag("ai").includes("AI 判断") && pTag("ai").includes("t-ai"));
check("pTag(human) = 「人的裁决」徽章",
  pTag("human").includes("人的裁决") && pTag("human").includes("t-human"));
check("未知 kind 返回空串（不造第四种身份）", pTag("foo") === "" && pTag("") === "");

/* ② 挂点齐全（静态断言：每类块至少一处） */
const repConclSrc = extract("renderRepConcl", "kind,d");
check("结论卡挂「机械事实」（renderRepConcl 调 pTag(fact)）",
  repConclSrc.includes('pTag("fact")'));
const triCall = (html.match(/\$\("tri-concl"\)\.innerHTML=[^;]+;/) || [""])[0];
check("蒸馏结论挂「机械事实」", triCall.includes('pTag("fact")'),
  "调用点: " + triCall.slice(0, 80));
const agentsSrc = extract("renderAgents", "d,out");
check("建议池条目挂「AI 判断」", agentsSrc.includes('pTag("ai")'));
check("建议条目的 adopted/rejected 状态挂「人的裁决」",
  agentsSrc.includes('pTag("human")'),
  "状态 pill（adopted/rejected）= 人的裁决，必须可见");
const cardsSrc = extract("renderCards", "d,out");
check("卡片本体挂「AI 判断」（蒸馏产物）", cardsSrc.includes('pTag("ai")'));
const ledgerSrc = extract("loadLedger", "quiet");
check("动作台账挂「人的裁决」", ledgerSrc.includes('pTag("human")'));

/* ③ 三种边线互不相同（肉眼可分的物理基础） */
check("CSS 定义 p-fact / p-ai / p-human 三种左边条",
  /\.p-fact\{[^}]*border-left/.test(html) &&
  /\.p-ai\{[^}]*border-left/.test(html) &&
  /\.p-human\{[^}]*border-left/.test(html));
const bf = (html.match(/\.p-fact\{[^}]*border-left:([^;}]+)/) || ["", ""])[1];
const ba = (html.match(/\.p-ai\{[^}]*border-left:([^;}]+)/) || ["", ""])[1];
const bh = (html.match(/\.p-human\{[^}]*border-left:([^;}]+)/) || ["", ""])[1];
check("三种边线（颜色/线型）互不相同", bf !== ba && ba !== bh && bf !== bh,
  "fact=" + bf + " | ai=" + ba + " | human=" + bh);
check("三身份配色全走 var（不写死底色——主题对比度门同款纪律）",
  [bf, ba, bh].every(c => c.includes("var(--")));

/* ④ renderFreshBar 真执行 */
check("无指纹时新鲜度条隐藏",
  (state.fresh = null, renderFreshBar(),
   $("freshbar").style.display === "none"));
check("指纹一致 = 灰（不黄）",
  (state.fresh = { base: { sessions: 5, errors: 1, db_mtime: "a" },
                   cur: { sessions: 5, errors: 1, db_mtime: "a" },
                   corpusStale: false },
   renderFreshBar(),
   $("freshbar").className !== "stale" &&
   $("freshbar").innerHTML.includes("同步")));
check("指纹变更 = 黄 + 说明「库已更新」",
  (state.fresh = { base: { sessions: 5, errors: 1, db_mtime: "a" },
                   cur: { sessions: 6, errors: 1, db_mtime: "b" },
                   corpusStale: false },
   renderFreshBar(),
   $("freshbar").className === "stale" &&
   $("freshbar").innerHTML.includes("库已更新")));
check("语料比库新 = 黄（corpusStale）",
  (state.fresh = { base: { sessions: 5, errors: 1, db_mtime: "a" },
                   cur: { sessions: 5, errors: 1, db_mtime: "a" },
                   corpusStale: true },
   renderFreshBar(),
   $("freshbar").className === "stale" &&
   $("freshbar").innerHTML.includes("语料比库新")));
check("黄态提供「刷新页面」与「以此为新基线」两个出口",
  (state.fresh = { base: { sessions: 5, errors: 1, db_mtime: "a" },
                   cur: { sessions: 6, errors: 1, db_mtime: "b" },
                   corpusStale: false },
   renderFreshBar(),
   $("freshbar").innerHTML.includes("fb-reload") &&
   $("freshbar").innerHTML.includes("fb-rebase")));
/* rebase 复灰：黄态下点「以此为新基线」→ base=cur → 灰 */
(state.fresh = { base: { sessions: 5, errors: 1, db_mtime: "a" },
                 cur: { sessions: 6, errors: 1, db_mtime: "b" },
                 corpusStale: false }, renderFreshBar());
$("fb-rebase").onclick();
check("「以此为新基线」后复灰", $("freshbar").className !== "stale");
check("动态值 db_mtime 进 innerHTML 过 esc",
  (state.fresh = { base: { sessions: 1, errors: 1, db_mtime: "x<y" },
                   cur: { sessions: 1, errors: 1, db_mtime: "x<y" },
                   corpusStale: false },
   renderFreshBar(),
   $("freshbar").innerHTML.includes("x&lt;y") &&
   !$("freshbar").innerHTML.includes("x<y")));

/* ⑤ pollFreshBar 真执行（api 桩：pollFreshBar 内部调 api("/api/meta")） */
let metaCalls = 0, metaPayload = null;
global.__apiStub = async () => { metaCalls++; return metaPayload; };
metaPayload = { db_fingerprint: { sessions: 5, errors: 1, db_mtime: "a" } };
state.fresh = null;
await pollFreshBar();
check("首拉建基线且不变黄",
  metaCalls === 1 && state.fresh.base && state.fresh.cur &&
  $("freshbar").className !== "stale");
metaPayload = { db_fingerprint: { sessions: 9, errors: 2, db_mtime: "c" } };
await pollFreshBar();
check("再拉指纹变更 → 变黄（随指纹轮询变色的本义）",
  metaCalls === 2 && $("freshbar").className === "stale");
check("轮询源是 /api/meta（apiserve 只读面，轻量指纹）",
  secSrc.includes('"/api/meta"'));

/* ⑥ 红线：三身份标签与新鲜度条只渲染不执行 */
check("P5-2 切片无 api/actions / copyText / runCmdAction",
  !secSrc.includes("api/actions") && !secSrc.includes("copyText") &&
  !secSrc.includes("runCmdAction"),
  "新鲜度条只读指纹，绝不发动作请求");

console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
  "\nALL FRESH/PROVENANCE CHECKS PASSED");
process.exit(failed ? 1 : 0);

})().catch(e => { console.error("GATE ERROR:", e); process.exit(1); });
