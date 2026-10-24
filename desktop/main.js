/*
 * main.js -- harvester-view 桌面壳（Electron，方案 C / 2026-10-20 用户裁决
 * 「桌面应用用方案C」）。
 *
 * 职责：一个窗口 + 三个隐藏服务（api-serve 8765 / actionsserve 8766 /
 * view 8088）的完整生命周期。取代 start-viewv2.cmd 的三个黑窗口：
 *   - spawn 时 windowsHide:true，不再有控制台窗口；
 *   - 端口已被监听则复用（服务已在跑就不重复起）；
 *   - 应用退出时 taskkill /T /F 树杀自己拉起的服务（含孙进程）；
 *   - 服务意外退出 → 对话框 fail loud（不静默残废）。
 * 服务命令行与 start-viewv2.cmd 逐条一致（唯一真源），解释器探测同序：
 * py -3 → python → %USERPROFILE%\.workbuddy\...\python.exe。
 *
 * 开发运行：cd desktop && npm install（npmmirror 镜像）&& npm start
 */
const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn, spawnSync, execFile } = require("child_process");
const net = require("net");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..");            // harvester-view/
const SRV = path.resolve(ROOT, "..", "session-harvester");
const VIEW_PORT = 8088, ACTIONS_PORT = 8766, API_PORT = 8765;
/* 目录改向口：与 start-viewv2.cmd 的 `if not defined ...` 同义（迁移时
 * set CARDS_ROOT=... 即可，不必改本文件）。 */
const CARDS_ROOT = process.env.CARDS_ROOT || path.join(SRV, "knowledge", "cards");
const CHAIN_ROOT = process.env.CHAIN_ROOT || path.join(SRV, "knowledge", "topics");
const BUNDLED_PY = path.join(process.env.USERPROFILE || "",
  ".workbuddy", "binaries", "python", "envs", "default",
  "Scripts", "python.exe");

let win = null;
const kids = [];          // 我拉起的服务进程（退出时树杀）

/* ---- 解释器探测（与 start-viewv2.cmd 同序：py -3 → python → bundled） ---- */
function pyVersionOk(exe, prefix) {
  try {
    const r = spawnSync(exe, [...prefix, "-c",
      "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
      { timeout: 8000, windowsHide: true });
    return r.status === 0;
  } catch (_) { return false; }
}
function pyPick() {
  const py = shellPath("py.exe");
  if (py && pyVersionOk(py, ["-3"])) return { exe: py, prefix: ["-3"] };
  const pp = shellPath("python.exe");
  if (pp && pyVersionOk(pp, [])) return { exe: pp, prefix: [] };
  if (fs.existsSync(BUNDLED_PY)) return { exe: BUNDLED_PY, prefix: [] };
  return { exe: "python", prefix: [] };   // 兜底交给 PATH，起不来走 fail loud
}
function shellPath(exe) {
  for (const dir of (process.env.PATH || "").split(";")) {
    if (!dir) continue;
    const p = path.join(dir.trim(), exe);
    try { if (fs.existsSync(p)) return p; } catch (_) { /* ignore */ }
  }
  return null;
}

/* ---- 端口探测：已监听=复用 ---- */
function portUp(port, timeoutMs = 700) {
  return new Promise((resolve) => {
    const s = net.connect({ port, host: "127.0.0.1" });
    const done = (ok) => { try { s.destroy(); } catch (_) {} resolve(ok); };
    s.setTimeout(timeoutMs, () => done(false));
    s.once("connect", () => done(true));
    s.once("error", () => done(false));
  });
}

/* ---- 服务清单（与 start-viewv2.cmd 逐条一致；门：harvester-view/tests/
 * test_v41_suppress_view.py::test_desktop_shell_matches_start_script） ---- */
function services() {
  const { exe, prefix } = pyPick();
  return [
    { port: API_PORT, name: "api-serve", cwd: SRV, args: [
      ...prefix, "-m", "harvester", "api-serve",
      "--db", path.join(SRV, "harvester.db"),
      "--cards-root", CARDS_ROOT,
      "--topics-meta", path.join(SRV, "topics_meta.db"),
      "--keywords-meta", path.join(SRV, "keywords_meta.db"),
      "--suggestions-meta", path.join(SRV, "suggestions_meta.db"),
      "--chain-root", CHAIN_ROOT,
      "--reviews-meta", path.join(SRV, "cards_meta.db"),
      "--corpus-watermark"] },
    { port: ACTIONS_PORT, name: "actions-serve", cwd: ROOT, args: [
      ...prefix, "-m", "actions.actionsserve",
      "--db", path.join(SRV, "harvester.db"),
      "--cards-root", CARDS_ROOT,
      "--topics-meta", path.join(SRV, "topics_meta.db"),
      "--artifacts-meta", path.join(SRV, "artifacts_meta.db"),
      "--keywords-meta", path.join(SRV, "keywords_meta.db"),
      "--suggestions-meta", path.join(SRV, "suggestions_meta.db"),
      "--chain-root", CHAIN_ROOT,
      "--exports-root", path.join(SRV, "exports"),
      "--reviews-meta", path.join(SRV, "cards_meta.db"),
      "--corpus-watermark"] },
    { port: VIEW_PORT, name: "view", cwd: ROOT, args: [
      ...prefix, "view.py", "--no-open"] },
  ].map(s => ({ ...s, exe }));
}

function startServices() {
  for (const s of services()) {
    // 异步探测：端口已 UP=复用（别的窗口已在跑），否则隐藏窗口拉起
    portUp(s.port).then((up) => {
      if (up) { log(`[reuse] ${s.name} :${s.port} 已在运行，复用`); return; }
      const kid = spawn(s.exe, s.args, {
        cwd: s.cwd, windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
      kids.push(kid);
      kid.stdout.on("data", () => {});          // 服务日志不刷屏；崩了走 exit
      kid.stderr.on("data", (b) => log(`[${s.name}] ${b}`));
      kid.once("exit", (code) => {
        if (!app.isQuitting && code !== 0) {
          failLoud(`${s.name} 意外退出（code=${code}）。` +
            "窗口还能看，但该服务功能不可用——重启本应用，或回到" +
            "start-viewv2.cmd 方式并查看其控制台。");
        }
      });
      log(`[start] ${s.name} :${s.port} pid=${kid.pid}`);
    });
  }
}

function log(msg) { console.log(msg); }

function failLoud(text) {
  log("[FAIL] " + text);
  dialog.showErrorBox("harvester-view 服务异常", text);
}

/* ---- 等页面就绪再开窗（复刻 cmd 的 3 秒等待，但真探测） ---- */
async function waitView(tries = 40, gapMs = 500) {
  for (let i = 0; i < tries; i++) {
    if (await portUp(VIEW_PORT, 400)) return true;
    await new Promise(r => setTimeout(r, gapMs));
  }
  return false;
}

function treeKill(pid) {
  // Windows 树杀（python -m 可能派生子进程）；非 Windows 简单 kill
  if (process.platform === "win32") {
    execFile("taskkill", ["/pid", String(pid), "/T", "/F"], () => {});
  } else {
    try { process.kill(pid); } catch (_) {}
  }
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360, height: 900,
    title: "harvester-view",
    autoHideMenuBar: true,
    backgroundColor: "#14171c",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.loadURL(`http://127.0.0.1:${VIEW_PORT}/`);
  // 页面里的 window.open（外部链接/原文跳转）一律走系统默认浏览器
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  win.on("closed", () => { win = null; });
}

/* ---- 单实例锁：二次启动只聚焦已有窗口 ---- */
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
  });

  app.whenReady().then(async () => {
    if (!fs.existsSync(path.join(SRV, "harvester.db"))) {
      failLoud(`数据库不存在：${path.join(SRV, "harvester.db")}\n` +
        "（桌面壳假定仓库布局与 start-viewv2.cmd 相同）");
      app.quit();
      return;
    }
    startServices();
    const up = await waitView();
    if (!up) {
      failLoud(`view 服务未在 ${VIEW_PORT} 端口就绪——查 python 环境` +
        "（需要 3.10+）。");
      app.quit();
      return;
    }
    createWindow();
  });

  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => { app.isQuitting = true; });
  app.on("quit", () => {
    // 只杀自己拉起的进程（复用的服务留给它的主人）
    for (const k of kids) { try { treeKill(k.pid); } catch (_) {} }
  });
}
