# -*- coding: utf-8 -*-
"""P10：`cards_new` / `kb_publish` 两个 B 档写动作的门。

用户 2026-10-24 裁决：这两条命令要能在页面点执行（此前只能复制）。B 档的定义是
**预演零副作用 → confirm_token → 才写**，所以本文件的第一条就是"预演一个字节都不动"，
而不是"执行能出结果"——后者好测，也最容易被写成"预演就写了一半"。

其余三条：
- 执行真的写了该写的东西（卡片脚手架有真锚点；镜像位收到文件）；
- **幂等**：发布第二次 0 复制（重复点不会反复改镜像）；
- **只喂不收**：镜像位里多出来的文件永远不许被删（这是 `publish_master` 的纪律）。
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

VW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VW))

from actions import actionsserve as AS          # noqa: E402
from actions import registrygen as RG           # noqa: E402
from actions import writeactions as WA          # noqa: E402


def _spec(aid: str) -> dict:
    for a in RG.build()["write_actions"]:
        if a["action_id"] == aid:
            return a
    raise AssertionError("注册表里没有 %s" % aid)


def _cfg(td: Path) -> AS.Config:
    return AS.Config(db=td / "harvester.db", cards_root=td / "cards",
                     glossary=td / "glossary.json",
                     actions_meta=td / "actions_meta.db")


def _make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        "CREATE TABLE sessions (sid TEXT, session_id TEXT, title TEXT,"
        " created_at TEXT, source TEXT);"
        "CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,"
        " status TEXT, error TEXT);")
    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                ("dsh:abc", "abc", "改文件前没读原文", "2026-10-24 08:00:00",
                 "dsh"))
    con.commit()
    con.close()


class TestCardsNew(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        _make_db(self.td / "harvester.db")
        self.cfg = _cfg(self.td)

    def tearDown(self):
        self._td.cleanup()

    def test_preview_writes_nothing(self):
        """预演 = 零副作用：连卡片目录都不许建，更不许有文件。"""
        prev = WA.plan("cards_new", _spec("cards_new"),
                       {"sid": "dsh:abc", "type": "pitfall"}, self.cfg)
        self.assertTrue(prev["human"])
        self.assertIn("脚手架", prev["human"][0])
        self.assertFalse((self.td / "cards").exists(),
                         "预演把卡片目录建出来了——预演不是只读")
        self.assertEqual(list((self.td / "cards").glob("*.md"))
                         if (self.td / "cards").exists() else [], [])
        self.assertIn(str(self.td / "cards"), prev["target_path"])

    def test_execute_writes_card_with_real_anchor(self):
        out = WA.WRITE_ACTIONS["cards_new"]["execute"](
            {"sid": "dsh:abc", "type": "pitfall"}, self.cfg)
        p = Path(out["path"])
        self.assertTrue(p.is_file())
        text = p.read_text(encoding="utf-8")
        self.assertIn("type: pitfall", text)
        self.assertIn('session_id: "abc"', text)     # 锚点是真的会话 id
        self.assertIn("<待补", text)                  # evidence/正文留白
        # 不覆盖既有卡：再生成一次必须是新文件
        out2 = WA.WRITE_ACTIONS["cards_new"]["execute"](
            {"sid": "dsh:abc", "type": "pitfall"}, self.cfg)
        self.assertNotEqual(Path(out2["path"]).name, p.name)

    def test_bad_sid_fails_loud(self):
        with self.assertRaises(WA.WriteError):
            WA.plan("cards_new", _spec("cards_new"), {}, self.cfg)
        with self.assertRaises(WA.WriteError):
            WA.plan("cards_new", _spec("cards_new"),
                    {"sid": "nope-nope"}, self.cfg)


class TestKbPublish(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        (self.td / "cards").mkdir(parents=True)
        (self.td / "topics").mkdir(parents=True)
        (self.td / "cards" / "kc-1.md").write_text(
            "card", encoding="utf-8")
        (self.td / "topics" / "t1.md").write_text(
            "topic", encoding="utf-8")
        self.cfg = _cfg(self.td)
        self.cfg.kb_mirror = self.td / "mirror"

    def tearDown(self):
        self._td.cleanup()

    def test_preview_writes_nothing(self):
        prev = WA.plan("kb_publish", _spec("kb_publish"), {}, self.cfg)
        self.assertFalse((self.td / "mirror").exists(),
                         "预演把镜像位建出来了——预演不是只读")
        text = "\n".join(prev["human"])
        self.assertIn("只喂不收", text)
        self.assertIn("2", text)                     # 会复制 2 个文件
        self.assertEqual(prev["target_path"], str(self.td / "mirror"))

    def test_execute_copies_and_is_idempotent(self):
        out = WA.WRITE_ACTIONS["kb_publish"]["execute"]({}, self.cfg)
        self.assertTrue((self.td / "mirror" / "cards" / "kc-1.md").is_file())
        self.assertTrue((self.td / "mirror" / "topics" / "t1.md").is_file())
        self.assertIn("发布", out["summary"])
        rep = out["report"]
        self.assertEqual(rep["cards"]["copied"], 1)
        # 幂等：正本没变 → 第二次 0 复制（不反复改镜像）
        prev = WA.plan("kb_publish", _spec("kb_publish"), {}, self.cfg)
        self.assertTrue(prev["noop"], "第二次预演应是 no-op（全部已最新）")

    def test_never_deletes_mirror_extra_files(self):
        """只喂不收：镜像位里多出来的文件不许被删（WorkBuddy 那份归它自己管）。"""
        extra = self.td / "mirror" / "cards" / "other.md"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("别人的", encoding="utf-8")
        WA.WRITE_ACTIONS["kb_publish"]["execute"]({}, self.cfg)
        self.assertTrue(extra.is_file(), "发布把镜像位里的多余文件删了")


class TestExportSavePlumbing(unittest.TestCase):
    """`save` 从页面 → `_clean_params` → handler → CLI `--save` 的整条接线。

    这条最容易假绿：`_clean_params` 只认声明过的键，`save` 不进动作表就会被
    **静默丢掉**（页面点了"落盘"，服务端照旧只打印），所以两段都要钉。
    """

    def test_bool_param_coerced(self):
        spec = {"params": {"save": {"type": "bool", "default": 0}}}
        for raw, want in (({"save": "1"}, 1), ({"save": 1}, 1),
                          ({"save": "true"}, 1), ({"save": "0"}, 0),
                          ({"save": "no"}, 0), ({}, 0)):
            self.assertEqual(AS._clean_params(spec, raw)["save"], want, raw)

    def test_action_passes_save_flag(self):
        seen = {}

        def fake_report(argv):
            seen["argv"] = list(argv)
            return {"stdout": "stub", "exit_code": 0}

        orig = AS._cli_report
        AS._cli_report = fake_report
        try:
            cfg = _cfg(Path(tempfile.mkdtemp()))
            AS._action_export_analysis(cfg, {"kind": "errors", "save": 1})
            self.assertIn("--save", seen["argv"])
            AS._action_export_analysis(cfg, {"kind": "errors", "save": 0})
            self.assertNotIn("--save", seen["argv"])
        finally:
            AS._cli_report = orig

    def test_action_declares_save(self):
        """动作表里必须声明：否则 `_clean_params` 会把它丢掉（本类第 2 条会红）。"""
        spec = AS.ACTIONS["export_analysis"]["params"]
        self.assertIn("save", spec)
        self.assertEqual(spec["save"]["type"], "bool")


if __name__ == "__main__":
    unittest.main()