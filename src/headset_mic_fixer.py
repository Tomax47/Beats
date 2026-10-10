#!/usr/bin/env python3
import array
import glob
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
import zipfile

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import beats_theme as theme  # noqa: E402
from beats_logo import paint_bars  # noqa: E402
from beats_theme import SIZE, SPACE  # noqa: E402

APP = "Beats"
APP_ID = "org.beats.Beats"
CFG = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
WP_CONF = f"{CFG}/wireplumber/wireplumber.conf.d/51-headset-mic-fixer.conf"
PW_CONF = f"{CFG}/pipewire/pipewire.conf.d/51-headset-mic-fixer.conf"
PREFS = f"{CFG}/beats/settings.json"
OUR_SRC = "headset_mic_fixer_source"
MODPROBE = "/etc/modprobe.d/headset-mic-fixer.conf"


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as exc:
        return 1, "", str(exc)


def pactl_json(kind):
    _, out, _ = run(["pactl", "-f", "json", "list", kind])
    try:
        return json.loads(out)
    except ValueError:
        return []


def get_sources():
    res = []
    for s in pactl_json("sources"):
        props = s.get("properties", {})
        name = s["name"]
        if props.get("device.class") == "monitor" or name.endswith(".monitor"):
            continue
        if name == OUR_SRC:
            continue
        first = next(iter(s.get("volume", {}).values()), {})
        pct = int(re.sub(r"\D", "", str(first.get("value_percent", "0"))) or 0)
        res.append(dict(
            name=name,
            desc=s.get("description", name),
            ports=s.get("ports", []),
            active=s.get("active_port"),
            vol=pct,
            mute=bool(s.get("mute")),
            bt=name.startswith("bluez"),
            internal=props.get("device.form-factor") == "internal",
        ))
    return res


def default_source():
    _, out, _ = run(["pactl", "get-default-source"])
    return out.strip()


def node_id(node_name):
    _, out, _ = run(["pw-dump"], 20)
    try:
        for o in json.loads(out):
            if o.get("info", {}).get("props", {}).get("node.name") == node_name:
                return o["id"]
    except ValueError:
        pass
    return None


def set_default_source(name):
    run(["pactl", "set-default-source", name])
    if default_source() == name:
        return True, ""
    nid = node_id(name)
    if nid is not None and shutil.which("wpctl"):
        run(["wpctl", "set-default", str(nid)])
        if default_source() == name:
            return True, ""
    return False, "the sound server kept a different default (see Diagnostics)"


def port_label(p):
    label = p.get("description", p["name"])
    if p.get("availability") == "not available":
        label += "  (nothing plugged in)"
    return label


def record_test(target, path, seconds=5):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    cmd = ["pw-record", "--rate", "48000", "--channels", "1", "--format", "s16"]
    if target:
        cmd += ["--target", target]
    cmd.append(path)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        return str(exc)
    time.sleep(seconds)
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    try:
        _, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, err = proc.communicate()
    return err or ""


def wav_levels(path):
    try:
        with wave.open(path, "rb") as w:
            data = w.readframes(w.getnframes())
    except Exception:
        try:
            data = open(path, "rb").read()[44:]
        except OSError:
            return None
    if len(data) < 2:
        return None
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    if not samples:
        return None
    peak = max(max(samples), -min(samples))
    rms = (sum(x * x for x in samples) / len(samples)) ** 0.5
    return peak / 327.68, rms / 327.68


def alsa_enable_capture():
    changed = []
    if not shutil.which("amixer"):
        return changed
    for path in sorted(glob.glob("/proc/asound/card[0-9]*")):
        idx = path.rsplit("card", 1)[-1]
        _, o, _ = run(["amixer", "-c", idx, "scontrols"])
        for m in re.finditer(r"'([^']*)',(\d+)", o):
            ctl = f"{m.group(1)},{m.group(2)}"
            _, g, _ = run(["amixer", "-c", idx, "sget", ctl])
            caps = re.search(r"Capabilities:(.*)", g)
            caps = caps.group(1) if caps else ""
            if "cswitch" in caps:
                run(["amixer", "-c", idx, "-q", "sset", ctl, "cap"])
                changed.append(f"card {idx}: {ctl} capture on")
            if "cvolume" in caps:
                pc = re.search(r"Capture \d+ \[(\d+)%\]", g) or re.search(r"\[(\d+)%\]", g)
                if pc and int(pc.group(1)) < 30:
                    run(["amixer", "-c", idx, "-q", "sset", ctl, "70%"])
                    changed.append(f"card {idx}: {ctl} raised to 70%")
    return changed


def jack_info():
    out = []
    if not shutil.which("amixer"):
        return out
    for path in sorted(glob.glob("/proc/asound/card[0-9]*")):
        idx = path.rsplit("card", 1)[-1]
        _, o, _ = run(["amixer", "-c", idx, "contents"])
        lines = o.splitlines()
        for i, l in enumerate(lines):
            if "Jack" in l and "name=" in l and i + 2 < len(lines):
                nm = re.search(r"name='([^']*)'", l)
                out.append(f"card {idx}: {nm.group(1) if nm else l.strip()} -> {lines[i + 2].strip()}")
    return out


def bt_cards():
    return [c for c in pactl_json("cards") if c["name"].startswith("bluez_card")]


def boost_controls():
    if not shutil.which("amixer"):
        return []
    out = []
    for path in sorted(glob.glob("/proc/asound/card[0-9]*")):
        idx = path.rsplit("card", 1)[-1]
        _, o, _ = run(["amixer", "-c", idx, "scontrols"])
        for m in re.finditer(r"'([^']*Boost[^']*)',(\d+)", o):
            _, g, _ = run(["amixer", "-c", idx, "sget", f"{m.group(1)},{m.group(2)}"])
            p = re.search(r"\[(\d+)%\]", g)
            if p:
                out.append((idx, m.group(1), m.group(2), int(p.group(1))))
    return out


RNNOISE_NAME = "librnnoise_ladspa.so"
RNNOISE_URL = ("https://github.com/werman/noise-suppression-for-voice/"
               "releases/download/v1.10/linux-rnnoise.zip")


def _app_dir():
    return os.path.dirname(os.path.abspath(__file__))


def find_rnnoise():
    cands = [
        os.path.join(_app_dir(), "ladspa", RNNOISE_NAME),
        os.path.normpath(os.path.join(_app_dir(), "..", "vendor", RNNOISE_NAME)),
        "/usr/share/beats/ladspa/" + RNNOISE_NAME,
        "/usr/share/headset-mic-fixer/ladspa/" + RNNOISE_NAME,
        "/usr/lib/ladspa/" + RNNOISE_NAME,
        os.path.expanduser("~/.local/lib/ladspa/" + RNNOISE_NAME),
        os.path.expanduser("~/.ladspa/" + RNNOISE_NAME),
        "/usr/local/lib/ladspa/" + RNNOISE_NAME,
    ]
    cands += glob.glob("/usr/lib/*/ladspa/" + RNNOISE_NAME)
    for c in cands:
        if os.path.isfile(c):
            return os.path.realpath(c)
    return None


def ensure_rnnoise():
    found = find_rnnoise()
    if found:
        return found
    dest_dir = os.path.expanduser("~/.local/lib/ladspa")
    dest = os.path.join(dest_dir, RNNOISE_NAME)
    os.makedirs(dest_dir, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory() as td:
            zpath = os.path.join(td, "linux-rnnoise.zip")
            urllib.request.urlretrieve(RNNOISE_URL, zpath)
            with zipfile.ZipFile(zpath) as zf:
                member = next((n for n in zf.namelist()
                               if n.endswith(RNNOISE_NAME) and "ladspa" in n), None)
                if not member:
                    return None
                zf.extract(member, td)
                shutil.copy2(os.path.join(td, member), dest)
        return dest if os.path.isfile(dest) else None
    except Exception:
        return None


def source_exists(name):
    _, out, _ = run(["pactl", "list", "short", "sources"])
    return any(l.split("\t")[1:2] == [name] for l in out.splitlines())


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def remove_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def filter_config(mode, target):
    head = "# Written by Beats\n"
    if mode == "strong":
        return head + f'''context.modules = [
  {{ name = libpipewire-module-echo-cancel
    args = {{
      library.name = aec/libspa-aec-webrtc
      aec.args = {{
        webrtc.noise_suppression = true
        webrtc.gain_control = true
        webrtc.high_pass_filter = true
        webrtc.voice_detection = true
      }}
      capture.props = {{
        node.name = "capture.{OUR_SRC}"
        node.passive = true
        target.object = "{target}"
      }}
      source.props = {{
        node.name = "{OUR_SRC}"
        node.description = "Beats (cleaned)"
      }}
      sink.props = {{
        node.name = "headset_mic_fixer_unused_sink"
        node.description = "Beats (internal)"
        priority.session = 1
      }}
      playback.props = {{
        node.name = "playback.headset_mic_fixer"
        node.passive = true
      }}
    }}
  }}
]
'''
    if mode == "light":
        graph = '''
        nodes = [
          { type = builtin name = hp label = bq_highpass control = { "Freq" = 90.0 "Q" = 0.7 } }
          { type = builtin name = lp label = bq_lowpass  control = { "Freq" = 8000.0 "Q" = 0.7 } }
        ]
        links = [ { output = "hp:Out" input = "lp:In" } ]
        inputs  = [ "hp:In" ]
        outputs = [ "lp:Out" ]'''
    else:
        graph = f'''
        nodes = [
          {{ type = ladspa name = rnnoise plugin = "{find_rnnoise()}"
            label = noise_suppressor_mono
            control = {{ "VAD Threshold (%)" = 50.0 }} }}
        ]'''
    return head + f'''context.modules = [
  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = "Beats (cleaned)"
      media.name       = "Beats (cleaned)"
      filter.graph = {{{graph}
      }}
      audio.rate = 48000
      audio.channels = 1
      audio.position = [ MONO ]
      capture.props = {{
        node.name = "capture.{OUR_SRC}"
        node.passive = true
        target.object = "{target}"
      }}
      playback.props = {{
        node.name = "{OUR_SRC}"
        media.class = Audio/Source
      }}
    }}
  }}
]
'''


def wp_config(msbc, no_autoswitch, no_suspend):
    parts = ["# Written by Beats\n"]
    if msbc:
        parts.append('''monitor.bluez.properties = {
  bluez5.enable-sbc-xq = true
  bluez5.enable-msbc = true
  bluez5.enable-hw-volume = true
}
''')
    if no_autoswitch:
        parts.append('''wireplumber.settings = {
  bluetooth.autoswitch-to-headset-profile = false
}
''')
    if no_suspend:
        parts.append('''monitor.alsa.rules = [
  {
    matches = [
      { node.name = "~alsa_input.*" }
      { node.name = "~alsa_output.*" }
    ]
    actions = { update-props = { session.suspend-timeout-seconds = 0 } }
  }
]
''')
    return "".join(parts)


def restart_audio(after=None):
    def work():
        run(["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"], 30)
        time.sleep(3)
        if after:
            GLib.idle_add(lambda: (after(), False)[1])
    threading.Thread(target=work, daemon=True).start()


def load_prefs():
    try:
        with open(PREFS) as f:
            prefs = json.load(f)
        return prefs if isinstance(prefs, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(prefs):
    try:
        os.makedirs(os.path.dirname(PREFS), exist_ok=True)
        with open(PREFS, "w") as f:
            json.dump(prefs, f)
    except OSError:
        pass


def gnome_interface_settings():
    src = Gio.SettingsSchemaSource.get_default()
    schema = src.lookup("org.gnome.desktop.interface", True) if src else None
    if schema and schema.has_key("color-scheme"):
        return Gio.Settings.new("org.gnome.desktop.interface")
    return None


def system_prefers_dark(iface):
    scheme = iface.get_string("color-scheme") if iface else "default"
    if scheme in ("prefer-dark", "prefer-light"):
        return scheme == "prefer-dark"
    name = Gtk.Settings.get_default().get_property("gtk-theme-name") or ""
    return "dark" in name.lower()


_POINTER = {}


def _pointer_cursor(widget):
    disp = widget.get_display()
    key = id(disp)
    if key not in _POINTER:
        _POINTER[key] = (Gdk.Cursor.new_from_name(disp, "pointer")
                         or Gdk.Cursor.new_for_display(disp, Gdk.CursorType.HAND2))
    return _POINTER[key]


def make_clickable(widget):
    widget.get_style_context().add_class("clickable")
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.POINTER_MOTION_MASK)

    def apply(_w=None, event=None):
        cur = _pointer_cursor(widget)
        win = event.window if event is not None else None
        if win is None:
            win = widget.get_window()
        if win is not None and cur is not None:
            win.set_cursor(cur)
        return False

    widget.connect("realize", lambda w: apply(w))
    widget.connect("enter-notify-event", apply)
    return widget


def apply_pointer_cursors(root):
    kinds = (Gtk.Button, Gtk.Switch, Gtk.ComboBox, Gtk.Range, Gtk.MenuItem)

    def walk(w):
        if isinstance(w, kinds):
            make_clickable(w)
        if isinstance(w, Gtk.Container):
            for child in w.get_children():
                walk(child)

    walk(root)


def _rounded_rect(cr, x, y, w, h, r):
    cr.new_path()
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 2 * math.pi)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()


def _draw_mic(cr):
    _rounded_rect(cr, 9, 2.5, 6, 11, 3)
    cr.stroke()
    cr.move_to(6.5, 12)
    cr.arc(12, 12, 5.5, math.pi, 0)
    cr.stroke()
    cr.move_to(12, 17.5)
    cr.line_to(12, 20.5)
    cr.move_to(8.5, 20.5)
    cr.line_to(15.5, 20.5)
    cr.stroke()


def _draw_filter(cr):
    cr.move_to(4, 4.5)
    cr.line_to(20, 4.5)
    cr.line_to(13.5, 13)
    cr.line_to(13.5, 19.5)
    cr.line_to(10.5, 21)
    cr.line_to(10.5, 13)
    cr.close_path()
    cr.stroke()


def _draw_bluetooth(cr):
    cr.move_to(8.5, 7.5)
    cr.line_to(15.5, 16.5)
    cr.line_to(12, 19.5)
    cr.line_to(12, 4.5)
    cr.line_to(15.5, 7.5)
    cr.line_to(8.5, 16.5)
    cr.stroke()


def _draw_headset(cr, fill=False):
    cr.move_to(5, 13.5)
    cr.arc(12, 13.5, 7, math.pi, 0)
    cr.stroke()
    _rounded_rect(cr, 3, 13, 4.5, 7, 1.6)
    (cr.fill if fill else cr.stroke)()
    _rounded_rect(cr, 16.5, 13, 4.5, 7, 1.6)
    (cr.fill if fill else cr.stroke)()
    cr.move_to(5.2, 19.5)
    cr.curve_to(4.2, 22.8, 8.2, 23.2, 9.6, 21.6)
    cr.stroke()
    cr.arc(10.2, 21.4, 1.15, 0, 2 * math.pi)
    (cr.fill if fill else cr.stroke)()


def _draw_logo(cr):
    paint_bars(cr, view=24, pad=2.0, max_h_ratio=0.72, base_ratio=0.88)


def _draw_pulse(cr):
    cr.move_to(3, 12)
    cr.line_to(6.5, 12)
    cr.line_to(8.5, 6)
    cr.line_to(12, 18.5)
    cr.line_to(14.5, 10)
    cr.line_to(16.5, 14)
    cr.line_to(21, 12)
    cr.stroke()


def _draw_sun(cr):
    cr.arc(12, 12, 3.5, 0, 2 * math.pi)
    cr.stroke()
    for i in range(8):
        a = i * math.pi / 4
        cr.move_to(12 + 6.5 * math.cos(a), 12 + 6.5 * math.sin(a))
        cr.line_to(12 + 8.5 * math.cos(a), 12 + 8.5 * math.sin(a))
    cr.stroke()


def _draw_moon(cr):
    # Crescent: circle (12,12) r=8 minus circle (16,8) r=6.5; angles meet at their intersections.
    cr.new_path()
    cr.arc(12, 12, 8, 0.149, 4.563)
    cr.arc_negative(16, 8, 6.5, 3.787, 0.925)
    cr.close_path()
    cr.stroke()


def _draw_auto(cr):
    cr.arc(12, 12, 8, 0, 2 * math.pi)
    cr.stroke()
    cr.arc(12, 12, 8, -math.pi / 2, math.pi / 2)
    cr.close_path()
    cr.fill()


def _draw_corners(cr, inward):
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        x, y = 12 - sx * 8, 12 - sy * 8
        if inward:
            cr.move_to(x + sx * 5, y)
            cr.line_to(x + sx * 5, y + sy * 5)
            cr.line_to(x, y + sy * 5)
        else:
            cr.move_to(x, y + sy * 5)
            cr.line_to(x, y)
            cr.line_to(x + sx * 5, y)
    cr.stroke()


_ICON_DRAW = {
    "mic": _draw_mic,
    "filter": _draw_filter,
    "bluetooth": _draw_bluetooth,
    "headset": lambda cr: _draw_headset(cr, False),
    "logo": _draw_logo,
    "pulse": _draw_pulse,
    "sun": _draw_sun,
    "moon": _draw_moon,
    "auto": _draw_auto,
    "fullscreen": lambda cr: _draw_corners(cr, False),
    "unfullscreen": lambda cr: _draw_corners(cr, True),
}


class VectorIcon(Gtk.DrawingArea):
    def __init__(self, kind, size=18, color=None):
        super().__init__()
        self.kind = kind
        self._color = color
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.connect("draw", self._on_draw)

    def set_kind(self, kind):
        self.kind = kind
        self.queue_draw()

    def _fg(self):
        if self._color:
            return self._color
        w = self
        while w:
            if w.get_style_context().has_class("header-icon"):
                return theme.rgb("text")
            if isinstance(w, Gtk.Button):
                flags = w.get_state_flags()
                if isinstance(w, Gtk.ToggleButton) and w.get_active():
                    return theme.rgb("accent")
                if flags & Gtk.StateFlags.PRELIGHT:
                    return theme.rgb("text")
                return theme.rgb("text_muted")
            w = w.get_parent()
        return theme.rgb("text")

    def _on_draw(self, _w, cr):
        alloc = self.get_allocation()
        side = max(1, min(alloc.width, alloc.height))
        cr.translate((alloc.width - side) / 2, (alloc.height - side) / 2)
        cr.scale(side / 24.0, side / 24.0)
        r, g, b = self._fg()
        cr.set_source_rgb(r, g, b)
        cr.set_line_width(1.75)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        _ICON_DRAW[self.kind](cr)
        return False


def render_app_icon(size=64):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    s = size / 64.0
    cr.scale(s, s)
    _rounded_rect(cr, 0, 0, 64, 64, 16)
    cr.set_source_rgb(*theme.rgb("surface"))
    cr.fill()
    _rounded_rect(cr, 1.25, 1.25, 61.5, 61.5, 14.75)
    cr.set_source_rgb(*theme.rgb("border"))
    cr.set_line_width(1.5)
    cr.stroke()
    cr.set_source_rgb(*theme.rgb("accent"))
    paint_bars(cr, view=64, pad=11.5)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def lbl(text, cls=None, wrap=True, xalign=0.0):
    w = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if cls:
        for c in cls.split():
            w.get_style_context().add_class(c)
    return w


def mkbtn(text, cb, kind=None):
    b = Gtk.Button(label=text)
    if kind:
        b.get_style_context().add_class(kind)
    b.connect("clicked", cb)
    return make_clickable(b)


def mkcombo():
    combo = make_clickable(Gtk.ComboBoxText())
    combo.set_size_request(SIZE["combo"], -1)
    for cell in combo.get_cells():
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        cell.set_property("max-width-chars", 32)
    return combo


def value_slider(lo, hi, fmt="{:.0f}%"):
    sc = make_clickable(Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1))
    sc.set_draw_value(False)
    sc.set_size_request(SIZE["slider"], -1)
    val = lbl("", "secondary num", wrap=False, xalign=1.0)
    val.set_size_request(40, -1)
    sc.connect("value-changed", lambda s: val.set_text(fmt.format(s.get_value())))
    box = Gtk.Box(spacing=SPACE["sm"] + SPACE["xs"])
    box.pack_start(sc, False, False, 0)
    box.pack_start(val, False, False, 0)
    return box, sc, val


class Column(Gtk.Box):
    def __init__(self, width, **kw):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kw)
        self._width = width
        self.set_halign(Gtk.Align.START)

    def do_get_preferred_width(self):
        lo, _nat = Gtk.Box.do_get_preferred_width(self)
        return lo, max(lo, self._width)


class Section(Gtk.Box):
    def __init__(self, title=None, note=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_margin_top(SPACE["section"])
        if title:
            head = lbl(title.upper(), "section-label", wrap=False)
            head.set_margin_bottom(SPACE["sm"])
            self.pack_start(head, False, False, 0)
        if note:
            self.note = lbl(note, "secondary")
            self.note.set_margin_bottom(SPACE["sm"])
            self.pack_start(self.note, False, False, 0)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pack_start(self.rows, False, False, 0)

    def add_row(self, label=None, control=None, desc=None):
        r = Gtk.Box(spacing=SPACE["md"])
        r.get_style_context().add_class("row")
        if label is not None:
            t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t.set_valign(Gtk.Align.CENTER)
            t.pack_start(lbl(label), False, False, 0)
            if desc:
                t.pack_start(lbl(desc, "secondary"), False, False, 0)
            r.pack_start(t, True, True, 0)
        if control is not None:
            control.set_valign(Gtk.Align.CENTER)
            if label is None:
                r.pack_start(control, True, True, 0)
            else:
                r.pack_end(control, False, False, 0)
        self.rows.pack_start(r, False, False, 0)
        return r

    def add_switch(self, label, desc=None, active=False):
        sw = make_clickable(Gtk.Switch())
        sw.set_active(active)
        self.add_row(label, sw, desc)
        return sw

    def add_actions(self, *widgets):
        r = Gtk.Box(spacing=SPACE["sm"])
        r.set_margin_top(SPACE["md"] - SPACE["xs"])
        for w in widgets:
            r.pack_start(w, False, False, 0)
        self.pack_start(r, False, False, 0)
        return r


class Meter:
    def __init__(self, cb):
        self.cb = cb
        self.proc = None
        self.gen = 0

    def start(self, source):
        self.stop()
        if not source or not shutil.which("parec"):
            return
        self.gen += 1
        gen = self.gen
        try:
            proc = subprocess.Popen(
                ["parec", "--device", source, "--rate=16000", "--channels=1",
                 "--format=s16le", "--latency-msec=60"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return
        self.proc = proc

        def work():
            while gen == self.gen:
                data = proc.stdout.read(1920)
                if not data:
                    break
                a = array.array("h")
                a.frombytes(data[: len(data) // 2 * 2])
                peak = (max(max(a), -min(a)) / 32768.0) if a else 0.0
                GLib.idle_add(self.cb, peak)
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        self.gen += 1
        if self.proc:
            try:
                self.proc.terminate()
            except OSError:
                pass
            self.proc = None


WAV = "/tmp/headset-mic-test.wav"
PAGES = [("mic", "Microphone", "mic"),
         ("filter", "Noise filter", "filter"),
         ("devices", "Devices & Bluetooth", "bluetooth"),
         ("wired", "Wired & power", "headset"),
         ("diag", "Diagnostics", "pulse")]
THEME_MODES = {"system": ("Match system", "auto"),
               "light": ("Light", "sun"),
               "dark": ("Dark", "moon")}


class BeatsWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(title=APP, application=application)
        self.set_show_menubar(False)
        self.set_default_size(980, 700)
        self.set_icon(render_app_icon(64))
        self.sources, self.cards, self.profile_names = [], [], []
        self._loading = False
        self._vol_timer = None
        self._refresh_timer = None
        self._rn_busy = find_rnnoise() is None
        self.meter = Meter(self.on_level)

        self.prefs = load_prefs()
        self.theme_mode = self.prefs.get("theme", "system")
        if self.theme_mode not in THEME_MODES:
            self.theme_mode = "system"
        self._fullscreen = False
        self.columns = []
        self._iface = gnome_interface_settings()
        if self._iface:
            self._iface.connect("changed::color-scheme", lambda *_: self.apply_theme())
        Gtk.Settings.get_default().connect("notify::gtk-theme-name", lambda *_: self.apply_theme())
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_theme()

        fs = Gio.SimpleAction.new("fullscreen", None)
        fs.connect("activate", lambda *_: self.set_fullscreen(not self._fullscreen))
        self.add_action(fs)
        application.set_accels_for_action("win.fullscreen", ["F11"])
        self.connect("window-state-event", self.on_window_state)
        self.connect("key-press-event", self.on_key)

        self.fs_buttons = []
        self.set_titlebar(self.header_bar())
        # GTK 3 hides the titlebar while fullscreen, so an in-window copy stands in for it.
        self.fs_header = self.header_bar()
        self.fs_header.set_decoration_layout(":close")
        self.fs_header.show_all()
        self.fs_header.set_no_show_all(True)
        self.fs_header.hide()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.pack_start(self.fs_header, False, False, 0)
        self.add(outer)
        root = Gtk.Box()
        root.get_style_context().add_class("app")
        outer.pack_start(root, True, True, 0)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        side.get_style_context().add_class("sidebar")
        side.set_size_request(SIZE["sidebar"], -1)
        side.set_margin_start(SPACE["md"] - SPACE["xs"])
        side.set_margin_end(SPACE["xs"])
        side.set_margin_top(SPACE["md"])
        brand_row = Gtk.Box(spacing=10)
        brand_row.set_margin_start(10)
        brand_row.set_margin_bottom(SPACE["md"])
        brand_row.pack_start(VectorIcon("logo", SIZE["icon"]), False, False, 0)
        brand_row.pack_start(lbl("Beats", "brand", wrap=False), False, False, 0)
        side.pack_start(brand_row, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        first = None
        for key, title, icon in PAGES:
            rb = Gtk.RadioButton.new_from_widget(first) if first is not None else Gtk.RadioButton()
            if first is None:
                first = rb
            rb.set_mode(False)
            rb.get_style_context().add_class("nav")
            row = Gtk.Box(spacing=10)
            glyph = VectorIcon(icon, SIZE["icon"])
            row.pack_start(glyph, False, False, 0)
            row.pack_start(lbl(title, wrap=False), False, False, 0)
            rb.add(row)
            rb.connect("toggled", self.on_nav, key)
            rb.connect("toggled", lambda *_w, g=glyph: g.queue_draw())
            rb.connect("enter-notify-event", lambda *_a, g=glyph: (g.queue_draw(), False)[1])
            rb.connect("leave-notify-event", lambda *_a, g=glyph: (g.queue_draw(), False)[1])
            make_clickable(rb)
            side.pack_start(rb, False, False, 0)
        side.set_margin_bottom(SPACE["md"] - SPACE["xs"])
        side.pack_end(self.view_controls(), False, False, 0)
        root.pack_start(side, False, False, 0)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(col, True, True, 0)
        col.pack_start(self.stack, True, True, 0)
        self.status = lbl("Ready.", "statusline")
        self.status.set_selectable(True)
        self.status.set_margin_start(SPACE["page_x"])
        self.status.set_margin_end(SPACE["page_x"])
        self.status.set_margin_top(SPACE["sm"])
        self.status.set_margin_bottom(SPACE["md"] - SPACE["xs"])
        col.pack_start(self.status, False, False, 0)

        self.add_page("mic", self.page_mic())
        self.add_page("filter", self.page_filter())
        self.add_page("devices", self.page_devices())
        self.add_page("wired", self.page_wired())
        self.add_page("diag", self.page_diag())

        self.connect("destroy", lambda *_: self.meter.stop())
        apply_pointer_cursors(self)
        self.refresh()
        self.start_watcher()
        self.prepare_rnnoise()
        self.say("Ready. Plug or unplug a headset - the list updates automatically.")

    def view_controls(self):
        bar = Gtk.Box(spacing=2)
        first = None
        for key, (tip, icon) in THEME_MODES.items():
            rb = Gtk.RadioButton.new_from_widget(first) if first is not None else Gtk.RadioButton()
            if first is None:
                first = rb
            rb.set_mode(False)
            rb.get_style_context().add_class("nav")
            rb.get_style_context().add_class("icon")
            rb.set_tooltip_text(tip)
            rb.add(VectorIcon(icon, SIZE["icon"]))
            rb.set_active(key == self.theme_mode)
            rb.connect("toggled", self.on_theme_mode, key)
            rb.connect("enter-notify-event", lambda w, _e: (w.get_child().queue_draw(), False)[1])
            rb.connect("leave-notify-event", lambda w, _e: (w.get_child().queue_draw(), False)[1])
            rb.connect("toggled", lambda w: w.get_child().queue_draw())
            bar.pack_start(make_clickable(rb), False, False, 0)
        return bar

    def header_bar(self):
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title(APP)
        icon = VectorIcon("fullscreen", 16)
        btn = Gtk.Button()
        btn.get_style_context().add_class("header-icon")
        btn.set_tooltip_text("Fullscreen (F11)")
        btn.set_action_name("win.fullscreen")
        btn.add(icon)
        btn.connect("enter-notify-event", lambda *_a: (icon.queue_draw(), False)[1])
        btn.connect("leave-notify-event", lambda *_a: (icon.queue_draw(), False)[1])
        hb.pack_end(make_clickable(btn))
        self.fs_buttons.append((btn, icon))
        return hb

    def apply_theme(self):
        if self.theme_mode == "system":
            dark = system_prefers_dark(self._iface)
        else:
            dark = self.theme_mode == "dark"
        theme.use("dark" if dark else "light")
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", dark)
        self.css.load_from_data(theme.stylesheet().encode())
        self.set_icon(render_app_icon(64))
        self.queue_draw()

    def on_theme_mode(self, btn, key):
        if not btn.get_active() or key == self.theme_mode:
            return
        self.theme_mode = key
        self.prefs["theme"] = key
        save_prefs(self.prefs)
        self.apply_theme()

    def set_fullscreen(self, on):
        if on != self._fullscreen:
            (self.fullscreen if on else self.unfullscreen)()

    def on_window_state(self, _w, event):
        self._fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        for btn, icon in self.fs_buttons:
            icon.set_kind("unfullscreen" if self._fullscreen else "fullscreen")
            btn.set_tooltip_text(
                "Leave fullscreen (F11 or Esc)" if self._fullscreen else "Fullscreen (F11)")
        self.fs_header.set_visible(self._fullscreen)
        align = Gtk.Align.FILL if self._fullscreen else Gtk.Align.START
        for col in self.columns:
            col.set_halign(align)
        return False

    def on_key(self, _w, event):
        if self._fullscreen and event.keyval == Gdk.KEY_Escape:
            self.unfullscreen()
            return True
        return False

    def add_page(self, key, content):
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(content)
        self.stack.add_named(sw, key)

    def page(self, title, subtitle=None):
        box = Column(SIZE["content"])
        self.columns.append(box)
        box.set_margin_start(SPACE["page_x"])
        box.set_margin_end(SPACE["page_x"])
        box.set_margin_top(SPACE["page_y"])
        box.set_margin_bottom(SPACE["page_y"])
        box.pack_start(lbl(title, "page-title"), False, False, 0)
        if subtitle:
            sub = lbl(subtitle, "secondary")
            sub.set_margin_top(SPACE["xs"])
            box.pack_start(sub, False, False, 0)
        return box

    def section(self, pg, title=None, note=None, expand=False):
        s = Section(title, note)
        pg.pack_start(s, expand, expand, 0)
        return s

    def say(self, msg):
        self.status.set_text(msg)

    def info(self, msg, kind=Gtk.MessageType.INFO):
        d = Gtk.MessageDialog(transient_for=self, message_type=kind,
                              buttons=Gtk.ButtonsType.OK, text=msg)
        d.get_style_context().add_class("app")
        apply_pointer_cursors(d)
        d.run()
        d.destroy()

    def on_nav(self, btn, key):
        if btn.get_active():
            self.stack.set_visible_child_name(key)
            self.sync_meter()

    def start_watcher(self):
        pattern = re.compile(r"'(new|remove)' on (source|card|server)|'change' on (card|server)")

        def work():
            while True:
                try:
                    proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True)
                    for line in proc.stdout:
                        if pattern.search(line):
                            GLib.idle_add(self.schedule_refresh)
                except Exception:
                    pass
                time.sleep(3)
        threading.Thread(target=work, daemon=True).start()

    def schedule_refresh(self):
        if self._refresh_timer:
            GLib.source_remove(self._refresh_timer)
        self._refresh_timer = GLib.timeout_add(700, self._do_refresh)
        return False

    def _do_refresh(self):
        self._refresh_timer = None
        self.refresh()
        self.say("Audio devices changed - list refreshed.")
        return False

    def page_mic(self):
        pg = self.page("Microphone")

        s = self.section(pg, "Input")
        self.src_combo = mkcombo()
        self.src_combo.connect("changed", self.on_src_changed)
        s.add_row("Device", self.src_combo)
        self.port_combo = mkcombo()
        self.port_combo.connect("changed", self.on_port_changed)
        s.add_row("Port", self.port_combo)
        vr, self.vol, self.vol_label = value_slider(0, 150)
        self.vol.connect("value-changed", self.on_vol)
        s.add_row("Volume", vr)
        self.mute = s.add_switch("Mute")
        self.mute.connect("notify::active", self.on_mute)

        s2 = self.section(pg, "Live level", "If the bar stays still while you speak, check mute, "
                                            "port and the card profile.")
        self.level = Gtk.LevelBar()
        self.level.set_min_value(0)
        self.level.set_max_value(1)
        self.level.add_offset_value("low", 0.5)
        self.level.add_offset_value("high", 0.8)
        self.level.add_offset_value("full", 0.97)
        self.level.add_offset_value("clip", 1.0)
        self.peak_lbl = lbl("-∞ dB", "secondary num", wrap=False, xalign=1.0)
        self.peak_lbl.set_size_request(56, -1)
        meter = Gtk.Box(spacing=SPACE["md"])
        self.level.set_valign(Gtk.Align.CENTER)
        meter.pack_start(self.level, True, True, 0)
        meter.pack_start(self.peak_lbl, False, False, 0)
        s2.add_row(control=meter)
        s2.add_actions(mkbtn("Record & play back (5 s)", lambda _: self.test(False)),
                       mkbtn("Test with active filter", lambda _: self.test(True)))

        s3 = self.section(pg, "Quick fixes", "Unmute turns on ALSA capture switches and raises a "
                                             "capture volume stuck near zero.")
        a = s3.add_actions(mkbtn("Unmute / enable capture", self.on_enable_capture, "tertiary"),
                           mkbtn("Refresh devices", lambda _: self.refresh(), "tertiary"))
        a.set_margin_top(0)
        return pg

    def on_level(self, peak):
        self.level.set_value(min(1.0, peak ** 0.5))
        self.peak_lbl.set_text(f"{20 * math.log10(peak):.0f} dB" if peak > 0.0001 else "-∞ dB")
        return False

    def sync_meter(self):
        s = self.cur_src()
        if self.stack.get_visible_child_name() == "mic" and s:
            self.meter.start(s["name"])
        else:
            self.meter.stop()

    def refresh(self):
        self._loading = True
        self.sources = get_sources()
        cur = default_source()
        self.src_combo.remove_all()
        sel = 0
        for i, s in enumerate(self.sources):
            self.src_combo.append_text(("[Bluetooth] " if s["bt"] else "") + s["desc"])
            if s["name"] == cur:
                sel = i
        if self.sources:
            self.src_combo.set_active(sel)
        self._loading = False
        self.load_source_details()
        self.refresh_cards()
        self.sync_meter()

    def cur_src(self):
        i = self.src_combo.get_active()
        return self.sources[i] if 0 <= i < len(self.sources) else None

    def load_source_details(self):
        s = self.cur_src()
        self._loading = True
        self.port_combo.remove_all()
        if s:
            for i, p in enumerate(s["ports"]):
                self.port_combo.append_text(port_label(p))
                if p["name"] == s["active"]:
                    self.port_combo.set_active(i)
            self.vol.set_value(s["vol"])
            self.vol_label.set_text(f"{s['vol']}%")
            self.mute.set_active(s["mute"])
        self._loading = False

    def on_src_changed(self, _):
        if self._loading:
            return
        s = self.cur_src()
        if s:
            ok, why = set_default_source(s["name"])
            self.say(f"Default input set to: {s['desc']}" if ok
                     else f"Could not change default input: {why}")
        self.load_source_details()
        self.sync_meter()

    def on_port_changed(self, _):
        s = self.cur_src()
        i = self.port_combo.get_active()
        if self._loading or not s or i < 0:
            return
        port = s["ports"][i]
        rc, _, err = run(["pactl", "set-source-port", s["name"], port["name"]])
        msg = "Port changed." if rc == 0 else f"Port change failed: {err.strip()}"
        if port.get("availability") == "not available":
            msg += " (The system reports nothing plugged into that port.)"
        self.say(msg)

    def on_vol(self, scale):
        self.vol_label.set_text(f"{int(scale.get_value())}%")
        if self._loading:
            return
        if self._vol_timer:
            GLib.source_remove(self._vol_timer)
        self._vol_timer = GLib.timeout_add(200, self.apply_vol, int(scale.get_value()))

    def apply_vol(self, pct):
        self._vol_timer = None
        s = self.cur_src()
        if s:
            run(["pactl", "set-source-volume", s["name"], f"{pct}%"])
        return False

    def on_mute(self, sw, _p):
        s = self.cur_src()
        if self._loading or not s:
            return
        run(["pactl", "set-source-mute", s["name"], "1" if sw.get_active() else "0"])
        self.say("Microphone muted." if sw.get_active() else "Microphone unmuted.")

    def on_enable_capture(self, _):
        changed = alsa_enable_capture()
        s = self.cur_src()
        if s:
            run(["pactl", "set-source-mute", s["name"], "0"])
        self.say("ALSA changes: " + ("; ".join(changed) if changed
                                     else "nothing needed changing."))
        self.refresh()

    def test(self, use_default):
        s = self.cur_src()
        if not s:
            self.say("No input device selected.")
            return
        target = None if use_default else s["name"]
        label = "the default input" if use_default else s["desc"]

        def work():
            GLib.idle_add(self.say, f"Recording 5 s from {label} - speak now…")
            err = record_test(target, WAV)
            lv = wav_levels(WAV)
            if lv is None:
                GLib.idle_add(self.say, "Recording failed: " + (err.strip() or "no audio captured"))
                return
            peak, rms = lv
            if peak < 0.5:
                verdict = ("SILENT - no signal. Likely muted, wrong port/profile, or the headset mic "
                           "isn't detected (see Devices & Diagnostics).")
            elif peak < 3:
                verdict = "very quiet - raise the volume or speak closer."
            elif peak >= 98:
                verdict = "clipping - lower the volume."
            else:
                verdict = "signal looks fine."
            GLib.idle_add(self.say, f"Peak {peak:.1f}% · avg {rms:.1f}% - {verdict}  Playing back…")
            rc, _, perr = run(["pw-play", WAV], 20)
            if rc != 0:
                GLib.idle_add(self.say, f"Peak {peak:.1f}% - {verdict}  Playback failed: {perr.strip()}")
        threading.Thread(target=work, daemon=True).start()

    def page_filter(self):
        pg = self.page("Noise filter", "Adds a cleaned virtual microphone, “Beats (cleaned)”, "
                                       "and makes it the default input.")
        s = self.section(pg, "Mode")
        self.modes = {}
        defs = [("off", "Off", None),
                ("light", "Light", "Cuts rumble below 90 Hz and hiss above 8 kHz."),
                ("strong", "Strong", "WebRTC noise suppression with automatic gain."),
                ("max", "Maximum", "RNNoise neural denoiser.")]
        first = None
        for key, name, desc in defs:
            rb = Gtk.RadioButton.new_from_widget(first) if first is not None else Gtk.RadioButton()
            if first is None:
                first = rb
            rb.get_style_context().add_class("choice")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            inner.pack_start(lbl(name), False, False, 0)
            if desc:
                inner.pack_start(lbl(desc, "secondary"), False, False, 0)
            rb.add(inner)
            make_clickable(rb)
            self.modes[key] = rb
            s.rows.pack_start(rb, False, False, 0)

        cur = "off"
        if os.path.exists(PW_CONF):
            txt = open(PW_CONF).read()
            cur = "strong" if "aec-webrtc" in txt else "max" if "rnnoise" in txt else "light"
        self.modes[cur].set_active(True)

        self.rn_note = lbl("", "secondary")
        self.rn_note.set_margin_top(SPACE["sm"])
        s.pack_start(self.rn_note, False, False, 0)
        restart = lbl("Restarts audio for a few seconds.", "secondary", wrap=False)
        restart.set_margin_start(SPACE["sm"])
        s.add_actions(mkbtn("Apply filter", self.on_apply_filter, "primary"),
                      mkbtn("Test with active filter", lambda _: self.test(True)),
                      restart)
        self.update_rnnoise_state()
        return pg

    def prepare_rnnoise(self):
        if find_rnnoise():
            self._rn_busy = False
            self.update_rnnoise_state()
            return
        self._rn_busy = True
        self.update_rnnoise_state()

        def work():
            ensure_rnnoise()
            self._rn_busy = False
            GLib.idle_add(self.update_rnnoise_state)

        threading.Thread(target=work, daemon=True).start()

    def update_rnnoise_state(self):
        if not hasattr(self, "modes"):
            return False
        ok = find_rnnoise() is not None
        self.modes["max"].set_sensitive(ok or self._rn_busy)
        if ok:
            self.rn_note.set_text("")
            self.rn_note.hide()
        elif self._rn_busy:
            self.rn_note.show()
            self.rn_note.set_text("Preparing Maximum mode…")
        else:
            self.rn_note.show()
            self.rn_note.set_text("Maximum mode is not ready on this machine. Strong is available.")
        return False

    def mode(self):
        for k, rb in self.modes.items():
            if rb.get_active():
                return k
        return "off"

    def on_apply_filter(self, _):
        mode = self.mode()
        if mode == "off":
            remove_file(PW_CONF)
            self.say("Removing filter…")
            restart_audio(lambda: (self.refresh(), self.say("Filter removed.")))
            return
        s = self.cur_src()
        if not s:
            self.info("Select a microphone first (Microphone page).")
            return
        if mode == "max" and not find_rnnoise():
            if self._rn_busy:
                self.say("Maximum mode is still being prepared. Try Apply again in a moment.")
                return
            self.prepare_rnnoise()
            self.say("Maximum mode is still being prepared. Try Apply again in a moment.")
            return
        write_file(PW_CONF, filter_config(mode, s["name"]))
        self.say("Applying filter - restarting audio services…")

        def verify(n=0):
            if source_exists(OUR_SRC):
                set_default_source(OUR_SRC)
                self.refresh()
                self.say("Filter active. Apps now use 'Beats (cleaned)'. "
                         "Try 'Test with active filter'.")
            elif n < 12:
                GLib.timeout_add(500, lambda: (verify(n + 1), False)[1])
            else:  # don't leave a broken filter in place
                remove_file(PW_CONF)
                restart_audio(lambda: (self.refresh(), self.say(
                    "That filter failed to load, so it was removed and your audio is back to normal. "
                    "Try another mode (see Diagnostics).")))
        restart_audio(verify)

    def page_devices(self):
        pg = self.page("Devices & Bluetooth")

        s = self.section(pg, "Sound card profile",
                         "A missing headset mic usually means an output-only profile. "
                         "Pick one with input, such as Duplex or Headset.")
        self.card_combo = mkcombo()
        self.card_combo.connect("changed", self.on_card_changed)
        s.add_row("Card", self.card_combo)
        self.prof_combo = mkcombo()
        s.add_row("Profile", self.prof_combo)
        s.add_actions(mkbtn("Switch profile", self.on_set_profile),
                      mkbtn("Auto-pick one with microphone", self.on_auto_profile, "tertiary"))

        txt = open(WP_CONF).read() if os.path.exists(WP_CONF) else ""
        s2 = self.section(pg, "Bluetooth & power",
                          "Bluetooth mics sound poor in call mode (HFP/HSP).")
        self.c_msbc = s2.add_switch("Wideband voice (mSBC)",
                                    "16 kHz call audio instead of 8 kHz. Also enables SBC-XQ "
                                    "and hardware volume.",
                                    "enable-msbc" in txt or not txt)
        self.c_auto = s2.add_switch("Don't auto-switch to call mode",
                                    "Keeps headphones in music quality.", "autoswitch" in txt)
        self.c_susp = s2.add_switch("Keep audio devices awake",
                                    "Stops pops when the mic starts. Slight power cost.",
                                    "suspend-timeout" in txt)
        s2.add_actions(mkbtn("Apply settings", lambda _: self.apply_wp(), "primary"),
                       mkbtn("Best quality: keep A2DP, use another mic", self.on_keep_a2dp))
        return pg

    def refresh_cards(self):
        self._loading = True
        self.cards = pactl_json("cards")
        self.card_combo.remove_all()
        for c in self.cards:
            pr = c.get("properties", {})
            name = pr.get("device.description") or pr.get("alsa.card_name") or c["name"]
            self.card_combo.append_text(("[Bluetooth] " if c["name"].startswith("bluez") else "") + name)
        self._loading = False
        if self.cards:
            self.card_combo.set_active(0)
        else:
            self.prof_combo.remove_all()

    def on_card_changed(self, _):
        self.prof_combo.remove_all()
        self.profile_names = []
        i = self.card_combo.get_active()
        if i < 0:
            return
        c = self.cards[i]
        for name, p in c.get("profiles", {}).items():
            if name == "off":
                continue
            self.profile_names.append(name)
            mark = "" if p.get("available", True) is not False else "  (unavailable)"
            ins = "  · has input" if p.get("sources", 0) else ""
            self.prof_combo.append_text(f"{p.get('description', name)}{ins}{mark}")
            if name == c.get("active_profile"):
                self.prof_combo.set_active(len(self.profile_names) - 1)

    def on_set_profile(self, _):
        i, j = self.card_combo.get_active(), self.prof_combo.get_active()
        if i < 0 or j < 0:
            return
        rc, _, err = run(["pactl", "set-card-profile", self.cards[i]["name"], self.profile_names[j]])
        self.say("Profile switched." if rc == 0 else f"Failed: {err.strip()}")

    def on_auto_profile(self, _):
        i = self.card_combo.get_active()
        if i < 0:
            return
        c = self.cards[i]
        cands = [(p.get("priority", 0), n) for n, p in c.get("profiles", {}).items()
                 if p.get("sources", 0) and p.get("available", True) is not False]
        if not cands:
            self.say("This card has no available profile with a microphone input.")
            return
        cands.sort(reverse=True)
        rc, _, err = run(["pactl", "set-card-profile", c["name"], cands[0][1]])
        self.say(f"Switched to profile {cands[0][1]}." if rc == 0 else f"Failed: {err.strip()}")

    def apply_wp(self, after=None):
        m, a, s_ = self.c_msbc.get_active(), self.c_auto.get_active(), self.c_susp.get_active()
        if not (m or a or s_):
            remove_file(WP_CONF)
        else:
            write_file(WP_CONF, wp_config(m, a, s_))
        self.say("Applying - restarting audio services…")

        def done():
            self.refresh()
            self.say("Settings applied. Reconnect your Bluetooth device if it doesn't pick them up.")
            if after:
                after()
        restart_audio(done)

    def on_keep_a2dp(self, _):
        self.c_auto.set_active(True)

        def after():
            for c in bt_cards():
                for pn in c.get("profiles", {}):
                    if pn.startswith("a2dp-sink"):
                        run(["pactl", "set-card-profile", c["name"], pn])
                        break
            others = [s for s in get_sources() if not s["bt"]]
            others.sort(key=lambda s: not s["internal"])
            if others:
                set_default_source(others[0]["name"])
                self.say(f"Headphones kept in high-quality mode; recording with: {others[0]['desc']}")
            else:
                self.say("No non-Bluetooth microphone found.")
            self.refresh()
        self.apply_wp(after)

    def page_wired(self):
        pg = self.page("Wired & power")
        controls = boost_controls()
        s = self.section(pg, "Hardware Mic Boost",
                         "Adds up to +30 dB before the signal reaches the computer, and amplifies "
                         "hiss with it. Lower it if recordings are noisy.")
        if controls:
            for card, name, idx, pct in controls:
                box, sc, val = value_slider(0, 100)
                sc.set_value(pct)
                val.set_text(f"{pct}%")
                sc.connect("value-changed", self.on_boost, card, name, idx)
                s.add_row(name, box, f"Card {card}")
        else:
            s.add_row(control=lbl("No Mic Boost control on this hardware.", "secondary"))

        s2 = self.section(pg, "Audio power saving",
                          "Intel HDA chips sleep when idle and pop or buzz on waking. Disabling it "
                          "costs a little battery and asks for your admin password.")
        a = s2.add_actions(mkbtn("Disable power saving", lambda _: self.power_save(True), "primary"),
                           mkbtn("Restore default", lambda _: self.power_save(False), "tertiary"))
        a.set_margin_top(SPACE["xs"])
        return pg

    def on_boost(self, scale, card, name, idx):
        run(["amixer", "-c", card, "sset", f"{name},{idx}", f"{int(scale.get_value())}%"])

    def power_save(self, disable):
        if disable:
            sh = (f'echo "options snd_hda_intel power_save=0 power_save_controller=N" > {MODPROBE}; '
                  'echo 0 > /sys/module/snd_hda_intel/parameters/power_save 2>/dev/null; '
                  'echo N > /sys/module/snd_hda_intel/parameters/power_save_controller 2>/dev/null; true')
        else:
            sh = (f'rm -f {MODPROBE}; '
                  'echo 1 > /sys/module/snd_hda_intel/parameters/power_save 2>/dev/null; true')
        rc, _, err = run(["pkexec", "sh", "-c", sh], 60)
        self.say(("Power saving disabled (persists after reboot)." if disable
                  else "Power saving restored.") if rc == 0 else f"Cancelled/failed: {err.strip()}")

    def page_diag(self):
        pg = self.page("Diagnostics", "A snapshot of your audio setup to paste into a bug report.")
        s = self.section(pg, "Output", expand=True)
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        block.get_style_context().add_class("codeblock")
        self.diag_view = Gtk.TextView(editable=False, monospace=True)
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(400)
        sw.add(self.diag_view)
        block.pack_start(sw, True, True, 0)
        s.pack_start(block, True, True, 0)
        s.add_actions(mkbtn("Run diagnostics", self.on_diag, "primary"),
                      mkbtn("Copy to clipboard", self.on_copy, "tertiary"))
        return pg

    def collect_diag(self):
        L = []

        def sec(title, text):
            L.append(f"== {title} ==")
            L.append(text.strip() if text.strip() else "(empty)")
            L.append("")
        sec("PipeWire", run(["pipewire", "--version"])[1])
        sec("pactl info", run(["pactl", "info"])[1])
        sec("Default source", default_source())
        lines = []
        for s in get_sources():
            lines.append(f"{s['name']}  vol={s['vol']}%  mute={s['mute']}  active_port={s['active']}")
            for p in s["ports"]:
                lines.append(f"    port {p['name']}: {p.get('description')} [{p.get('availability', '?')}]")
        sec("Input devices (sources)", "\n".join(lines))
        lines = []
        for c in pactl_json("cards"):
            lines.append(f"{c['name']}  active_profile={c.get('active_profile')}")
            for n, p in c.get("profiles", {}).items():
                lines.append(f"    profile {n}: sinks={p.get('sinks')} sources={p.get('sources')} "
                             f"available={p.get('available')}")
        sec("Cards & profiles", "\n".join(lines))
        sec("Jack detection (ALSA)", "\n".join(jack_info()) or "no jack controls reported")
        rc, o, e = run(["arecord", "-l"])
        sec("arecord -l", o or e)
        sec("Hardware Mic Boost controls", str(boost_controls() or "none"))
        sec("RNNoise plugin", find_rnnoise() or "not ready")
        sec("Config files", "\n".join(f"{p}  {'(exists)' if os.path.exists(p) else '(none)'}"
                                      for p in (WP_CONF, PW_CONF)))
        sec("wpctl status", run(["wpctl", "status"], 20)[1])
        return "\n".join(L)

    def on_diag(self, _):
        self.say("Collecting diagnostics…")
        self.diag_view.get_buffer().set_text(self.collect_diag())
        self.say("Diagnostics ready - use 'Copy to clipboard'.")

    def on_copy(self, _):
        buf = self.diag_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if not text:
            self.on_diag(None)
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
        self.say("Copied to clipboard.")


class BeatsApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = BeatsWindow(self)
            self.window.connect("destroy", self._on_window_destroy)
            self.window.show_all()
            self.window.update_rnnoise_state()
        self.window.deiconify()
        self.window.present()
        self.window.grab_focus()

    def _on_window_destroy(self, *_):
        if self.window is not None:
            self.window.meter.stop()
        self.window = None


def main():
    GLib.set_prgname("beats")
    GLib.set_application_name(APP)
    Gdk.set_program_class("Beats")
    app = BeatsApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
