DARK = {
    "bg": "#151514",
    "surface": "#1B1B1A",
    "surface_hover": "#232321",
    "border": "#2B2B28",
    "text": "#E8E6E1",
    "text_muted": "#9A978F",
    "text_faint": "#6B6963",
    "accent": "#7FA896",
    "on_accent": "#151514",
    "warning": "#C9A15B",
    "danger": "#C97B6B",
}

LIGHT = {
    "bg": "#F7F6F3",
    "surface": "#FFFFFF",
    "surface_hover": "#EFEDE8",
    "border": "#E4E1DA",
    "text": "#1F1E1B",
    "text_muted": "#6F6C65",
    "text_faint": "#9A978F",
    "accent": "#4F7F6D",
    "on_accent": "#FFFFFF",
    "warning": "#946B1F",
    "danger": "#A4503F",
}

PALETTE = DARK

FONT = '"Inter", "Cantarell", system-ui, sans-serif'
MONO = '"JetBrains Mono", "DejaVu Sans Mono", monospace'

TYPE = {
    "title": (20, 600),
    "section": (12, 500),
    "body": (14, 400),
    "secondary": (12, 400),
    "mono": (12, 400),
}
SECTION_TRACKING = "0.6px"

SPACE = {
    "xs": 4,
    "sm": 8,
    "md": 16,
    "lg": 24,
    "section": 32,
    "page_x": 40,
    "page_y": 32,
}

SIZE = {
    "row": 44,
    "control": 36,
    "nav": 36,
    "sidebar": 200,
    "content": 640,
    "combo": 280,
    "slider": 220,
    "icon": 18,
}

RADIUS = {
    "control": 6,
    "container": 8,
}

MOTION = "130ms ease-out"


def rgb(name, palette=None):
    h = (palette or PALETTE)[name].lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def stylesheet(palette=None):
    p = palette or PALETTE
    colors = "\n".join(f"@define-color {k} {v};" for k, v in p.items())
    t = {k: f"font-size: {s}px; font-weight: {w};" for k, (s, w) in TYPE.items()}
    r_ctl, r_box = RADIUS["control"], RADIUS["container"]
    ctl_h = SIZE["control"] - 2  # minus 1px border top and bottom
    return f"""
{colors}

* {{
  outline-color: @accent; outline-style: solid; outline-width: 2px;
  outline-offset: -2px; -gtk-outline-radius: {r_ctl}px;
}}
window, .app {{ background-color: @bg; color: @text; font-family: {FONT}; {t["body"]} }}
selection, *:selected {{ background-color: alpha(@accent, 0.30); color: @text; }}

headerbar, headerbar:backdrop {{
  background-color: @bg; background-image: none; border: none; box-shadow: none; color: @text;
}}
headerbar .title {{ {t["body"]} font-weight: 500; }}
headerbar:backdrop .title {{ color: @text_muted; }}

.app scrolledwindow, .app viewport {{ background-color: transparent; border: none; }}
.app scrollbar {{ background-color: transparent; border: none; }}
.app scrollbar slider {{ background-color: @text_faint; border: none; border-radius: {r_ctl}px; }}

.app *:disabled {{ color: @text_faint; }}

.page-title {{ {t["title"]} }}
.section-label {{ {t["section"]} color: @text_muted; letter-spacing: {SECTION_TRACKING}; }}
.secondary {{ {t["secondary"]} color: @text_muted; }}
.num {{ font-feature-settings: "tnum"; }}
.brand {{ {t["body"]} font-weight: 600; }}

.sidebar {{ background-color: @bg; border: none; }}
.app button.nav {{
  min-height: {SIZE["nav"]}px; padding: 0 10px; border: none; border-radius: {r_ctl}px;
  background-color: transparent; color: @text_muted; font-weight: 400;
}}
.app button.nav:hover {{ background-color: @surface; color: @text; }}
.app button.nav:checked {{ background-color: @surface_hover; color: @accent; }}

.row {{ min-height: {SIZE["row"] - 12}px; padding: 6px 0; border-top: 1px solid @border; }}
.row:first-child {{ border-top: none; }}

.app button {{
  min-height: {ctl_h}px; padding: 0 14px; border: 1px solid @border; border-radius: {r_ctl}px;
  background-color: transparent; background-image: none; box-shadow: none; text-shadow: none;
  -gtk-icon-shadow: none; color: @text; font-weight: 500; transition: all {MOTION};
}}
.app button:hover {{ background-color: @surface; }}
.app button:active {{ background-color: @surface_hover; }}
.app button:disabled {{ color: @text_faint; background-color: transparent; }}
.app button.primary {{ background-color: @accent; border-color: @accent; color: @on_accent; outline-color: @text; }}
.app button.primary:hover {{ background-color: shade(@accent, 1.08); border-color: shade(@accent, 1.08); }}
.app button.primary:active {{ background-color: shade(@accent, 0.94); }}
.app button.primary:disabled {{ background-color: @surface_hover; border-color: @surface_hover; color: @text_faint; }}
.app button.tertiary {{ border-color: transparent; padding: 0 10px; color: @text_muted; }}
.app button.tertiary:hover {{ background-color: @surface; color: @text; }}

.app combobox button.combo {{
  background-color: @surface; border: 1px solid @border; padding: 0 8px 0 12px; font-weight: 400;
}}
.app combobox button.combo:hover {{ background-color: @surface_hover; }}
.app combobox arrow {{
  -gtk-icon-source: -gtk-icontheme("pan-down-symbolic"); color: @text_muted;
  min-width: 16px; min-height: 16px;
}}
menu, .menu {{
  background-color: @surface; color: @text; border: 1px solid @border;
  border-radius: {r_box}px; padding: 4px; box-shadow: none;
}}
menu menuitem {{ min-height: 32px; padding: 0 10px; border-radius: {r_ctl}px; color: @text; }}
menu menuitem:hover {{ background-color: @surface_hover; color: @text; }}
menu arrow {{ background-color: @surface; border: none; }}

.app scale {{ padding: 8px 0; }}
.app scale trough {{
  min-height: 3px; border: none; border-radius: 2px; background-color: @border; background-image: none;
}}
.app scale highlight {{ min-height: 3px; border: none; border-radius: 2px; background-color: @accent; background-image: none; }}
.app scale slider {{
  min-width: 14px; min-height: 14px; margin: -6px; border: none; border-radius: 50%;
  background-color: @text; background-image: none; box-shadow: none; outline-offset: 1px;
  -gtk-outline-radius: 50%; transition: background-color {MOTION};
}}
.app scale slider:hover {{ background-color: shade(@text, 1.06); }}
.app scale:disabled highlight, .app scale:disabled slider {{ background-color: @text_faint; }}

.app switch {{
  min-height: 20px; padding: 0; border: none; border-radius: {r_ctl}px; box-shadow: none;
  background-color: @border; background-image: none; color: transparent; font-size: 0;
  transition: background-color {MOTION};
}}
.app switch:checked {{ background-color: @accent; }}
.app switch image {{ color: transparent; -gtk-icon-source: none; min-width: 0; min-height: 0; }}
.app switch slider {{
  min-width: 16px; min-height: 16px; margin: 2px; border: none; border-radius: 4px; box-shadow: none;
  background-color: @text_muted; background-image: none; transition: all {MOTION};
}}
.app switch:checked slider {{ background-color: @on_accent; }}

.app radiobutton.choice {{ padding: 10px 8px; border-radius: {r_ctl}px; transition: background-color {MOTION}; }}
.app radiobutton.choice:hover {{ background-color: @surface; }}
.app radio {{
  min-width: 16px; min-height: 16px; margin-right: 12px; border: 1px solid @text_faint;
  border-radius: 50%; background-color: transparent; background-image: none; box-shadow: none;
  -gtk-icon-source: none; color: transparent;
}}
.app radio:checked {{ border-color: @accent; background-color: @accent; box-shadow: inset 0 0 0 3px @bg; }}
.app radiobutton.choice:hover radio:checked {{ box-shadow: inset 0 0 0 3px @surface; }}
.app radio:disabled {{ border-color: @border; }}

.app levelbar trough {{
  min-height: 4px; padding: 0; border: none; border-radius: 2px; box-shadow: none;
  background-color: @border; background-image: none;
}}
.app levelbar block {{ min-height: 4px; border: none; border-radius: 2px; box-shadow: none; background-image: none; }}
.app levelbar block.empty {{ background-color: transparent; }}
.app levelbar block.filled {{ background-color: @accent; }}
.app levelbar block.filled.full {{ background-color: @warning; }}
.app levelbar block.filled.clip {{ background-color: @danger; }}

.codeblock {{ background-color: @surface; border-radius: {r_box}px; padding: 12px; }}
.codeblock textview, .codeblock textview text {{
  background-color: @surface; color: @text; font-family: {MONO}; {t["mono"]}
}}

.statusline {{ {t["secondary"]} color: @text_muted; background-color: transparent; }}

messagedialog .dialog-action-area button {{ min-width: 72px; }}
"""
