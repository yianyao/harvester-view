/* 今日工作台渲染门（node 运行，退出码 0=全过）。
 *
 * 为什么单独一个门：工作台的纪律是「**只渲染服务端给的数，不二次聚合**」。
 * 页面里再算一遍，就会出现两套数字；而"页面算错/漏算"在别的门里看不到
 * （它们只 extract 各自关心的渲染函数）。
 *
 * v0.49 变更（用户 2026-10-13 实测反馈驱动）：
 *   ① 队列卡整张可点（含数字）→ 去「处理单」，**不再**跳到报告页让页面自己再筛
 *      （旧行为跳过去之后明细对不上，正是用户报的缺陷）；故本门的第一跳断言
 *      从「送到 reports/triage 页签」改成「送到 queue:<id> 这个 target」；
 *   ② 队列明细行与闭环健康的每一段都带 target → 都渲染出「去处理」按钮；
 *   ③ 聚合数（不派活）必须显式标注，不能伪装成可处理的清单。
 *
 * 断言：
 *  - 四队列按 label/数字/口径/真源四件套渲染（**口径必须显示**：P3-3 验收原文）；
 *  - 三环每段都渲染出 value+unit+definition+source；
 *  - 数字皆门第一跳把用户送到**它自己那批**明细（target 语义）；
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
/* v0.49：数字皆门现在送到「处理单」的某个 target，故门要拦 openWork */
const workCalls = [];
function openWork(t) { workCalls.push(t); return t; }
function loadWork() { return { catch: () => {} }; }
function loadReport() { return { catch: () => {} }; }
function loadGlossary() { return Promise.resolve(null); }
function panelIntro() { return ""; }
function applyTermTooltips() {}
function patternPlain() { return ""; }
function anchor(sid, seq) { return "<a class='anchor'>" + sid + "#" + seq +
  "</a>"; }
function bindAnchors() {}
const glossary = { loaded: false, data: null };
const state = { ov: null, act: { actions: [] }, loaded: {}, workTarget: null };

/* 注意：**不** extract openWork —— 本门要用自己的桩来记录"送到了哪个 target"。
   被测源码由下面的 openWorkSrc 单独取出来做静态断言。 */
const openWorkSrc = (html.match(/^function openWork\(target\)\{[\s\S]*?^\}/m)
  || [""])[0];

for (const [n, sig] of [
  ["renderQueues", "d"], ["goQueue", "qid"], ["renderQueueDetail", "d"],
  ["armHtml", "a"], ["renderRings", "d"], ["bindWorkGo", "root"],
]) {
  eval(extract(n, sig));
}

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

/* 夹具：形状与真 /api/overview 一致（数字取自 2026-10-13 实测，但断言
   **从载荷推导**，不写死数字——写死就会随语料变化假红） */
const OV = {
  view_actions_api: 1,
  view_overview_api: 2,
  queues: [
    { queue_id: "decide", label: "待你裁决", question: "有裁决等着我做吗？",
      count: 0, unit: "条", target: "queue:decide", metric: "pending",
      source: "suggestmeta.load_records[status=pending]",
      definition: "建议台账里 status=pending 的条数",
      suggestion: "每条要么采纳、要么驳回", owner: "人", level: "ok",
      reconcile_ok: true, where: "报告页 → 建议条目（G2 建议池）" },
    { queue_id: "repair", label: "待修补", question: "哪里有坑该填？",
      count: 6, unit: "条", target: "queue:repair", metric: "entries",
      source: "api_reports_agents.entries[unresolved_count>=1]",
      definition: "建议条目里 unresolved_count>=1 的条数",
      suggestion: "先点锚点回原文", owner: "人", level: "warn",
      reconcile_ok: true, where: "报告页 → G2 建议条目" },
    { queue_id: "distill", label: "待蒸馏", question: "有什么素材值得做成卡片/主题？",
      count: 30, unit: "个", target: "queue:distill", metric: "patterns",
      source: "triage.collect_triage.new_patterns",
      definition: "分诊队列里的新错误模式数", suggestion: "挑次数高的先做",
      owner: "Agent", level: "warn", reconcile_ok: true, where: "蒸馏队列页" },
    { queue_id: "verify", label: "待验证", question: "采纳的班规真的有效吗？",
      count: 9, unit: "条", target: "queue:verify", metric: "entries",
      source: "harvesteval.run_eval.counts[不下结论]",
      definition: "已采纳但 A/B 样本不足、下不了结论的条数",
      suggestion: "只能等样本或放宽门槛", owner: "人", level: "warn",
      reconcile_ok: false, where: "报告页 → G1/G2 对照（跑 harvest-eval）" },
  ],
  queue_details: {
    repair: [
      { value: 17, unit: "次", level: "warn", metric: "unresolved",
        target: "queue:repair", aggregate: false, reconcile_ok: true,
        source: "api_reports_agents.entries[unresolved_count]",
        definition: "未解决次数合计（条目维度求和）" },
      { value: 16, unit: "个", level: "warn", metric: "patterns",
        target: "ring:errors.givenup", aggregate: false, reconcile_ok: true,
        source: "errstats.pattern_stats.given_up",
        definition: "错误模式维度：至少有 1 次未解决的归一模式数" },
      { value: 163, unit: "条", level: "warn", metric: null, target: null,
        aggregate: true,
        source: "suggestmeta.registry_counts[sessions_noise]",
        definition: "零散/噪声登记表里的条数" },
    ],
  },
  rings: [
    { ring_id: "errors", title: "错误 → 班规 → 效果",
      definition: "错误面（G2 的 given_up）→ 建议池裁决 → harvest-eval 效果判定",
      arms: [
        { value: 381, unit: "条", level: "warn", target: "ring:errors.0",
          metric: "errors", reconcile_ok: true,
          metrics: [ { name: "errors", label: "全库错误步骤数", mode: "sum",
                       unit: "条", value: 381 },
                     { name: "patterns", label: "错误模式数", mode: "items",
                       unit: "个", value: 57 } ],
          suggestion: "按次数从大到小处理头部",
          source: "errstats.collect_errors_from_db",
          definition: "有料：全库错误步骤数" },
        { value: 22, unit: "条", level: "warn", target: "ring:errors.1",
          metric: "entries", reconcile_ok: true, metrics: [],
          source: "suggestmeta.load_records[status=adopted]",
          definition: "已行动：已采纳为班规的条数" },
        { value: 9, unit: "条", level: "warn", target: "ring:errors.2",
          metric: "entries", reconcile_ok: true, metrics: [],
          source: "harvesteval.run_eval.counts[不下结论]",
          definition: "待验证：样本不足、下不了结论的条数" },
      ] },
  ],
  db_fingerprint: { sessions: 1981, steps: 44969, errors: 381,
    db_mtime: "2026-10-12 14:32:24", stale: true },
};

/* ① 四队列：label/数字/问题/口径/真源/建议 六件套 */
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
check("处理建议逐条显示（用户 2026-10-13 新需求）",
  OV.queues.every(x => q.includes(esc(x.suggestion))),
  "队列卡必须带「这批该怎么处理」，否则用户点进去还是不知道做什么");
check("零积压的队列有视觉弱化（class=zero）", q.includes("qcard zero"));
check("每张卡都可点（整卡含数字都是入口）",
  (q.match(/data-qgo=/g) || []).length === 4);
check("对账断了的卡会响亮报警（不许静默）",
  q.includes("对账断了"));

/* ② 口径明细：拆解臂带 value+unit+definition+source，且每行指向它自己的 target */
renderQueueDetail(OV);
const det = $("home-qdetail").innerHTML;
check("明细渲染被拆解的臂", det.includes("未解决次数合计"));
check("明细里每条臂都带真源", det.includes("errstats.pattern_stats.given_up"));
check("可派活的明细行带「去处理」（指向它自己的 target）",
  (det.match(/data-wgo=/g) || []).length === 2);
check("聚合数显式标注，不伪装成可处理清单",
  det.includes("聚合数（不派活）"));

/* ③ 三环：每段口径可见 + 积压段计数 + 每段可点 */
renderRings(OV);
const r = $("home-rings").innerHTML;
check("环标题与定义渲染", r.includes("错误 → 班规 → 效果") &&
  r.includes("harvest-eval 效果判定"));
check("环三段都渲染", ["381", "有料：全库错误步骤数", "待验证：样本不足"]
  .every(s => r.includes(s)));
check("环段带真源（口径可审计）",
  r.includes("errstats.collect_errors_from_db"));
check("环标注有几段有积压", r.includes("有 3 段有积压"));
check("闭环健康的每一段都可点（用户 2026-10-13：当前不能跳转）",
  (r.match(/data-wgo=/g) || []).length === 3);
check("闭环健康的每一段都带建议",
  r.includes(esc(OV.rings[0].arms[0].suggestion)));
check("多指标段把每条指标都列出来（条目数 ≠ 次数和）",
  r.includes("错误模式数") && r.includes("全库错误步骤数"));

/* ④ 数字皆门第一跳：**送到它自己那批明细**（不再是"跳去另一个页面再筛一遍"） */
workCalls.length = 0;
goQueue("repair");
check("待修补 → 它自己的处理单（queue:repair）",
  workCalls.length === 1 && workCalls[0] === "queue:repair",
  "旧行为跳 reports 页展示全部条目 → 明细对不上，正是用户报的缺陷");
workCalls.length = 0;
goQueue("distill");
check("待蒸馏 → queue:distill", workCalls[0] === "queue:distill");
workCalls.length = 0;
goQueue("verify");
check("待验证 → queue:verify", workCalls[0] === "queue:verify");
workCalls.length = 0;
goQueue("queue:decide");
check("完整 target 也能直接进（环段/明细行走的就是这条路）",
  workCalls[0] === "queue:decide");

/* ⑤ 启动序列顺序（**真缺陷回归门**）
 *
 * 2026-10-12 用户实测：工作台永远停在「加载中…」。根因是启动处写成了
 *   state.loaded.home=true;  // 先置位
 *   switchTab("home");       // switchTab 里 if(!state.loaded[name]) 才加载
 * → loadOverview() 被守卫挡掉、永不执行。
 * 本门只做**顺序**断言：启动块里 switchTab("home") 必须出现在
 * loaded.home 置位之前（上一版只查"源码里有没有 switchTab"，所以没抓到）。
 */
const bootBlock = (html.match(/initUpstreams\(\)\.then\(\(\)=>\{[\s\S]*?\}\)\.catch/) || [""])[0];
const iSwitch = bootBlock.indexOf('switchTab("home")');
const iLoaded = bootBlock.indexOf("state.loaded.home=true");
check("启动块里能找到 switchTab(\"home\") 与 loaded.home 置位",
  iSwitch >= 0 && iLoaded >= 0);
check("顺序正确：先 switchTab（守卫放行→真加载）再置位",
  iSwitch >= 0 && iLoaded >= 0 && iSwitch < iLoaded,
  "顺序反了会让 loadOverview() 被 if(!state.loaded[name]) 挡掉 → 永远『加载中』");
check("启动不再无条件 loadAll（避免白拉一趟会话）",
  !/switchTab\("home"\);[\s\S]{0,80}loadAll\(\)/.test(bootBlock));
check("openWork 不预先置位 loaded.work（否则守卫会挡掉加载）",
  openWorkSrc.length > 0 &&
  !/state\.loaded\.work\s*=\s*true[\s\S]{0,60}switchTab\("work"\)/
    .test(openWorkSrc),
  "SOP §5 第 1 条：state.loaded.X=true 写在 switchTab 之前 = 加载函数永不执行");

console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
  "\nALL HOME-DASHBOARD CHECKS PASSED");
process.exit(failed ? 1 : 0);
