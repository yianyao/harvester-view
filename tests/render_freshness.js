/* v0.81 P1-4 渲染门：库龄 / STALE 提示（node 运行，退出码 0=全过）。
 *
 * 纪律同 render_harness：从 index.html 提取**真源码**执行。
 * 本块的验收红线（报告 P1-4「库半个月没同步而看板照常渲染，是慢性误导」）：
 *  - **"没判定"（null）绝不画成"新鲜✓"**——未开 --corpus-watermark 或库里
 *    没水位记录时，页脚与新鲜度条都不许出现「一致 / 新鲜」字样；
 *  - stale=true 时报 STALE；stale=false 才报「一致」；
 *  - 库龄基准**优先入库水位**，退让到库文件 mtime 时必须写明"可能偏旧"；
 *  - 库龄过阈值（STALE_AGE_DAYS=7）→ 新鲜度条转黄并给出人话理由；
 *  - 时间戳一律 esc（恶意串不落地）。
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

function mkEl(id) {
  const el = { id, innerHTML: "", textContent: "", style: {}, disabled: false,
    dataset: {}, className: "", _qa: {}, value: "", checked: false,
    querySelector(sel) { return mkEl(id + ">" + sel); },
    querySelectorAll(sel) { return el._qa[sel] || []; } };
  return el;
}
const els = {};
function $(id) { if (!els[id]) els[id] = mkEl("#" + id); return els[id]; }

/* 依赖桩 */
const STALE_AGE_DAYS = 7;   /* 与页面常量同名同值（门里再写一份是刻意的：
                               页面改了阈值而门没跟着改 → 这里会红） */
const state = { fresh: null };
eval(extract("fpAgeDays", "fp"));
eval(extract("ageHuman", "d"));
eval(extract("homeFootHtml", "d"));
eval(extract("freshKey", "fp"));
eval(extract("renderFreshBar", ""));

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

const NOW = Date.now();
function tsAgo(days) {
  const d = new Date(NOW - days * 86400000);
  const p = n => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
    " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

/* ---- 真形载荷 ---- */
const FP = { sessions: 1720, steps: 70864, errors: 362,
             db_mtime: tsAgo(10), generated_at: tsAgo(0) };

(async () => {
  /* ① 库龄：基准=入库水位 */
  check("fpAgeDays 优先取入库水位",
    Math.abs(fpAgeDays({ corpus_indexed_mtime: tsAgo(3), db_mtime: tsAgo(30) })
      - 3) < 0.01);
  check("无水位时退让到库文件 mtime",
    Math.abs(fpAgeDays({ db_mtime: tsAgo(30) }) - 30) < 0.01);
  check("什么都没有 → null（不造数）", fpAgeDays({}) === null);
  check("坏串 → null", fpAgeDays({ db_mtime: "not-a-time" }) === null);

  /* ② 人话：不足 1 天说小时，避免「0.0 天」看着像没算 */
  check("0.5 天说小时", ageHuman(0.5).indexOf("小时") >= 0);
  check("3.25 天保留一位", ageHuman(3.25) === "3.3 天");
  check("20 天取整", ageHuman(20) === "20 天");
  check("非法值说「—」", ageHuman(null) === "—");

  /* ③ stale=true → 页脚报 STALE，且带入库水位与库龄 */
  const hot = { db_fingerprint: Object.assign({}, FP,
      { corpus_indexed_mtime: tsAgo(5), stale: true }),
    freshness: { corpus_indexed_mtime: tsAgo(5), stale: true, age_days: 5.0,
      age_basis: "corpus_indexed_mtime",
      note: "语料比入库水位新（当前语料 X > 入库 Y）——有会话还没收割进库，跑一次 update。" } };
  let h = homeFootHtml(hot);
  check("stale=true 报 STALE", h.includes("⚠ STALE 语料比库新"));
  check("带入库水位", h.includes("入库水位 " + tsAgo(5)));
  check("带库龄与基准", h.includes("库龄 5.0 天") && h.includes("基准=入库水位"));
  check("带服务端给的说明", h.includes("跑一次 update"));
  check("stale=true 时不说「一致」", !h.includes("语料与入库水位一致"));

  /* ④ stale=false → 报一致 */
  const ok = { db_fingerprint: Object.assign({}, FP,
      { corpus_indexed_mtime: tsAgo(2), stale: false }),
    freshness: { corpus_indexed_mtime: tsAgo(2), stale: false, age_days: 2.0,
      age_basis: "corpus_indexed_mtime", note: "语料与入库水位一致，库是新鲜的。" } };
  h = homeFootHtml(ok);
  check("stale=false 报一致", h.includes("✓ 语料与入库水位一致"));
  check("stale=false 不报 STALE", !h.includes("⚠ STALE"));

  /* ⑤ stale=null（没判）→ **既不报 STALE 也不报一致**，只说明原因 */
  for (const note of ["未做陈旧判定：起服时没加 --corpus-watermark（判定要 stat "
        + "一遍语料盘，默认关）。这是「没判」，不是「新鲜」。",
      "库里还没有入库水位记录（跑一次 update 才会记），无法判定陈旧——同样是"
        + "「没判」，不是「新鲜」。"]) {
    const un = { db_fingerprint: Object.assign({}, FP, { stale: null }),
      freshness: { stale: null, age_days: null, age_basis: null, note: note } };
    h = homeFootHtml(un);
    check("没判定时不画成新鲜（" + note.slice(0, 10) + "…）",
      !h.includes("语料与入库水位一致") && !h.includes("⚠ STALE") &&
      h.includes(note));
  }

  /* ⑥ 退让基准必须写明「可能偏旧」 */
  const fall = { db_fingerprint: Object.assign({}, FP, { stale: null }),
    freshness: { db_mtime: tsAgo(10), stale: null, age_days: 10.0,
      age_basis: "db_mtime", note: "库里还没有入库水位记录。" } };
  h = homeFootHtml(fall);
  check("退让到 db_mtime 时写明可能偏旧",
    h.includes("库文件 mtime，无水位记录，可能偏旧"));

  /* ⑦ esc：时间戳里的恶意串不落地 */
  const evil = { db_fingerprint: Object.assign({}, FP,
      { db_mtime: "<img src=x onerror=alert(1)>", stale: true }),
    freshness: { corpus_indexed_mtime: "<b>x</b>", stale: true, age_days: 1,
      age_basis: "corpus_indexed_mtime", note: "<script>bad</script>" } };
  h = homeFootHtml(evil);
  check("时间戳 esc（恶意串不落地）",
    h.includes("&lt;img src=x onerror=alert(1)&gt;") &&
    !h.includes("<img src=x") && !h.includes("<script>bad"));

  /* ⑧ 新鲜度条：库龄过 7 天转黄并给理由 */
  const bar = $("freshbar");
  state.fresh = { base: Object.assign({}, FP,
                    { corpus_indexed_mtime: tsAgo(20) }),
                  cur: Object.assign({}, FP,
                    { corpus_indexed_mtime: tsAgo(20) }),
                  corpusStale: false };
  renderFreshBar();
  check("库龄 20 天 → 条转黄（stale 类）", bar.className === "stale");
  check("库龄 20 天 → 给出人话理由",
    bar.innerHTML.includes("未同步") && bar.innerHTML.includes("20 天"));
  check("条上带库龄与基准",
    bar.innerHTML.includes("库龄 20 天") &&
    bar.innerHTML.includes("基准=入库水位"));

  /* ⑨ 库龄 2 天 → 不黄，说指纹一致 */
  state.fresh = { base: Object.assign({}, FP,
                    { corpus_indexed_mtime: tsAgo(2) }),
                  cur: Object.assign({}, FP,
                    { corpus_indexed_mtime: tsAgo(2) }),
                  corpusStale: false };
  renderFreshBar();
  check("库龄 2 天 → 不黄", bar.className !== "stale");
  check("库龄 2 天 → 指纹一致", bar.innerHTML.includes("指纹一致"));

  /* ⑩ 指纹变化优先于库龄（库已更新 = 更该先说的事） */
  state.fresh = { base: Object.assign({}, FP, { sessions: 1000,
                    corpus_indexed_mtime: tsAgo(2) }),
                  cur: Object.assign({}, FP, { sessions: 1720,
                    corpus_indexed_mtime: tsAgo(2) }),
                  corpusStale: false };
  renderFreshBar();
  check("指纹变化 → 说库已更新",
    bar.innerHTML.includes("库已更新") && bar.innerHTML.includes("1000"));

  /* ⑪ corpusStale=true → 说语料比库新（且优先于库龄理由） */
  state.fresh = { base: Object.assign({}, FP,
                    { corpus_indexed_mtime: tsAgo(20) }),
                  cur: Object.assign({}, FP,
                    { corpus_indexed_mtime: tsAgo(20) }),
                  corpusStale: true };
  renderFreshBar();
  check("corpusStale → 说语料比库新",
    bar.innerHTML.includes("语料比库新") && bar.className === "stale");

  console.log(failed ? "\nFAILED: " + failed : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("GATE CRASH:", e); process.exit(1); });
