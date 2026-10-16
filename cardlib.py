# -*- coding: utf-8 -*-
"""cardlib -- 知识卡片 frontmatter 只读解析（工程决策 D9，用户 2026-10-12 批准）。

为什么存在：P1-3 要在 view 上渲染卡片本体（标题/结论/证据片段/锚点/置信度），
但兄弟仓库 session-harvester 的 /api/cards 实测只回校验结果
（每卡 {path, errors, warnings}，见 harvester/cards.py validate_cards 返回结构），
本体字段（frontmatter）从未透出。铁律 #2 禁止改 session-harvester，
故解析在本仓库内自建：只读卡片 md 文件，绝不写入。

口径纪律：
- 只解析、不校验：校验口径的唯一权威是 session-harvester 的 validate_cards
  （其结果由上游 /api/cards 提供，页面两路合并）。本模块对缺字段等一律宽容
  （返回 None / 空结构），不重复发明校验、不产生第二套真伪结论。
- 锚点形态：cards 库真实形态为
  anchors: [{session_id: "dsh:...session.v4.jsonl.zstd", turn: 263}]
  session_id 可能很长，页面显示时交给前端截断；这里原样透出。
- YAML 解析不引第三方依赖（view 仓零依赖纪律）：卡片 frontmatter 是
  cards new 脚手架产生的受限子集（标量/内联序列/单层块标量），
  用专用小解析器覆盖该子集；解析失败 -> parsed=False + 原文保留，
  绝不吞内容。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

__all__ = ["parse_card", "scan_cards", "cards_payload"]

_FM_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.S)
_KV_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")


def _scalar(s):
    """受限标量：去引号 / 布尔 / 数值；其余原样字符串。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _inline_list(s):
    """解析受限内联序列。元素可为标量或 {k: v, ...} 单层映射。
    解析失败抛 ValueError——调用方据此保留原值。"""
    s = s.strip()
    if not (s.startswith("[") and s.endswith("]")):
        raise ValueError("not an inline list: %r" % s[:40])
    inner = s[1:-1].strip()
    if not inner:
        return []
    parts, depth, quote, cur = [], 0, None, ""
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
        elif ch in "[{":
            depth += 1
            cur += ch
        elif ch in "]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    out = []
    for p in parts:
        p = p.strip()
        if p.startswith("{") and p.endswith("}"):
            obj = {}
            for kv in re.split(r"\s*,\s*(?=(?:[^\"']*\"[^\"']*\")*[^\"']*$)", p[1:-1].strip()):
                if ":" not in kv:
                    raise ValueError("map item without colon: %r" % kv[:40])
                k, v = kv.split(":", 1)
                obj[k.strip()] = _scalar(v)
            out.append(obj)
        else:
            out.append(_scalar(p))
    return out


def _parse_frontmatter(text):
    """返回 (fm, body)。fm 为 None 表示 frontmatter 缺失/不可解析。"""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    fm = {}
    cur_key = None
    block = []
    for raw in m.group(1).splitlines():
        line = raw.rstrip("\r")
        if cur_key is not None and (line.startswith(("  ", "\t")) or line == ""):
            block.append(line.strip())
            continue
        if cur_key is not None:
            fm[cur_key] = "\n".join(block)
            cur_key, block = None, []
        kv = _KV_RE.match(line)
        if not kv:
            continue  # 容忍注释/空行等行
        key, val = kv.group(1), kv.group(2).strip()
        if val in ("|", "|-", ">", ">-"):
            cur_key, block = key, []
        elif val.startswith("["):
            try:
                fm[key] = _inline_list(val)
            except ValueError:
                fm[key] = val  # 原样保留，不吞内容
        else:
            fm[key] = _scalar(val)
    if cur_key is not None:
        fm[cur_key] = "\n".join(block)
    return fm, text[m.end():]


_H2_RE = re.compile(r"^##\s+", re.M)


def _conclusion(body):
    """结论抽取：真卡正文以「## 现象」开头——第一节非空行即一句话结论。
    无小节标题时取正文第一个非空行。"""
    body = body.strip()
    if not body:
        return ""
    if _H2_RE.match(body):
        lines = []
        for ln in body.splitlines()[1:]:
            if ln.startswith("## "):
                break
            if ln.strip():
                lines.append(ln.strip())
        return " ".join(lines)[:300]
    for ln in body.splitlines():
        if ln.strip():
            return ln.strip()[:300]
    return ""


def parse_card(path):
    """解析单张卡片 -> 本体 dict（永不抛异常；解析失败也返回结构）。

    字段（P1-3 渲染所需最小集，additive 哲学：宁可少给不可错给）：
    path / parsed / id / title / type / confidence / evidence /
    summary（v0.76 V5：主仓 v0.74 拆角色后的综述字段，旧卡为空串） /
    conclusion / anchors[{sid, turn, raw}] / tags / created / body_chars
    """
    out = {"path": str(path), "parsed": False, "id": None, "title": None,
           "type": None, "confidence": None, "evidence": "",
           "summary": "",
           "conclusion": "", "anchors": [], "tags": [], "created": None,
           "body_chars": 0}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    fm, body = _parse_frontmatter(text)
    out["body_chars"] = len(body or "")
    if fm is None:
        return out
    out["parsed"] = True
    for k in ("id", "title", "type", "created"):
        v = fm.get(k)
        out[k] = str(v) if v is not None else None
    conf = fm.get("confidence")
    try:
        out["confidence"] = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        out["confidence"] = None
    ev = fm.get("evidence")
    out["evidence"] = str(ev).strip() if ev else ""
    # v0.76 V5：summary 综述字段（主仓 v0.74 拆角色）。additive——
    # 旧卡无此键保持空串，渲染层按 falsy 跳过。
    sm = fm.get("summary")
    out["summary"] = str(sm).strip() if sm else ""
    tags = fm.get("tags")
    out["tags"] = tags if isinstance(tags, list) else []
    anchors = fm.get("anchors")
    if isinstance(anchors, list):
        for a in anchors:
            if isinstance(a, dict):
                out["anchors"].append({
                    "sid": str(a.get("session_id") or ""),
                    "turn": a.get("turn") if isinstance(a.get("turn"), int) else 0,
                    "raw": a})
    out["conclusion"] = _conclusion(body)
    return out


def scan_cards(root):
    """扫描目录下全部 *.md（跳过临时/隐藏名），按相对路径排序。
    与 session-harvester validate_cards 的遍历面保持同形（rglob *.md），
    但内容口径独立：只读解析，不校验。"""
    cards = []
    for p in sorted(root.rglob("*.md")):
        if p.name.startswith(("~", ".")):
            continue
        rel = p.relative_to(root)
        c = parse_card(p)
        c["path"] = str(rel)
        cards.append(c)
    return cards


def cards_payload(root):
    """/view/cards 响应体（含 additive 版本号，方便前端握手）。"""
    root = Path(root)
    return {"view_api": 1, "root": str(root),
            "cards": scan_cards(root) if root.is_dir() else []}


def _selftest():  # pragma: no cover
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "demo.md"
        p.write_text(
            "---\nid: kc-x-0001\ntitle: 标题甲\ntype: pitfall\n"
            'anchors: [{session_id: "dsh:a/session.v4.jsonl.zstd", turn: 3}]\n'
            "evidence: |\n  证据引文甲\n"
            "summary: |\n  综述甲\n"
            "confidence: 0.9\ncreated: 2026-10-06\n"
            "---\n## 现象\n\n一句话结论甲\n\n## 做法\n\n1. 先读后写\n",
            encoding="utf-8")
        got = parse_card(p)
        assert got["parsed"] and got["title"] == "标题甲", got
        assert got["anchors"] and got["anchors"][0]["sid"].startswith("dsh:")
        assert got["anchors"][0]["turn"] == 3
        assert got["conclusion"] == "一句话结论甲", got
        assert got["confidence"] == 0.9
        assert got["evidence"] == "证据引文甲"
        assert got["summary"] == "综述甲"
        assert json.dumps(got, ensure_ascii=False)
    print("cardlib selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())