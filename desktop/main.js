/*
 * main.js -- harvester-view 桌面壳（Electron，方案 C / 2026-10-20 用户裁决
 * 「桌面应用用方案C」）。
 *
 * 职责：一个窗口 + 三个隐藏服务（api-serve 8765 / actionsserve 8766 /
 * view 8088）的完整生命周期。取代 start-viewv2.cmd 的三个黑窗口：
 *   - spawn 时 windowsHide:true，不再有控制台窗口；
 *   - **启动前先回收端口**（2026-10-26 用户令：「都用桌面应用启动还要先清
 *     端口，这个有点搞笑」）。旧行为是"端口已监听就复用"，于是复用的可能
 *     是我改代码之前起的旧服务——页面是新的、后端是旧的，点了报"注册表
 *     里有、服务里没有"，时好时坏。现在改成：先找占着三个端口、且属于
 *     本项目的进程（python/node/electron + 命令行带本项目特征）树杀，
 *     等端口释放，再起自己的。**别的程序占端口一律不杀**（8088 是 Hadoop/
 *     YARN 的常用端口），走 fail loud 让人自己决定；
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

/* ---- 端口探测：已监听=有人占着（清端口后只剩"外人"会命中） ---- */
function portUp(port, timeoutMs = 700) {
  return new Promise((resolve) => {
    const s = net.connect({ port, host: "127.0.0.1" });
    const done = (ok) => { try { s.destroy(); } catch (_) {} resolve(ok); };
    s.setTimeout(timeoutMs, () => done(false));
    s.once("connect", () => done(true));
    s.once("error", () => done(false));
  });
}

/* ---- 清端口（2026-10-26 用户令）----
 * 两条通道，谁先拿到用谁：
 *   ① 扫全部 python 进程（Get-CimInstance Win32_Process，能拿命令行）——
 *      这是主通道。为什么不用端口表当主判据：本机实测 netstat 的输出里
 *      看不到这三个端口的 LISTENING 行（只有连接行），Get-NetTCPConnection
 *      在部分环境下也返回空；**按进程找比按端口找可靠**，因为残留服务
 *      的命令行一定带本项目特征。
 *   ② 端口监听者（Get-NetTCPConnection + CIM 反查 pid→命令行）——补漏，
 *      主通道可用时只用来发现"外人占端口"。
 *
 * **安全边界**：本项目三个服务全部由 python 拉起，故只认 python/pythonw；
 * electron/node 一律不碰（桌面壳自己就是 electron，误杀会崩窗口）。
 * 命令行判据认三种起法：`-m harvester …`（api-serve 及各子命令）、
 * `-m actions.actionsserve`（动作面）、`view.py`（视图）。
 */
const PROCS_PS = [
  "[Console]::OutputEncoding=[Text.Encoding]::UTF8;",
  "Get-CimInstance Win32_Process |",
  "Where-Object { $_.Name -match '^(python|pythonw)\\.exe$' } |",
  "ForEach-Object { Write-Output ($_.ProcessId.ToString() + '|' + $_.Name + '|' + $_.CommandLine) }",
].join(" ");

const LISTENERS_PS = [
  "[Console]::OutputEncoding=[Text.Encoding]::UTF8;",
  "$ports=@(8765,8766,8088);",
  "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |",
  "Where-Object { $ports -contains $_.LocalPort } |",
  "ForEach-Object {",
  "  $pr=Get-CimInstance Win32_Process -Filter ('ProcessId='+$_.OwningProcess) -ErrorAction SilentlyContinue;",
  "  if ($pr) { Write-Output ($_.LocalPort.ToString() + '|' + $pr.ProcessId + '|' + $pr.Name + '|' + $pr.CommandLine) }",
  "}",
].join(" ");

function psLines(script, timeoutMs = 25000) {
  try {
    const r = spawnSync("powershell.exe",
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
       "-Command", script],
      { timeout: timeoutMs, windowsHide: true, encoding: "utf8" });
    if (!r || r.status !== 0 || !r.stdout) return [];
    return String(r.stdout).split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  } catch (_) { return []; }
}

function ourProc(name, cmdline) {
  const n = String(name || "").toLowerCase();
  if (!/^(python|pythonw)(\.exe)?$/.test(n)) return false;   // 只碰 python
  const c = String(cmdline || "").toLowerCase().replace(/\\/g, "/");
  return /\B-m\s+harvester\b/.test(c)          // api-serve 等全部子命令
      || c.includes("actionsserve")            // 动作面
      || c.includes("view.py");                // 视图
}

function parseProc(line) {
  const m = String(line).split("|");
  if (m.length < 3) return null;
  const pid = parseInt(m[0], 10);
  if (!Number.isFinite(pid)) return null;
  return { pid, name: m[1] || "", cmdline: m.slice(2).join("|") };
}

function parseListener(line) {
  const m = String(line).split("|");
  if (m.length < 3) return null;
  const port = parseInt(m[0], 10), pid = parseInt(m[1], 10);
  if (!Number.isFinite(port) || !Number.isFinite(pid)) return null;
  return { port, pid, name: m[2] || "", cmdline: m.slice(3).join("|") };
}

async function reclaimPorts() {
  const ports = [API_PORT, ACTIONS_PORT, VIEW_PORT];
  if (process.platform !== "win32") {
    for (const p of ports) {
      if (await portUp(p)) log(`[reclaim] :${p} 已被监听（非 Windows 不自动清）`);
    }
    return;
  }
  const procs = psLines(PROCS_PS).map(parseProc).filter(Boolean);
  const myProcs = procs.filter(p => p.pid !== process.pid
                                    && ourProc(p.name, p.cmdline));
  if (!procs.length) {
    // 主通道拿不到进程清单：不静默当"没残留"，把话说清楚
    log("[reclaim] 拿不到进程清单（PowerShell/CIM 不可用）——本次**跳过**" +
        "自动清场；若启动后功能缺失，先手动结束残留服务再重开");
  } else {
    for (const p of myProcs) {
      log(`[reclaim] 结束残留 ${p.name} pid=${p.pid}：${String(p.cmdline).slice(0, 90)}`);
      spawnSync("taskkill", ["/PID", String(p.pid), "/T", "/F"],
        { timeout: 8000, windowsHide: true });
    }
  }
  // 清完再等端口真释放（最多 5 秒）；此时仍 UP 的就是"外人"
  for (const port of ports) {
    for (let i = 0; i < 20; i++) {
      if (!(await portUp(port, 300))) break;
      await new Promise(r => setTimeout(r, 250));
    }
  }
  if (myProcs.length) log(`[reclaim] 已清 ${myProcs.length} 个残留进程`);
  const listeners = psLines(LISTENERS_PS, 12000).map(parseListener).filter(Boolean);
  const foreign = listeners.filter(l => !ourProc(l.name, l.cmdline));
  if (foreign.length) {
    log("[reclaim] 非本项目进程占着端口：" +
        foreign.map(l => `:${l.port} ← ${l.name}(pid=${l.pid})`).join("；"));
  }
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
    // 清端口之后仍有监听 = 外人占着（不是本项目的进程，不替人杀）
    portUp(s.port).then((up) => {
      if (up) {
        failLoud(`${s.name} 的端口 :${s.port} 被**别的程序**占着——` +
          "我没有替你结束它（可能是 Hadoop/YARN 之类）。请先腾出该端口，" +
          "或改本项目的端口配置后再启动。");
        return;
      }
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
    await reclaimPorts();          // 先清（用户令：别让人手动清）
    startServices();               // 再起
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
