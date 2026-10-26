# -*- coding: utf-8 -*-
"""参数类型全覆盖门（2026-10-26 用户报障后的防复发门）。

**报障原话**（截图）：页面点「产物台账」→
`台账动作入队失败：动作参数类型未定义：group=enum`。

根因不是"旧进程不加载新代码"——注册表里 `group`/`mode` 声明成 `enum`，
而 `_clean_params` 当时根本没有 `enum` 分支。**重启服务也修不好**，因为
磁盘上的新代码同样不认识这个类型；那句「若刚升级代码…重启」把人引到了
错误方向。同类隐患还有一批：B 档 `normalize_params` 只认 `bool`，而注册表
发的 kind 名是 `switch`（force/allow_audit/vacuum/keep_sources 四个开关
全被 str() 成 "False"，而 "False" 是真值 → 下游 `bool(...)` 判成"开"）。

本门钉三件事：

1. **A 档**：`ACTIONS` 里每个参数按它声明的 `type` 真跑一遍 `_clean_params`，
   不认识就红。新增一个没人实现的类型 → 这条先红，不必等用户点出来。
2. **B 档**：`registrygen.build()` 里每个参数的 `kind` 真跑一遍
   `normalize_params`，未声明实现就红；`switch` 必须真收成 bool（"False"
   不许当"开"）。
3. **enum 语义**：`choices` 必须非空、`default` 必须在 choices 内、
   非法值报错**带出可选值**（不写清可选值 = 让人猜）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from actions import actionsserve as AS          # noqa: E402
from actions import registrygen, writeactions   # noqa: E402

#: 每种 A 档参数类型喂一个合法样例。**新类型必须先加到这里**——加不到就是
#: 本门的失败信号（真跑会抛「类型未定义」）。
SAMPLE = {
    "text": "x", "sid": "dsh:x", "topic_id": "tp-1", "level": "title",
    "path": "x.txt", "int": "3", "days": "7", "bool": "1",
    # enum 的样例取它自己的第一个 choice（见 _sample）
}


def _sample(spec: dict):
    typ = spec.get("type")
    if typ == "enum":
        choices = spec.get("choices") or []
        if not choices:            # 空 choices 的 enum 没有合法值可喂
            return None
        return choices[0]
    return SAMPLE.get(typ)


def _all_read_params():
    """(动作 id, 参数名, 参数 spec) —— 只取需要服务端校验的（server_choice 跳过）。"""
    for aid, spec in AS.ACTIONS.items():
        for name, ps in (spec.get("params") or {}).items():
            if ps.get("src") == "server_choice":
                continue
            yield aid, name, ps


class ReadParamsImplemented(unittest.TestCase):
    """A 档：声明的每个类型都必须真能跑。"""

    def test_every_type_is_implemented(self):
        bad = []
        for aid, name, ps in _all_read_params():
            sample = _sample(ps)
            if sample is None:
                bad.append("%s.%s: type=%r 没有可喂的样例（新类型？补 SAMPLE）"
                           % (aid, name, ps.get("type")))
                continue
            try:
                out = AS._clean_params({"params": {name: ps}}, {name: sample})
            except ValueError as e:
                bad.append("%s.%s: %s" % (aid, name, e))
                continue
            if name not in out:
                bad.append("%s.%s: 过滤后少了这个键" % (aid, name))
        self.assertEqual(bad, [], "有参数类型没实现：\n" + "\n".join(bad))

    def test_no_action_declares_empty_choices_enum(self):
        bad = [f"{aid}.{n}: enum 但没有 choices"
               for aid, n, ps in _all_read_params()
               if ps.get("type") == "enum" and not (ps.get("choices") or [])]
        self.assertEqual(bad, [])

    def test_enum_default_is_within_choices(self):
        bad = []
        for aid, n, ps in _all_read_params():
            if ps.get("type") != "enum":
                continue
            d = ps.get("default")
            ch = [str(c) for c in (ps.get("choices") or [])]
            if d is not None and str(d) not in ch:
                bad.append("%s.%s: default=%r 不在 %s 里" % (aid, n, d, ch))
        self.assertEqual(bad, [])

    def test_enum_rejects_unknown_value_and_lists_choices(self):
        spec = {"params": {"group": AS.ACTIONS["artifact_ledger"]["params"]["group"]}}
        with self.assertRaises(ValueError) as cm:
            AS._clean_params(spec, {"group": "zzz"})
        msg = str(cm.exception)
        for w in ("group", "topic", "session", "file"):
            self.assertIn(w, msg, "报错必须带出可选值，不能只说「类型未定义」")

    def test_enum_accepts_each_choice(self):
        spec = {"params": {"group": AS.ACTIONS["artifact_ledger"]["params"]["group"]}}
        for c in ("topic", "session", "file"):
            self.assertEqual(AS._clean_params(spec, {"group": c})["group"], c)
        spec2 = {"params": {"mode": AS.ACTIONS["review_pack"]["params"]["mode"]}}
        self.assertEqual(AS._clean_params(spec2, {"mode": "file"})["mode"], "file")

    def test_unknown_type_still_fails_loud(self):
        """未知类型必须报错（别为了"能跑"把它当 str 放过去）。"""
        spec = {"params": {"x": {"type": "no_such_type", "default": None}}}
        with self.assertRaises(ValueError) as cm:
            AS._clean_params(spec, {"x": "1"})
        self.assertIn("类型未定义", str(cm.exception))


class WriteParamsImplemented(unittest.TestCase):
    """B 档：注册表发的 kind 必须都被 normalize_params 认识。"""

    @classmethod
    def setUpClass(cls):
        cls.reg = registrygen.build()

    def test_every_kind_is_known(self):
        bad = []
        for a in self.reg["write_actions"]:
            for p in (a.get("params") or []):
                if p["kind"] not in writeactions.PARAM_KINDS:
                    bad.append("%s.%s kind=%r" % (a["action_id"], p["name"],
                                                  p["kind"]))
        self.assertEqual(bad, [], "写动作参数 kind 没实现：" + "；".join(bad))

    def test_switch_really_becomes_bool(self):
        """switch 的真值化：字符串 "False"/"0"/"" 不许被当成"开"。"""
        spec = {"params": [{"name": "force", "kind": "switch",
                            "server": False, "required": False,
                            "default": False, "label": "强制"}]}
        cases = [("1", True), ("true", True), ("on", True), (True, True),
                 ("0", False), ("false", False), ("", False),
                 (False, False), (None, False)]
        for raw, want in cases:
            got = writeactions.normalize_params(
                spec, {"force": raw}, None)["force"]
            self.assertIs(got, want, "force=%r 应收成 %r，实收 %r"
                          % (raw, want, got))

    def test_enum_rejects_unknown_value(self):
        spec = {"params": [{"name": "verdict", "kind": "enum",
                            "server": False, "required": True,
                            "choices": ["accepted", "rejected"],
                            "default": "accepted", "label": "裁决"}]}
        self.assertEqual(writeactions.normalize_params(
            spec, {"verdict": "accepted"}, None)["verdict"], "accepted")
        with self.assertRaises(writeactions.WriteError) as cm:
            writeactions.normalize_params(spec, {"verdict": "accept"}, None)
        self.assertIn("accepted", str(cm.exception))

    def test_unknown_kind_fails_loud(self):
        spec = {"params": [{"name": "x", "kind": "no_such_kind",
                            "server": False, "required": False,
                            "default": None, "label": "x"}]}
        with self.assertRaises(writeactions.WriteError) as cm:
            writeactions.normalize_params(spec, {"x": "1"}, None)
        self.assertIn("类型未定义", str(cm.exception))

    def test_required_still_enforced(self):
        """别为了加类型校验把必填校验挤掉。"""
        spec = {"params": [{"name": "reason", "kind": "str",
                            "server": False, "required": True,
                            "default": None, "label": "原因"}]}
        with self.assertRaises(writeactions.WriteError):
            writeactions.normalize_params(spec, {}, None)


class ServerChoiceParamsUnchanged(unittest.TestCase):
    """src=server_choice 的参数由服务端定值——客户端传什么都忽略。"""

    def test_client_value_is_ignored(self):
        found = 0
        for aid, spec in AS.ACTIONS.items():
            for name, ps in (spec.get("params") or {}).items():
                if ps.get("src") != "server_choice":
                    continue
                out = AS._clean_params({"params": {name: ps}},
                                       {name: "CLIENT_VALUE"})
                self.assertEqual(out[name], ps.get("default"),
                                 "%s.%s 应取服务端默认值" % (aid, name))
                found += 1
        self.assertGreaterEqual(found, 0)


class HealthReportsCodeStaleness(unittest.TestCase):
    """`/api/health` 必须能说出「这一套在跑旧代码」。

    2026-10-26 的现场：主机上同时活着两整套服务，页面连到哪套看谁先绑端口。
    人肉眼分不出，只能由服务端自己报——判据=进程导入时的代码指纹 vs 此刻
    磁盘上的指纹。
    """

    def _cfg(self):
        import tempfile
        td = Path(tempfile.mkdtemp())
        gl = td / "g.json"
        gl.write_text("{}", encoding="utf-8")
        return AS.Config(db=td / "h.db", cards_root=td / "cards",
                         glossary=gl, actions_meta=td / "am.db")

    def test_stamp_changes_when_file_changes(self):
        import os
        import tempfile
        d = Path(tempfile.mkdtemp())
        f = d / "a.py"
        f.write_text("x = 1\n", encoding="utf-8")
        s1 = AS.code_stamp([f])
        f.write_text("x = 22\n", encoding="utf-8")     # 大小变了
        s2 = AS.code_stamp([f])
        self.assertNotEqual(s1, s2, "文件改过而指纹没变＝这个判据是假的")
        os.utime(f, (1, 1))
        self.assertNotEqual(s2, AS.code_stamp([f]),
                            "mtime 改了指纹也该变（只比内容会漏掉纯时间戳差异）")

    def test_health_carries_the_fields(self):
        h = AS.health(self._cfg())
        for k in ("started_at", "pid", "code_stamp", "code_stamp_now",
                  "code_stale"):
            self.assertIn(k, h, "health 缺 %s——页面就没法判新旧" % k)
        self.assertIs(h["code_stale"], False,
                      "刚导入的进程应当不是旧代码（若为 True，说明指纹"
                      "基线取错了时机）")

    def test_stale_flips_when_disk_moved_on(self):
        old = AS._PROC_CODE_STAMP
        AS._PROC_CODE_STAMP = "deadbeefcafe"
        try:
            h = AS.health(self._cfg())
        finally:
            AS._PROC_CODE_STAMP = old
        self.assertIs(h["code_stale"], True,
                      "进程内的基线指纹与磁盘不一致时必须判 stale——"
                      "这正是「改了代码但没重启」的样子")


if __name__ == "__main__":
    unittest.main()
