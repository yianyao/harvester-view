# -*- coding: utf-8 -*-
"""P4 进化层分析模块包（D8 落点）。

约定（SOP-harvester-view-v2 §P4 开工提示 + 用户 2026-10-13 裁决）：
- 这里的模块是 actions-serve 内的**独立分析模块**：只 import harvester
  （复用其冻结口径与工具函数），**不改它**——session-harvester 零改动
  是 P4-1/P4-3 的验收项；
- 只读：对 harvester.db 一律 mode=ro，模块源码不得出现写库语句；
- 锚点 = messages.rowid（messages 无 seq 列；交接 §5 第 9 条）；
- 一律读 `raw`（text 是 FTS bigram 分词列——交接 §5 第 9 条）。

成员：
- nags.py      P4-1 反复叮嘱（层一：指令遵循提醒）；
- revisions.py P4-3 打磨/反转候选（层二：产出推敲琢磨）。
"""
