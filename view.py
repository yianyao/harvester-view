# -*- coding: utf-8 -*-
"""harvester-view —— 独立只读视图：静态页 + 本地代理（全部 stdlib）。

定位（设计稿 v0.2 §2/§4）：
- 零 Python 依赖上游项目：不 import 任何主项目代码，不知道 DB 存在，
  只认识"返回 api_version=1 JSON 的 HTTP 服务"（下称上游）；
- 职责：1) 服务 static/index.html 单页前端；2) 把 GET /u/<编号>/api/...
  转发到 config.json 里下标为 <编号> 的上游（编号从 0 起）；
  3) GET /u/all/facets 做多上游 facets 聚合（中台形态的 v1 伏笔，
  不可达上游跳过并在结果中标注 error）；
- 安全：全服务只读，非 GET 一律 405；默认绑 127.0.0.1；仅转发 /api/*
  子路径；上游配置了 token 时转发自动携带 X-Token 头；
- 版本协商：对上游 200 响应做 api_version 校验——响应带该字段且
  major != 1 时报"请升级对应侧"（502）；不带该字段的端点
  （如 /api/facets）原样放行；上游 401/404/5xx 原样透传给页面；
- 上游不可达/超时 → 502 kind="unreachable"（页面显示黄条引导，
  其他上游不受影响）。

启动：``python view.py [--port 8088] [--host 127.0.0.1] [--config 路径]
[--no-open]``。无 config.json 时使用默认上游 http://127.0.0.1:8765。
"""
from __future__ import annotations

import copy
import json
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

API_MAJOR = 1
DEFAULT_TIMEOUT = 10.0  # 上游单请求超时（秒）

#: 无 config.json 时的兜底上游（设计稿 §4：开箱即用）
DEFAULT_CONFIG: dict = {
    "upstreams": [
        {"name": "本机", "url": "http://127.0.0.1:8765", "token": ""},
    ],
}


class UpstreamError(Exception):
    """代理访问上游失败的统一异常。kind ∈ unreachable/bad_gateway/
    version_mismatch，页面据此区分黄条与红条。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# ---------- 配置加载（fail loud） ----------

def load_config(path: Path) -> dict:
    """读 config.json；文件不存在 → 默认上游；损坏/形状非法 → SystemExit。"""
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"[view] config.json 解析失败（fail loud）: {e}")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("upstreams"), list) \
            or not cfg["upstreams"]:
        raise SystemExit('[view] config.json 需形如 {"upstreams": [..]} '
                         "且至少配置一个上游")
    return cfg


def normalize_upstreams(cfg: dict) -> list[dict]:
    """规整上游清单：补默认名、剥尾部斜杠、校验 URL 形状。"""
    if not cfg.get("upstreams"):
        raise SystemExit("[view] upstreams 不能为空（至少配置一个上游）")
    outs: list[dict] = []
    for i, up in enumerate(cfg["upstreams"]):
        if not isinstance(up, dict) or not up.get("url"):
            raise SystemExit(f"[view] upstreams[{i}] 缺 url 字段")
        url = str(up["url"]).rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise SystemExit(f"[view] upstreams[{i}].url 必须以 http(s):// "
                             f"开头: {up['url']!r}")
        outs.append({
            "name": str(up.get("name") or f"上游{i}"),
            "url": url,
            "token": str(up.get("token") or ""),
        })
    return outs


# ---------- 上游访问 ----------

#: 上游是本机/内网服务，绝不经过系统代理转发（urllib 默认会读
#: http_proxy/HTTP_PROXY 环境变量，机器配了全局代理时会走歪）。
_NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}))


def fetch_upstream(up: dict, path_qs: str,
                   timeout: float) -> tuple[int, bytes]:
    """GET 转发单个请求。4xx/5xx 原样返回 (code, body)；
    连不上/超时 → UpstreamError("unreachable")。"""
    url = up["url"] + path_qs
    req = urllib.request.Request(url, method="GET")
    if up["token"]:
        req.add_header("X-Token", up["token"])
    try:
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:  # 注意：URLError 子类，须先接
        return e.code, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError,
            ConnectionError, OSError) as e:
        raise UpstreamError(
            "unreachable", f"上游「{up['name']}」不可达: {e}") from e


def _check_api_version(obj: dict) -> str | None:
    """校验响应中的 api_version。返回错误说明，None=通过。
    响应未携带 api_version 字段（如 /api/facets）→ 放行。"""
    v = obj.get("api_version")
    if v is None:
        return None
    try:
        major = int(v)
    except (TypeError, ValueError):
        return f"上游 api_version 非整数: {v!r}"
    if major != API_MAJOR:
        return (f"上游 api_version={major}，本视图支持 major={API_MAJOR}；"
                "上游 API 版本过旧/过新，请升级对应侧")
    return None


def proxy_get(up: dict, path_qs: str, timeout: float) -> tuple[int, bytes]:
    """带版本协商的转发：200 响应必须是 JSON 对象且 api_version 兼容；
    非 200 原样透传（401/404/5xx 的错误 JSON 由页面按状态处理）。"""
    status, body = fetch_upstream(up, path_qs, timeout)
    if status != 200:
        return status, body
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UpstreamError("bad_gateway",
                            f"上游「{up['name']}」返回 200 但不是合法 "
                            f"JSON: {e}") from e
    if not isinstance(obj, dict):
        raise UpstreamError("bad_gateway",
                            f"上游「{up['name']}」返回 200 但 JSON 顶层"
                            "不是对象")
    err = _check_api_version(obj)
    if err:
        raise UpstreamError("version_mismatch", err)
    return status, body


def merge_facets(ups: list[dict], timeout: float) -> dict:
    """/u/all/facets：聚合全部上游的 facets（按 name 计数求和）。
    不可达上游不拖垮整体，逐个标注 error（设计稿 §2"黄条降级"）。"""
    per: list[dict] = []
    agg: dict[str, dict[str, int]] = {"sources": {}, "models": {},
                                      "skills": {}}
    tmins: list[str] = []
    tmaxs: list[str] = []
    for up in ups:
        entry: dict = {"name": up["name"]}
        try:
            _, body = proxy_get(up, "/api/facets", timeout)
            obj = json.loads(body.decode("utf-8"))
            entry["facets"] = obj
            for key in agg:
                for it in obj.get(key) or []:
                    agg[key][it["name"]] = (agg[key].get(it["name"], 0)
                                            + int(it.get("count", 0)))
            if obj.get("time_min"):
                tmins.append(obj["time_min"])
            if obj.get("time_max"):
                tmaxs.append(obj["time_max"])
        except UpstreamError as e:
            entry["error"] = f"{e.kind}: {e.message}"
        per.append(entry)

    def _rank(d: dict[str, int]) -> list[dict]:
        return [{"name": k, "count": v}
                for k, v in sorted(d.items(), key=lambda kv: -kv[1])]

    merged: dict = {k: _rank(agg[k]) for k in agg}
    if tmins:
        merged["time_min"] = min(tmins)
    if tmaxs:
        merged["time_max"] = max(tmaxs)
    return {"upstreams": per, "merged": merged}


# ---------- HTTP 层 ----------

def make_handler(cfg: dict, static_html: Path,
                 timeout: float = DEFAULT_TIMEOUT):
    """生成 Handler 类（闭包携带配置，测试用随机端口起停）。"""
    ups = normalize_upstreams(cfg)

    class ViewHandler(BaseHTTPRequestHandler):
        server_version = "harvester-view/1"

        def log_message(self, fmt, *args):  # 日志一行一条走 stderr
            print(f"[view] {self.address_string()} {fmt % args}",
                  file=sys.stderr)

        # -- 响应工具 --
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status,
                       json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        # -- 路由 --
        def do_GET(self) -> None:  # noqa: N802 (http.server 命名约定)
            u = urlparse(self.path)
            path = u.path
            if path in ("/", "/index.html"):
                try:
                    self._send(200, static_html.read_bytes(), "text/html; "
                               "charset=utf-8")
                except OSError as e:
                    self._json({"error": f"前端页面缺失 "
                                         f"({static_html.name}): {e}"}, 500)
                return
            if path == "/view/upstreams":
                self._json([{"no": i, "name": up["name"]}
                            for i, up in enumerate(ups)])
                return
            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            m = re.fullmatch(r"/u/(all|\d+)(/.*)", path)
            if not m:
                self._json({"error": f"未知路由 {path}（页面在 /，代理在 "
                                     "/u/<编号>/api/*）"}, 404)
                return
            target, sub = m.groups()
            qs = f"?{u.query}" if u.query else ""
            if target == "all":
                # 设计稿 §2 的规范 URL 是 /u/all/facets（无 /api 前缀），
                # 兼容 /u/all/api/facets 写法；此分支须在 /api/* 守卫之前。
                if sub not in ("/facets", "/api/facets"):
                    self._json({"error": "/u/all 仅支持 /facets 聚合"
                                         "（跨实例搜索属 v2）"}, 404)
                    return
                self._json(merge_facets(ups, timeout))
                return
            if not sub.startswith("/api/"):
                self._json({"error": f"代理仅转发 /api/* 子路径，"
                                     f"拒绝 {sub}"}, 404)
                return
            idx = int(target)
            if idx >= len(ups):
                self._json({"error": f"上游编号 {idx} 不存在"
                                     f"（共 {len(ups)} 个，从 0 起）"}, 404)
                return
            try:
                status, body = proxy_get(ups[idx], sub + qs, timeout)
            except UpstreamError as e:
                self._json({"error": e.message, "kind": e.kind}, 502)
                return
            self._send(status, body, "application/json; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            self._json({"error": "view 只读，代理仅转发 GET"}, 405)

        do_PUT = do_POST
        do_DELETE = do_POST

    return ViewHandler


# ---------- 启动入口 ----------

def run(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="harvester-view",
        description="session 套件的独立只读视图（静态页 + 上游代理）")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--config",
                    default=str(Path(__file__).resolve().parent
                                / "config.json"))
    ap.add_argument("--no-open", action="store_true",
                    help="不自动打开浏览器")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    ups = normalize_upstreams(cfg)
    static_html = (Path(__file__).resolve().parent
                   / "static" / "index.html")

    srv = ThreadingHTTPServer((args.host, args.port),
                              make_handler(cfg, static_html))
    real = srv.socket.getsockname()[1]
    url = f"http://{args.host}:{real}/"
    names = ", ".join(f"{i}:{u['name']}" for i, u in enumerate(ups))
    print(f"[view] 已启动 {url} （上游 {len(ups)} 个: {names}）",
          file=sys.stderr)
    print("[view] Ctrl+C 停止", file=sys.stderr)
    if not args.no_open:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[view] 已停止", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
