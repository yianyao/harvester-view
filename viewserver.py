# -*- coding: utf-8 -*-
"""viewserver -- view 仓本地只读服务（P1 配套；工程决策 D9，用户 2026-10-12 批准）。

定位（铁律 #2 零改动红线下的自建数据面）：
- 端口 8766（冻结：8765=apiserve，8766=本服务）；只绑 127.0.0.1；
- 只读：仅 GET；对 session-harvester 与卡片库零写入；
- /view/cards    知识卡片本体（cardlib 解析 frontmatter；与上游 /api/cards
                 的校验结果互补，页面两路合并）；
- /view/glossary 释义数据（static/glossary.json 原样透出；P1-1/P1-4 消费）；
- /view/health   自检（卡片根目录在位与否、glossary 是否可加载）。

CORS：view 页面跑在 8088，本服务在 8766，跨端口即跨源——响应带
Access-Control-Allow-Origin: *（本服务只回只读 JSON，绑定回环地址，无凭据）。

P2 归并说明：SOP D3 的 actions-serve（actions/actionsserve.py）落地后，
本文件的端点将原样并入（路径与响应结构不变，additive）；届时本文件退化为
兼容薄壳或直接退役。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cardlib

DEFAULT_CARDS_ROOT = Path.home() / ".workbuddy" / "knowledge" / "cards"


def make_handler(cards_root: Path, glossary_path: Path):
    """生成 Handler（闭包携带路径，测试用随机端口起停）。glossary 惰性加载：
    首次请求读盘并缓存；文件损坏 fail loud（500 + 明确错误信息，不静默吞）。"""

    class ViewHandler(BaseHTTPRequestHandler):
        server_version = "harvester-view-loc/1"

        def log_message(self, fmt, *args):  # stderr 一行一条
            print("[viewserver] %s %s" % (self.address_string(), fmt % args),
                  file=sys.stderr)

        def _headers(self, status: int, length: int, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _json(self, obj, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._headers(status, len(body),
                          "application/json; charset=utf-8")
            self.wfile.write(body)

        def do_OPTIONS(self):  # noqa: N802（跨源预检）
            self._headers(204, 0, "text/plain")

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/view/cards":
                self._json(cardlib.cards_payload(cards_root))
                return
            if path == "/view/glossary":
                try:
                    text = glossary_path.read_text(encoding="utf-8")
                except OSError as e:
                    self._json({"error": "glossary 文件不可读: %s" % e}, 500)
                    return
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError as e:
                    self._json({"error": "glossary JSON 损坏（fail loud）: %s"
                                         % e}, 500)
                    return
                self._json(obj)
                return
            if path == "/view/health":
                self._json({
                    "ok": True,
                    "cards_root": str(cards_root),
                    "cards_root_exists": cards_root.is_dir(),
                    "glossary": str(glossary_path),
                    "glossary_exists": glossary_path.exists(),
                })
                return
            if path == "/":
                self._json({
                    "service": "harvester-view 本地只读服务",
                    "endpoints": ["/view/cards", "/view/glossary",
                                  "/view/health"]})
                return
            self._json({"error": "未知路由 %s（可用：/view/cards "
                                 "/view/glossary /view/health）" % path}, 404)

        def do_POST(self):  # noqa: N802
            self._json({"error": "本服务只读，仅 GET"}, 405)

        do_PUT = do_POST
        do_DELETE = do_POST

    return ViewHandler


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="viewserver",
        description="harvester-view 本地只读服务（卡片本体 + 释义数据，8766）")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--cards-root", default=str(DEFAULT_CARDS_ROOT))
    ap.add_argument("--glossary",
                    default=str(Path(__file__).resolve().parent
                                / "static" / "glossary.json"))
    args = ap.parse_args(argv)

    cards_root = Path(args.cards_root)
    glossary_path = Path(args.glossary)
    if not cards_root.is_dir():
        print("[viewserver] 警告: 卡片目录不存在: %s（/view/cards 将返回空列"
              "表，不阻断启动）" % cards_root, file=sys.stderr)
    if not glossary_path.exists():
        print("[viewserver] 警告: glossary 文件不存在: %s（/view/glossary 将"
              "返回 500，不阻断启动）" % glossary_path, file=sys.stderr)

    srv = ThreadingHTTPServer((args.host, args.port),
                              make_handler(cards_root, glossary_path))
    real = srv.socket.getsockname()[1]
    print("[viewserver] 已启动 http://%s:%d/  （cards=%s, glossary=%s）"
          % (args.host, real, cards_root, glossary_path.name), file=sys.stderr)
    print("[viewserver] Ctrl+C 停止", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[viewserver] 已停止", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())