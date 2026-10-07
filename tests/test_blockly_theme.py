"""Blockly編集画面のダークテーマの検証。

VS Code・MakeCode のテーマ切替に相当する。画面の色は既存の CSS 変数で
決まるため、ダーク時は `html[data-theme="dark"]` で上書きし、Blockly 側は
`pokecon-dark` テーマを `ws.setTheme` で切り替える。選んだ状態は画面設定
`pokecon.blockly.theme` に保存し、無ければ OS の設定に従う。
"""

from __future__ import annotations

import re

from blockly_node import BLOCKLY, NEEDS_NODE, run_editor

pytestmark = NEEDS_NODE

#: 画面の色を決める主要な CSS 変数（ダーク時にすべて上書きする）。
MAIN_VARS = [
    "--bg",
    "--panel",
    "--line",
    "--text",
    "--muted",
    "--accent",
    "--accent-ink",
    "--warn",
    "--err",
    "--err-bg",
]


def read_editor() -> str:
    """編集画面の全文を読む。"""
    return (BLOCKLY / "editor.html").read_text(encoding="utf-8")


def style_block(html: str) -> str:
    """`<style>` の中身を切り出す。"""
    return html[html.index("<style>") : html.index("</style>")]


def vars_in(block: str, selector: str) -> dict[str, str]:
    """指定の選択子の中にある `--var: value;` を集める。"""
    start = block.index(selector)
    body = block[start : block.index("}", start)]
    found: dict[str, str] = {}
    for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", body):
        found[m.group(1)] = m.group(2).strip()
    return found


def rel_lum(hex_color: str) -> float:
    """sRGB hex から相対輝度を求める（WCAG の式）。"""
    c = hex_color.strip().lower()
    if c.startswith("var("):
        raise ValueError(f"変数のまま: {hex_color}")
    h = c.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    rgb = [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """前景・背景の hex からコントラスト比を求める。"""
    l1 = rel_lum(fg)
    l2 = rel_lum(bg)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def test_dark_theme_overrides_all_main_css_variables() -> None:
    """ダーク時は主要な変数をすべて `html[data-theme="dark"]` で上書きすること。"""
    # Given: 編集画面の CSS
    style = style_block(read_editor())
    # When: ダーク用の上書きを取り出す
    assert 'html[data-theme="dark"]' in style
    dark = vars_in(style, 'html[data-theme="dark"]')
    # Then: 主要な変数がすべて上書きされ、値が明るい方と違う
    light = vars_in(style, ":root")
    for name in MAIN_VARS:
        assert name in dark, f"{name} のダーク上書きが無い"
        assert dark[name] != light[name], f"{name} が明るい方と同じ値"


def test_main_foreground_background_pairs_meet_contrast_45() -> None:
    """主な前景・背景の組が両テーマでコントラスト比 4.5 以上であること。"""
    # Given: 明・暗の変数表
    style = style_block(read_editor())
    themes = {
        "light": vars_in(style, ":root"),
        "dark": vars_in(style, 'html[data-theme="dark"]'),
    }
    # When/Then: 主な組（本文・薄い文字・主ボタン・エラー文・状態行）を計算する
    pairs = [
        ("本文/背景", "text", "bg"),
        ("本文/窓", "text", "panel"),
        ("薄字/窓", "muted", "panel"),
        ("薄字/背景", "muted", "bg"),
        ("主ボタン字/主ボタン", "accent-ink", "accent"),
        ("エラー文/エラー欄", "err", "err-bg"),
        ("エラー文/窓", "err", "panel"),
        ("注意文/背景", "warn", "bg"),
        ("状態行/背景", "muted", "bg"),
    ]
    for theme_name, table in themes.items():
        for label, fg, bg in pairs:
            ratio = contrast(table["--" + fg], table["--" + bg])
            assert ratio >= 4.5, f"{theme_name} {label} の比が {ratio:.2f}"


def test_theme_menu_item_has_expected_markup_and_place() -> None:
    """「⋯」メニューに「🌙 ダークテーマ」の切替項目があること。"""
    # Given: 編集画面の全文と⋯メニューの範囲
    html = read_editor()
    menu = html[html.index('id="moremenu"') : html.index('id="statusline"')]
    # When/Then: 切替項目の約束（切替の印・初期は明るい方）
    assert 'id="themetoggle"' in menu
    assert 'role="menuitemcheckbox"' in menu
    assert "🌙 ダークテーマ" in menu
    assert 'aria-checked="false"' in menu
    # When/Then: 削除の後・使い方の前に置く（既存の並びは変えない）
    assert menu.index('id="del"') < menu.index('id="themetoggle"')
    assert menu.index('id="themetoggle"') < menu.index('id="help"')


def test_inline_script_stays_single() -> None:
    """inline `<script>` はちょうど1個のままであること。"""
    # Given: 編集画面の全文
    html = read_editor()
    # When/Then: 素の `<script>`（src 無し）は1個だけ
    assert html.count("<script>") == 1


def test_help_mentions_the_dark_theme() -> None:
    """使い方にダークテーマの1行案内があること。"""
    # Given: 使い方（details#help）の範囲
    html = read_editor()
    start = html.index('id="help"')
    help_body = html[start : html.index("</details>", start)]
    # When/Then: 切替の案内が1行ある
    assert "ダークテーマ" in help_body


def test_theme_toggle_is_wired_through_guarded_helpers() -> None:
    """切替は要素が無くても落ちない on() 経由で結線されていること。"""
    # Given: inline script
    html = read_editor()
    script = html[html.index("<script>\n") :]
    # When/Then: 切替・保存・Blockly テーマの結線がある
    assert "on('themetoggle'" in script
    assert "writePref('theme'" in script
    assert "pokecon-dark" in script
    assert "prefers-color-scheme" in script


#: 最小DOMに足す部品（`documentElement` と `setTheme` の記録）。
#: `run_editor` の検証スタブには無いため、本文側で足してから起動する。
THEME_PRELUDE = """
doc.documentElement = { attrs: {},
  setAttribute(k, v) { this.attrs[k] = v; },
  getAttribute(k) { return (k in this.attrs) ? this.attrs[k] : null; },
  removeAttribute(k) { delete this.attrs[k]; } };
const themeCalls = [];
ws.setTheme = function (t) { themeCalls.push(t); };
"""


def test_toggle_switches_to_dark_persists_and_themes_blockly() -> None:
    """切替を押すと暗くなり、保存され、Blockly テーマも変わること。"""
    # Given: 起動直後の編集画面（OS は明るい方・保存なし）
    res = run_editor(
        THEME_PRELUDE
        + """
        sandboxWin.matchMedia = () => ({ matches: false });
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        // When: 切替を押す
        el('themetoggle').handlers.click();
        await flush(100);
        const t = themeCalls[themeCalls.length - 1] || null;
        done({ theme: doc.documentElement.attrs['data-theme'],
               checked: el('themetoggle').attrs['aria-checked'],
               saved: store['pokecon.blockly.theme'],
               name: t && t.name,
               styles: t && t.componentStyles });
        """
    )
    # Then: 暗くなり、保存され、Blockly テーマが当たる
    assert res["theme"] == "dark"
    assert res["checked"] == "true"
    assert res["saved"] == "dark"
    assert res["name"] == "pokecon-dark"
    assert "workspaceBackgroundColour" in res["styles"]
    assert "toolboxBackgroundColour" in res["styles"]
    assert "toolboxForegroundColour" in res["styles"]
    assert "flyoutBackgroundColour" in res["styles"]
    assert "flyoutForegroundColour" in res["styles"]
    assert "scrollbarColour" in res["styles"]
    assert "insertionMarkerColour" in res["styles"]
    assert "cursorColour" in res["styles"]


def test_toggle_again_returns_to_light() -> None:
    """もう一度押すと明るい方に戻り、保存も戻ること。"""
    # Given: 暗くした直後の編集画面
    res = run_editor(
        THEME_PRELUDE
        + """
        sandboxWin.matchMedia = () => ({ matches: false });
        routes.list = { stems: [], appId: 'app1' };
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        el('themetoggle').handlers.click();
        await flush(100);
        // When: もう一度押す
        el('themetoggle').handlers.click();
        await flush(100);
        const t = themeCalls[themeCalls.length - 1] || null;
        done({ theme: (doc.documentElement.attrs['data-theme'] || null),
               checked: el('themetoggle').attrs['aria-checked'],
               saved: store['pokecon.blockly.theme'],
               name: t && t.name });
        """
    )
    # Then: 明るい方に戻る（属性は外す・Classic に戻す）
    assert res["theme"] is None
    assert res["checked"] == "false"
    assert res["saved"] == "light"
    assert res["name"] == "classic"


def test_saved_dark_is_applied_at_startup() -> None:
    """保存済みの dark で起動すると最初から暗いこと。"""
    # Given: 前回 dark で閉じた画面設定
    res = run_editor(
        THEME_PRELUDE
        + """
        sandboxWin.matchMedia = () => ({ matches: false });
        store['pokecon.blockly.theme'] = 'dark';
        routes.list = { stems: [], appId: 'app1' };
        // When: 起動する（押さない）
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        const t = themeCalls[themeCalls.length - 1] || null;
        done({ theme: doc.documentElement.attrs['data-theme'],
               checked: el('themetoggle').attrs['aria-checked'],
               name: t && t.name });
        """
    )
    # Then: 最初から暗い
    assert res["theme"] == "dark"
    assert res["checked"] == "true"
    assert res["name"] == "pokecon-dark"


def test_os_dark_setting_is_used_when_nothing_is_saved() -> None:
    """保存が無ければ OS の暗い設定に従うこと。"""
    # Given: 保存なし・OS は暗い方
    res = run_editor(
        THEME_PRELUDE
        + """
        sandboxWin.matchMedia = (q) => ({ matches: q.indexOf('dark') !== -1 });
        routes.list = { stems: [], appId: 'app1' };
        // When: 起動する（押さない）
        boot();
        await flush(400);
        const el = (id) => doc.getElementById(id);
        done({ theme: doc.documentElement.attrs['data-theme'],
               checked: el('themetoggle').attrs['aria-checked'] });
        """
    )
    # Then: OS に従って暗い
    assert res["theme"] == "dark"
    assert res["checked"] == "true"
