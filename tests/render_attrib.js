/* 归因台渲染门（P6-9；node 运行，退出码 0=全过）。
 *
 * 判据即规格（设计 §2 第 5 项「归因台」）：
 *   候选与判定槽同屏 —— 左边机器出的**候选**（会错），右边人写的**裁决**（只进台账）。
 *
 * 本门 extract 页面里的**真函数**执行（与 render_dualtrack 同风格），钉住：
 *  ① 判据头（等级 + 覆盖度）**只显示载荷给的**；缺了就明说缺，不猜 R3；
 *  ② 覆盖度是纯文本形态、不漏 Markdown 标记、且绝不说"全库/全覆盖"；
 *  ③ 已归因行带 why/fix/置信/规则号；**未归因行明说"只有计数、没有根因候选"**；
 *  ④ 判定槽**默认空**（预填 = 替人做判断）；
 *  ⑤ 已裁决段不许静默消失（空台账也要明说"暂无裁决"）；
 *  ⑥ 落账真走 B 档两段提交：空裁决不发请求；有裁决 → action_id=attrib_verdict
 *     且参数照候选表原样（pattern/rule/cls/verdict/note）。
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
function extractConst(decl) {
  const m = html.match(new RegExp("^const " + decl + "=[\\s\\S]*?;\\n", "m"));
  if (!m) throw new Error("extract const fail: " + decl);
  return m[0].replace(/^const /, "var ");
}

let failed = 0;
function ok(cond, msg) {
  if (cond) { console.log("ok - " + msg); return; }
  failed++; console.error("FAIL - " + msg);
}
function eq(a, b, msg) {
  if (a === b) { console.log("ok - " + msg); return; }
  failed++;
  console.error("FAIL - " + msg + "（期望 " + JSON.stringify(b) + " 实得 " +
    JSON.stringify(a) + "）");
}

/* ---- 最小桩 ---- */
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
const state = { act: { actions: [{ action_id: "attrib_verdict" }] } };
function actionSpec(aid) {
  return aid === "attrib_verdict"
    ? { action_id: aid, tier: "B", params: {} } : null;
}
function loadCmdData() { return Promise.resolve({}); }
const posts = [];
let postReply = { status: "needs_confirm", confirm_token: "tok-1",
  risk: "reversible", target_path: "attrib_meta.db",
  human: ["动作：归因裁决落账"], expires_in: 300 };
function actJson(path, body) { posts.push({ path: path, body: body });
  return Promise.resolve(Object.assign({}, postReply)); }
function actApi() { return Promise.resolve({ status: "done", result: {} }); }
function jobResultHtml(x) { return "<div>" + esc(x.label || "") + "</div>"; }
function loadLedger() { return Promise.resolve({}); }

eval(extractConst("ATR_VERDICTS"));
eval(extractConst("ATR_VERDICT_LABEL"));
eval(extractConst("CLASS_LABEL"));
eval(extract("atrParams", ""));
eval(extract("atrSlotHtml", "p,rule"));
eval(extract("renderAttrib", "d,out"));
eval(extract("atrSubmit", "btn"));
eval(extract("runWriteActionWith", "spec,params,outId,opts"));
eval(extract("showConfirmDialog", "r,outId"));

/* ---- 夹具：与 attrib --json 的真实形状一致（探针实取） ---- */
function fixture(over) {
  const base = {
    schema: "attrib/1", generated_at: "2026-10-22 18:30:03",
    summary: { patterns_total: 67, patterns_attributed: 19,
      patterns_unattributed: 48, errors_total: 532,
      errors_attributed: 401, errors_unattributed: 131 },
    reviewable: { level: "R3", label: "可裁决",
      reason: "有候选、有回填通道", can_review: true, can_verdict: true },
    coverage: {
      patterns_total: 67, patterns_attributed: 19, pct_patterns: 28.4,
      errors_total: 532, errors_attributed: 401, pct_errors: 75.4,
      line: "- 覆盖度：**已归因 19 / 67 个模式（28.4%）**；**未归因的 48 个模式不在本面**",
      line_text: "- 覆盖度：已归因 19 / 67 个模式（28.4%）、401 / 532 条错误（75.4%）；未归因的 48 个模式（131 条错误）不在本面" },
    patterns: [
      { pattern: "Edit error on <path> EBUSY", class: "env", count: 4,
        rule: { rule_id: "AR-003", class: "env", why: "文件被占用",
          fix: "关闭占用进程", confidence: 0.9, candidate: true } },
      { pattern: "ABORTED", class: "unclassified", count: 1, rule: null },
    ],
    reviewed: [],
  };
  return Object.assign(base, over || {});
}

/* ===== ① 判据头只显示载荷给的等级 ===== */
{
  const out = mkEl();
  renderAttrib(fixture(), out);
  ok(out.innerHTML.includes("R3") && out.innerHTML.includes("可裁决"),
    "① 等级徽标来自载荷（R3 可裁决）");
  const out2 = mkEl();
  const f1 = fixture({ reviewable: { level: "R1", label: "不可评审",
    reason: "规则全未命中：有计数、无根因候选", can_review: false } });
  renderAttrib(f1, out2);
  ok(out2.innerHTML.includes("R1") && out2.innerHTML.includes("不可评审"),
    "① 后端说 R1 时页面不冒充可评审");
  const out3 = mkEl();
  renderAttrib(fixture({ reviewable: {} }), out3);
  ok(out3.innerHTML.includes("等级未知"),
    "① 载荷缺等级 → 明说「等级未知」，不默认 R3");
}

/* ===== ② 覆盖度：纯文本、带分母、不说全库 ===== */
{
  const out = mkEl();
  renderAttrib(fixture(), out);
  const covP = (out.innerHTML.match(/<p class='sub2 atr-cov'>[\s\S]*?<\/p>/) ||
    [""])[0];
  ok(covP.includes("已归因 19 / 67"), "② 覆盖度带模式分母");
  ok(covP.includes("401 / 532"), "② 覆盖度带错误分母");
  ok(covP.includes("不在本面"), "② 未归因部分被点名，不隐藏");
  ok(!covP.includes("**") && !covP.includes("`"),
    "② 覆盖度行不漏 Markdown 标记");
  ok(!/全覆盖|全库/.test(covP), "② 覆盖度行不出现「全库/全覆盖」措辞");
  const out2 = mkEl();
  renderAttrib(fixture({ coverage: {} }), out2);
  ok(out2.innerHTML.includes("覆盖度未知") &&
     out2.innerHTML.includes("不代表全库已归因"),
    "② 载荷缺 coverage → 明说未知，不默认成已归因");
}

/* ===== ③ 候选行：已归因给 why/fix；未归因不编 ===== */
{
  const out = mkEl();
  renderAttrib(fixture(), out);
  ok(out.innerHTML.includes("文件被占用") && out.innerHTML.includes("关闭占用进程"),
    "③ 已归因行显示 why 与 fix");
  ok(out.innerHTML.includes("AR-003"), "③ 已归因行带规则号");
  ok(out.innerHTML.includes("未归因：只有计数，没有根因候选"),
    "③ 未归因行明说没有根因候选");
  ok(out.innerHTML.includes("候选是<b>机器判断</b>"),
    "③ 候选/裁决三层分离的话在页面上");
  /* 未归因行不许出现编造的 why/fix 单元格（colspan 独占一格） */
  const abn = out.innerHTML.slice(out.innerHTML.indexOf("ABORTED"));
  ok(!abn.includes("置信"), "③ 未归因行不出「置信」列（没有候选就没有置信）");
}

/* ===== ④ 判定槽默认空 ===== */
{
  const out = mkEl();
  renderAttrib(fixture(), out);
  const slots = out.innerHTML.match(/<select class='atr-v'>[\s\S]*?<\/select>/g) || [];
  eq(slots.length, 1, "④ 只有已归因行有判定槽（未归因无槽）");
  ok(slots[0] && slots[0].includes("<option value=''>选择裁决…</option>"),
    "④ 判定槽第一个选项是空值（不预填裁决）");
  ok(!/selected/.test(slots[0] || ""), "④ 判定槽没有预选中任何裁决");
}

/* ===== ⑤ 已裁决段不许静默消失 ===== */
{
  const out = mkEl();
  renderAttrib(fixture(), out);
  ok($("atr-decided").innerHTML.includes("台账暂无裁决"),
    "⑤ 空台账也要明说「暂无裁决」");
  const out2 = mkEl();
  renderAttrib(fixture({ reviewed: [{ pattern: "ABORTED", count: 1,
    rule: { rule_id: "AR-009" },
    verdict: { verdict: "rejected", note: "不是录制中断" } }] }), out2);
  ok($("atr-decided").innerHTML.includes("驳回") &&
     $("atr-decided").innerHTML.includes("不是录制中断"),
    "⑤ 已裁决条目照实显示（裁决 + 说明）");
  ok($("atr-decided").innerHTML.includes("2 条") === false,
    "⑤ 已裁决条数写的是真实条数");
}

/* ===== ⑥ 落账真走两段提交 ===== */
async function testSubmit() {
  /* 空裁决：一个请求都不发 */
  posts.length = 0;
  const slot = { dataset: { pat: "ABORTED", rule: "AR-003", cls: "env" },
    querySelector: sel => sel === ".atr-v" ? { value: "" }
      : { value: "说明" } };
  await atrSubmit({ closest: () => slot });
  eq(posts.length, 0, "⑥ 没选裁决 → 不发任何写请求");
  ok(banners.some(b => b[1].includes("先选裁决")),
    "⑥ 没选裁决 → 明说不做判断");

  /* 有裁决：预演一次，参数照候选表原样 */
  posts.length = 0;
  const slot2 = { dataset: { pat: "ABORTED", rule: "AR-003", cls: "env" },
    querySelector: sel => sel === ".atr-v" ? { value: "rejected" }
      : { value: "只是录制中断" } };
  await atrSubmit({ closest: () => slot2 });
  eq(posts.length, 1, "⑥ 有裁决 → 只发第一段（预演）");
  const body = posts[0] && posts[0].body;
  eq(body && body.action_id, "attrib_verdict", "⑥ 动作 id 是 attrib_verdict");
  eq(body && body.params.pattern, "ABORTED", "⑥ 参数 pattern 照抄");
  eq(body && body.params.rule, "AR-003", "⑥ 参数 rule 照抄");
  eq(body && body.params.verdict, "rejected", "⑥ 参数 verdict 照抄");
  eq(body && body.params.note, "只是录制中断", "⑥ 参数 note 照抄");
  ok($("atr-packbox").innerHTML.includes("将写什么"),
    "⑥ 预演结果进确认框（不是直接写）");
}

testSubmit().then(() => {
  if (failed) { console.error("\n" + failed + " 条失败"); process.exit(1); }
  console.log("\nALL ATTRIB CHECKS PASSED");
});