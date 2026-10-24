/* v0.81 P1-3 渲染门：skill 治理档案面板（node 运行，退出码 0=全过）。
 *
 * 纪律与 render_harness 同源：从 index.html 提取**真源码**执行。
 * 本面板的验收红线（用户定位②「一眼看到值得看的」+ 诚实话术纪律）：
 *  - 「服务主题 0」必须解释原因（无 chain 主题不计入），不能读成"没服务过"；
 *  - 偶发 skill（调用 <阈值）默认折叠，但**折了几条、合计几次必须报数**；
 *  - skill 名 / 错误文本里的 HTML 必须 esc；
 *  - 已扫 chain 数为 0 时明确说「没扫到」，不是"这些 skill 没服务过"；
 *  - 跳过的 chain 文档必须留痕；
 *  - md 导出与渲染同口径（同一份响应的两种出口）。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

function extract(name, sig) {
  /* 允许 `) {`（花括号前有空格）的函数写法——本页两种风格都有 */
  const re = new RegExp("^(?:async )?function " + name + "\\(" + sig +
    "\\)\\s*\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name + "(" + sig + ")");
  return m[0];
}

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));

/* 元素桩 */
function mkEl(id) {
  const el = { id, innerHTML: "", textContent: "", style: {}, disabled: false,
    dataset: {}, className: "", _qa: {}, value: "", checked: false,
    querySelector(sel) { return mkEl(id + ">" + sel); },
    querySelectorAll(sel) { return el._qa[sel] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }
$("rep-q").value = "";
$("rep-erronly").checked = false;

/* 依赖桩 */
const REP_MIN_DEFAULT = 3;
const state = { repMin: REP_MIN_DEFAULT, repKind: "skillmatrix" };
function repFilter() { return { q: $("rep-q").value.trim().toLowerCase(),
                                only: $("rep-erronly").checked }; }
function hitQ(text, q) { return !q || String(text || "").toLowerCase().includes(q); }

eval(extract("repMinOf", ""));
eval(extract("repGate", "rows, min"));
eval(extract("repMinChips", ""));
eval(extract("wireRepThresh", "out"));
eval(extract("renderSkillMatrix", "d,out"));
eval(extract("cell", "x"));
eval(extract("mdTable", "head,rows"));
eval(extract("mdReport", "kind,d"));

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

/* ---- 真形载荷（2026-10-24 真库 38 skill 的形状；含恶意串试金石） ---- */
const D = {
  skills: [
    { skill: "delivery-artifact", calls: 15, sids: 4, sources: ["workbuddy"],
      ok: 15, err: 0, no_result: 0, fail_rate: 0.0,
      topics_served: ["主题甲", "主题乙"], topics_count: 2, top_error: null,
      args_samples: [], chains: {}, disambig: [] },
    { skill: "skill-creator", calls: 4, sids: 4,
      sources: ["workbuddy", "dsh"], ok: 4, err: 0, no_result: 0,
      fail_rate: 0.0, topics_served: ["主题甲"], topics_count: 1,
      top_error: null, args_samples: [], chains: {},
      disambig: ["跨 2 个 harness（同名可能不同物）"] },
    { skill: "<b>bad</b>-skill", calls: 6, sids: 3, sources: ["dsh"],
      ok: 3, err: 3, no_result: 0, fail_rate: 0.5, topics_served: [],
      topics_count: 0, top_error: { text: "ENOENT: <x>", count: 3 },
      args_samples: [], chains: {},
      disambig: ["名解析不出（需人工定名）"] },
    { skill: "rare-skill", calls: 1, sids: 1, sources: ["dsh"], ok: 1,
      err: 0, no_result: 0, fail_rate: 0.0, topics_served: [],
      topics_count: 0, top_error: null, args_samples: [], chains: {},
      disambig: [] },
  ],
  totals: { skills: 4, calls: 26, failed: 3, unknown_named: 1,
            chains_scanned: 3, chains_total: 3, topics_registered: 16 },
  chain_errors: ["chain-坏档.md：解析失败（缺 frontmatter）"],
  note: "G4 画像（调用/成功/失败）来自 behstats skill_summary（上游口径）；"
      + "「服务的主题」来自 skilljoin 交叉的本页聚合——**两者不同源**；"
      + "无 chain 的主题不计入服务主题数。",
};

(async () => {
  const out = $("rep-out");
  state.repLast = D;
  state.repMin = REP_MIN_DEFAULT;

  /* ① meta 行把账目报全（skill 数/调用/失败/未识别/已扫 chain/注册主题） */
  renderSkillMatrix(D, out);
  const meta = $("rep-meta").textContent;
  check("meta 行报全六项账目",
    meta.includes("skill 4 个") && meta.includes("调用 26 次") &&
    meta.includes("失败 3 次") && meta.includes("名解析不出 1 个") &&
    meta.includes("已扫 chain 3/3") && meta.includes("注册主题 16"));

  /* ② 偶发 skill 默认折叠 + 报数（折了几条、合计几次） */
  check("默认折叠调用 <3 的 skill（rare-skill 不在表内）",
    !out.innerHTML.includes("rare-skill"));
  check("折叠报数（1 个 / 合计 1 次）",
    out.innerHTML.includes("另有 1 个调用次数 &lt;3") &&
    out.innerHTML.includes("合计 1 次调用"));

  /* ③ 服务主题 0 必须解释原因，不能读成"没服务过" */
  check("服务主题 0 注明「无 chain 主题不计入」",
    out.innerHTML.includes("0（无 chain 主题不计入）"));

  /* ④ esc：skill 名与错误文本里的 HTML 不落地 */
  check("skill 名 esc（<b>bad</b> 不落地）",
    out.innerHTML.includes("&lt;b&gt;bad&lt;/b&gt;-skill") &&
    !out.innerHTML.includes("<b>bad</b>"));
  check("错误文本 esc（ENOENT: <x> 不落地）",
    out.innerHTML.includes("ENOENT: &lt;x&gt;") &&
    !out.innerHTML.includes("ENOENT: <x>"));

  /* ⑤ 消歧状态渲染（跨 harness / 名解析不出） */
  check("消歧状态：跨 2 个 harness",
    out.innerHTML.includes("跨 2 个 harness"));
  check("消歧状态：名解析不出",
    out.innerHTML.includes("名解析不出"));

  /* ⑥ 跳过的 chain 文档必须留痕 */
  check("跳过的 chain 文档留痕",
    out.innerHTML.includes("chain-坏档.md：解析失败"));

  /* ⑦ 点头顶阈值 chip「全部」→ 偶发 skill 出现（chip 接线不空转） */
  const chips = [{ dataset: { repmin: "1" }, onclick: null },
                 { dataset: { repmin: String(REP_MIN_DEFAULT) }, onclick: null }];
  out._qa["[data-repmin]"] = chips;
  renderSkillMatrix(D, out);
  check("阈值 chip 已接线", typeof chips[0].onclick === "function");
  chips[0].onclick();
  check("点「全部（阈值 ≥1）」后 rare-skill 出现",
    out.innerHTML.includes("rare-skill"));
  check("全量态不再显示折叠报数",
    !out.innerHTML.includes("另有 1 个调用次数"));
  /* 点回「只看 ≥3」→ 再次折叠（双向都要生效） */
  chips[1].onclick();
  check("点回「只看 ≥3」再次折叠",
    !out.innerHTML.includes("rare-skill"));

  /* ⑧ 已扫 chain = 0 时明说「没扫到」而非"没服务过" */
  const noChain = JSON.parse(JSON.stringify(D));
  noChain.totals.chains_scanned = 0;
  noChain.totals.chains_total = 0;
  renderSkillMatrix(noChain, out);
  check("已扫 chain=0 时明说没扫到",
    out.innerHTML.includes("当前没有扫到任何 chain 长文"));

  /* ⑨ 空态：全库没有 skill 调用点 */
  renderSkillMatrix({ skills: [], totals: {}, chain_errors: [] }, out);
  check("空态诚实（全库没有 skill 调用点）",
    out.innerHTML.includes("全库没有 skill 调用点"));

  /* ⑩ P1-5：`cards index-md` 面板位存在且按需载入（不自动跑、不落盘） */
  renderSkillMatrix(D, out);
  check("面板底部有 cards index-md 的位（触发词消歧原料）",
    out.innerHTML.includes("触发词消歧素材（cards index-md）") &&
    out.innerHTML.includes("data-sm-indexmd"));
  check("触发词素材按需载入（默认不预跑，box 为空）",
    $("sm-indexmd").innerHTML === "" ||
    $("sm-indexmd").textContent.indexOf("载入中") !== 0);

  /* ⑪ md 导出与渲染同口径 */
  const md = mdReport("skillmatrix", D);
  check("md 导出含账目与每 skill 行",
    md.includes("skill 治理档案") && md.includes("delivery-artifact") &&
    md.includes("已扫 chain 3/3"));
  check("md 导出服务主题 0 也带解释",
    md.includes("0（无 chain 主题不计入）"));
  check("md 导出含消歧状态", md.includes("跨 2 个 harness"));

  console.log(failed ? "\nFAILED: " + failed : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
