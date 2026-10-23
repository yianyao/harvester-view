# -*- coding: utf-8 -*-
"""v0.49 测试：配色对比度门（用户 2026-10-13「今日工作台浅底浅色，看得眼花」）。

## 缺陷是什么（实测根因，不是猜的）

`static/index.html` 有 `@media (prefers-color-scheme: dark)`：深色模式下
`:root` 把 `--ink` 翻成**浅色字**（`#e6e9ef`）。但工作台的组件把底色**写死**成
浅色（`.qcard{background:#fff}`、`.ring{background:#fff}`、`.arm{background:#fafbfc}` …）
→ 浅色字落在浅色底上，对比度约 **1.1:1**（WCAG 正文要求 ≥4.5:1），
眼睛当然花。同一个毛病还在 `.cmd-card.on` / `.cmd-cli` / `.confirm-box` 上。

另外还有一类：**用了不存在的 CSS 变量**。历史上有 13 处写 `var(--sub)`，而
`--sub` 从未定义 → 那些「口径/真源」小字根本没被置灰，整块糊在一起。

## 本门怎么才会红（说得出触发条件，才不是恒真断言）

1. 任何规则在**存在 dark 覆盖的页面里**给「浅色字用的组件」写死浅底，
   且该规则自己既没有显式 `color:` 也没有 `color-scheme:light`
   → 红（这正是上面那个 1.1:1 的成因）；
2. 任何 `var(--x)` 引用了 `:root`/dark 块里都没定义的 `--x` → 红；
3. 算出来的对比度（深色模式的字 vs 写死的底）< 4.5:1 → 红。

判据边界（诚实写明）：本门**不解析继承链**，只看「同一条规则里同时出现的
底色与字色」以及「页面是否存在 dark 覆盖」这两件可判定的事。它挡的是
「浅字浅底/变量名写错」这一整类缺陷，不是通用的 CSS 可读性证明。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "static" / "index.html"

#: WCAG 2.1 正文对比度下限（AA）
MIN_RATIO = 4.5

#: 会被当作"浅底"的写法（含 #fff 系列与已知的浅色面板色）
LIGHT_HEX = re.compile(
    r"#(?:fff|ffffff|fafbfc|f6f8fa|f7faff|fffbe8|fff5f6|fffdf5|fff7d6|"
    r"fde8e8|eef4ff|f2f4f7|f8f9fa)\b", re.I)

def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


def _read_css() -> tuple[str, str]:
    text = HTML.read_text(encoding="utf-8")
    m = re.search(r"<style>([\s\S]*?)</style>", text)
    if not m:
        raise AssertionError("index.html 里找不到 <style> 块")
    full = _strip_comments(m.group(1))
    dm = re.search(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{"
                   r"([\s\S]*?)\n\}", full)
    return full, (dm.group(1) if dm else "")


def _rules(css: str) -> list[tuple[str, str]]:
    """取出顶层 `选择器 { 声明 }`（跳过 @media 包裹层本身）。"""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        if sel.startswith("@"):
            continue
        out.append((sel, body))
    return out


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lum(rgb) -> float:
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a_hex: str, b_hex: str) -> float:
    la, lb = _lum(_hex_to_rgb(a_hex)), _lum(_hex_to_rgb(b_hex))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _var_map(css: str, scope: str) -> dict:
    """取某个作用域（`:root{...}` 或 dark 媒体块）里的变量定义。"""
    out = {}
    for m in re.finditer(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", scope):
        out["--" + m.group(1)] = m.group(2).lower()
    return out


class ColorContrastGateTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.css, cls.dark = _read_css()
        cls.rules = _rules(cls.css)

    def test_page_has_a_dark_override(self):
        """前提断言：**因为有 dark 覆盖，写死浅底才危险**。

        如果哪天把 dark 覆盖删了（改回固定浅色主题），这条会红，
        提醒下一会话：那条"必须用主题变量"的规则可以放宽，但**要显式裁决**。
        """
        self.assertTrue(self.dark, "页面没有 dark 覆盖了——配色纪律需重新裁决")

    def test_theme_vars_defined_in_both_scopes(self):
        light = _var_map(self.css, re.search(r":root\s*\{([^}]*)\}", self.css)
                         .group(1))
        dark = _var_map(self.css, self.dark)
        for name in ("--bg", "--panel", "--ink", "--ink2", "--line",
                     "--warn-bg", "--warn-ink", "--err-bg", "--err-ink",
                     "--ok-bg", "--ok-ink", "--chip-on-bg"):
            self.assertIn(name, light, "浅色主题缺变量 %s" % name)
            self.assertIn(name, dark, "深色主题缺变量 %s" % name)
        # 关键前提：字色在深色模式下**确实翻成了浅色**
        self.assertGreater(_lum(_hex_to_rgb(dark["--ink"])),
                           _lum(_hex_to_rgb(light["--ink"])),
                           "--ink 在深色模式下没有翻成浅色，本门的假设不成立")

    def test_no_undefined_css_vars(self):
        """`var(--x)` 的 `--x` 必须真被定义过（历史缺陷：13 处 var(--sub)）。"""
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", self.css))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", self.css))
        missing = sorted(used - defined)
        self.assertEqual(missing, [],
                         "用了没定义的 CSS 变量（那些声明会被整条丢弃）：%s"
                         % missing)

    def test_no_hardcoded_light_surface_with_flipping_text(self):
        """**这条就是用户那次"看得眼花"的正面防线。**

        规则里出现写死的浅色底、又没自己钉住前景（显式 color 或
        color-scheme:light）→ 在深色模式下就是浅字浅底。
        """
        bad = []
        for sel, body in self.rules:
            bg = re.search(r"background(?:-color)?\s*:\s*([^;}]+)", body)
            if not bg:
                continue
            val = bg.group(1).strip()
            if not LIGHT_HEX.search(val):
                continue
            pinned = (re.search(r"(?<![-\w])color\s*:\s*#", body)
                      or "color-scheme" in body)
            if not pinned:
                bad.append((sel, val))
        self.assertEqual(
            bad, [],
            "这些规则写死了浅色底却没钉住前景，深色模式下会「浅字浅底」"
            "（请改用主题变量：var(--panel)/var(--bg)/var(--warn-bg)/"
            "var(--err-bg)）：%s" % bad)

    def test_hardcoded_light_surfaces_have_adequate_contrast(self):
        """兜底：确实需要写死浅底的（如悬停卡，已钉 color-scheme:light），
        其前景与底色的对比度必须达 WCAG AA ≥4.5:1。"""
        checked = 0
        for sel, body in self.rules:
            bg = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})",
                           body)
            fg = re.search(r"(?<![-\w])color\s*:\s*(#[0-9a-fA-F]{3,8})", body)
            if not (bg and fg) or not LIGHT_HEX.search(bg.group(1)):
                continue
            checked += 1
            ratio = contrast(fg.group(1), bg.group(1))
            self.assertGreaterEqual(
                ratio, MIN_RATIO,
                "%s 的前景/底色对比度只有 %.2f:1（要求 ≥%.1f:1）"
                % (sel, ratio, MIN_RATIO))
        self.assertGreaterEqual(checked, 1,
                                "一条写死浅底的规则都没检查到，门是空转的")

    def test_workbench_components_use_theme_vars(self):
        """用户点名的那几个工作台组件：底色必须是主题变量（不许写死）。"""
        for sel in (".qcard", ".ring", ".arm", ".wi-item", ".wi-head"):
            rule = [b for s, b in self.rules if s.split(",")[0].strip() == sel]
            self.assertTrue(rule, "找不到规则 %s（组件改名了？）" % sel)
            body = rule[0]
            if "background" not in body:
                continue
            self.assertNotRegex(
                body, LIGHT_HEX,
                "%s 的底色写死了浅色 —— 深色模式下会变成浅字浅底" % sel)
            self.assertRegex(body, r"background[^;}]*var\(--")

    def test_dark_mode_text_survives_on_theme_surfaces(self):
        """真算一遍：深色模式的四种字色 vs 四种主题底，全部 ≥4.5:1。"""
        light_root = re.search(r":root\s*\{([^}]*)\}", self.css).group(1)
        light = _var_map(self.css, light_root)
        dark = _var_map(self.css, self.dark)
        for mode, vm in (("light", light), ("dark", dark)):
            for fg in ("--ink", "--ink2"):
                for bg in ("--bg", "--panel"):
                    r = contrast(vm[fg], vm[bg])
                    self.assertGreaterEqual(
                        r, MIN_RATIO,
                        "%s 模式：%s 落在 %s 上只有 %.2f:1"
                        % (mode, fg, bg, r))


    def test_selected_chip_passes_in_both_modes(self):
        """选中态 chip 的白字必须**两种模式都** ≥4.5:1。

        历史（2026-10-13）：`.chip.on` 原来是 `background:var(--accent); color:#fff`
        ——浅色模式 4.8:1 达标，**深色模式只有 3.2:1**（深色的 `--accent` 是为深底
        刻意调亮的浅蓝 `#5b8def`）。用户裁决「**不改深色，改其他的**」，故给它一个
        专用底 `--chip-on-bg`（浅 `#2563eb` / 深 `#1d4ed8`），`--accent` 一个字节
        没动（链接/描边/选中态仍用它）。

        本条替换掉先前那条"已知弱项登记"断言——**弱项已修，登记改为真判据**。
        """
        light = _var_map(
            self.css, re.search(r":root\s*\{([^}]*)\}", self.css).group(1))
        dark = _var_map(self.css, self.dark)
        self.assertIn("--chip-on-bg", light, "浅色主题缺 --chip-on-bg")
        self.assertIn("--chip-on-bg", dark, "深色主题缺 --chip-on-bg")
        # 反面对照：这条断言存在的理由（若哪天深色 accent 变成深蓝，注释与它都该复核）
        self.assertEqual(dark["--accent"], "#5b8def",
                         "深色 --accent 变了 → 请复核本条与 chip 专用底的取色依据")
        for mode, vm in (("light", light), ("dark", dark)):
            ratio = contrast("#ffffff", vm["--chip-on-bg"])
            self.assertGreaterEqual(
                ratio, MIN_RATIO,
                "%s 模式：选中 chip 的白字落在 --chip-on-bg(%s) 上只有 %.2f:1"
                % (mode, vm["--chip-on-bg"], ratio))

    def test_chip_on_does_not_use_the_global_accent_as_its_surface(self):
        """用户裁决的落地形态：chip 用**自己的**底，不拿 `--accent` 当底。

        为什么单列一条：修法很容易被"顺手改回 var(--accent) 更省事"覆盖掉，
        而那样在深色模式下会**悄悄退回 3.2:1**（视觉上只是"有点糊"，不会报错）。
        """
        rule = [b for s, b in self.rules
                if s.split(",")[0].strip() == ".chip.on"]
        self.assertTrue(rule, "找不到 .chip.on 规则")
        self.assertIn("var(--chip-on-bg)", rule[0])
        self.assertNotIn("var(--accent)", rule[0])


if __name__ == "__main__":
    unittest.main()
