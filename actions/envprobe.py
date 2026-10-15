# -*- coding: utf-8 -*-
"""envprobe -- 本机执行环境探测（2026-10-15，用户批注：这台机子有 uv）。

为什么要探测：复制到终端执行的命令行，前缀必须匹配**用户终端里真实可用的
解释器入口**，而不是写死 `python -m harvester`——用户机器上可能：
  ① `python` 不在 PATH（实测：PowerShell 报 CommandNotFoundException）；
  ② 有 uv（`uv run python` 会自己解析项目环境，最稳）；
  ③ 都没有 → 退回本服务进程自己的解释器绝对路径（一定能跑，只是难看）。
判据优先级：uv → python → sys.executable。每次现探测不缓存：which() 开销
极小，且改 PATH / 装 uv 后无需重启服务。

另回 `repo_root`：命令必须在**上游仓根**（session-harvester）目录下执行——
`--db` 等参数都是仓根相对路径；页面据此提示"在哪个目录粘贴"，避免用户踩过
的坑（在上级目录 `uv run` 报 No module named harvester）。
"""
from __future__ import annotations

import shutil
import sys

from . import upstream_root


def probe() -> dict:
    """探测本机可用的命令前缀与仓根。纯只读，不落地任何文件。"""
    uv = shutil.which("uv") is not None
    py = shutil.which("python") is not None
    if uv:
        prefix = "uv run python"
    elif py:
        prefix = "python"
    else:
        prefix = '"%s"' % sys.executable
    root = upstream_root()
    return {
        "uv": uv,
        "python": py,
        "cli_prefix": prefix,
        "repo_root": str(root),
        "repo_root_ok": (root / "harvester" / "__init__.py").is_file(),
    }
