# -*- coding: utf-8 -*-
"""三类计数与分级的**唯一判据**（用户 2026-10-23 批准的第②步）。

## 为什么要有这个文件

页面原先在**两处**各算一遍同一件事：`actions/workitems.py` 的 `_targets_all`
（`"ok" if count == 0 else "warn"`）与 `actions/overview.py` 的队列明细段
（`"ok" if not a["value"] else "warn"`）。两处都是"非零即黄"，于是首页 18 个
target 里 14 个全黄——**全库错误总数 532**、**已拍板 24**、**已登记 skill 38**
这些"累计量"也在报警。颜色因此不再携带信息（用户原话：异常"不是太少，而是
偏多，失衡了"）。本文件把判据收成一处，两处都调它。

## 三类（**本项目自定义**，按用户 2026-10-23 的决定）

| kind | 含义 | 分级 |
|---|---|---|
| `action` 待处理量 | 有人要动手的活 | `0` = ok；`1–2` = **watch**（零星，不报警，虚线框）；`≥3` = warn |
| `stock` 累计量/已完成 | 只说明"现在有多少"，不是活 | **永远 info**（中性色，不作警报） |
| `rate` 比率 | 失败率这类比值 | 必须**样本 ≥20 且 ≥10%** 才 warn；否则 ok |

阈值放在本文件顶部；页面只读 `kind`/`level` 渲染，**不自己拍数字**（否则又是
第二套口径）。`explain()` 给人话理由，供悬停与"被滤掉 N 项"的说明复用。
"""
from __future__ import annotations

#: 待处理量：到这个数才算"要处理"（1–2 条归"零星"，不报警）
ACTION_MIN = 3
#: 待处理量：0 < count < ACTION_MIN 时进 watch（虚线框、不进"需处理"区）
WATCH_MAX = ACTION_MIN - 1
#: 比率：样本下限（低于它不下结论——"1 次调用 1 次失败"也是 100%）
RATE_MIN_SAMPLE = 20
#: 比率报警线（10%）
RATE_MIN = 0.10
#: 新意：**首次出现**的坑从 1 次起就算信号（与次数无关）。
#: 页面用它的地方叫「首见」：窗口内首见、且次数低于复发阈值的那几条，
#: 被滤掉时必须单独报数（用户 2026-10-23 批准的方案 B）。1 = 首见即算。
NEW_PATTERN_MIN = 1

#: 阈值登记表（方案 B 的核心）：**每个数字都要说清用途**。
#: 页面/报告只读这张表里的数，不许各自拍数字——四种含义混用正是
#: "同一个词四种口径"漂移的根源（用户 2026-10-23 的判词）。
THRESHOLDS = (
    {"id": "recur", "name": "复发次数", "value": ACTION_MIN,
     "applies": "错误模式 / 根因明细（报告页、工具页）",
     "purpose": "同一个坑出现 ≥3 次才够写一条班规；1–2 次只观察。"},
    {"id": "sample", "name": "样本下限", "value": RATE_MIN_SAMPLE,
     "applies": "工具排行、skill 画像、比率类判断",
     "purpose": "样本 <20 不下结论（1 次调用 1 次失败不是 100% 的失败率）。"},
    {"id": "fresh", "name": "新意（首见）", "value": NEW_PATTERN_MIN,
     "applies": "蒸馏队列 A 区、以及被阈值滤掉时的余量说明",
     "purpose": "窗口内首次出现的坑，1 次就值得做卡——它的价值是「没见过」，"
                "不是「次数多」。"},
    {"id": "zero", "name": "零星上限", "value": WATCH_MAX,
     "applies": "工作台待处理量分级",
     "purpose": "1–2 条算零星（watch，虚线框），不报警。"},
)


def threshold_notes() -> list[str]:
    """给人读的阈值清单：一行一个数 + 用途（页面「怎么读」与报告尾部共用）。"""
    return ["%s = %d：%s" % (t["name"], int(t["value"]), t["purpose"])
            for t in THRESHOLDS]

KIND_ACTION = "action"
KIND_STOCK = "stock"
KIND_RATE = "rate"
KINDS = (KIND_ACTION, KIND_STOCK, KIND_RATE)

#: 给页面用的中文标签（不是行业术语，**本项目自定义**，首次出现须自带解释）
KIND_LABEL = {
    KIND_ACTION: "待处理",
    KIND_STOCK: "累计",
    KIND_RATE: "比率",
}

LEVEL_OK = "ok"
LEVEL_WATCH = "watch"
LEVEL_WARN = "warn"
LEVEL_INFO = "info"


def level_of(kind: str, count: int, denom: int = 0,
             rate: float | None = None) -> str:
    """这是**唯一**的分级函数：kind + 数值 → ok / watch / warn / info。

    未知 kind 直接抛 ValueError（fail loud）：新增 target 必须显式分类，
    否则它会被当成"待处理"，等于把累计量重新变成警报。
    """
    if kind not in KINDS:
        raise ValueError("未知的计数类型 kind=%r（必须是 %s 之一）"
                         % (kind, "/".join(KINDS)))
    n = int(count or 0)
    if kind == KIND_STOCK:
        return LEVEL_INFO
    if kind == KIND_RATE:
        r = rate
        if r is None:
            r = (n / denom) if denom else 0.0
        if int(denom or 0) >= RATE_MIN_SAMPLE and r >= RATE_MIN:
            return LEVEL_WARN
        return LEVEL_OK
    # action
    if n <= 0:
        return LEVEL_OK
    return LEVEL_WATCH if n <= WATCH_MAX else LEVEL_WARN


def explain(kind: str, count: int, denom: int = 0,
            rate: float | None = None) -> str:
    """一句话说清"为什么是这个颜色"——页面悬停与"低于阈值"的说明共用。"""
    n = int(count or 0)
    if kind == KIND_STOCK:
        return "累计量：只说明现在有多少，不等于是待办的活，所以不作警报。"
    if kind == KIND_RATE:
        r = rate
        if r is None:
            r = (n / denom) if denom else 0.0
        if int(denom or 0) < RATE_MIN_SAMPLE:
            return ("样本不足（分母 %d < %d），比率不下结论。"
                    % (int(denom or 0), RATE_MIN_SAMPLE))
        return ("比率 %.1f%%（阈值 %.0f%%），分母 %d。"
                % (r * 100, RATE_MIN * 100, int(denom or 0)))
    if n <= 0:
        return "没有待处理的活。"
    if n <= WATCH_MAX:
        return "零星 %d 条（少于阈值 %d），归观察，不报警。" % (n, ACTION_MIN)
    return "待处理 %d 条（阈值 %d）。" % (n, ACTION_MIN)