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

/* v0.83：研读面错误模式表要显示「人话 / 原始码 / 下一步」，用到这组 helper。
   它们是真实代码（不重写一份），连 CLASS_LABEL 常量一起从页面里取——
   常量跨两行，故单独用正则抓；漏同步会直接 ReferenceError（不是静默少功能）。 */
const glossary = { loaded: true, data: { patterns: [
  { pattern: "FS_STALE_VERSION", plain: "改文件前没重新读：记的版本和磁盘对不上。",
    next: "继续改同一个文件之前再 Read 一次；别拿旧内容重试 Edit。",
    from: "设计文档 2026-10-22（人工核对过原文）" },
  { pattern: "FILE_NOT_FOUND", plain: "要读的文件不存在。",
    next: "先确认路径再读。", from: "名称推断（本项目自定义）" } ] } };
{
  /* 注：不能直接 eval("const CLASS_LABEL=…")——const 绑在 eval 自己的作用域里，
     出了这句就没了（实测 ReferenceError，而 var/globalThis 才对外可见）。 */
  const mCL = html.match(/^const CLASS_LABEL=\{[\s\S]*?\};/m);
  if (!mCL) throw new Error("extract fail: CLASS_LABEL");
  globalThis.CLASS_LABEL = eval("(" +
    mCL[0].replace(/^const CLASS_LABEL=/, "").replace(/;\s*$/, "") + ")");
}
eval(extract("patternPlain", "p"));
eval(extract("patternNext", "p"));
eval(extract("patternFrom", "p"));
eval(extract("clsLabel", "c"));
eval(extract("runBrief", ""));
eval(extract("pollBrief", "jid"));
eval(extract("renderBrief", "d,out"));
eval(extract("openTopic", "id"));
eval(extract("openTopicChain", "id, idx"));

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
  errors: { patterns: [{ pattern: "FS_STALE_VERSION", "class": "tool_interface",
              count: 84, given_up: 1 }], total: 84,
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
  ok(html1.includes("FS_STALE_VERSION"), "错误模式节有真数据（原始码保留）");
  /* 用户 2026-10-26 批注：光有码看不懂，要人话 + 下一步该咋办 + 依据 */
  ok(html1.includes("改文件前没重新读"), "错误模式格有人话");
  ok(html1.includes("下一步") && html1.includes("再 Read 一次"),
     "错误模式格有「下一步该咋办」");
  ok(html1.includes("依据：设计文档 2026-10-22"), "解释的依据如实标出来");
  ok(html1.includes("用法类"), "类别显示成人话（tool_interface → 用法类）");
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
                     "重复会话", "健康检查", "链的结论"])
    ok(html1.includes(sec) || src.includes(sec), "结构节存在：" + sec);

  console.log(failed ? ("\nFAILED: " + failed) : "\nALL PASSED");
  process.exit(failed ? 1 : 0);
}
main();
