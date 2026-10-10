# -*- coding: utf-8 -*-
"""真库取数 + 调 node 跑真实载荷渲染检查（跨仓库集成门的一步）。

原为 session-harvester 的 `docs/reports/check-view-real.py`（一次性脚本）；
v0.33 把它移进 view 仓库，因为它**每改一次 view 都该跑**（按后端仓库 AGENTS.md
的铁律：以后还会再跑的东西要进工具本体，不能只躺在 docs/reports/）。

分工：
  - 本脚本：用真库现算 `/api/topics` 与「链最多的那个主题」的
    `/api/topic/<id>/chain` 载荷 → 落 `tests/_tmp/view-payload.json`；
  - `tests/render_real.js`：把载荷喂给从真实 index.html 提取的渲染函数做断言。

用法：
    python tests/check_real_payload.py [--harvester-root <后端仓库>]
                                      [--chain-root <chain 正式位>]

退出码：0 通过 / 1 渲染断言失败 / **3 未执行**（后端仓库或库不在 → 调用方据此
skip，而不是当成通过；见项目 AGENTS.md §五 16）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIEW = HERE.parent
OUT = HERE / "_tmp" / "view-payload.json"
CHECKER = HERE / "render_real.js"
NOT_RUN = 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harvester-root", default=str(VIEW.parent / "session-harvester"),
                    help="后端仓库根（含 harvester/ 与两个库）")
    ap.add_argument("--db", default=None, help="索引库（默认 <root>/harvester.db）")
    ap.add_argument("--meta", default=None, help="主题 meta 库（默认 <root>/topics_meta.db）")
    ap.add_argument("--chain-root", default=None,
                    help="chain 正式位目录（默认 ~/.workbuddy/knowledge/topics）")
    a = ap.parse_args()

    root = Path(a.harvester_root)
    db = Path(a.db) if a.db else root / "harvester.db"
    meta = Path(a.meta) if a.meta else root / "topics_meta.db"
    chains = Path(a.chain_root) if a.chain_root else \
        Path.home() / ".workbuddy" / "knowledge" / "topics"
    for what, p in (("后端仓库", root / "harvester"), ("索引库", db),
                    ("主题 meta 库", meta)):
        if not p.exists():
            print(f"未执行：{what} 不存在 → {p}", file=sys.stderr)
            return NOT_RUN
    sys.path.insert(0, str(root))
    try:
        from harvester.apiserve import api_topic_chain, api_topics
    except ImportError as exc:                       # pragma: no cover
        print(f"未执行：import harvester 失败（{exc}）", file=sys.stderr)
        return NOT_RUN

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        topics = api_topics(con, db, meta, chains)
        # 选**链最多**的主题做多链断言：不写死 id，数据变了也自洽
        ranked = sorted(topics["topics"],
                        key=lambda t: -(t.get("chains_count") or 0))
        tid = ranked[0]["id"] if ranked else None
        mc = api_topic_chain(con, db, tid, meta, chains) if tid else {}
    finally:
        con.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"topics": topics, "multichain": mc,
                               "multichain_topic_id": tid},
                              ensure_ascii=False), encoding="utf-8")
    print(f"载荷: {OUT}（主题 {len(topics['topics'])} 个；"
          f"多链主题 {tid} 有 {mc.get('chain_count', 0)} 条链）")

    r = subprocess.run(["node", str(CHECKER), str(OUT),
                        str(VIEW / "static" / "index.html")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
