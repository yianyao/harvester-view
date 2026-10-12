# -*- coding: utf-8 -*-
"""actions -- harvester-view 的动作面（工程 SOP D3/D5）。

本包是**唯一**会 import 兄弟仓库 session-harvester 的地方（铁律 #2：
只 import 调用，不修改、不猴子补丁、不绕过其只读连接）。

`_ensure_harvester_importable()` 必须在使用 harvester 之前调用：它把
`../session-harvester` 放进 sys.path（可用环境变量
`HARVESTER_VIEW_UPSTREAM_ROOT` 覆盖，测试据此刻意指向不存在的目录以
验证"缺上游时诚实降级"而不是半死不活）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["upstream_root", "ensure_harvester_importable"]

#: 本包所在目录（harvester-view/actions）
_HERE = Path(__file__).resolve().parent


def upstream_root() -> Path:
    """兄弟仓库 session-harvester 的根目录（默认 `../session-harvester`）。"""
    env = os.environ.get("HARVESTER_VIEW_UPSTREAM_ROOT")
    if env:
        return Path(env).expanduser()
    return _HERE.parent.parent / "session-harvester"


def ensure_harvester_importable() -> str | None:
    """把上游仓库根放进 sys.path。返回错误说明，None=可以 import harvester。

    刻意**不抛异常**：调用方（/api/health、动作执行器）需要把"上游不在"
    渲染成页面上的黄条，而不是让服务起不来（SOP §2 黄条降级）。
    """
    root = upstream_root()
    if not (root / "harvester" / "__init__.py").is_file():
        return "找不到上游仓库：%s（期望 %s/harvester/__init__.py；用 " \
               "HARVESTER_VIEW_UPSTREAM_ROOT 指定）" % (
                   root, root)
    p = str(root)
    if p not in sys.path:
        sys.path.insert(0, p)
    return None
