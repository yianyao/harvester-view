/* 双轨时间线渲染门（P4-4；node 运行，退出码 0=全过）。
 *
 * TODO P4-4 验收原文（判据即规格）：
 *   「任选有 artifacts 的主题上/下轨同屏对齐；打磨带与 P4-3 候选一致；
 *     反转点标记可点击看两轮原文对比」
 *
 * 本门 extract 渲染函数**真执行**（render_dualtrack 的教训同 render_work：
 * 只查源码字符串抓不到执行路径缺陷），钉住：
 *  ① 上/下轨同屏（dt-grid 两列、上轨行数=timeline.rows、下轨文件数=artifacts）；
 *  ② 打磨带条数与候选一致、每条自带 a/b 两轮原文预览（不二次请求）；
 *  ③ 反转点每条自带 prev/cur 两轮原文对比；
 *  ④ 数字皆门：主题内候选数与全库口径（band_all/turn_hits_all）并列渲染；
 *  ⑤ 诚实空态：artifacts 未配置/无记录时渲染 hint，不装作有数据；
 *  ⑥ level 兜底：选择器不在 coarse/mid 时 dualtrackLevel() 返回 coarse；
 *  ⑦ 作业链路真跑：runDualtrack → 入队 → 轮询 queued → 完成后渲染结果。
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
function eq(a, b, msg) {
  if (a === b) { console.log("ok - " + msg); return; }
  failed++;
  console.error("FAIL - " + msg + "（期望 " + JSON.stringify(b) + " 实得 " +
    JSON.stringify(a) + "）");
}

/* ---- 最小桩（与 render_work.js 同风格） ---- */
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
const opened = [];
function openTopic(id) { opened.push(["topic", id]); return Promise.resolve(); }
function openSession(sid) { opened.push(["session", sid]); return Promise.resolve({}); }
const state = { topicCur: { id: "t-fix" }, anchorRun: 0, dualJob: null };

/* ---- 动作面桩：runDualtrack 入队 → pollDualJob 轮询两拍 ---- */
const runPosts = [];
let jobResponses = [];
function actJson(path, body) {
  runPosts.push({ path: path, body: body });
  return Promise.resolve({ job_id: "j-1", est_sec: 30 });
}
function actApi(path) {
  const r = jobResponses.shift() || { status: "done", result: FIXTURE };
  return Promise.resolve(r);
}

eval(extract("packLevel", ""));
eval(extract("dualtrackLevel", ""));
eval(extract("runDualtrack", ""));
eval(extract("pollDualJob", "jid"));
eval(extract("renderDualtrack", "d,out"));

/* ---- 夹具：与 dualtrack.build 的输出形状一致 ---- */
const FIXTURE = {
  view_actions_api: 1, topic_id: "t-fix", level: "coarse",
  topic: { id: "t-fix", name: "叙事节奏" },
  members: 3,
  timeline: { level: "coarse", members: 3, rows: [
    { sid: "s-a", title: "会话A", first_user: "帮我看看开头", last_asst: "好的" },
    { sid: "s-b", title: "会话B", first_user: "续写", last_asst: "续好了" },
  ] },
  revisions: {
    thresholds: { band: 0.9 },
    band: [
      { sid: "s-a", rowid_a: 11, rowid_b: 12, jaccard: 0.93,
        a: { preview: "前轮原文A" }, b: { preview: "后轮原文B" } },
    ],
    turn_hits: [
      { sid: "s-b", rowid: 21, word: "不对", head: "不对，换成另一版开头",
        cur: { preview: "本轮原文" }, prev: { preview: "前轮原文" } },
      { sid: "s-b", rowid: 22, word: "不对", head: "不对，重来",
        cur: { preview: "本轮原文2" }, prev: null },
    ],
    account_all: { band: 9, turn_hits: 17 },
    honest: "打磨 vs 反转由人甄别（候选非结论）；预览超 800 字符截断",
  },
  artifacts: { configured: true, n_files: 1, n_versions: 2, files: [
    { sid: "s-a", file_path: "docs/x.md", n_versions: 2, versions: [
      { seq: 1, ts: "2026-10-13T10:00:00", tool: "Write",
        old_len: 0, new_len: 300, old_preview: "", new_preview: "新版全文…" },
    ] },
  ] },
  nags: {
    account: { user_turns: 400, n_inject: 31, n_short: 22, n_paste: 15,
               n_lane: 332, n_hits: 9, n_template_groups: 1, n_groups: 4 },
    groups: [
      { n: 3, sids: ["s-a", "s-b", "s-c"], hit_sids: ["s-a", "s-b"],
        sample: { sid: "s-a", rowid: 5, preview: "记得收尾文件写完再退出" } },
    ],
    honest: "口径=过滤版探针 v2（剥注入/剥过短/剥长粘贴/剥模板/相似归并，"
          + "nags.py 冻结常量）；候选只列不裁决，归宿由人定（P4-2 建议池）",
  },
  account: { members: 3, band: 1, turn_hits: 2, band_all: 9,
             turn_hits_all: 17, artifact_files: 1, artifact_versions: 2,
             nags_groups: 1, nags_groups_all: 4 },
};

async function main() {
  /* ⑥ level 兜底（v0.74：下拉已删 chain/fine/artifact，只留可直接跑的
     三档；这里用历史/异常值钉 packLevel 白名单与 dualtrackLevel 降档） */
  $("topic-level").value = "chain";
  eq(packLevel(), "title", "packLevel: chain（历史遗留值）→ 兜底 title");
  eq(dualtrackLevel(), "coarse", "选择器=chain → 兜底 coarse");
  $("topic-level").value = "mid";
  eq(packLevel(), "mid", "packLevel: mid 原样通过");
  eq(dualtrackLevel(), "mid", "选择器=mid → 原样通过");
  $("topic-level").value = "fine";
  eq(packLevel(), "title", "packLevel: fine（按需档，已不在下拉）→ 兜底 title");
  eq(dualtrackLevel(), "coarse", "选择器=fine → 兜底 coarse");

  /* ⑥b 结构门：分层下拉只许出现可直接跑的三档（chain 不是 pack 分层；
     fine/artifact 是按需档，在命令台选——那边会出 sid 字段） */
  const selBody = (html.match(/<select id="topic-level">([\s\S]*?)<\/select>/)
    || [])[1] || "";
  ok(selBody && !/value="chain"|value="fine"|value="artifact"/.test(selBody) &&
     /value="title" selected/.test(selBody),
     "分层下拉只含 title（默认）/coarse/mid 三档");

  /* ⑦ 作业链路真跑：入队 + 轮询 + 渲染 */
  jobResponses = [{ status: "queued" }, { status: "done", result: FIXTURE }];
  $("topics-timeline").innerHTML = "";
  await runDualtrack();
  eq(runPosts.length, 1, "runDualtrack 发了 1 次 POST");
  eq(runPosts[0].path, "/api/actions/run", "走的是 /api/actions/run");
  eq(runPosts[0].body.action_id, "dualtrack", "action_id=dualtrack（冻结注册表）");
  eq(runPosts[0].body.params.id, "t-fix", "params.id=当前主题");
  eq(runPosts[0].body.params.level, "coarse", "params.level=兜底后的 coarse");
  await new Promise(r => setTimeout(r, 1200));   /* 等两拍轮询（800ms 间隔） */
  const rendered = $("topics-timeline").innerHTML;
  ok(rendered.includes("双轨时间线"), "轮询完成后中栏已渲染双轨视图");

  /* ①②③④⑤ 渲染主体（直接对 out 元素真执行） */
  const out = mkEl();
  renderDualtrack(FIXTURE, out);
  const h = out.innerHTML;
  ok(h.includes("dt-grid") && h.includes("上轨 · 讨论流") &&
     h.includes("下轨 · 产物流"), "① 上/下轨同屏（dt-grid 两列都有标题）");
  eq((h.match(/class='dt-up'/g) || []).length, FIXTURE.timeline.rows.length,
     "① 上轨行数=timeline.rows（只渲染服务端给的那批）");
  eq((h.match(/class='dt-file'/g) || []).length, FIXTURE.artifacts.files.length,
     "① 下轨文件数=artifacts.files");
  eq((h.match(/class='dt-cand'/g) || []).length, 4,
     "②③ 打磨带+反转点+反复叮嘱候选条数一致（1+2+1），每条可点开");
  ok(h.includes("前轮原文A") && h.includes("后轮原文B"),
     "② 打磨带自带 a/b 两轮原文预览（无二次请求）");
  ok(h.includes("本轮原文") && h.includes("前轮原文"),
     "③ 反转点自带 prev/cur 两轮原文对比");
  ok(h.includes("（同会话无更早 user 轮）"),
     "③ prev=null 的反转点渲染诚实占位，不是空白");
  ok(h.includes("全库 9") && h.includes("全库 17"),
     "④ 主题内候选数与全库口径（band_all/turn_hits_all）并列渲染");
  ok(h.includes("由人甄别"), "④ 诚实说明随视图渲染");
  ok(h.includes("原始 JSON"), "④ 原始 JSON 可展开（机器口径不丢）");

  /* ⑧ 反复叮嘱候选（P5-③，过滤版口径 + 只列不裁决 + 登记零散 P5-⑧） */
  ok(h.includes("反复叮嘱候选 1") && h.includes("全库 4"),
     "⑧ 主题内命中组数与全库口径并列渲染");
  ok(h.includes("过滤账目") && h.includes("含注入剥块 31") &&
     h.includes("逐字模板丢 1 组"),
     "⑧ 过滤账目随视图渲染（口径透明，候选才可信）");
  ok(h.includes("只列不裁决"),
     "⑧ 「候选只列不裁决」的诚实说明必须可见");
  ok(h.includes("记得收尾文件写完再退出"),
     "⑧ 候选组样例渲染");
  ok(h.includes('data-dt-sid="s-a"') && h.includes('data-dt-sid="s-b"') &&
     !h.includes('data-dt-sid="s-c"'),
     "⑧ 锚点只给主题成员 hit_sids（s-c 是主题外，只在对账文案里出现）");
  ok(h.includes("另涉主题外 1 会话"),
     "⑧ 命中组含主题外成员 → 对账提示可见");
  ok(h.includes("data-noise-reg=\"s-a,s-b\""),
     "⑧ 登记零散按钮只带主题成员 sid（P5-⑧，B 档两段提交）");
  const out5 = mkEl();
  renderDualtrack(Object.assign({}, FIXTURE, { nags: Object.assign({},
    FIXTURE.nags, { groups: [] }) }), out5);
  ok(out5.innerHTML.includes("没有跨 ≥2 会话的非模板叮嘱"),
     "⑧ 无命中 → 诚实空态（不装作有候选）");

  /* ⑤ 诚实空态 */
  const out2 = mkEl();
  renderDualtrack(Object.assign({}, FIXTURE, { artifacts: {
    configured: false, files: [], n_files: 0, n_versions: 0,
    hint: "未配置 artifacts_meta" } }), out2);
  ok(out2.innerHTML.includes("未配置 artifacts_meta"),
     "⑤ artifacts 未配置 → 渲染服务端 hint，不装作有数据");
  const out3 = mkEl();
  renderDualtrack(Object.assign({}, FIXTURE, { artifacts: {
    configured: true, files: [], n_files: 0, n_versions: 0, honest: "x" } }),
    out3);
  ok(out3.innerHTML.includes("暂无产物记录"),
     "⑤ 有配置但成员无产物 → 诚实空态+登记命令提示");

  /* 转义抽查：title 带 HTML 不逃逸 */
  const out4 = mkEl();
  renderDualtrack(Object.assign({}, FIXTURE, { topic: { id: "t", name:
    "<img src=x onerror=alert(1)>" } }), out4);
  ok(!out4.innerHTML.includes("<img src=x"),
     "主题名经 esc 转义，无注入");

  if (failed) { console.error("\n" + failed + " CHECK(S) FAILED"); process.exit(1); }
  console.log("\nALL DUALTRACK CHECKS PASSED");
}
main().catch(e => { console.error(e); process.exit(1); });
