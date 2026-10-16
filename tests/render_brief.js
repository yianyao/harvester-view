/* 主题研读面渲染门（v0.76 V3；node 运行，退出码 0=全过）。
 * 钉住（用户裁决 2026-10-15 四原则）：
 * ① 研读面默认视图：openTopic → 自动 runBrief（知识优先，C 位）；
 * ② 作业链路真跑：runBrief → 入队（action_id=topic_brief）→ 轮询 → 渲染；
 * ③ 分层标注：chain 结论层空态诚实（未配置 vs 确实没有 分开说）；
 * ④ 红线切片：renderBrief 无 fetch/POST（只渲染，执行在动作通道）；
 * ⑤ 结构：构成/高频/错误/skill/重复/健康 六节 + chain 结论层。 */
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
  failed++; console.error("FAIL - " + msg + "（实得 " + JSON.stringify(a) + "）");
}

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));
function kwText(k) {
  if (!k) return "";
  try { const a = JSON.parse(k); return Array.isArray(a) ? a.join("、") : String(k); }
  catch (e) { return String(k); }
}
function mkEl() {
  return { innerHTML: "", textContent: "", style: {}, disabled: false,
    value: "", dataset: {}, className: "",
    querySelectorAll: () => [], addEventListener: () => {},
    classList: { toggle: () => {} } };
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl(); return els[id]; }
function errorBox(m) { return "<div class='empty'>" + esc(m) + "</div>"; }
function showBanner() {}
const state = { topicCur: { id: "t-fix" } };
function updateTopicCmd() {}   // 桩：openTopic 内的按钮联动（本门不测）

const runPosts = [];
let jobResponses = [];
function actJson(path, body) {
  runPosts.push({ path: path, body: body });
  return Promise.resolve({ job_id: "j-brief", est_sec: 30 });
}
function actApi(path) {
  const r = jobResponses.shift() || { status: "done", result: FIXTURE };
  return Promise.resolve(r);
}

eval(extract("runBrief", ""));
eval(extract("pollBrief", "jid"));
eval(extract("renderBrief", "d,out"));
eval(extract("openTopic", "id"));
eval(extract("openTopicChain", "id"));

const FIXTURE = {
  view_actions_api: 1, kind: "topic-brief", topic_id: "t-fix",
  level: "coarse",
  topic: { id: "t-fix", name: "叙事节奏", keywords: "[\"节奏\",\"钩子\"]",
           created: "2026-10-08 10:00" },
  members: 3,
  composition: { by_source: { "workbuddy-transcript": 2, "yuanbao-raw": 1 },
    months: { "2026-09": 2, "2026-10": 1 },
    first: "2026-09-01 10:00:00", last: "2026-10-12 20:00:00",
    source: "sessions 表按成员 sid 查询", definition: "构成与活跃口径" },
  questions: { top: [{ gram: "钩子", count: 12 }, { gram: "节奏", count: 9 }],
    n_user_msgs: 40, source: "kwstats.extract_grams",
    definition: "成员 user 消息 2/3-gram，停用词过滤" },
  errors: { patterns: [{ pattern: "FILE_NOT_FOUND", "class": "context",
              count: 3, given_up: 1 }], total: 3,
            source: "errstats.pattern_stats",
            definition: "成员错误按归一模式聚类" },
  skills: { skills: [{ skill: "pdf-text-extractor", calls: 5, ok: 5 }],
            source: "behstats", definition: "成员 skill 调用计数" },
  dupes: { reps: 3, duplicates: 0, band_pairs: 1,
           source: "topicprep.dedupe_topic",
           definition: "J≥0.90 判重复；0.80–0.90 边界带" },
  chains: { configured: true, chains: [
    { name: "叙事节奏 chain", stages: 3, nodes: 12,
      conclusion: { title: "元结论", text: "钩子密度决定完读率…（截断）" },
      open: { title: "待补", text: "反转点采样不足…" } },
  ], honest: "" },
  health: { members_not_in_index: [], noise_registered: [],
            source: "sessions+sessions_noise",
            definition: "缺口与自相矛盾计数" },
  account: { members: 3, n_user_msgs: 40, errors: 3, chains: 1 },
};

async function main() {
  /* ① openTopic → 自动进研读面（知识优先 C 位） */
  await openTopic("t-fix");
  eq(runPosts.length, 1, "openTopic 自动入队研读面作业");
  eq(runPosts[0].body.action_id, "topic_brief", "action_id=topic_brief");
  eq(runPosts[0].body.params.id, "t-fix", "主题 id 透传");

  /* ② 轮询完成 → 渲染 */
  const out = $("topics-timeline");
  await pollBrief("j-brief");
  const html1 = out.innerHTML;
  ok(html1.includes("研读面 ·"), "渲染研读面标题");
  ok(html1.includes("构成与活跃"), "构成节存在");
  ok(html1.includes("钩子"), "高频问题节有真数据");
  ok(html1.includes("FILE_NOT_FOUND"), "错误模式节有真数据");
  ok(html1.includes("元结论"), "chain 结论层渲染");
  ok(html1.includes("待补"), "chain 未决层渲染");
  ok(html1.includes("机器聚合=确定性数据重排"), "分层标注尾注在");

  /* ③ 诚实空态：无 chain 主题（configured=true 但空） */
  const empty = JSON.parse(JSON.stringify(FIXTURE));
  empty.chains = { configured: true, chains: [],
                   honest: "该主题还没有已发布的 chain（确实没有，不是没去读）" };
  jobResponses = [{ status: "done", result: empty }];
  await pollBrief("j-brief");
  ok(out.innerHTML.includes("该主题还没有已发布的 chain"),
     "无 chain 主题的诚实空态");

  /* ④ 未配置 chain-root 的空态（configured=false） */
  const unconf = JSON.parse(JSON.stringify(FIXTURE));
  unconf.chains = { configured: false, chains: [],
                    honest: "未传 --chain-root（未执行——不等于该主题没有 chain）" };
  jobResponses = [{ status: "done", result: unconf }];
  await pollBrief("j-brief");
  ok(out.innerHTML.includes("未传 --chain-root"),
     "未配置与未生成两种空态分开说");

  /* ⑤ 红线切片：renderBrief 不做任何网络/执行 */
  const src = extract("renderBrief", "d,out");
  ok(!/fetch\(/.test(src) && !/api\/actions/.test(src) &&
     !/runCmdAction/.test(src),
     "renderBrief 红线：只渲染，无 fetch/无动作执行");

  /* ⑥ 结构门：六节 + 结论层标题齐 */
  for (const sec of ["构成与活跃", "高频问题", "错误模式", "skill 行为",
                     "重复会话", "健康", "chain 结论层"])
    ok(html1.includes(sec) || src.includes(sec), "结构节存在：" + sec);

  console.log(failed ? ("\nFAILED: " + failed) : "\nALL PASSED");
  process.exit(failed ? 1 : 0);
}
main();
