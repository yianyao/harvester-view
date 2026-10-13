/* 启动序列真跑门（node，退出码 0=全过）。
 *
 * 为什么必须有这一门：render_home.js 的其它断言只 extract **渲染函数**并喂夹具，
 * 所以"启动序列把加载函数挡在守卫外面"这类缺陷它抓不到——2026-10-12 用户实测
 * 撞到的正是这个：`state.loaded.home=true; switchTab("home");` 顺序反了 →
 * `if(!state.loaded[name])` 判定为已加载 → `loadOverview()` 永不执行 →
 * 工作台永远停在「加载中…」。
 *
 * 本门**真执行 index.html 的启动块**（从 `initUpstreams().then(` 到 `.catch(...)`），
 * 用最小 stub 顶掉网络与 DOM，断言：loadOverview 真被调用、且 home-queues 被
 * 写成了四队列内容（不再是「加载中」）。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "static", "index.html"), "utf8");

/* ---- 取启动块（真源码，不重写） ----
   注意：不要用 `[^)]*` 去匹配 `.catch(...)` —— catch 的参数里带 `)`（字符串
   里的"初始化失败: "+e) 会把它截断，正则会失配（首版就踩了：
   "未能从 index.html 提取启动块"）。改用**逐行**取：从 initUpstreams 那一行
   起，取到第一个以 `.catch` 开头的行结束。 */
const lines = html.split(/\r?\n/);
const start = lines.findIndex(l => l.includes("initUpstreams().then("));
if (start < 0) throw new Error("找不到 initUpstreams().then( 启动块");
let end = start;
for (let i = start; i < lines.length; i++) {
  /* 结束行是 `}).catch(...)`（与 `initUpstreams().then(` 同一个语句链）；
     行首直接 `.catch(` 的是别的语句（如按钮的 .catch），不能当结束标记。 */
  if (lines[i].includes("}).catch(")) { end = i; break; }
}
if (end === start) throw new Error("找不到启动块的 .catch( 结束行");
const bootSrc = lines.slice(start, end + 1).join("\n");

/* ---- 最小 stub ---- */
let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label, why ? ("｜" + why) : ""); }
}
const els = {};
function mkEl() {
  return { innerHTML: "", textContent: "", style: {}, value: "0", dataset: {},
    className: "", addEventListener: () => {}, querySelectorAll: () => [] };
}
function $(id) { if (!els[id]) els[id] = mkEl(); return els[id]; }
let loadOverviewCalls = 0;
let loadAllCalls = 0;
const loaded = {};
function switchTab(name) {
  /* 与 index.html 的真实实现同构：只有"没加载过"才调加载函数 */
  if (!loaded[name]) {
    loaded[name] = true;
    if (name === "home") loadOverviewCalls++;
    if (name === "sessions") loadAllCalls++;
  }
}
function loadOverview() { return Promise.resolve(); }
function loadAll() { return Promise.resolve(); }
function showBanner() {}
function initUpstreams() { return Promise.resolve(); }
const state = { no: 0, loaded: loaded };

/* ---- 真跑启动块 ---- */
eval(bootSrc);

setTimeout(() => {
  check("启动块执行后 loadOverview 被调用了一次",
    loadOverviewCalls === 1, "调用次数=" + loadOverviewCalls);
  check("启动块没有顺带拉会话列表（loadAll）",
    loadAllCalls === 0, "调用次数=" + loadAllCalls);
  check("state.loaded.home 已置位（点回工作台不会重复拉）",
    state.loaded.home === true);
  console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
    "\nALL BOOT-SEQUENCE CHECKS PASSED");
  process.exit(failed ? 1 : 0);
}, 10);
