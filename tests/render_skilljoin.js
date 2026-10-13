/* skill 调用点挂轨道渲染门（P4-5；node 运行，退出码 0=全过）。
 *
 * TODO P4-5 验收原文：「pdf-text-extractor 案例在时间线上可见调用点与成败」。
 * 渲染函数**真执行**（render_work 以来的纪律：只查字符串抓不到执行路径缺陷），
 * 钉住：
 *  ① 工具行常驻：skill 输入（留空=全部）+ margin 输入（默认 90 认可口径）
 *     + 挂轨道/收起按钮；
 *  ② 挂轨道后每个 stage 下有 sk-line；条数=该 stage rows（服务端给的，
 *     本页不重算 join）；无调用的 stage 显式「本阶段无调用」；
 *  ③ 成败可视：ok→✅ / error→❌ / no_result→⚠；margin 区调用带 sk-after
 *     虚线类与 ⚠ 前缀；
 *  ④ 汇总行：命中 X/Y + skill 名 + margin + 阶段后计数 + 未归组数；
 *  ⑤ chip 带会话跳转钩子（data-dt-sid）与 title（kind·status·ts）；
 *  ⑥ 未挂轨道（skJoin=null）时不出任何 sk-chip；收起按钮不出现。
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

let failed = 0;
function ok(cond, msg) {
  if (cond) { console.log("ok - " + msg); return; }
  failed++; console.error("FAIL - " + msg);
}

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));
function mkEl() {
  return { innerHTML: "", textContent: "", style: {}, disabled: false,
    value: "", dataset: {}, className: "",
    querySelectorAll: () => [], addEventListener: () => {},
    classList: { toggle: () => {} } };
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl(); return els[id]; }
function errorBox(m) { return "<div class='empty'>" + esc(m) + "</div>"; }
const banners = [];
function showBanner(k, m) { banners.push([k, m]); }
function switchTab(n) { tabCalls.push(n); }
const tabCalls = [];

/* ---- 与 render_dualtrack.js 同款动作面/会话跳转桩 ---- */
function openSession(sid) { opened.push(sid); return Promise.resolve({}); }
const opened = [];
function actApi(p) { return Promise.resolve(SKJOIN); }
function bindTopicNodes(root) { bound.push("nodes"); }
function bindChainSwitch(root, d) { bound.push("switch"); }
const bound = [];
const state = {};   /* CHAIN/SKJOIN 声明后填（避免 TDZ） */

eval(extract("renderChain", "d,out,idx"));
eval(extract("loadSkillJoin", ""));
eval(extract("clearSkillJoin", ""));

/* ---- 夹具：与 /api/topic/<id>/chain、skilljoin 输出形状一致 ---- */
const CHAIN = {
  topic_id: "tp-x",
  fm: { topic: "挂轨道测试主题", members: ["s1"],
        anchors: [
          { stage: "阶段一·调用可见", span: "2026-01-01 ~ 2026-01-02",
            nodes: [{ sid: "s1", turn: 1, note: "调用点所在回合" }] },
          { stage: "阶段二·安静", span: "2026-02-01 ~ 2026-02-02",
            nodes: [] },
        ] },
  body: "# 正文",
};
const SKJOIN = {
  skill: "pdf-text-extractor", topic: "挂轨道测试主题", members: 1,
  total: 4, joined: 3, after_stage: 1,
  stages: [
    { stage: "阶段一·调用可见", span: "2026-01-01 ~ 2026-01-02", rows: [
      { sid: "s1", seq: 1, anchor: "s1#1", turn: 1, kind: "member",
        ts: "2026-01-01 09:30:00", after_stage: false, status: "ok",
        args: "", skill: "pdf-text-extractor", title: "会话s1" },
      { sid: "s1", seq: 3, anchor: "s1#3", turn: 1, kind: "member",
        ts: "2026-01-01 09:40:00", after_stage: false, status: "error",
        args: "", skill: "pdf-text-extractor", title: "会话s1" },
      { sid: "s2", seq: 1, anchor: "s2#1", turn: null, kind: "temporal",
        ts: "2026-01-02 08:00:00", after_stage: false, status: "no_result",
        args: "", skill: "pdf-text-extractor", title: "会话s2" },
      { sid: "s4", seq: 1, anchor: "s4#1", turn: null,
        kind: "temporal_after", ts: "2026-03-20 08:00:00",
        after_stage: true, status: "ok", args: "",
        skill: "pdf-text-extractor", title: "会话s4" },
    ] },
    { stage: "阶段二·安静", span: "2026-02-01 ~ 2026-02-02", rows: [] },
  ],
  unassigned: [
    { sid: "s3", seq: 1, anchor: "s3#1", turn: null, kind: "unassigned",
      ts: "2026-03-01", after_stage: false, status: "no_result",
      args: "", skill: "pdf-text-extractor", title: "会话s3" },
  ],
};

async function main() {
  Object.assign(state, { topicCur: { id: "tp-x" }, anchorRun: 0,
    chainLast: CHAIN, chainIdx: 0, skJoin: null, skJoinQuery: null });
  /* ⑥ 未挂轨道：无 chip、无收起按钮 */
  state.skJoin = null; state.skJoinQuery = null;
  const out0 = mkEl();
  renderChain(CHAIN, out0, 0);
  ok(!out0.innerHTML.includes("sk-chip"), "⑥ 未挂轨道无 sk-chip");
  ok(out0.innerHTML.includes("data-sk-load"), "① 工具行常驻（挂轨道按钮）");
  ok(out0.innerHTML.includes("id='sk-join-margin'") &&
     out0.innerHTML.includes("value='90'"),
     "① margin 输入默认 90（叙事节奏认可口径）");
  ok(out0.innerHTML.includes("pdf-text-extractor") &&
     out0.innerHTML.includes("placeholder"),
     "① skill 输入 placeholder 给了验收案例示例（仅文案，不写死逻辑）");
  ok(!out0.innerHTML.includes("data-sk-clear"), "⑥ 未挂轨道无收起按钮");

  /* ②③④⑤ 挂轨道渲染 */
  state.skJoin = SKJOIN;
  state.skJoinQuery = { skill: "pdf-text-extractor", margin: "90" };
  const out = mkEl();
  renderChain(CHAIN, out, 0);
  const h = out.innerHTML;
  ok(h.includes("data-sk-clear"), "① 挂轨道后出现收起按钮");
  eq2((h.match(/class='sk-line'/g) || []).length, 2,
      "② 每个阶段下都有 sk-line（2 阶段）");
  ok(h.includes("本阶段无调用"), "② 无调用阶段显式声明，不静默");
  eq2((h.match(/class='sk-chip/g) || []).length, 4,
      "② chip 条数=服务端 rows（本页不重算 join）");
  ok(h.includes("s1#1 ✅") || h.includes("s1#1</span>") &&
     h.includes("✅"), "③ ok 调用带 ✅");
  ok(h.includes("❌"), "③ error 调用带 ❌（成败可见=验收主判据）");
  ok(h.includes("⚠ s4#1") && h.includes("sk-after"),
     "③ margin 区调用带 ⚠ 前缀 + sk-after 虚线类");
  ok(h.includes("skill 调用点：命中 3/4") && h.includes("margin=90"),
     "④ 汇总行：命中 X/Y + margin");
  ok(h.includes("1 次在阶段结束之后"), "④ 汇总行：阶段后计数单独点名");
  ok(h.includes("未归组 1"), "④ 汇总行：未归组数");
  ok(h.includes("data-dt-sid=") && h.includes("member · ok") &&
     h.includes("temporal_after · ok"),
     "⑤ chip 带跳转钩子与 kind·status·ts title");
  ok(!h.includes("onerror="), "转义抽查：无注入逃逸");

  /* 收起真执行 */
  clearSkillJoin();
  ok(state.skJoin === null, "收起真执行：skJoin 清空");
  if (failed) { console.error("\n" + failed + " CHECK(S) FAILED"); process.exit(1); }
  console.log("\nALL SKILLJOIN CHECKS PASSED");
}
function eq2(a, b, msg) {
  if (a === b) { console.log("ok - " + msg); return; }
  failed++;
  console.error("FAIL - " + msg + "（期望 " + b + " 实得 " + a + "）");
}
main().catch(e => { console.error(e); process.exit(1); });
