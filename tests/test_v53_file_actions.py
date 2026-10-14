# -*- coding: utf-8 -*-
"""v0.62 写工作文件类 B 档动作：suggest_agents / draft_pack。

用户令（2026-10-14）：命令不再只给"复制"，命令台要能就地执行（B 档两段提交）。
这两个动作写的是**主库仓内工作文件**（建议池 md / 蒸馏包 md），验收四条：

1. **预演无副作用**：plan 前后索引库字节不变（B 档铁律，与 test_v36 同口径）；
2. **执行真的写**：目标文件产出、内容非空；覆盖前自动备份 .bak-<时间戳>，
   备份内容 = 旧文件；
3. **不越界**：只写那一个目标文件；sid 不存在 / 卡型非法 / 缺 sid → WriteError
   （400），绝不静默；
4. **路径服务端定**：目标路径由服务端拼（auto_out 语义），客户端传什么都不改
   目的地（normalize_params 丢弃未声明键，plan 里也不读任何路径参数）。

夹具：**最小自建索引库**（sessions/messages/steps 三表最小列集，绝不动真库）。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS  # noqa: E402
from actions import writeactions as WA  # noqa: E402


def _make_cfg(td: Path) -> AS.Config:
    glossary = td / "glossary.json"
    glossary.write_text("{}", encoding="utf-8")
    cards = td / "cards"
    cards.mkdir(exist_ok=True)
    return AS.Config(db=td / "harvester.db", cards_root=cards,
                     glossary=glossary,
                     actions_meta=td / "actions_meta.db")


def _make_db(db: Path) -> None:
    """最小索引库：一个会话、两条消息、一个错误步 + 一个后续 call（可自愈）。"""
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE sessions (sid TEXT PRIMARY KEY, source TEXT,"
        " session_id TEXT, title TEXT, model TEXT);"
        "CREATE TABLE messages (sid TEXT, role TEXT, ts TEXT,"
        " text TEXT, raw TEXT);"
        "CREATE TABLE steps (sid TEXT, seq INTEGER, ts TEXT, tool TEXT,"
        " phase TEXT, status TEXT, error TEXT, detail TEXT);")
    con.execute("INSERT INTO sessions VALUES ('sid:test-1','dsh','uuid-1',"
                "'测试会话','test-model')")
    con.execute("INSERT INTO messages VALUES ('sid:test-1','user',"
                "'2026-10-14 10:00:00','改文件报错了','改文件报错了')")
    con.execute("INSERT INTO messages VALUES ('sid:test-1','assistant',"
                "'2026-10-14 10:01:00','重读再改即可','重读再改即可')")
    con.execute("INSERT INTO steps VALUES ('sid:test-1',1,"
                "'2026-10-14 10:00:30','webfetch','result','error',"
                "'fetch failed: ECONNRESET','瞬时网络抖动')")
    con.execute("INSERT INTO steps VALUES ('sid:test-1',2,"
                "'2026-10-14 10:00:40','edit','call','ok',NULL,NULL)")
    con.commit()
    con.close()


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        _make_db(self.td / "harvester.db")
        self.cfg = _make_cfg(self.td)

    def tearDown(self):
        self._td.cleanup()

    @staticmethod
    def _fp(p: Path) -> bytes:
        return Path(p).read_bytes()

    def _spec(self, action_id: str) -> dict:
        import json
        reg = __import__("json").loads(
            (ROOT / "actions" / "actions_registry.json")
            .read_text(encoding="utf-8"))
        return {a["action_id"]: a for a in reg["write_actions"]}[action_id]


class SuggestAgentsTest(_Fixture):
    def test_plan_no_side_effects_and_counts(self):
        dbp = self.td / "harvester.db"
        before = self._fp(dbp)
        prev = WA.plan("suggest_agents", self._spec("suggest_agents"),
                       {"min_count": 1, "max": 15}, self.cfg)
        self.assertEqual(self._fp(dbp), before, "plan 改了索引库！")
        self.assertEqual(prev["count"], 1,
                         "fetch failed 应命中内置网络模板，合并后 1 条候选")
        self.assertTrue(Path(prev["target_path"])
                        == self.td / "agents_suggestions.md")
        self.assertFalse(any(Path(self.td).glob("agents_suggestions*")),
                         "plan 不该落任何文件")

    def test_execute_writes_and_backs_up(self):
        out = self.td / "agents_suggestions.md"
        out.write_text("旧内容", encoding="utf-8")
        r = WA.execute("suggest_agents", self._spec("suggest_agents"),
                       {"min_count": 1, "max": 15}, self.cfg)
        self.assertTrue(out.is_file())
        self.assertNotEqual(out.read_text(encoding="utf-8"), "旧内容")
        baks = list(self.td.glob("agents_suggestions.md.bak-*"))
        self.assertEqual(len(baks), 1, "覆盖前必须留 .bak")
        self.assertEqual(baks[0].read_text(encoding="utf-8"), "旧内容")
        self.assertIn("AGENTS.md", r["summary"])

    def test_client_cannot_steer_out_path(self):
        """客户端传 out/路径类键 → 被丢弃，目的地不变（auto_out 语义）。"""
        prev = WA.plan("suggest_agents", self._spec("suggest_agents"),
                       {"out": "C:/Windows/evil.md", "min_count": 1},
                       self.cfg)
        self.assertEqual(Path(prev["target_path"]),
                         self.td / "agents_suggestions.md")

    def test_missing_db_is_write_error(self):
        (self.td / "harvester.db").unlink()
        with self.assertRaises(WA.WriteError):
            WA.plan("suggest_agents", self._spec("suggest_agents"), {},
                    self.cfg)


class DraftPackTest(_Fixture):
    def test_plan_and_execute_roundtrip(self):
        dbp = self.td / "harvester.db"
        before = self._fp(dbp)
        params = {"sid": "sid:test-1", "type": "pitfall"}
        prev = WA.plan("draft_pack", self._spec("draft_pack"),
                       dict(params), self.cfg)
        self.assertEqual(self._fp(dbp), before, "plan 改了索引库！")
        self.assertGreater(prev["bytes"], 0, "预演就该算出包大小")
        self.assertIn("docs", prev["target_path"])
        self.assertNotIn("sid:test-1", Path(prev["target_path"]).name,
                         "sid 原文含冒号斜杠，不该直接当文件名")
        r = WA.execute("draft_pack", self._spec("draft_pack"),
                       dict(params), self.cfg)
        out = Path(r["path"])
        self.assertTrue(out.is_file())
        content = out.read_text(encoding="utf-8")
        self.assertIn("蒸馏包", content)
        self.assertIn("改文件报错了", content, "包里必须有会话原文")
        # 幂等重跑：同名覆盖，且留 .bak
        WA.execute("draft_pack", self._spec("draft_pack"),
                   dict(params), self.cfg)
        self.assertEqual(len(list(out.parent.glob(out.name + ".bak-*"))), 1)

    def test_bad_sid_is_write_error(self):
        with self.assertRaises(WA.WriteError):
            WA.plan("draft_pack", self._spec("draft_pack"),
                    {"sid": "sid:不存在"}, self.cfg)

    def test_bad_type_is_write_error(self):
        with self.assertRaises(WA.WriteError):
            WA.plan("draft_pack", self._spec("draft_pack"),
                    {"sid": "sid:test-1", "type": " nonsense"}, self.cfg)

    def test_missing_sid_is_write_error(self):
        with self.assertRaises(WA.WriteError):
            WA.plan("draft_pack", self._spec("draft_pack"), {}, self.cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
