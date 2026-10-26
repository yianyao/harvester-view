/* P2 #14 渲染门：产物台账 + 评审包（node 运行，退出码 0=全过）。
 *
 * 纪律与 render_skillmatrix / render_harness 同源：从 index.html 提取**真源码**
 * 执行，不复制一份"看起来像"的实现。
 *
 * 本面板的验收红线（都是"静默错"——错了页面照样有数字）：
 *  - **覆盖度必须排在表前面**：先给分母再给数，否则读者把 0.5% 当全库；
 *  - 工具名为空时明说「工具名未登记」，不许静默留白或补一个 Write/Edit；
 *  - 行数被截断时，总数不许跟着截（否则"共 N 行"是假的）；
 *  - 「打评审包」按钮必须真的接线（拿得到 sid/file）；
 *  - 评审包里两句话的措辞纪律：「时间上紧挨着」不许写成因果；
 *  - 卡片弱命中必须写明撞了哪个词（否则看不出是真命中还是撞词）；
 *  - 一切来自库的文本都要 esc（错误原文里有 HTML）。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

function extract(name, sig) {
  const re = new RegExp("^(?:async )?function " + name + "\\(" + sig +
    "\\)\\s*\\{[\\s\\S]*?^\\}", "m");
  const m = html.match(re);
  if (!m) throw new Error("extract fail: " + name + "(" + sig + ")");
  return m[0];
}

const esc = s => String(s == null ? "" : s)
  .replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));

/* 元素桩：querySelector 返回新桩（供 #rp-copy），querySelectorAll 可注入 */
function mkEl(id) {
  const el = { id, innerHTML: "", textContent: "", style: {}, disabled: false,
    dataset: {}, className: "", value: "", checked: false, _qa: {},
    /* 同一选择器返回**同一个**桩：页面在 render 里给 #rp-copy 挂 onclick，
       门要能拿到挂了之后的那一个（每次新建就永远拿不到，门会假红） */
    querySelector(s) {
      if (!el._qc) el._qc = {};
      if (!el._qc[s]) el._qc[s] = mkEl(id + ">" + s);
      return el._qc[s];
    },
    querySelectorAll(s) { return el._qa[s] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }

/* 依赖桩：copyText 只记录调用（页面里是真剪贴板） */
let copied = null;
function copyText(text, btn) { copied = { text, btn }; }

eval(extract("renderLedger", "d,out"));
eval(extract("renderReviewPack", "d,box"));
eval(extract("renderTopicPack", "d,box"));

/* 页面里的视角清单是模块级 const，提取函数时不会一起带出来。
   注意 `eval("const X=...")` 的 const 只活在 eval 自己的作用域里，出了
   这句就 ReferenceError——必须显式挂到 globalThis（render_brief.js 踩过）。 */
const _lv = html.match(/const LEDGER_VIEWS\s*=\s*(\[[^;]*\]);/);
if (!_lv) throw new Error("extract fail: LEDGER_VIEWS");
globalThis.LEDGER_VIEWS = eval("(" + _lv[1] + ")");

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

/* ---- 台账真形载荷（真库形状 + HTML 试金石） ---- */
const LEDGER = {
  schema: "artifact_ledger/2",
  configured: true,
  group: "file",
  coverage: {
    sources: ["dsh"],
    line_text: "- 覆盖度：仅 dsh 源（artifacts.py:8 自陈），已登记 8 / 总会话 "
      + "1776（0.5%）——其余源已实测判负；本面不代表全库",
  },
  totals: { sessions_registered: 8, files: 738, pairs: 1105,
            pairs_in_topics: 1028, versions: 2109, changed_chars: 4310213 },
  rows: [
    { sid: "dsh:a", file_path: "D:\\g\\<img onerror=x>稿.md",
      file_short: "D:\\g\\<img onerror=x>稿.md", n_versions: 63,
      first_ts: "2026-10-04 09:03:29", last_ts: "2026-10-04 10:09:13",
      changed_chars: 33733, tools: [], topics: [{ id: "tp-1", name: "主题甲" }],
      topic_names: "主题甲" },
    { sid: "dsh:b", file_path: "src\\a.py", file_short: "src\\a.py",
      n_versions: 2, first_ts: "2026-10-05 09:00:00",
      last_ts: "2026-10-05 09:10:00", changed_chars: 40, tools: [],
      topics: [], topic_names: "" },
  ],
  rows_total: 1105,
  truncated: true,
  candidates: { revisions: { account: { n_band: 113, n_turn_words: 8 } },
                nags: { account: { n_groups: 19, n_hits: 471 } } },
  definitions: { n_versions: "该文件在这一会话里被写/改的版数" },
  sources: { n_versions: "artifacts_meta.db · artifacts 行数" },
  honest: ["本面**只覆盖已登记提取的源**", "台账记的是写入记录，不是快照"],
  hint: "",
};

const PACK = {
  schema: "review_pack/2",
  configured: true,
  sid: "dsh:a",
  file_path: "D:\\g\\<img onerror=x>稿.md",
  auto_picked: false,
  target: { n_versions: 2, changed_chars: 200,
            first_ts: "2026-10-04 09:03:29", last_ts: "2026-10-04 09:05:00" },
  versions: [
    { seq: 70, ts: "2026-10-04 09:03:29", tool: "", old_len: 0, new_len: 120,
      old_preview: "", new_preview: "<script>alert(1)</script>第一版" },
    { seq: 71, ts: "2026-10-04 09:04:00", tool: "", old_len: 120, new_len: 80,
      old_preview: "第一版", new_preview: "第二版" },
  ],
  versions_truncated: true,
  revisions: { thresholds: { band: 0.8, dup: 0.9 },
    band: [], turn_hits: [],
    process: { band_pairs: 3, turn_words: 1, band_per_version: 1.5 } },
  timeline: [
    { kind: "打磨候选", ts: "2026-10-04 09:03:40", jaccard: 0.851,
      rowid_a: 44120, rowid_b: 44125, before_version: 70 },
    { kind: "反转候选", ts: "2026-10-04 09:04:50", word: "推翻重来",
      rowid: 44130, before_version: null },
  ],
  errors: { total: 2, groups: [
    { class: "tool_interface", pattern: "FS_STALE_VERSION", n: 2,
      first: { seq: 10, ts: "x", tool: "Edit",
               text: "FS_STALE_VERSION <b>原文</b>", resolved: true } },
    { class: "env", pattern: "SetNamedSecurityInfoW", n: 1,
      first: { seq: 30, ts: "y", tool: "Bash", text: "权限不足",
               resolved: false } }] },
  cards: [
    { path: "kc-1.md", title: "改文件前要重读", type: "pitfall",
      confidence: 0.4, strong: true,
      why: "强命中：这张卡的锚点就指向本会话", anchors: [] },
    { path: "kc-2.md", title: "别的", type: "insight", confidence: 0.5,
      strong: false, why: "弱命中：triggers 里「稿.md」出现在文件名或改动内容里",
      anchors: [] },
  ],
  cards_matched: 2,
  anchors: [
    { kind: "产物", ref: "(sid, seq=70)", note: "台账行" },
    { kind: "打磨候选", ref: "messages.rowid=44120 / 44125", note: "相似度 0.851" },
    { kind: "错误步", ref: "steps.seq=10（工具 Edit）", note: "归一模式 x ×2" },
    { kind: "卡片", ref: "kc-1.md", note: "强命中" },
  ],
  steps: ["① 生成包（只读）", "② 交给任何 Agent 评", "③ 结论回 cards new"],
  honest: ["**「改动之前你在反复打磨」是按时间排的队，不是因果。**",
           "**过程指标不是质量分。**"],
  md: "# 评审包\n\n整段可复制",
};

(async () => {
  /* ---------- 台账 ---------- */
  const out = $("rep-out");
  const clicks = [];
  out._qa["[data-rp-sid]"] = [
    { dataset: { rpSid: "dsh:a", rpFile: "D:\\g\\稿.md" }, onclick: null },
  ];
  renderLedger(LEDGER, out);
  const h = out.innerHTML;

  /* 覆盖度用后端给的**纯文本版**（line_text）：表格区不许出现 markdown 星号，
     否则页面上原样显示 `**`。honest 区是另一段，那边星号是允许的模板记法。 */
  const covZone = h.slice(0, h.indexOf("<table>"));
  check("覆盖度行是纯文本版（表格区不带 ** 星号）",
    covZone.includes("已登记 8 / 总会话 1776") && !covZone.includes("**"));
  check("覆盖度排在台账表之前",
    h.indexOf("已登记 8 / 总会话 1776") < h.indexOf("<table>"));
  check("账目都报出来（含核心/其余两项）",
    h.includes("有写记录的会话 8") && h.includes("文件路径 738") &&
    h.includes("会话×文件 1105") && h.includes("能反查到主题的 1028") &&
    h.includes("写入记录 2109 条") && h.includes("核心产物（改过 3 版以上）") &&
    h.includes("其余 "));
  check("文件路径 esc（<img onerror> 不落地）",
    h.includes("&lt;img onerror=x&gt;") && !h.includes("<img onerror"));
  check("工具名为空时明说「工具名未登记」", h.includes("工具名未登记"));
  check("没有主题的行显示破折号占位",
    h.includes("<span class='sub2'>—</span>"));
  check("截断时报总数（共 1105 行）",
    h.includes("只列了前 2 行 / 共 1105 行"));
  check("「打文件评审包」按钮带 sid 与文件路径",
    h.includes("打文件评审包") && h.includes("data-rp-sid"));
  check("按钮已接线（onclick 是函数）",
    typeof out._qa["[data-rp-sid]"][0].onclick === "function");
  check("候选账目段在（打磨/反转/反复叮嘱）",
    h.includes("打磨候选") && h.includes("113 对") &&
    h.includes("反复叮嘱候选") && h.includes("19 组"));
  check("口径与来源逐条透出",
    h.includes("该文件在这一会话里被写/改的版数") &&
    h.includes("artifacts_meta.db · artifacts 行数"));
  check("别把这面读过头逐条透出", h.includes("不是快照"));

  /* ---------- 主题视角（默认口径，用户 2026-10-26 反馈的那条）---------- */
  const TL = {
    schema: "artifact_ledger/2", configured: true, group: "topic",
    coverage: LEDGER.coverage,
    totals: { sessions_registered: 8, files: 738, pairs: 1105,
              pairs_in_topics: 1028, versions: 2109, changed_chars: 4310213,
              core_pairs: 111, bulk_pairs: 994 },
    rows: LEDGER.rows, rows_total: 1105, truncated: false,
    groups: [{
      kind: "topic", topic_id: "tp-1", topic_name: "主题甲 <img onerror=x>",
      sessions: 6, files: 660, pairs: 1027, versions: 1644,
      changed_chars: 4018535, sids: ["dsh:a", "dsh:b"],
      buckets: { core: { pairs: 72, versions: 652 }, few: { pairs: 37 },
                 once: { pairs: 918, versions: 918 } },
      top_dirs: [{ dir: "git/specSkill/", files: 300, versions: 710 },
                 { dir: "specSkill/skillverify/", files: 90, versions: 198 }],
      first_ts: "2026-10-04 09:00:00", last_ts: "2026-10-16 18:00:00",
      core_files: [{ file_path: "a.md", file_short: "a.md", n_versions: 129,
                     changed_chars: 900, sessions: ["dsh:a", "dsh:b"],
                     first_ts: "2026-10-04 09:00:00",
                     last_ts: "2026-10-04 10:00:00" }],
    }, {
      kind: "unfiled", topic_id: "", topic_name: "（未归主题）", sessions: 1,
      files: 77, pairs: 77, versions: 463, changed_chars: 260663,
      sids: ["dsh:c"], note: "这些会话还没注册进任何主题……",
      buckets: { core: { pairs: 39 }, few: { pairs: 0 }, once: { pairs: 38 } },
      top_dirs: [{ dir: "tests/", files: 20, versions: 173 }],
      first_ts: "2026-10-05 09:00:00", last_ts: "2026-10-08 09:00:00",
      core_files: [],
    }],
    groups_total: 2,
    empty_groups: [{ id: "tp-9", name: "空主题", members_registered: 0 }],
    candidates: LEDGER.candidates, definitions: LEDGER.definitions,
    sources: LEDGER.sources, honest: LEDGER.honest, hint: "",
  };
  const out2 = mkEl("#led2");
  out2._qa["[data-tp-topic]"] = [
    { dataset: { tpTopic: "tp-1", tpName: "主题甲" }, onclick: null },
  ];
  renderLedger(TL, out2);
  const t2 = out2.innerHTML;
  check("主题视角默认出「主题」表头（不是文件表）",
    t2.includes("<th>主题</th>") && !t2.includes("<th>文件</th><th>主题</th>"));
  check("主题视角不渲染文件行（rows 不进页面）",
    !t2.includes("<img") && !t2.includes("<td title=\"D:"),
    "主题视角只出分批行；文件行是 file 视角的事");
  check("主题名里的 HTML 被 esc",
    t2.includes("&lt;img onerror=x&gt;") && !t2.includes("<img onerror=x>"));
  check("主题行四个数字都在（会话/文件/版数/核心）",
    t2.includes(">6<") && t2.includes(">660<") && t2.includes(">1644<") &&
    t2.includes(">72<"));
  check("「打主题评审包」按钮带 topic_id",
    t2.includes("打主题评审包") && t2.includes('data-tp-topic="tp-1"'));
  check("按钮已接线（onclick 是函数）",
    typeof out2._qa["[data-tp-topic]"][0].onclick === "function");
  check("未归主题行标出来了", t2.includes("没注册进主题的会话"));
  check("空主题折成一句（不占一行空表）",
    t2.includes("另有 1 个主题") && t2.includes("空主题") &&
    t2.includes("不代表这些主题没发生过工作"));
  check("三个视角切换按钮都在，当前视角点亮",
    t2.includes("data-ledger-group='topic'") &&
    t2.includes("data-ledger-group='file'") &&
    t2.includes("data-ledger-group='session'"));

  /* 未配置：只给 hint，不出表 */
  renderLedger({ schema: "artifact_ledger/2", configured: false,
                 coverage: LEDGER.coverage, totals: {}, rows: [],
                 hint: "先跑 python -m harvester artifacts extract --sid x" }, out);
  check("未配置时只显示 hint、不出表",
    out.innerHTML.includes("artifacts extract") &&
    !out.innerHTML.includes("<table>"));

  /* ---------- 主题评审包 ---------- */
  const TPACK = {
    schema: "review_pack/2", kind: "topic", configured: true,
    topic: { id: "tp-1", name: "主题甲", members: 191 },
    target: { sessions: 6, files: 660, pairs: 1027, versions: 1644,
              changed_chars: 4018535, core_pairs: 46, bulk_files: 622,
              batches: 6 },
    batches: [{ sid: "dsh:a", short: "…-a141", files: 401, versions: 641,
                core: 25, once: 367, changed_chars: 100,
                first_ts: "2026-10-04 09:00:00",
                last_ts: "2026-10-05 09:00:00",
                errors: { total: 22, groups: [] }, band_pairs: 1,
                turn_words: 0 }],
    core_files: [{ file_path: "D:////g////稿.md", file_short: "稿.md",
                   n_versions: 129, changed_chars: 900, n_sessions: 2,
                   short: "…-a141、…-9404",
                   first_ts: "2026-10-04 09:00:00",
                   last_ts: "2026-10-04 10:00:00" }],
    core_total: 46,
    bulk: { files: 622, pairs: 900, versions: 992,
            top_dirs: [{ dir: "git/specSkill/", files: 300, versions: 710 }],
            sample: ["<b>temp.py</b>"] },
    errors: { total: 1, groups: [{ class: "tool_interface",
      pattern: "FS_STALE_VERSION", n: 35,
      first: { seq: 3, ts: "", tool: "Edit", text: "<i>x</i>", resolved: false } }] },
    revisions: { thresholds: {}, band: [], turn_hits: [] },
    cards: [], anchors: [], steps: ["① 生成包"],
    definitions: { files: "文件数" }, sources: { files: "artifacts" },
    honest: ["**这个包只摊开「核心产物」。**"], hint: "",
    md: "# 评审包（主题）",
  };
  const box2 = mkEl("#rp-box2");
  renderTopicPack(TPACK, box2);
  const q = box2.innerHTML;
  check("主题包标题带主题名与「主题」二字",
    q.includes("评审包（主题）") && q.includes("主题甲"));
  check("分批概览在（会话短名 + 文件/版数/核心）",
    q.includes("…-a141") && q.includes(">401<") && q.includes(">641<"));
  check("核心产物按文件一条（129 版）+ 标几个会话改过",
    q.includes(">129<") && q.includes("2 个"));
  check("「其余」只给计数不给文件行",
    q.includes("622 个文件") && q.includes("只计数"));
  check("其余样例里的 HTML 被 esc",
    q.includes("&lt;b&gt;temp.py&lt;/b&gt;") && !q.includes("<b>temp.py</b>"));
  check("核心产物超过摊开数时说清「只摊开前 N 条」",
    q.includes("只摊开前 1 条"));
  check("错误原文里的 HTML 被 esc",
    q.includes("&lt;i&gt;x&lt;/i&gt;") && !q.includes("<i>x</i>"));
  check("没卡片时说明 triggers 没填会漏",
    q.includes("triggers") && q.includes("建卡与触发词"));
  check("主题包复制按钮已接线", (() => {
    const cb = box2.querySelector("#rp-copy");
    if (typeof cb.onclick !== "function") return false;
    cb.onclick();
    return copied && copied.text === "# 评审包（主题）";
  })());
  /* 空主题包：只给 hint */
  const box3 = mkEl("#rp-box3");
  renderTopicPack({ schema: "review_pack/2", kind: "topic", configured: true,
                    topic: { id: "tp-9", name: "空主题" }, target: {},
                    hint: "这个主题在台账里一条产物都没有" }, box3);
  check("空主题包只给 hint、不出表",
    box3.innerHTML.includes("一条产物都没有") &&
    !box3.innerHTML.includes("<table>"));

  /* ---------- 评审包 ---------- */
  const box = mkEl("#rp-box");
  renderReviewPack(PACK, box);
  const p = box.innerHTML;

  check("版本序列逐条在（含 seq 与时间）",
    p.includes("#70") && p.includes("2026-10-04 09:03:29") &&
    p.includes("#71"));
  check("版本里的 HTML esc（<script> 不落地）",
    p.includes("&lt;script&gt;alert(1)&lt;/script&gt;") &&
    !p.includes("<script>alert(1)"));
  check("版本被截断时提示全量命令",
    p.includes("artifacts show --sid"));
  check("过程指标三个数都在",
    p.includes("3 对") && p.includes("1 轮") && p.includes("平均磨 1.5 轮"));
  check("时间对位两行都在（打磨 + 反转）",
    p.includes("相似度 0.851") && p.includes("推翻重来"));
  check("归不到版本的候选标破折号，不乱指一个版本号",
    p.includes("<td>—</td>"));
  check("措辞纪律：没有写成因果",
    !p.includes("因为") && p.includes("不是因果") && p.includes("不是质量分"));
  check("错误组按归一模式聚并带首次原文（原文里的 HTML 被 esc）",
    p.includes("FS_STALE_VERSION") && p.includes("×2") &&
    p.includes("&lt;b&gt;原文&lt;/b&gt;") && !p.includes("<b>原文</b>"));
  check("有重试 / 放弃两态都渲染",
    p.includes("有") && p.includes("没有（放弃）"));
  check("卡片强命中与弱命中都标了原因",
    p.includes("强命中") && p.includes("弱命中") && p.includes("稿.md"));
  check("锚点五类里的样本都在",
    p.includes("(sid, seq=70)") && p.includes("messages.rowid=44120") &&
    p.includes("steps.seq=10") && p.includes("kc-1.md"));
  check("三步流在", p.includes("1 版本序列") && p.includes("6 怎么用（三步）"));
  check("复制按钮已接线", (() => {
    const cb = box.querySelector("#rp-copy");
    if (typeof cb.onclick !== "function") return false;
    cb.onclick();
    return copied && copied.text === PACK.md;
  })());

  /* 空包：只给 hint */
  renderReviewPack({ configured: true, versions: [], hint: "这个会话没有写入记录" },
    box);
  check("空包只给 hint、不出表",
    box.innerHTML.includes("这个会话没有写入记录") &&
    !box.innerHTML.includes("<table>"));

  console.log(failed ? "\nFAILED: " + failed : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
