/* 链创作包 / 链发布入口 / 产物流渲染门（P11；node 运行，退出码 0=全过）。
 *
 * 用户 2026-10-24 原话：「不仅仅是补一条链那么简单，要补一个生成链的命令给我。
 * 更干脆的，是我能直接就看到链」「同理，有个双轨还是三轨的，现在也不知道跑哪
 * 去了」「产出物的分析还是啥，也不见了」。
 *
 * 本门钉住三件事：
 *  ① **入口可达**：选中主题即显示工具栏，且「生成链 / 校验并发布 / 产物流」
 *     三按钮一并启用；链为空时（chain 接口 404）空态必须给「生成链创作包」
 *     按钮并真能触发（不是死胡同文案）；
 *  ② **不把包当链**：创作包视图必须自陈「这不是链」「机器不代笔」，
 *     给出草稿位与下一步；包正文必须转义（防包内 HTML 注入页面）；
 *  ③ **产物流诚实**：覆盖度行原样透传（缺了就明说「覆盖度未知」）、
 *     未配置 / 无记录两种空态区分开、版本明细逐条可读。
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
function shortSid(s) { return String(s).slice(0, 8); }
const banners = [];
function showBanner(k, m) { banners.push([k, m]); }
const cmdOpened = [];
function openInCmd(aid, params) { cmdOpened.push([aid, params]); return Promise.resolve(true); }
const state = { topicCur: { id: "tp-fix", name: "夹具主题" } };

/* 动作面桩：入队 → 轮询一拍 done */
const posts = [];
let jobQueue = [];
function actJson(p, body) { posts.push({ path: p, body }); return Promise.resolve({ job_id: "j-1" }); }
function actApi() { return Promise.resolve(jobQueue.shift() || { status: "done", result: {} }); }
function updateTopicCmd() {}

/* ---- 夹具 ---- */
const PACK = "## 0 判据头\n- 主题：夹具\n<script>alert(1)</script>\n";
const CHAIN_RESULT = {
  view_actions_api: 1, topic_id: "tp-fix",
  topic: { id: "tp-fix", name: "夹具主题" },
  counts: { members: 39, sessions: 39, buckets: 13,
            anchor_candidates: 39, anchor_shown: 36 },
  budget: { max_chars: 72000, truncated: true, budget_skipped: 3,
            per_bucket_cap: 6 },
  prompt_version: "t3-chainbuild-20261024",
  chars: PACK.length,
  pack: PACK,
  draft_path: "D:/repo/session-harvester/exports/chains/tp-fix.md",
  next_steps: ["把包交给 Agent", "Agent 写到草稿位", "点「校验并发布」"],
};
const ART_RESULT = {
  view_actions_api: 1, topic_id: "tp-fix",
  topic: { id: "tp-fix", name: "夹具主题" }, members: 39,
  artifacts: {
    configured: true, n_files: 1, n_versions: 2,
    honest: "版本为 Write/Edit 提取记录（artifacts.py 口径）",
    coverage: { line_text: "产物仅来自 dsh 源：已登记 8 / 总会话 1776（0.5%）" },
    files: [{ sid: "src-a:s-1", file_path: "src/app.js", n_versions: 2,
      versions: [
        { seq: 1, ts: "2026-10-01 10:00:00", tool: "Write", old_len: 0,
          new_len: 120, new_preview: "第一版内容" },
        { seq: 2, ts: "2026-10-01 11:00:00", tool: "Edit", old_len: 120,
          new_len: 200, new_preview: "改过之后" },
      ] }],
  },
};

async function main() {
  eval(extract("renderChainPack", "d,out"));
  eval(extract("renderArtifactsFlow", "d,out"));
  eval(extract("runChainBuild", ""));
  eval(extract("pollChainBuild", "jid"));
  eval(extract("runArtifactsFlow", ""));
  eval(extract("pollArtifactsFlow", "jid"));
  eval(extract("openTopicChain", "id"));

  /* ① 创作包视图：不把包当链 + 转义 + 报截断 */
  const o1 = mkEl();
  renderChainPack(CHAIN_RESULT, o1);
  const h1 = o1.innerHTML;
  ok(h1.includes("链创作包已生成"), "① 创作包视图有明确标题");
  ok(h1.includes("这不是链"), "① 必须自陈「这不是链」（不许把包当成品）");
  ok(h1.includes("机器不代笔"), "① 必须自陈「机器不代笔」");
  ok(h1.includes("把包交给 Agent") && h1.includes("点「校验并发布」"),
     "① 下一步三步都在（含 Agent 与发布）");
  ok(h1.includes("D:/repo/session-harvester/exports/chains/tp-fix.md"),
     "① 草稿位路径可见（Agent 要往哪写）");
  ok(h1.includes("另有 3 个被字符预算砍掉"),
     "① 预算截断报数（不静默少给锚点）");
  ok(!h1.includes("<script>alert(1)</script>"), "① 包正文必须转义（防注入）");
  ok(h1.includes("&lt;script&gt;"), "① 转义后原样可见（不吞内容）");
  ok(h1.includes("id='btn-cp-copy'") && h1.includes("id='btn-cp-pub'"),
     "① 复制 / 校验并发布两个按钮都在");
  ok(h1.includes("id='btn-cp-export'"),
     "①b 必须有「导出草稿」按钮（包 1.9 万字，手抄不叫可用）");

  /* 发布按钮 → 走 B 档动作 chain_publish（不造第二条写路径） */
  els["btn-cp-pub"].onclick();
  eq(cmdOpened[0][0], "chain_publish", "① 发布按钮跳 B 档动作 chain_publish");
  eq(cmdOpened[0][1].id, "tp-fix", "① 发布参数带当前主题 id");

  /* ①b 导出草稿（P12）：**写文件必须走 B 档两段提交**，不许点了就偷偷写。
     （首次实现曾错归 A 档，被 test_v38/test_v54 的 writes 门当场抓红。） */
  els["btn-cp-export"].onclick();
  const exCmd = cmdOpened.find(c => c[0] === "chain_export");
  ok(!!exCmd, "①b 导出按钮跳 B 档动作 chain_export（写文件先给预演）");
  eq(exCmd && exCmd[1].id, "tp-fix", "①b 导出按当前主题 id 走");
  ok(!posts.some(p => p.body && p.body.action_id === "chain_export"),
     "①b 不许绕过预演直接打执行接口（写文件 = 两段提交）");
  // #cp-draft 只在渲染出的 HTML 里（桩 DOM 不会为它建节点），故查 h1
  ok(h1.includes("id='cp-draft'"),
     "①b 草稿位那行有独立锚点（Agent 要往哪写）");
  ok(h1.includes("草稿位：") && h1.includes("校验并发布"),
     "①b 草稿位旁指向下一步（发布）");

  /* ② 产物流：覆盖度透传 + 明细可读 */
  const o2 = mkEl();
  renderArtifactsFlow(ART_RESULT, o2);
  const h2 = o2.innerHTML;
  ok(h2.includes("产物仅来自 dsh 源：已登记 8 / 总会话 1776（0.5%）"),
     "② 覆盖度行原样透传（不自行拼数字）");
  ok(h2.includes("本面不代表全库"), "② 覆盖度旁明说本面不代表全库");
  ok(h2.includes("src/app.js") && h2.includes("2") , "② 文件名与版本数渲染");
  ok(h2.includes("120→200 字"), "② 最近一版前后长度可见");
  ok(h2.includes("改过之后"), "② 逐版明细带预览文本");
  ok(h2.includes("版本为 Write/Edit 提取记录"), "② honest 口径随视图渲染");

  /* ② 产物流两种空态分开 */
  const o3 = mkEl();
  renderArtifactsFlow(Object.assign({}, ART_RESULT, { artifacts: {
    configured: false, files: [], n_files: 0, n_versions: 0,
    coverage: null, hint: "未配置 artifacts_meta" } }), o3);
  ok(o3.innerHTML.includes("未配置 artifacts_meta"),
     "② 未配置 → 渲染服务端 hint，不当作「零产物」");
  ok(o3.innerHTML.includes("覆盖度未知"),
     "② 载荷缺 coverage → 明说未知，绝不默认全覆盖");
  const o4 = mkEl();
  renderArtifactsFlow(Object.assign({}, ART_RESULT, { artifacts: {
    configured: true, files: [], n_files: 0, n_versions: 0,
    coverage: { line_text: "x" }, hint: "该主题成员暂无产物记录" } }), o4);
  ok(o4.innerHTML.includes("暂无产物记录"),
     "② 已配置但无记录 → 独立空态（与未配置区分）");

  /* ③ 真跑链路：入队 action_id 正确 + 轮询后渲染到中栏 */
  posts.length = 0;
  jobQueue = [{ status: "done", result: CHAIN_RESULT }];
  els["topics-timeline"] = mkEl();
  await runChainBuild();
  eq(posts[0].body.action_id, "chain_build", "③ 生成链走 A 档 chain_build");
  eq(posts[0].body.params.id, "tp-fix", "③ 参数带当前主题 id");
  await new Promise(r => setTimeout(r, 900));
  ok(els["topics-timeline"].innerHTML.includes("链创作包已生成"),
     "③ 轮询完成后中栏渲染创作包");

  posts.length = 0;
  jobQueue = [{ status: "done", result: ART_RESULT }];
  els["topics-timeline"] = mkEl();
  await runArtifactsFlow();
  eq(posts[0].body.action_id, "artifacts_flow",
     "③ 产物流走 A 档 artifacts_flow");
  await new Promise(r => setTimeout(r, 700));
  ok(els["topics-timeline"].innerHTML.includes("产物流"),
     "③ 轮询完成后中栏渲染产物流");

  /* ④ 链为空（404）不是死胡同：空态给「生成链创作包」按钮且能点 */
  els["topics-timeline"] = mkEl();
  els["topics-right"] = mkEl();
  global.fetch = () => Promise.resolve({ status: 404, ok: false });
  await openTopicChain("tp-fix");
  const empty = els["topics-timeline"].innerHTML;
  ok(empty.includes("尚无 chain 长文"), "④ 404 空态说明为什么没有链");
  ok(empty.includes("链创作包") && empty.includes("生成链创作包"),
     "④ 空态给出「生成链创作包」按钮（不再是只让去命令台）");
  ok(typeof els["btn-chain-empty-build"].onclick === "function",
     "④ 空态按钮已绑定处理函数");

  /* ⑤ 自动写链（v0.83，用户 2026-10-26「不能老是在 WorkBuddy 中耗费 token
     干这活」）：入口必须长在主题工具栏，必须走 B 档动作（写草稿位），
     且必须明说"不耗 WorkBuddy 的 token / 走你自己的 key"——不然用户
     不知道这钱是谁出的。 */
  ok(html.includes("btn-topic-chaingen"),
     "⑤ 工具栏有「自动写链」按钮（不用 Agent 写链）");
  ok(/openInCmd\("chain_generate",\{id:c\.id/.test(html),
     "⑤ 自动写链跳 B 档动作 chain_generate（写文件先给预演）");
  ok(/btn-topic-chaingen[\s\S]{0,600}?不耗 WorkBuddy/.test(html),
     "⑤ 按钮说明里点明「不耗 WorkBuddy 的 token」（钱从哪出要说清）");
  const reg = JSON.parse(fs.readFileSync(
    path.join(__dirname, "..", "actions", "actions_registry.json"), "utf8"));
  const cg = (reg.write_actions || []).find(a => a.action_id === "chain_generate");
  ok(!!cg, "⑤ 注册表里 chain_generate 是 B 档动作（不是偷偷执行）");
  ok(!!cg && cg.tier === "B", "⑤ chain_generate 定档 B（写文件 → 两段提交）");

  if (failed) { console.error("\n" + failed + " CHECK(S) FAILED"); process.exit(1); }
  console.log("\nALL CHAINFLOW CHECKS PASSED");
}
main().catch(e => { console.error(e); process.exit(1); });
