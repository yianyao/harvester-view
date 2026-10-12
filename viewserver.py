# -*- coding: utf-8 -*-
"""viewserver -- **兼容薄壳**（P2-1 归并后）。

历史：P1（工程决策 D9）本文件是 view 仓的本地只读服务，提供
`/view/cards`（卡片本体）与 `/view/glossary`（释义数据）。SOP D3 规定
P2 建 actions-serve 时把它**归并**进 `actions/` 目录，端点路径与响应
结构不变（additive）。

现状：**实现全部搬到了 `actions/actionsserve.py`**（8766 一个端口、一个
进程，不再起第二套本地服务）。本文件只保留：

- `serve()`   —— 起服（转发到 actions.actionsserve.serve，签名不变）；
- `make_handler(cards_root, glossary)` —— 测试与调用方友好的薄壳：
  按 P1 的两个参数构造 Config（db 走默认解析），返回 handler 类；
- `run(argv)` —— 命令行入口（参数与 P1 同形，多了可选的路径类参数）。

**为什么保留薄壳而不是删掉**：`tests/test_v34_local_data.py` 与
`start-viewv2.cmd` 都按本文件名调用。保留它使归并对调用方**零破坏**；
新代码一律直接用 `actions/actionsserve.py`。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from actions import actionsserve as _a

__all__ = ["make_handler", "serve", "run", "DEFAULT_CARDS_ROOT"]

DEFAULT_CARDS_ROOT = _a.DEFAULT_CARDS_ROOT
DEFAULT_GLOSSARY = _a.PACKAGE_ROOT / "static" / "glossary.json"


def default_db() -> Path:
    """默认索引库：兄弟仓库 `../session-harvester/harvester.db`（按需取）。"""
    from actions import upstream_root
    return upstream_root() / "harvester.db"


def _sibling(name: str) -> Path | None:
    """兄弟仓库里的 meta 库：存在才返回（不存在＝不配置，端点诚实降级）。"""
    from actions import upstream_root
    p = upstream_root() / name
    return p if p.is_file() else None


def make_config(cards_root: Path, glossary: Path,
                db: Path | None = None) -> _a.Config:
    from actions import upstream_root
    root = upstream_root()
    return _a.Config(
        db=db or default_db(), cards_root=Path(cards_root),
        glossary=Path(glossary),
        topics_meta=_sibling("topics_meta.db"),
        artifacts_meta=_sibling("artifacts_meta.db"),
        chain_root=Path.home() / ".workbuddy" / "knowledge" / "topics",
        keywords_meta=_sibling("keywords_meta.db"),
        suggestions_meta=_sibling("suggestions_meta.db"),
        exports_root=root / "exports" if root.is_dir() else None)


def make_handler(cards_root: Path, glossary_path: Path,
                 db: Path | None = None):
    """兼容 P1 签名 `(cards_root, glossary_path)`；多出的 db 可选。"""
    return _a.make_handler(make_config(cards_root, glossary_path, db))


def serve(cfg: _a.Config, host: str = "127.0.0.1",
          port: int = _a.DEFAULT_PORT) -> None:
    _a.serve(cfg, host=host, port=port)


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="viewserver",
        description="harvester-view 本地只读服务（P2 起由 actions/actionsserve"
                    " 实现；本入口为兼容薄壳）")
    ap.add_argument("--port", type=int, default=_a.DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=str(default_db()),
                    help="索引库（只读）；缺省取兄弟仓库的 harvester.db")
    ap.add_argument("--cards-root", default=str(DEFAULT_CARDS_ROOT))
    ap.add_argument("--glossary", default=str(DEFAULT_GLOSSARY))
    args = ap.parse_args(argv)
    cfg = make_config(Path(args.cards_root), Path(args.glossary),
                      db=Path(args.db))
    print("[viewserver] 兼容薄壳：实现已归并至 actions/actionsserve.py",
          file=sys.stderr)
    serve(cfg, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
