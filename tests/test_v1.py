# -*- coding: utf-8 -*-
"""harvester-view v1 测试：代理转发 / 版本协商 / 降级 / 安全守卫。

口径红线（对齐 HANDOFF-harvester-view.md §4 与设计稿 v0.2 §5）：
- 上游 mock 三态：正常转发 / 不可达+超时 / api_version 不匹配；
- 多上游切换与默认上游兜底（无 config.json）；
- 代理对非 GET 一律 405；仅转发 /api/* 子路径；
- token 配置经 X-Token 头到达上游；/u/all/facets 聚合可降级。

踩坑备忘（承接主项目 test_v17 §6 教训）：测试里起的 HTTP server
tearDown 必须 shutdown()+server_close() 收干净，避免 ResourceWarning。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from view import (  # noqa: E402
    DEFAULT_CONFIG,
    load_config,
    make_handler,
    normalize_upstreams,
)

ROOT = Path(__file__).resolve().parent.parent
STATIC_HTML = ROOT / "static" / "index.html"
VIEW_TIMEOUT = 0.4  # 测试用短超时，让超时用例快速触发


# ---------- mock 上游 ----------

def _mock_upstream(mode: str = "good", delay: float = 0.0):
    """起一个假上游（随机端口）。mode: good / badversion（badversion
    把全部 200 响应的 api_version 覆写为 2，触发 view 版本协商拒绝）。
    /api/echo 回显收到的 path+query（验证转发保真）；
    /api/whoami 回显 X-Token 头；/api/secret401 返回 401。"""
    def handle(path: str, headers):
        if delay:
            time.sleep(delay)
        if path.startswith("/api/whoami"):
            return 200, {"api_version": 1,
                         "x_token": headers.get("X-Token", "")}
        if path.startswith("/api/echo"):
            return 200, {"api_version": 1, "path": path}
        if path == "/api/secret401":
            return 401, {"error": "unauthorized"}
        if path == "/api/meta":
            return 200, {"api_version": 1, "server": f"mock-{mode}",
                         "readonly": True, "sessions": 3,
                         "time_min": "2026-01-01 00:00:00",
                         "time_max": "2026-10-07 12:00:00"}
        if path == "/api/facets":
            return 200, {"sources": [{"name": f"src-{mode}", "count": 2}],
                         "models": [{"name": "m1", "count": 2}],
                         "skills": [{"name": f"skill-{mode}", "count": 1}],
                         "time_min": "2026-01-01 00:00:00",
                         "time_max": "2026-10-07 12:00:00"}
        if path.startswith("/api/sessions"):
            return 200, {"api_version": 1, "total": 1, "offset": 0,
                         "limit": 50, "items": [
                             {"sid": f"m:{mode}", "source": f"src-{mode}",
                              "title": f"{mode} 的会话", "category": "",
                              "model": "m1",
                              "created_at": "2026-10-07 10:00:00",
                              "updated_at": "2026-10-07 10:00:00"}]}
        if path.startswith("/api/session/x/turn/1"):
            return 200, {"api_version": 1, "sid": "x", "turn": 1,
                         "messages": [{"role": "user",
                                       "ts": "2026-10-07 10:00:00",
                                       "content": "问题"}]}
        if path.startswith("/api/session/"):
            return 200, {"api_version": 1, "meta": {"sid": "x"}, "n_turns": 1,
                         "turns": [{"no": 1, "n_messages": 1,
                                    "roles": ["user"], "ts": "t",
                                    "preview": "问题"}]}
        return 404, {"error": f"未知路由 {path}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):  # noqa: N802
            if delay:
                time.sleep(delay)
            status, obj = handle(self.path, self.headers)
            if mode == "badversion" and status == 200 \
                    and isinstance(obj, dict):
                obj["api_version"] = 2
            self._json(status, obj)

        def do_POST(self):  # noqa: N802
            self._json(405, {"error": "mock 只收 GET"})

        def _json(self, status, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",
                             "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, f"http://127.0.0.1:{srv.socket.getsockname()[1]}"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------- view 服务 ----------

class ViewFixture(unittest.TestCase):
    """统一 fixture：起 view（随机端口）+ 按需挂 mock 上游。"""

    def setUp(self):
        self.mocks: list = []

    def tearDown(self):
        for srv in self.mocks:
            srv.shutdown()
            srv.server_close()

    def start_view(self, cfg: dict) -> int:
        handler = make_handler(cfg, STATIC_HTML, timeout=VIEW_TIMEOUT)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv.socket.getsockname()[1]

    def add_mock(self, mode: str = "good", delay: float = 0.0) -> dict:
        srv, url = _mock_upstream(mode=mode, delay=delay)
        self.mocks.append(srv)
        return {"name": f"mock-{mode}", "url": url, "token": ""}


def _get(port: int, path: str, method: str = "GET"):
    """请求 view，返回 (status, 解析后的 JSON)。"""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=VIEW_TIMEOUT * 4) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


# ---------- 用例 ----------

class TestStaticAndForward(ViewFixture):
    def test_index_served(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        with urllib.request.urlopen(req, timeout=2) as r:
            body = r.read().decode("utf-8")
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers["Content-Type"])
        self.assertIn("harvester-view", body)
        self.assertIn("</html>", body)

    def test_forward_meta_ok(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        status, body = _get(port, "/u/0/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(body["api_version"], 1)
        self.assertEqual(body["server"], "mock-good")

    def test_forward_preserves_query(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        qs = "q=" + quote("饼干") + "&limit=1"
        status, body = _get(port, f"/u/0/api/echo?{qs}")
        self.assertEqual(status, 200)
        self.assertEqual(body["path"], f"/api/echo?{qs}")

    def test_token_forwarded_via_x_token(self):
        up = self.add_mock()
        up["token"] = "s3cret"
        port = self.start_view({"upstreams": [up]})
        status, body = _get(port, "/u/0/api/whoami")
        self.assertEqual(status, 200)
        self.assertEqual(body["x_token"], "s3cret")

    def test_upstream_error_passthrough_401(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        status, body = _get(port, "/u/0/api/secret401")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")


class TestVersionAndDegrade(ViewFixture):
    def test_version_mismatch_rejected(self):
        port = self.start_view({"upstreams": [self.add_mock("badversion")]})
        status, body = _get(port, "/u/0/api/meta")
        self.assertEqual(status, 502)
        self.assertEqual(body["kind"], "version_mismatch")
        self.assertIn("升级对应侧", body["error"])

    def test_upstream_unreachable_closed_port(self):
        port = self.start_view({"upstreams": [
            {"name": "死口", "url": f"http://127.0.0.1:{_free_port()}",
             "token": ""}]})
        status, body = _get(port, "/u/0/api/meta")
        self.assertEqual(status, 502)
        self.assertEqual(body["kind"], "unreachable")

    def test_upstream_timeout_is_unreachable(self):
        port = self.start_view({"upstreams": [
            self.add_mock(delay=1.2)]})  # 延迟 > VIEW_TIMEOUT(0.4s)
        status, body = _get(port, "/u/0/api/meta")
        self.assertEqual(status, 502)
        self.assertEqual(body["kind"], "unreachable")


class TestRoutingAndSafety(ViewFixture):
    def test_non_get_405(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        for path in ("/u/0/api/meta", "/"):
            status, body = _get(port, path, method="POST")
            self.assertEqual(status, 405, path)
            self.assertIn("error", body)

    def test_out_of_range_upstream_404(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        status, body = _get(port, "/u/5/api/meta")
        self.assertEqual(status, 404)
        self.assertIn("不存在", body["error"])

    def test_proxy_only_forwards_api_paths(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        status, body = _get(port, "/u/0/static/other")
        self.assertEqual(status, 404)
        self.assertIn("/api/", body["error"])

    def test_unknown_route_404(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        status, body = _get(port, "/garbage")
        self.assertEqual(status, 404)
        self.assertIn("未知路由", body["error"])


class TestMultiUpstream(ViewFixture):
    def test_switch_between_upstreams(self):
        port = self.start_view({"upstreams": [self.add_mock("good"),
                                              self.add_mock("badversion")]})
        _, b0 = _get(port, "/u/0/api/meta")
        self.assertEqual(b0["server"], "mock-good")
        status, _ = _get(port, "/u/1/api/meta")
        self.assertEqual(status, 502)  # 上游 1 版本不匹配，不影响上游 0

    def test_upstreams_listing(self):
        port = self.start_view({"upstreams": [self.add_mock("good"),
                                              self.add_mock("badversion")]})
        _, body = _get(port, "/view/upstreams")
        self.assertEqual(body, [{"no": 0, "name": "mock-good"},
                                {"no": 1, "name": "mock-badversion"}])

    def test_all_facets_merge_and_degrade(self):
        good = self.add_mock("good")
        dead = {"name": "死口", "url": f"http://127.0.0.1:{_free_port()}",
                "token": ""}
        port = self.start_view({"upstreams": [good, dead]})
        status, body = _get(port, "/u/all/facets")
        self.assertEqual(status, 200)
        merged = body["merged"]
        self.assertEqual(merged["sources"],
                         [{"name": "src-good", "count": 2}])
        self.assertEqual(merged["skills"],
                         [{"name": "skill-good", "count": 1}])
        errs = [u for u in body["upstreams"] if "error" in u]
        self.assertEqual(len(errs), 1)
        self.assertIn("unreachable", errs[0]["error"])
        # 存活上游的时间范围照常聚合
        self.assertEqual(merged["time_min"], "2026-01-01 00:00:00")

    def test_all_rejects_non_facets(self):
        port = self.start_view({"upstreams": [self.add_mock()]})
        status, _ = _get(port, "/u/all/api/meta")
        self.assertEqual(status, 404)


class TestConfigLoading(unittest.TestCase):
    def test_missing_config_falls_back_to_default(self):
        cfg = load_config(Path("Z:/不存在的路径/config.json"))
        self.assertEqual(cfg, DEFAULT_CONFIG)
        ups = normalize_upstreams(cfg)
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0]["url"], "http://127.0.0.1:8765")

    def test_broken_config_fails_loud(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "config.json"
            bad.write_text("{不是 json", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_config(bad)

    def test_empty_upstreams_fails_loud(self):
        with self.assertRaises(SystemExit):
            normalize_upstreams({"upstreams": []})
        with self.assertRaises(SystemExit):
            normalize_upstreams({"upstreams": [{"name": "x"}]})  # 缺 url

    def test_url_shape_validation(self):
        with self.assertRaises(SystemExit):
            normalize_upstreams({"upstreams": [
                {"name": "x", "url": "ftp://nope"}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
