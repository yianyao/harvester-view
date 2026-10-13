/* P5-1 五页引导「5 分钟之旅」渲染门（node 运行，退出码 0=全过）。
 *
 * 为什么单独一个门：引导的验收是「新手未读文档能跟随五页完成一次完整闭环
 * 浏览」——这要求五页脚本本身把闭环故事线讲完整（错误→原文→建议→裁决→
 * A/B 动作），且引导只能**导航与讲解**，绝不能代执行任何命令（红线：
 * view 只读不执行）。别的门 extract 的是渲染函数，引导这块没人看。
 *
 * 断言：
 *  - 五页 tab 序列恰好 home→reports→sessions→work→cmd（闭环动线）；
 *  - 每步 name/title/body/todo 四件套齐全，故事线覆盖「Edit 前先 Read」
 *    真实班规案例（SOP P5-1 原文口径）；
 *  - tourRender 真渲染：进度「第 1/5」、标题、这一步做什么；
 *  - tourNav 真走：下一步 → switchTab 到下一页；走完 → 收卡 + 置 seen；
 *  - 引导源码切片里**没有** fetch / api/actions / copyText —— 红线：
 *    引导只导航不执行（乱点不可逆）；
 *  - esc 纪律：title/body/todo 进 innerHTML 必须过 esc；
 *  - 启动块挂了 maybeAutoTour（首次访问自动开引导）。
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

/* 引导源码切片（从 section 注释到 end 注释）——做红线静态断言用 */
const TOUR_MARK = "===== P5-1 五页引导（tour） =====";
const iTour = html.indexOf(TOUR_MARK);
if (iTour < 0) throw new Error("找不到引导 section 注释 " + TOUR_MARK);
const iTourEnd = html.indexOf("===== P5-1 tour end =====", iTour);
if (iTourEnd < 0) throw new Error("找不到引导 section 结束注释");
const tourSrc = html.slice(iTour, iTourEnd);

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
const tabCalls = [];
function switchTab(n) { tabCalls.push(n); }
function showBanner(k, m) {}
const state = { tour: null };

for (const [n, sig] of [
  ["tourSteps", ""], ["tourRender", ""], ["tourNav", "d"],
  ["tourStart", ""], ["tourEnd", "done"],
]) {
  eval(extract(n, sig));
}

let failed = 0;
function check(label, cond, why) {
  if (cond) { console.log("ok -", label); }
  else { failed++; console.error("FAIL -", label + (why ? ("\n      " + why) : "")); }
}

/* ① 五页脚本：tab 序列 = 闭环动线；四件套齐全 */
const steps = tourSteps();
check("恰好五页", steps.length === 5, "实际 " + steps.length);
check("tab 序列 = 工作台→报告→会话→处理单→命令台（闭环动线）",
  JSON.stringify(steps.map(s => s.tab)) ===
    JSON.stringify(["home", "reports", "sessions", "work", "cmd"]),
  "实际 " + JSON.stringify(steps.map(s => s.tab)));
check("每步 name/title/body/todo 四件套齐全且非空",
  steps.every(s => s.name && s.title && s.body && s.todo),
  "缺件: " + JSON.stringify(steps.map(s =>
    ["name", "title", "body", "todo"].filter(k => !s[k]))));

/* ② 故事线完整性（SOP P5-1 原文口径：Edit 前先 Read 真实班规闭环：
   112 次错误→建议→采纳→A/B）——数字写「上百次」防漂移，案例名必须点名 */
const story = steps.map(s => (s.title + s.body + s.todo)).join("\n");
for (const [label, kw] of [
  ["真实班规案例「Edit 前先 Read」被点名", "Edit 前先 Read"],
  ["第一段：错误被看见（机械事实）", "错误"],
  ["第二段：回原文核实（候选必须对得上原文）", "原文"],
  ["第三段：建议与裁决（AI 判断 / 人的裁决）", "裁决"],
  ["第四段：A 档 / B 档动作闭环", "B 档"],
  ["第五段：采纳后的效果验证", "验证"],
  ["建议池有归宿（班规条目 / 问题清单）", "归宿"],
  ["台账留痕（永不静默）", "台账"],
]) {
  check("故事线：" + label, story.includes(kw), "缺关键词「" + kw + "」");
}
check("不写死会漂移的数字（用「上百次」指路，真数字去页面看）",
  story.includes("上百次") && !/1 ?1 ?2 ?次|112次/.test(story));

/* ③ tourRender 真渲染 */
state.tour = { i: 0 };
tourRender();
const c1 = $("tour").innerHTML;
check("卡片渲染出进度「第 1/5 页」", c1.includes("第 1/5 页"));
check("卡片渲染出当前页标题", c1.includes(esc(steps[0].title)));
check("卡片渲染出「这一步做什么」", c1.includes("这一步做什么") &&
  c1.includes(esc(steps[0].todo)));
check("首步没有「上一步」按钮", !c1.includes("tour-prev"));
check("末步按钮叫「完成」",
  (state.tour = { i: 4 }, tourRender(), $("tour").innerHTML.includes("完成")));
check("非末步按钮指向下一页的名字",
  (state.tour = { i: 0 }, tourRender(),
   $("tour").innerHTML.includes("下一步") &&
   $("tour").innerHTML.includes(esc(steps[1].name))));

/* ④ tourNav 真走：下一步 → switchTab 下一页 */
tabCalls.length = 0;
state.tour = { i: 0 };
tourNav(1);
check("第 1 步点下一步 → switchTab 到第 2 页（reports）",
  tabCalls.length === 1 && tabCalls[0] === "reports" && state.tour.i === 1,
  "tabCalls=" + JSON.stringify(tabCalls));
tabCalls.length = 0;
state.tour = { i: 0 };
tourNav(-1);
check("第 1 步点上一步不越界（留在第 1 页）",
  tabCalls.length === 0 && state.tour.i === 0);

/* ⑤ 走完 → 收卡 + seen 落盘（真实班规闭环完成才算 seen 也行，这里
   按最简口径：任何出口都落 seen，避免反复打扰） */
tabCalls.length = 0;
state.tour = { i: 4 };
tourNav(1);
check("末步点完成 → 收卡（隐藏 + 清空 + state 复位）",
  $("tour").style.display === "none" && $("tour").innerHTML === "" &&
  state.tour === null);

/* ⑥ 红线：引导只导航，绝不执行 */
check("引导源码无 fetch（不拉动作面）", !tourSrc.includes("fetch("),
  "引导只该 switchTab 导航，不该自己发请求");
check("引导源码无 api/actions（不碰动作面）", !tourSrc.includes("api/actions"));
check("引导源码无 copyText（不复制命令——红线：view 只读不执行）",
  !tourSrc.includes("copyText"));
check("引导源码无 runCmdAction（不代执行命令）",
  !tourSrc.includes("runCmdAction"));
check("title/body/todo 进 innerHTML 全部过 esc",
  tourSrc.includes("esc(st.title)") && tourSrc.includes("esc(st.body)") &&
  tourSrc.includes("esc(st.todo)"),
  "引导文案进 innerHTML 必须 esc（注入纪律）");

/* ⑦ 启动块挂 maybeAutoTour（首次访问自动开引导） */
const bootBlock = (html.match(/initUpstreams\(\)\.then\(\(\)=>\{[\s\S]*?\}\)\.catch/) || [""])[0];
check("启动块调用了 maybeAutoTour()", bootBlock.includes("maybeAutoTour()"));
check("maybeAutoTour 在 switchTab(\"home\") 之后（复用已加载态，不重复拉）",
  bootBlock.indexOf("maybeAutoTour()") > bootBlock.indexOf('switchTab("home")'));
const maybeSrc = (html.match(/^function maybeAutoTour\(\)\{[\s\S]*?^\}/m) || [""])[0];
check("maybeAutoTour 有 localStorage 守卫（seen 过不再自动弹）",
  maybeSrc.includes("hv_tour_seen") && maybeSrc.includes("try"));
check("自动开的是 tourStart（只导航，不带任何动作）",
  maybeSrc.includes("tourStart()"));

console.log(failed ? ("\n" + failed + " CHECK(S) FAILED") :
  "\nALL TOUR CHECKS PASSED");
process.exit(failed ? 1 : 0);
