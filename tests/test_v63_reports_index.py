# -*- coding: utf-8 -*-
"""v0.79 测试：报告族 md 索引区 + 昨日报告卡「查看全文」（P5 后续）。

依据：`../session-harvester/docs/HANDOFF-v0.52-next.md` §三-2 挂账两项
（§2-⑤ 报告族 md 索引页 / 「昨日报告」卡正文全文查看），用户 2026-10-17
批准「做完挂账的」。

本文件钉**静态结构**（渲染行为由 render_reports.js 动态门钉）：

1. index.html 有 `loadReportsIndex` / `fetchReportFile`，调用
   `/api/reports-index` 与 `/api/report-get`（与主仓端点名逐字一致，
   手滑改错端点 → 红）；
2. 文件名与全文渲染必须走 esc（纪律：只渲染服务端给的内容，页面
   **不解析 md、不二次聚合**）→ 谁把 esc 摘了直接拼 innerHTML → 红；
3. 「查看全文」只对**截断**的昨日卡出现（n_chars>md_head 长度才出），
   name 取服务端白名单文件名 `daily-<date>.md`；
4. 报告页有 `btn-rep-index` 按钮与 `rep-index` 挂点且已接线（按钮不
   接线 = 死按钮 → 红）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def _src(fn_name: str) -> str:
    """按顶层函数名抠出源码段（与渲染门 extract 同口径）。"""
    import re
    m = re.search(r"^(?:async )?function %s\([^)]*\)\{[\s\S]*?^\}"
                  % fn_name, HTML, re.M)
    assert m, "index.html 里找不到函数 %s" % fn_name
    return m.group(0)


class ReportsIndexStatic(unittest.TestCase):

    def test_endpoints_called_verbatim(self):
        self.assertIn('api("/api/reports-index")', HTML,
                      "报告索引必须打主仓 /api/reports-index（端点名逐字）")
        self.assertIn('"/api/report-get?name="+encodeURIComponent(name)',
                      HTML, "全文必须走 /api/report-get 且 name 编码传参")

    def test_load_reports_index_escapes_names_and_body(self):
        src = _src("loadReportsIndex")
        self.assertIn("esc(f.name)", src, "文件名必须 esc 后再进 innerHTML")
        self.assertIn("esc(md)", src, "全文必须 esc 原样展示（不解析 md）")
        self.assertIn("esc(d.hint", src, "空态 hint 也要 esc")
        self.assertNotIn("marked", src)

    def test_fetch_report_file_wraps_honest_empty(self):
        src = _src("fetchReportFile")
        self.assertIn("found", src, "诚实空态（found=false）要转成可读报错")

    def test_daily_card_full_text_button(self):
        src = _src("loadDailyCard")
        self.assertIn("查看全文", src)
        self.assertIn("data-dfull=", src)
        self.assertIn('"daily-"+', src.replace(" ", ""),
                      "全文按钮的 name 必须取服务端白名单口径 daily-<date>.md")
        self.assertIn('n_chars>(d.md_head||"").length', src.replace(" ", ""),
                      "只有截断时才出「查看全文」")

    def test_reports_tab_mount_and_wiring(self):
        self.assertIn('id="rep-index"', HTML, "报告页缺索引挂点")
        self.assertIn('id="btn-rep-index"', HTML, "报告页缺索引按钮")
        self.assertRegex(HTML, r'btn-rep-index"\)\.onclick=\(\)=>'
                               r'loadReportsIndex\(\)',
                         "索引按钮没接线（死按钮）")
        self.assertRegex(HTML, r'name==="reports"\)\{ loadReport\(\)\.'
                               r'catch\(\(\)=>\{\}\); loadReportsIndex\(\)',
                         "报告页签首次进入要拉索引")


if __name__ == "__main__":
    unittest.main()
