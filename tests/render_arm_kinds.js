/* 渲染门（第②步）：累计量必须长得"不像警报"，待处理才发黄。
 *
 * 判据在后端 `actions/severity.py`（kind + level），页面只渲染。本门钉住：
 *   stock → class 'arm info' + 「累计」徽章 + 徽章 tooltip 说明"不作警报"；
 *   watch → class 'arm watch'（零星，虚线），**不带**累计徽章；
 *   warn  → class 'arm warn'，不带徽章；
 *   rate  → 「比率」徽章；
 *   旧载荷（无 kind）→ 不造徽章，仍按 level 上色（向后兼容）。
 *
 * 断言先切出 .av 那一小片再查（AGENTS.md §三 15）。
 */
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "static", "index.html"), "utf8");

function extract(name) {
  const m = html.match(new RegExp("^function " + name + "\\([^)]*\\)\\{[\\s\\S]*?^\\}", "m"));
  if (!m) throw new Error("extract fail: " + name);
  return m[0];
}

const esc = s => String(s == null ? "" : s);
eval(extract("armHtml"));

let fail = 0;
const ok = (cond, msg) => {
  if (cond) { console.log("ok - " + msg); } else { fail++; console.log("FAIL - " + msg); }
};

function arm(p) {
  return Object.assign({ value: 0, unit: "条", definition: "口径",
                         source: "src", target: "ring:x", suggestion: "" }, p);
}
/* 切出 .av 那一片（数值 + 徽章都在这儿），后面断言只查它 */
function avSlice(h) {
  const m = h.match(/<div class='av'>[\s\S]*?<\/div>/);
  return m ? m[0] : "";
}

/* ---------- 累计量：532 条错误总数 ---------- */
const stock = armHtml(arm({
  value: 532, kind: "stock", level: "info",
  level_why: "累计量：只说明现在有多少，不等于是待办的活，所以不作警报。",
}));
ok(/class='arm info'/.test(stock), "累计量渲染成 arm info（中性色）");
ok(avSlice(stock).indexOf("累计") >= 0, "累计量带「累计」徽章");
ok(avSlice(stock).indexOf("不作警报") >= 0,
   "徽章 tooltip 说明「不作警报」（不是靠人猜）");
ok(!/class='arm warn'/.test(stock), "累计量不带 warn 类");

/* ---------- 待处理三档 ---------- */
const watch = armHtml(arm({ value: 2, kind: "action", level: "watch" }));
ok(/class='arm watch'/.test(watch), "零星（2 条）渲染成 arm watch（虚线）");
ok(avSlice(watch).indexOf("累计") < 0, "零星卡不带累计徽章");

const warn = armHtml(arm({ value: 3, kind: "action", level: "warn" }));
ok(/class='arm warn'/.test(warn), "待处理（3 条）渲染成 arm warn");
ok(avSlice(warn).indexOf("累计") < 0, "待处理卡不带累计徽章");

/* ---------- 比率 ---------- */
const rate = armHtml(arm({ value: 12, kind: "rate", level: "warn" }));
ok(avSlice(rate).indexOf("比率") >= 0, "比率卡带「比率」徽章");

/* ---------- 旧载荷：无 kind ---------- */
const old = armHtml(arm({ value: 5, level: "warn" }));
ok(/class='arm warn'/.test(old), "旧载荷仍按 level 上色");
ok(avSlice(old).indexOf("累计") < 0 && avSlice(old).indexOf("比率") < 0,
   "旧载荷不造徽章（没有 kind 就不猜）");

/* ---------- 环标题的"有 N 段有积压"只数待处理段 ---------- */
const homeRingsBox = { innerHTML: "" };
const qBox = { innerHTML: "" };
const $ = id => (
  id === "home-rings" ? homeRingsBox
    : (id === "home-qdetail" ? qBox : { innerHTML: "", textContent: "" }));
const bindWorkGo = () => {};
eval(extract("armHtml"));
eval(extract("renderRings"));
renderRings({
  rings: [{
    ring_id: "errors", title: "错误环", definition: "三段",
    arms: [
      arm({ value: 532, kind: "stock", level: "info", unit: "条" }),
      arm({ value: 6, kind: "action", level: "warn", unit: "条" }),
      arm({ value: 2, kind: "action", level: "watch", unit: "条" }),
    ],
  }],
});
const ringHtml = homeRingsBox.innerHTML;
const ringHead = (ringHtml.match(/<div class='rt'>[\s\S]*?<\/div>/) || [""])[0];
/* 2026-10-23 用户口径「不到的就不显示」：积压只数**待处理**（warn）。
   零星(watch) 与累计(stock) 折进 details，不再算积压。 */
ok(ringHead.indexOf("有 1 段有积压") >= 0,
   "环标题只数待处理段（warn）：期望「有 1 段有积压」");
ok(ringHead.indexOf("有 3 段") < 0, "零星/累计段没有被算进积压");
ok(ringHtml.indexOf("class='arm info'") >= 0, "环里累计段仍是 info（只是折进 details）");

/* 折叠是"默认不占视野"，不是"删掉"：待处理在外、其余在 details 里，条数写清 */
const headPart = ringHtml.split("<details class='arms-more'>")[0];
const foldPart = ringHtml.split("<details class='arms-more'>")[1] || "";
ok(headPart.indexOf("class='arm warn'") >= 0, "待处理段直接显示在环上");
ok(headPart.indexOf("class='arm watch'") < 0 && headPart.indexOf("class='arm info'") < 0,
   "零星/累计段不在环面上（未达阈值不显示）");
ok(/另有 2 段未达阈值/.test(foldPart), "折叠摘要写明还有 2 段（不静默消失）");
ok(foldPart.indexOf("class='arm watch'") >= 0 && foldPart.indexOf("class='arm info'") >= 0,
   "点开后能看到同一批卡片（数据没丢）");

/* ---------- 队列明细（处理单/工作台的拆解）同一口径 ---------- */
eval(extract("renderQueueDetail"));
renderQueueDetail({
  queues: [{ queue_id: "repair", label: "待修" }],
  queue_details: {
    repair: [
      arm({ value: 5, kind: "action", level: "warn" }),
      arm({ value: 2, kind: "action", level: "watch" }),
      arm({ value: 38, kind: "stock", level: "info" }),
    ],
  },
});
const qHtml = qBox.innerHTML;
const qHead = qHtml.split("<details class='arms-more'>")[0];
const qFold = qHtml.split("<details class='arms-more'>")[1] || "";
ok(qHead.indexOf("class='arm warn'") >= 0 && qHead.indexOf("待处理 1 项") >= 0,
   "队列拆解只把待处理摆面上，并写明条数");
ok(qHead.indexOf("class='arm watch'") < 0 && qHead.indexOf("class='arm info'") < 0,
   "队列拆解里零星/累计不在面上");
ok(/另有 2 项未达阈值/.test(qFold), "队列拆解折叠摘要写明还有 2 项");
ok(qFold.indexOf("class='arm info'") >= 0, "队列拆解点开仍能看到累计项（没删）");

console.log(fail ? ("FAILED: " + fail) : "ALL ARM-KIND RENDER CHECKS PASSED");
process.exit(fail ? 1 : 0);