# -*- coding: utf-8 -*-
"""P9-③ 门：四条报告动作要**同时**给「人读文本」与「机读结构」。

为什么不用 `--json -`：那会把 stdout 让给 JSON，人读报告消失（chain-audit 还会
让位到 stderr）——是回归。所以服务端走 `--json <临时文件>`：文本照旧在 stdout，
结构读回进 `result["json"]`，临时文件跑完即删。

本文件钉三件事：
① `_cli_report_both` **真的**把 `--json <路径>` 追加进 argv、真的读回该文件、
   真的删掉它（用一个会写文件的假 `_cli_report` 验证——不是只看源码有没有写）；
② 落盘不是合法 JSON、或命令根本没写文件 → `json_error` **响亮**，不装成功；
③ 四个 handler（report_daily / cards_validate / chain_audit / report_skill_join）
   都接这条通道，且**人读 stdout 不被改动**（命令台照旧显示文本）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_HERE = Path(__file__).resolve().parent
VIEW_ROOT = _HERE.parent
sys.path.insert(0, str(VIEW_ROOT))

from actions import ensure_harvester_importable  # noqa: E402

_ERR = ensure_harvester_importable()
if _ERR is None:
    from actions import actionsserve  # noqa: E402

PAYLOAD = {"schema": "cards-validate/1",
           "generated_at": "2026-10-23T00:00:00+0800",
           "db_fingerprint": {"sessions": 3, "steps": 9, "errors": 1},
           "data": {"root": "x", "summary": {"cards": 2}, "cards": [{}, {}]}}


def _cfg(**over):
    base = dict(db=Path("h.db"), cards_root=Path("cards"),
                chain_root=Path("chains"), topics_meta=None,
                suggestions_meta=None, attrib_meta=None, reviews_meta=None,
                keywords_meta=None, artifacts_meta=None, exports_root=None)
    base.update(over)
    return SimpleNamespace(**base)


class _FakeCli:
    """假 CLI：把 JSON 写到 argv 里 `--json` 后面的路径（模拟真命令）。"""

    def __init__(self, payload=PAYLOAD, text="人读报告", write=True,
                 rc=0):
        self.payload, self.text, self.write, self.rc = payload, text, write, rc
        self.seen: list[list[str]] = []
        self.paths: list[Path] = []

    def __call__(self, argv):
        self.seen.append(list(argv))
        if "--json" in argv:
            p = Path(argv[argv.index("--json") + 1])
            self.paths.append(p)
            if self.write:
                p.write_text(json.dumps(self.payload, ensure_ascii=False),
                             encoding="utf-8")
        return {"exit_code": self.rc, "stdout": self.text, "stderr": ""}


class TestPlumbing(unittest.TestCase):
    def test_json_path_is_appended_and_read_back(self):
        fake = _FakeCli()
        with mock.patch.object(actionsserve, "_cli_report", fake):
            r = actionsserve._cli_report_both(["cards", "validate"])
        self.assertEqual(len(fake.paths), 1, "必须把 --json <路径> 追加进 argv")
        self.assertEqual(fake.seen[0][:2], ["cards", "validate"])
        self.assertIn("--json", fake.seen[0])
        self.assertEqual(r["stdout"], "人读报告", "人读 stdout 不许被改动")
        self.assertEqual(r["json"]["schema"], "cards-validate/1")
        self.assertFalse(fake.paths[0].exists(),
                         "临时文件必须删掉（不然每跑一次留一个垃圾）")

    def test_broken_json_is_loud(self):
        # 让假 CLI 往 --json 路径里写非 JSON
        def bad(argv):
            p = Path(argv[argv.index("--json") + 1])
            p.write_text("not json", encoding="utf-8")
            return {"exit_code": 0, "stdout": "文本", "stderr": ""}
        with mock.patch.object(actionsserve, "_cli_report", bad):
            r = actionsserve._cli_report_both(["x"])
        self.assertNotIn("json", r)
        self.assertIn("json_error", r, "坏了要响亮，不许静默空结构")

    def test_command_wrote_nothing_is_loud(self):
        fake = _FakeCli(write=False)
        with mock.patch.object(actionsserve, "_cli_report", fake):
            r = actionsserve._cli_report_both(["x"])
        self.assertNotIn("json", r)
        self.assertIn("json_error", r)
        self.assertIn("没有写出机读结构", r["json_error"])

    def test_no_temp_file_left_behind(self):
        fake = _FakeCli()
        with mock.patch.object(actionsserve, "_cli_report", fake):
            actionsserve._cli_report_both(["x"])
        self.assertFalse(any(p.exists() for p in fake.paths))


class TestHandlersUseIt(unittest.TestCase):
    """四个 handler 都要走这条通道（源码门，带调用上下文避免"别处有同名"）。"""

    def setUp(self):
        self.src = (VIEW_ROOT / "actions" / "actionsserve.py").read_text(
            encoding="utf-8")

    def _body(self, name: str) -> str:
        i = self.src.index(f"def {name}(")
        j = self.src.index("\ndef ", i + 1)
        return self.src[i:j]

    def test_four_handlers_use_both(self):
        for fn in ("_action_report_daily", "_action_cards_validate",
                   "_action_chain_audit", "_action_report_skill_join"):
            with self.subTest(handler=fn):
                body = self._body(fn)
                self.assertIn("_cli_report_both(argv)", body,
                              f"{fn} 没接人读+机读双出口")
                self.assertNotIn("_cli_report(argv)", body,
                                 f"{fn} 仍只给文本（结构拿不到）")

    def test_chain_audit_keeps_text_on_stdout(self):
        """chain-audit 的人读报告必须还在 stdout（不许改成 --json -）。"""
        body = self._body("_action_chain_audit")
        self.assertNotIn('"--json", "-"', body,
                         "用 --json - 会把报告挤到 stderr（页面看不到）")

    def test_helper_cannot_delegate_to_stdout_mode(self):
        body = self._body("_cli_report_both")
        self.assertIn('"--json", path', body)
        self.assertNotIn('"--json", "-"', body)


if __name__ == "__main__":
    unittest.main()