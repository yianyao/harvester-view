# -*- coding: utf-8 -*-
"""v0.81 测试：词云导航（P7-①，用户 2026-10-19 批准「落地吧」）。

钉的验收（每条红=什么坏了）：
1. 静态结构：词云 tab 按钮 + tab-cloud 区 + #cloud-out + switchTab 分发
   → 有人删入口 → 红；
2. 数据通路：loadWordcloud 调 /api/keywords（n 档参数 + limit）→ 红；
3. esc 纪律：gram 经 esc 原样展示（页面不解析不聚合）→ 有人拼裸串 → 红；
4. 下钻通路：jumpCloudWord 把词灌进会话检索（state.q + f-q + loadSessions，
   复用真检索不臆造词→会话归属）→ 有人改成臆造跳转 → 红；
5. 诚实空态：hint 分支原样渲染提示 + 生成命令 → 红；
6. 数据新鲜度：start-viewv2.cmd 已配 --keywords-meta（端点有真数据源）
   → 有人删启动参数 → 红。

渲染行为（分档/点击/空态）由 render_wordcloud.js 渲染门覆盖。
"""
from __future__ import annotations

import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


class TestWordcloudStatic(unittest.TestCase):
    def test_tab_entry_and_section(self):
        self.assertIn('data-tab="cloud"', HTML, "导航缺词云入口")
        self.assertIn('id="tab-cloud"', HTML, "缺词云区")
        self.assertIn('id="cloud-out"', HTML, "缺词云渲染容器")
        self.assertIn('id="cloud-n2"', HTML, "缺二元词档位")
        self.assertIn('id="cloud-n3"', HTML, "缺三元词档位")

    def test_dispatch_wired(self):
        self.assertIn('if(name==="cloud") loadWordcloud().catch(()=>{});',
                      HTML, "switchTab 未分发词云加载")

    def test_data_path_and_esc(self):
        self.assertIn('api("/api/keywords?n="+cloudN+"&limit=150")', HTML,
                      "loadWordcloud 数据通路不对")
        self.assertIn("esc(r.gram)", HTML, "词必须 esc 原样展示")
        self.assertIn("esc(d.hint)", HTML, "空态提示必须 esc 原样展示")

    def test_drilldown_via_real_search(self):
        i = HTML.index("function jumpCloudWord")
        seg = HTML[i:HTML.index("}", i) + 1]
        for token in ('switchTab("sessions")', "state.q=w",
                      '$("f-q").value=w', "loadSessions()"):
            self.assertIn(token, seg, f"下钻缺 {token}（必须复用真检索通路）")

    def test_honest_empty(self):
        self.assertIn("最新 run 没有词频数据", HTML, "空 rows 无诚实空态")
        self.assertIn("python -m harvester keywords", HTML,
                      "空态未给生成命令")

    def test_start_cmd_feeds_keywords_meta(self):
        cmd = (Path(__file__).resolve().parent.parent
               / "start-viewv2.cmd").read_text(encoding="utf-8",
                                               errors="replace")
        self.assertIn("--keywords-meta", cmd,
                      "启动脚本没配 --keywords-meta（端点将永远诚实空态）")


if __name__ == "__main__":
    unittest.main()
