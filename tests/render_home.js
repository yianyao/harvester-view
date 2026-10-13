/* P3-3 今日工作台渲染门（node 运行，退出码 0=全过）。
 *
 * 为什么单独一个门：工作台的纪律是「**只渲染服务端给的数，不二次聚合**」。
 * 页面里再算一遍，就会出现两套数字；而"页面算错/漏算"在别的门里看不到
 * （它们只 extract 各自关心的渲染函数）。
 *
 * 本门 extract 工作台的渲染函数真执行，断言：
 *  - 四队列按 label/数字/口径/真源四件套渲染（**口径必须显示**：P3-3 验收原文）；
 *  - 三环每段都渲染出 value+unit+definition+source；
 *  - 队列深链把用户送到对应页签（数字皆门第一跳）；
 *  - 页面**没有**对服务端的数做二次加工（值原样出现）。
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
function mkEl() {
  return { innerHTML: "", textContent: "", style: {}, disabled: false,
    value: "", dataset: {}, className: "", type: "", tagName: "DIV",
    querySelectorAll: () => [], addEventListener: () => {},
    classList: { toggle: () => {} } };
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl(); return els[id]; }
function errorBox(m) { return "<div class='empty'>" + esc(m) + "</div>"; }
const banners = [];
function showBanner(k, m) { banners.push([k, m]); }
const tabCalls = [];
function switchTab(n) { tabCalls.push(n); }
function loadReport() { return { catch: () => {} }; }
function loadGlossary() { return Promise.resolve(null); }
function panelIntro() { return ""; }
function applyTermTooltips() {}
function patternPlain() { return ""; }
const glossary = { loaded: false, data: null };
const state = { ov: null, act: { actions: [] } };

for (const [n, sig] of [
  ["renderQueues", "d"], ["goQueue", "qid"], ["renderQueueDetail", "d"],
  ["armHtml", "a"], ["renderRings", "d"],
]) {
  eval(extract(n, sig));
}

let failed = 0;
function check(label, cond) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label); }
}

/* 夹具：形状与真 /api/overview 一致（数字取自 2026-10-12 实测，但断言
   **从载荷推导**，不写死数字——写死就会随语料变化假红） */
const OV = {
  view_actions_api: 1,
  queues: [
    { queue_id: "decide", label: "待你裁决", question: "有裁决等着我做吗？",
      count: 0, source: "suggestmeta.load_records[status=pending]",
      definition: "建议台账里 status=pending 的条数",
      where: "报告页 → 建议条目（G2 建议池）" },
    { queue_id: "repair", label: "待修补", question: "哪里有坑该填？",
      count: 6, source: "api_reports_agents.entries[unresolved_count>=1]",
      definition: "建议条目里 unresolved_count>=1 的条数",
      where: "报告页 → G2 建议条目" },
    { queue_id: "distill", label: "待蒸馏", question: "有什么素材值得做成卡片/主题？",
      count: 30, source: "triage.collect_triage.new_patterns",
      definition: "分诊队列里的新错误模式数", where: "蒸馏队列页" },
    { queue_id: "verify", label: "待验证", question: "采纳的班规真的有效吗？",
      count: 9, source: "harvesteval.run_eval.counts[不下结论]",
      definition: "已采纳但 A/B 样本不足、下不了结论的条数",
      where: "报告页 → G1/G2 对照（跑 harvest-eval）" },
  ],
  queue_details: {
    repair: [
      { value: 17, unit: "次",
        source: "api_reports_agents.entries[unresolved_count]",
        definition: "未解决次数合计（条目维度求和）", level: "warn" },
      { value: 16, unit: "个", source: "errstats.pattern_stats.given_up",
        definition: "错误模式维度：至少有 1 次未解决的归一模式数",
        level: "warn" },
    ],
  },
  rings: [
    { ring_id: "errors", title: "错误 → 班规 → 效果",
      definition: "错误面（G2 的 given_up）→ 建议池裁决 → harvest-eval 效果判定",
      arms: [
        { value: 381, unit: "条", source: "errstats.collect_errors_from_db",
          definition: "有料：全库错误步骤数", level: "warn" },
        { value: 22, unit: "条", source: "suggestmeta.load_records[status=adopted]",
          definition: "已行动：已采纳为班规的条数", level: "warn" },
        { value: 9, unit: "条", source: "harvesteval.run_eval.counts[不下结论]",
          definition: "待验证：样本不足、下不了结论的条数", level: "warn" },
      ] },
  ],
  db_fingerprint: { sessions: 1981, steps: 44969, errors: 381,
    db_mtime: "2026-10-12 14:32:24", stale: true },
};

/* ① 四队列：label/数字/问题/口径/真源 五件套 */
renderQueues(OV);
const q = $("home-queues").innerHTML;
check("四队列都渲染", ["待你裁决", "待修补", "待蒸馏", "待验证"]
  .every(l => q.includes(l)));
check("数字原样出现（页面不做二次加工）",
  OV.queues.every(x => q.includes(">" + x.count + "</span>")));
check("口径逐条显示（P3-3 验收：口径在页面可见）",
  OV.queues.every(x => q.includes(esc(x.definition))));
check("真源逐条显示（可审计）",
  OV.queues.every(x => q.includes(esc(x.source))));
check("零积压的队列有视觉弱化（class=zero）", q.includes("qcard zero"));
check("每张卡都有去处理按钮", (q.match(/data-qgo=/g) || []).length === 4);

/* ② 口径明细：拆解臂带 value+unit+definition+source */
renderQueueDetail(OV);
const det = $("home-qdetail").innerHTML;
check("明细渲染被拆解的臂", det.includes("未解决次数合计"));
check("明细里每条臂都带真源", det.includes("errstats.pattern_stats.given_up"));

/* ③ 三环：每段口径可见 + 积压段计数 */
renderRings(OV);
const r = $("home-rings").innerHTML;
check("环标题与定义渲染", r.includes("错误 → 班规 → 效果") &&
  r.includes("harvest-eval 效果判定"));
check("环三段都渲染", ["381", "有料：全库错误步骤数", "待验证：样本不足"]
  .every(s => r.includes(s)));
check("环段带真源（口径可审计）",
  r.includes("errstats.collect_errors_from_db"));
check("环标注有几段有积压", r.includes("有 3 段有积压"));

/* ④ 数字皆门第一跳：队列深链送到对应页签 */
tabCalls.length = 0;
goQueue("repair");
check("待修补 → 报告页", tabCalls.includes("reports"));
tabCalls.length = 0;
goQueue("distill");
check("待蒸馏 → 蒸馏队列页", tabCalls.includes("triage"));
tabCalls.length = 0;
goQueue("verify");
check("待验证 → 报告页", tabCalls.includes("reports"));

/* ⑤ 落地页 = 今日工作台（动线；若用户裁决改回，改的是 BOOT 那一处） */
check("导航里有今日工作台且默认高亮",
  html.includes('data-tab="home" class="on"'));
check("启动切到 home（switchTab(\"home\")）",
  html.includes('state.loaded.home=true;') &&
  html.includes('switchTab("home");'));
check("启动不再无条件 loadAll（避免白拉一趟会话）",
  /state\.loaded\.home=true;\s*\n\s*switchTab\("home"\);/.test(html));

console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
  "\nALL HOME-DASHBOARD CHECKS PASSED");
process.exit(failed ? 1 : 0);
