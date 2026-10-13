# -*- coding: utf-8 -*-
"""registrygen -- 生成**冻结的动作注册表** `actions_registry.json`（P3-1，SOP D5）。

分工（与 D11 同一纪律：**事实由代码派生，判断由人写**）：

- `actions/commands.json`（P2-2 生成物）＝**事实**：命令名、参数名、参数类型与
  默认值、参数数据源 `src`、三档 `tier`、聚合命令的子动作分档；
- `actions/write_actions.json`＝**判断**：哪些写动作允许经由本服务落库、
  写哪个 meta 库、风险等级（reversible / destructive）、确认页怎么说人话、
  动作参数 → CLI 参数的映射。

本模块把两者**合并**成一张表（`actions_registry.json`），并在合并时报错：

- 引用了不存在的命令 / 子动作 → 直接失败（不静默丢动作）；
- 声明的参数在真 CLI 里不存在 → 直接失败（否则到运行时才 500）；
- **真 CLI 里的写参数没被声明** → 直接失败（这是最危险的一类：漏一声明，
  就等于页面无论如何都写不了那个字段，而用户以为能写）。

产物是**功能性数据**（被 `actionsserve` 消费），随 view 仓入库（铁律 1.3）。

用法：
    python -m actions.registrygen            # 写 actions_registry.json
    python -m actions.registrygen --check    # 只比对，不写（漂移退 1）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMANDS = HERE / "commands.json"
WRITES = HERE / "write_actions.json"
OUT = HERE / "actions_registry.json"

#: A 档（只读直执）动作 id → 对应 CLI 命令。与 `actionsserve.ACTIONS` 同集合，
#: 但**这里只记录映射**（谁是真源？`actionsserve.ACTIONS` 是可执行的实现，
#: 本表是给人看的契约）。测试会比对两者，防"注册表说能做、代码里没有"。
#: 聚合命令（如 topic）的子动作级动作写 `(cmd, subaction)` 二元组——
#: 生成时会校验该子动作在真 CLI 里存在且为 A 档（写子动作绝不进这张表）。
READ_ACTIONS: dict[str, object] = {
    "report_tools": "report-tools",
    "report_errors": "report-errors",
    "topic_list": "topic",
    "topic_pack": ("topic", "pack"),
    "regress": "regress",
}

#: 参数的 src 取值里，"路径类、页面不暴露"的那一类：B 档动作的这类参数
#: 由服务端起服时定（`Config.default_for`），确认页要**显式印出库路径**，
#: 否则用户点了"确认"却不知道写进哪个文件。
SERVER_SRC = "server"


def _load(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit("[registrygen] %s 不可读/损坏（fail loud）: %s"
                         % (what, e))


def _commands() -> dict:
    d = _load(COMMANDS, "commands.json")
    return {c["cmd"]: c for c in d["commands"]}


def _spec_for(cmd: dict, subaction: str | None) -> dict:
    """该命令的参数事实（含子动作的档位校验）。"""
    if subaction is None:
        return cmd
    subs = {s["name"]: s["tier"] for s in (cmd.get("subactions") or [])}
    if subaction not in subs:
        raise SystemExit("[registrygen] 命令 %s 没有子动作 %r（真 CLI 里只有 %s）"
                         % (cmd["cmd"], subaction, sorted(subs)))
    return cmd


def build() -> dict:
    cmds = _commands()
    spec = _load(WRITES, "write_actions.json")

    actions: list[dict] = []
    for raw in spec["actions"]:
        aid = raw["action_id"]
        cmdname = raw["cmd"]
        cmd = cmds.get(cmdname)
        if cmd is None:
            raise SystemExit("[registrygen] 写动作 %s 引用了不存在的命令 %r"
                             % (aid, cmdname))
        _spec_for(cmd, raw.get("subaction"))
        argfacts = {a["dest"]: a for a in cmd["args"]}
        params: list[dict] = []
        for pname, p in raw["params"].items():
            dest = p["map"]
            fact = argfacts.get(dest)
            if fact is None:
                raise SystemExit(
                    "[registrygen] 写动作 %s 声明了参数 %s→--%s，但命令 %s 里"
                    "没有这个参数（真参数：%s）"
                    % (aid, pname, dest.replace("_", "-"), cmdname,
                       sorted(argfacts)))
            params.append({
                "name": pname,
                "dest": dest,
                "arg": fact["name"],
                "kind": fact["kind"],
                "src": fact["src"],
                "server": fact["src"] == SERVER_SRC,
                "required": bool(p.get("required")),
                "choices": fact.get("choices"),
                "default": fact.get("default"),
                "note": fact.get("note"),
                "label": p.get("label") or fact["name"],
            })
        # **双向检查**（只按"该动作真正消费的字段"比，聚合命令尤其重要）：
        # 一个动作声明了它**不消费**的参数，是这个动作最危险的缺陷——
        # 用户以为填了会生效，实际被静默丢弃。故每个写动作必须显式声明
        # `allow`（= 该子动作从 argparse 命名空间读的字段，照 cli.py 抄）。
        declared = {p["dest"] for p in params}
        # 位置参数（`topic` 的 `cmd` / `weblogin` 的 `action`）是**子动作选择器**
        # 本身，不是可选参数，故不在 args 事实表里，也不该由页面传值。
        allow = set(raw.get("allow") or []) - {"cmd", "action"}
        if not allow:
            raise SystemExit("[registrygen] 写动作 %s 缺 allow 声明（必须列出"
                             "该子动作真正消费的参数）" % aid)
        unknown = allow - set(argfacts)
        if unknown:
            raise SystemExit("[registrygen] 写动作 %s 的 allow 里有真 CLI 不存在"
                             "的参数 %s" % (aid, sorted(unknown)))
        unused = declared - allow
        if unused:
            raise SystemExit(
                "[registrygen] 写动作 %s 声明了它**不消费**的参数 %s —— "
                "填了不生效却看起来能写，必须删掉或补上真正的消费逻辑"
                % (aid, sorted(unused)))
        # allow 里扣掉声明过的（页面会传），剩下的只允许是**服务端已定的路径类**
        # 参数（如 `--meta`：CLI 真读它，但值由起服时定，页面不暴露也不传）。
        # 别的剩余一律报错——那意味着"CLI 会读某个字段，而这里既没声明也没绑定"。
        unbound = [k for k in sorted(allow - declared)
                   if argfacts[k]["src"] != SERVER_SRC]
        if unbound:
            raise SystemExit(
                "[registrygen] 写动作 %s 的 allow 里 %s 既未声明为可传参数、"
                "也不是服务端路径类参数" % (aid, unbound))
        tier = ("B" if raw.get("subaction") is None
                else _subaction_tier(cmd, raw["subaction"]))
        actions.append({
            "action_id": aid,
            "tier": tier,
            "cmd": cmdname,
            "subaction": raw.get("subaction"),
            "risk": raw["risk"],
            "target": raw["target"],
            "summary": raw["summary"],
            "detail": raw.get("detail") or "",
            "params": params,
            "writes": ("%s（两段提交：先拿 confirm_token，带 token 再执行；"
                       "每次执行进 actions_meta.db 台账）" % raw["target"]),
            "reversible": {
                "reversible": "幂等/可回到原状（仍有台账留痕）",
                "destructive": "会丢信息（源主题行/历史 run 删除后页面上找不回）",
            }[raw["risk"]],
        })
    actions.sort(key=lambda a: a["action_id"])

    read_actions = []
    for aid, tgt in sorted(READ_ACTIONS.items()):
        sub: str | None = None
        if isinstance(tgt, tuple):
            cmdname, sub = tgt
        else:
            cmdname = tgt
        cmd = cmds.get(cmdname)
        if cmd is None:
            raise SystemExit("[registrygen] A 档动作 %s 引用了不存在的命令 %r"
                             % (aid, cmdname))
        if sub is not None:
            if _subaction_tier(cmd, sub) != "A":
                raise SystemExit("[registrygen] A 档动作 %s 指向的子动作 "
                                 "%s %s 不是 A 档（写子动作绝不进 READ_ACTIONS）"
                                 % (aid, cmdname, sub))
        entry: dict = {"action_id": aid, "tier": "A", "cmd": cmdname,
                       "est_sec": None}
        if sub is not None:
            entry["subaction"] = sub
        read_actions.append(entry)

    cards = _load(COMMANDS, "commands.json")["cards"]
    return {
        "schema": 1,
        "kind": "harvester-view 动作注册表 v1（冻结白名单；SOP D5/D11）",
        "generated_by": "actions/registrygen.py（python -m actions.registrygen）",
        "hint": "三个档：A 只读直接执行 / B 写 meta 两段提交 / C 只生成命令永不执行。"
                "本表由 commands.json（事实）+ write_actions.json（人写的判断）合并生成，"
                "合并时做双向检查（引用不存在的命令/参数 → 失败；真 CLI 的写参数漏声明 → 失败）。"
                "执行器只认本表：表外动作一律 403。",
        "tiers": {
            "A": "只读直接执行（写 reports/ 与临时目录不算写库）",
            "B": "写 meta 库：两段提交（confirm_token）+ 台账留痕",
            "C": "只生成命令，页面永不执行",
        },
        "write_targets": {
            "suggestions_meta": "suggestions_meta.db（建议池状态与人账）",
            "topics_meta": "topics_meta.db（主题注册表）",
            "keywords_meta": "keywords_meta.db（n-gram 统计累积表）",
        },
        "cards": cards,
        "read_actions": read_actions,
        "write_actions": actions,
        "counts": {
            "cards": len(cards),
            "read_actions": len(read_actions),
            "write_actions": len(actions),
            "write_reversible": len([a for a in actions
                                     if a["risk"] == "reversible"]),
            "write_destructive": len([a for a in actions
                                      if a["risk"] == "destructive"]),
        },
    }


def _subaction_tier(cmd: dict, sub: str) -> str:
    for s in cmd.get("subactions") or []:
        if s["name"] == sub:
            return s["tier"]
    raise SystemExit("[registrygen] %s 无子动作 %s" % (cmd["cmd"], sub))


def check(path: Path = OUT) -> list[str]:
    """比对磁盘产物与重新生成的结果。返回差异清单（空=一致）。"""
    want = build()
    if not path.exists():
        return ["%s 不存在" % path]
    got = _load(path, path.name)
    diffs: list[str] = []
    for field in ("cards", "read_actions", "write_actions", "counts"):
        if got.get(field) != want[field]:
            diffs.append("%s 不一致" % field)
    return diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="actions.registrygen")
    ap.add_argument("--check", action="store_true", help="只比对不写")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    path = Path(args.out)
    if args.check:
        diffs = check(path)
        if diffs:
            print("[registrygen] 注册表已漂移：")
            for d in diffs:
                print("  - " + d)
            return 1
        print("[registrygen] OK：%s 与事实层一致" % path.name)
        return 0
    obj = build()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    c = obj["counts"]
    print("[registrygen] 写入 %s：A 档 %d / B 档 %d（可逆 %d、破坏性 %d）/ %d 卡"
          % (path, c["read_actions"], c["write_actions"], c["write_reversible"],
             c["write_destructive"], c["cards"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
