"""The One Production Fusion kit: fixed building blocks for the AI's per-shot Fusion scripts.

Resolve's MCP `run_script` runs sandboxed Python inside Resolve. `exec` and file access are
blocked there, so a script can't load a library itself. Instead the AI starts its script with
the line `from op_kit import *`. When the owner approves the card, the app replaces that line
with KIT below (`expand`). The card shows the AI's own code plus the kit version.

The kit is style-agnostic. Every color, font, size and timing comes from the episode's art
direction (stored in each shot's comp by the runner as "OneProduction.style") or from the call,
so episodes don't all look alike (owner, 2026-10-06: "not always same templeted style").
Everything here was checked against Resolve Studio 21.1 (docs/P10-fusion-plan.md, F1/F1b):
- EllipseMask: Width and Height are both in frame-width units. RectangleMask: Height is in
  frame-height units.
- Colors are premultiplied. Cube3D has SizeLock on by default and its colors are per face.
- Animation is BezierSpline keys. The OpenGL renderer needs lighting and shadows switched on.
"""
from __future__ import annotations

import re

KIT_VERSION = 3
MARKER = re.compile(r"^[ \t]*from[ \t]+op_kit[ \t]+import[ \t]+\*[ \t]*$", re.M)
FONTS = ("Inter", "Bahnschrift", "Cambria", "Sitka", "Consolas", "Segoe UI", "Segoe UI Emoji", "Segoe Fluent Icons")

# Windows 11 "Segoe Fluent Icons", codepoints verified by rendering them in Fusion (F1b)
ICONS = {
    "people": 0xE716, "person": 0xE77B, "home": 0xE80F, "bank": 0xE825, "chart": 0xE9D2, "globe": 0xE909,
    "card": 0xE8C7, "warning": 0xE7BA, "news": 0xE8A1, "report": 0xE9F9, "trend_up": 0xEAFC, "calculator": 0xE8EF,
    "check": 0xE73E, "cross": 0xE711, "plus": 0xE710, "arrow": 0xE72A, "document": 0xE7C3, "library": 0xE8F1,
    "idea": 0xEA80, "folder": 0xE8B7, "tag": 0xE8EC, "history": 0xE81C, "question": 0xE9CE, "briefcase": 0xE821,
    "sort": 0xE8CB, "archive": 0xE7B8, "grid": 0xE80A, "heartbeat": 0xE95E, "gear": 0xE713, "chat": 0xE8BD,
    "flag": 0xE7C1, "thumbs_up": 0xE8E1, "thumbs_down": 0xE8E0, "monitor": 0xE7F4, "heart": 0xEB51,
    "antenna": 0xEC05, "sliders": 0xE9E9, "thermometer": 0xE9CA, "swap": 0xE8AB,
}

KIT = r'''
# ---- One Production Fusion kit v%(version)d (fixed code, added by One Production) ----
OP_ICONS = %(icons)r
OP_W, OP_H = 1920.0, 1080.0
OP_QUALITY = "final"     # One Production sets "draft" for first-review drafts (lighter motion blur, no supersampling)
OP_ROOT = ""             # the project's files folder (generated assets live in OP_ROOT\assets)
OP_ASSETS = {}           # name -> {"file": "assets/<name>.png", "w": px, "h": px}
_FAR = -32768

def op_rgb(h):
    h = str(h).lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]

class Shot:
    """One shot's comp. Use: S = begin("shot-003"); ...build...; finish(S, layer)."""
    def __init__(self, comp, style, info):
        self.comp, self.style, self.info = comp, style or {}, info or {}
        self.frames = int(self.info.get("frames") or 180)
        self.fps = float(self.info.get("fps") or 60)
        self.last = self.frames - 1

    # -- style values: the episode's art direction, with the caller able to override
    def c(self, key, default="#FFFFFF"):
        return (self.style.get("palette") or {}).get(key) or default
    def font(self, role="body"):
        return (self.style.get("fonts") or {}).get(role) or "Inter"
    def sec(self, s):
        return int(round(float(s) * self.fps))
    def media(self):
        """A stock shot's footage (MediaIn1) to build on; None for a pure motion-graphics shot."""
        return self.comp.FindToolByID("MediaIn") if self.info.get("stock") else None

    # -- raw tools
    def add(self, tool_id, **inputs):
        t = self.comp.AddTool(tool_id, _FAR, _FAR)
        if not t:
            raise RuntimeError("Fusion could not add " + tool_id)
        for k, v in inputs.items():
            t.SetInput(k.replace("__", "."), v)
        return t
    def keys(self, tool, inp, pairs):
        """Animate an input: pairs of (frame, value). Fusion's default keys are smooth (eased)."""
        tool.AddModifier(inp, "BezierSpline")
        for f, v in pairs:
            tool.SetInput(inp, v, int(f))
        return tool
    @property
    def draft(self):
        return OP_QUALITY == "draft"
    def mblur(self, tool, quality=None):
        """Motion blur. Drafts use 2 samples (owner, 2026-10-12: renders were ~15 min for 1:46), finals 6."""
        q = quality or (2 if self.draft else 6)
        tool.SetInput("MotionBlur", 1); tool.SetInput("Quality", q); tool.SetInput("ShutterAngle", 180)
        return tool

    # -- 2D layers
    def solid(self, color, alpha=1.0):
        r, g, b = op_rgb(color)
        return self.add("Background", TopLeftRed=r * alpha, TopLeftGreen=g * alpha, TopLeftBlue=b * alpha, TopLeftAlpha=alpha)
    def gradient(self, tl, tr, bl, br):
        t = self.add("Background", Type="Corner")
        for corner, col in (("TopLeft", tl), ("TopRight", tr), ("BottomLeft", bl), ("BottomRight", br)):
            r, g, b = op_rgb(col)
            t.SetInput(corner + "Red", r); t.SetInput(corner + "Green", g); t.SetInput(corner + "Blue", b)
        return t
    def clear(self):
        return self.solid("#000000", 0.0)
    def over(self, back, front, fade=None, mode=None, blend=None):
        """Merge front over back. fade=(f0, f1) fades it in; mode e.g. "Overlay"; blend 0..1."""
        m = self.add("Merge")
        m.ConnectInput("Background", back); m.ConnectInput("Foreground", front)
        if mode: m.SetInput("ApplyMode", mode)
        if blend is not None: m.SetInput("Blend", blend)
        if fade: self.keys(m, "Blend", [(fade[0], 0.0), (fade[1], 1.0 if blend is None else blend)])
        return m
    def masked(self, color, mask, alpha=1.0):
        s = self.solid(color, alpha); s.ConnectInput("EffectMask", mask); return s
    def rect(self, x, y, w, h=None, radius=0.0, square=False, outline=0.0, soft=0.0):
        """Rectangle mask. w in frame widths; h in frame heights (square=True: h from w)."""
        if h is None or square:
            h = w * OP_W / OP_H
        m = self.add("RectangleMask", Center={1: x, 2: y}, Width=w, Height=h, CornerRadius=radius, SoftEdge=soft)
        if outline:
            m.SetInput("Solid", 0); m.SetInput("BorderWidth", outline)
        return m
    def circle(self, x, y, d, outline=0.0, soft=0.0):
        """Circle mask, d in frame widths (EllipseMask uses width units for both axes)."""
        m = self.add("EllipseMask", Center={1: x, 2: y}, Width=d, Height=d, SoftEdge=soft)
        if outline:
            m.SetInput("Solid", 0); m.SetInput("BorderWidth", outline)
        return m
    def text(self, s, x, y, size, color=None, role="body", style="Regular", font=None, align="center"):
        r, g, b = op_rgb(color or self.c("ink", "#FFFFFF"))
        t = self.add("TextPlus", StyledText=s, Font=font or self.font(role), Style=style, Size=size,
                     Center={1: x, 2: y}, Red1=r, Green1=g, Blue1=b)
        if align != "center":
            t.SetInput("HorizontalJustificationNew", 0 if align == "left" else 2)
        return t
    def icon(self, name, x, y, size, color=None):
        code = OP_ICONS.get(name, name) if isinstance(name, str) else name
        ch = chr(code) if isinstance(code, int) else str(code)
        return self.text(ch, x, y, size, color, font="Segoe Fluent Icons", style="Regular")
    def typewriter(self, text_tool, f0, f1):
        self.keys(text_tool, "End", [(f0, 0.0), (f1, 1.0)]); return text_tool

    # -- motion on a whole layer (returns the Transform to merge)
    def move(self, layer, pivot=(0.5, 0.5), blur=None):
        """blur=None: motion blur on every moving layer in a final, none in a draft (the shot's camera move
        in finish() still blurs) - stacked per-layer blur multiplies render time."""
        t = self.add("Transform", Pivot={1: pivot[0], 2: pivot[1]}, Center={1: 0.5, 2: 0.5})
        t.ConnectInput("Input", layer)
        if blur if blur is not None else not self.draft:
            self.mblur(t)
        return t
    def pop(self, layer, x, y, f0, length=None, overshoot=1.08):
        """Scale in from nothing with a small overshoot, around (x, y)."""
        n = length or self.sec(0.35)
        t = self.move(layer, (x, y))
        self.keys(t, "Size", [(f0, 0.0), (f0 + int(n * 0.65), overshoot), (f0 + n, 1.0)])
        return t
    def rise(self, layer, f0, dy=0.03, length=None):
        n = length or self.sec(0.45)
        t = self.move(layer)
        self.keys(t, "Center", [(f0, {1: 0.5, 2: 0.5 - dy}), (f0 + n, {1: 0.5, 2: 0.5})])
        return t
    def line(self, x0, y0, x1, y1, color=None, width=0.003, draw=None):
        """A straight line from (x0, y0) to (x1, y1), optionally drawing itself on over draw=(f0, f1)."""
        dx, dy = (x1 - x0) * OP_W, (y1 - y0) * OP_H
        length = (dx * dx + dy * dy) ** 0.5 / OP_W
        ang = 0.0
        if dx or dy:
            ang = _atan2(dy, dx) * 57.29577951308232
        m = self.add("RectangleMask", Center={1: (x0 + x1) / 2, 2: (y0 + y1) / 2}, Width=length,
                     Height=width * OP_W / OP_H, CornerRadius=1.0, Angle=ang)
        if draw:
            self.keys(m, "WriteLength", [(draw[0], 0.0), (draw[1], 1.0)])
        return self.masked(color or self.c("ink", "#FFFFFF"), m)
    def dashed(self, x0, x1, y, color=None, dash=0.008, gap=0.006, thickness=0.0022, fade=None):
        n = max(1, int((x1 - x0) / (dash + gap)))
        d = self.add("sRectangle", Width=dash, Height=thickness)
        r, g, b = op_rgb(color or self.c("muted", "#8A8F99"))
        d.SetInput("Red", r); d.SetInput("Green", g); d.SetInput("Blue", b)
        d.SetInput("Translate.X", x0 - 0.5 + dash / 2); d.SetInput("Translate.Y", (y - 0.5) * OP_H / OP_W)
        dup = self.add("sDuplicate", Copies=n - 1, XOffset=dash + gap); dup.ConnectInput("Input", d)
        out = self.add("sRender"); out.ConnectInput("Input", dup)
        return out
    def glow(self, layer, gain=0.8, size=12):
        """A soft glow of layer (keep gain under 1 for a tasteful halo)."""
        g = self.add("SoftGlow", Gain=gain, XGlowSize=size); g.ConnectInput("Input", layer); return g
    def grain(self, layer, amount=0.05):
        n = self.add("FastNoise", Detail=10, Contrast=1.2, XScale=600, SeetheRate=0.6)
        return self.over(layer, n, mode="Overlay", blend=amount)
    def vignette(self, layer, color="#000000", strength=0.35):
        edge = self.solid(color, strength)
        m = self.circle(0.5, 0.5, 1.5, soft=0.45); m.SetInput("Invert", 1)
        edge.ConnectInput("EffectMask", m)
        return self.over(layer, edge)

    # -- generated assets, stage and depth (kit v3, owner 2026-10-12: "use image generation for asset")
    def image(self, name, x=0.5, y=0.5, h=0.45, shadow=True):
        """A generated asset (assets/<name>.png, transparent) centred at (x, y), h = height in frame
        heights. Returns a full-frame layer with the asset (and a soft contact shadow) on it."""
        info = OP_ASSETS.get(name) or OP_ASSETS.get(str(name).replace("assets/", "").rsplit(".", 1)[0])
        if not info:
            raise RuntimeError("No generated asset called %s (assets: %s)" % (name, ", ".join(sorted(OP_ASSETS)[:20])))
        ld = self.add("Loader")
        ld.Clip = OP_ROOT + "\\" + info["file"].replace("/", "\\")
        ld.SetInput("Loop", 1)
        m = self.add("Merge", Center={1: x, 2: y}, Size=h * OP_H / float(info["h"]))
        m.ConnectInput("Background", self.clear()); m.ConnectInput("Foreground", ld)
        if not shadow:
            return m
        sh = self.add("Blur", XBlurSize=18)
        dark = self.add("ColorCorrector", MasterRGBGain=0.0, MasterAlphaGain=0.55)
        dark.ConnectInput("Input", m); sh.ConnectInput("Input", dark)
        drop = self.add("Transform", Center={1: 0.5 + 0.006, 2: 0.5 - 0.012})
        drop.ConnectInput("Input", sh)
        return self.over(drop, m)
    def backdrop(self, base=None, light=None, grain=0.035, vignette=0.45):
        """A lit stage instead of a flat colour: base colour, two soft light pools, grain and a vignette."""
        bg = self.solid(base or self.c("bg", "#101418"))
        lc = light or self.c("accent", "#FFFFFF")
        for (lx, ly, d, a) in ((0.32, 0.72, 0.9, 0.10), (0.78, 0.28, 0.7, 0.06)):
            pool = self.masked(lc, self.circle(lx, ly, d, soft=0.5), alpha=a)
            bg = self.over(bg, pool)
        return self.vignette(self.grain(bg, grain), strength=vignette)
    def float(self, layer, amount=0.006, period=None):
        """A slow bob so a hero object never sits dead still."""
        n = period or self.sec(3.0)
        t = self.move(layer, blur=False)
        keys, f, up = [], 0, True
        while f <= self.last + n:
            keys.append((f, {1: 0.5, 2: 0.5 + (amount if up else -amount)})); f += n // 2; up = not up
        self.keys(t, "Center", keys)
        return t
    def parallax(self, layers, depth=0.02):
        """Layers back to front; each drifts sideways a little more than the one behind it."""
        out = None
        for i, layer in enumerate(layers):
            t = self.move(layer, blur=False)
            d = depth * i / max(1, len(layers) - 1)
            self.keys(t, "Center", [(0, {1: 0.5 + d / 2, 2: 0.5}), (self.last, {1: 0.5 - d / 2, 2: 0.5})])
            out = t if out is None else self.over(out, t)
        return out
    def quote_card(self, source, line, highlight=None, date="", x=0.5, y=0.55, w=0.42, f0=0):
        """A designed source card (no logo, no imitation of a real page): source name, date, the quoted line,
        and the key phrase on a highlighter bar that wipes in. Returns the card layer."""
        h = 0.30 if highlight else 0.24
        card = self.masked("#F4F1EA", self.rect(x, y, w, h, radius=0.04))
        edge = self.masked(self.c("accent", "#E0234E"), self.rect(x, y, w, h, radius=0.04, outline=0.004))
        layer = self.over(self.over(self.clear(), card), edge)
        layer = self.over(layer, self.text(str(source).upper(), x - w / 2 + 0.02, y + h / 2 - 0.045, 0.022, "#1B1A17",
                                           style="Bold", align="left"))
        if date:
            layer = self.over(layer, self.text(date, x + w / 2 - 0.02, y + h / 2 - 0.045, 0.02, "#6B675F", align="right"))
        words, rows, cur = str(line).split(), [], ""
        for wd in words:
            if len(cur) + len(wd) > 36 and cur:
                rows.append(cur); cur = wd
            else:
                cur = (cur + " " + wd).strip()
        if cur: rows.append(cur)
        body = self.text("\n".join(rows[:4]), x - w / 2 + 0.02, y + 0.01, 0.034, "#1B1A17", role="heading",
                         style="SemiBold", align="left")
        layer = self.over(layer, body)
        if highlight:
            hy = y - h / 2 + 0.06
            bar = self.add("RectangleMask", Center={1: x - w / 2 + 0.02, 2: hy}, Width=w - 0.04, Height=0.07, CornerRadius=0.2)
            self.keys(bar, "Width", [(f0 + self.sec(0.4), 0.0), (f0 + self.sec(0.9), w - 0.04)])
            self.keys(bar, "Center", [(f0 + self.sec(0.4), {1: x - w / 2 + 0.02, 2: hy}), (f0 + self.sec(0.9), {1: x, 2: hy})])
            layer = self.over(layer, self.masked(self.c("accent", "#E0234E"), bar, alpha=0.85))
            layer = self.over(layer, self.text(highlight, x, hy, 0.032, "#FFFFFF", style="Bold"))
        return layer
    def person_tag(self, name, role="", x=0.5, y=0.2, figure=None):
        """A name chip (and optionally a generated generic figure asset above it). Never a real face."""
        layer = self.clear()
        if figure:
            layer = self.over(layer, self.image(figure, x, y + 0.26, 0.42))
        w = max(0.12, 0.012 * len(name) + 0.04)
        layer = self.over(layer, self.masked(self.c("accent", "#E0234E"), self.rect(x, y, w, 0.06, radius=0.15)))
        layer = self.over(layer, self.text(name.upper(), x, y + 0.004, 0.024, "#FFFFFF", style="Bold"))
        if role:
            layer = self.over(layer, self.text(role, x, y - 0.05, 0.02, self.c("muted", "#B8B8B8")))
        return layer

    # -- charts
    def bars(self, values, x0, y0, width, height, color=None, f0=0, stagger=None, gap=0.35, highlight=None, hcolor=None):
        """Vertical bars growing from the baseline y0 (frame units). Returns the layer of bars;
        self.bar_x holds each bar's center x (for labels). Only the highlighted bar glows."""
        layer = self.clear()
        top = max(values) or 1
        n = len(values)
        bw = width / (n + (n - 1) * gap)
        st = stagger if stagger is not None else self.sec(0.08)
        self.bar_x = []
        for i, v in enumerate(values):
            x = x0 + i * bw * (1 + gap) + bw / 2
            self.bar_x.append(x)
            h = height * v / top
            m = self.add("RectangleMask", Width=bw, CornerRadius=0.08)
            fa, fb = f0 + i * st, f0 + i * st + self.sec(0.5)
            self.keys(m, "Height", [(fa, 0.0), (fb, h)])
            self.keys(m, "Center", [(fa, {1: x, 2: y0}), (fb, {1: x, 2: y0 + h / 2})])
            if highlight == i:
                bar = self.masked(hcolor or self.c("accent", "#E0234E"), m)
                layer = self.over(self.over(layer, self.glow(bar, 0.6, 14), blend=0.7), bar)
            else:
                layer = self.over(layer, self.masked(color or self.c("muted", "#5A6270"), m))
        return layer

    # -- 3D
    def stage3d(self):
        return Stage3D(self)

def _atan2(y, x):
    # small atan2 without importing math (the sandbox may block imports)
    if x == 0:
        return 1.5707963267948966 if y > 0 else (-1.5707963267948966 if y < 0 else 0.0)
    z = y / x
    if abs(z) <= 1:
        a = z / (1 + 0.28125 * z * z)
    else:
        a = (1.5707963267948966 if z > 0 else -1.5707963267948966) - z / (z * z + 0.28125)
    if x < 0:
        a += 3.141592653589793 if y >= 0 else -3.141592653589793
    return a

class Stage3D:
    """A lit 3D scene. Add objects, a camera and lights, then .render() to get a 2D layer."""
    def __init__(self, shot):
        self.S, self.parts = shot, []
    def _place(self, t, pos=(0, 0, 0), rot=(0, 0, 0), scale=None):
        for axis, v in zip("XYZ", pos): t.SetInput("Transform3DOp.Translate." + axis, v)
        for axis, v in zip("XYZ", rot):
            if v: t.SetInput("Transform3DOp.Rotate." + axis, v)
        if scale is not None: t.SetInput("Transform3DOp.Scale.X", scale)
        return t
    def keep(self, t):
        self.parts.append(t); return t
    def cube(self, color, w, h, d, pos=(0, 0, 0), rot=(0, 0, 0)):
        c = self.S.add("Cube3D", SurfaceCubeInputs__SizeLock=0)
        c.SetInput("SurfaceCubeInputs.Width", w); c.SetInput("SurfaceCubeInputs.Height", h); c.SetInput("SurfaceCubeInputs.Depth", d)
        r, g, b = op_rgb(color)
        for face in ("Front", "Back", "Left", "Right", "Top", "Bottom"):
            c.SetInput(face + ".MtlStdInputs.Diffuse.Color.Red", r); c.SetInput(face + ".MtlStdInputs.Diffuse.Color.Green", g); c.SetInput(face + ".MtlStdInputs.Diffuse.Color.Blue", b)
        return self._place(c, pos, rot)
    def shape(self, kind, color, pos=(0, 0, 0), rot=(0, 0, 0), **dims):
        """kind: Plane, Cylinder, Sphere, Cone, Torus; dims like Radius=, Height=, Width=."""
        s = self.S.add("Shape3D", Shape="Surface%sInputs" % kind)
        for k, v in dims.items():
            s.SetInput("Surface%sInputs.%s" % (kind, k), v)
        r, g, b = op_rgb(color)
        s.SetInput("MtlStdInputs.Diffuse.Color.Red", r); s.SetInput("MtlStdInputs.Diffuse.Color.Green", g); s.SetInput("MtlStdInputs.Diffuse.Color.Blue", b)
        return self._place(s, pos, rot)
    def text3d(self, s, size, color, pos=(0, 0, 0), rot=(0, 0, 0), font="Inter", style="SemiBold"):
        t = self.S.add("Text3D", StyledText=s, Font=font, Style=style, Size=size)
        r, g, b = op_rgb(color)
        t.SetInput("Red1", r); t.SetInput("Green1", g); t.SetInput("Blue1", b)
        return self._place(t, pos, rot)
    def icon3d(self, name, size, color, pos=(0, 0, 0), rot=(-90, 0, 0)):
        code = OP_ICONS.get(name, name) if isinstance(name, str) else name
        return self.text3d(chr(code) if isinstance(code, int) else str(code), size, color, pos, rot, font="Segoe Fluent Icons", style="Regular")
    def model(self, path, size=1.0, pos=(0, 0, 0), rot=(0, 0, 0)):
        """An FBX/OBJ/DAE model from the project's folder (absolute path)."""
        m = self.S.add("SurfaceFBXMesh", ImportFile=path)
        m.SetInput("Size", size)
        return self._place(m, pos, rot)
    def duplicate(self, src, copies, **offsets):
        """Copies of src: offsets like Translate__X=1.0, Rotate__Y=10."""
        d = self.S.add("Duplicate3D", LastCopy=copies); d.ConnectInput("SceneInput", src)
        for k, v in offsets.items():
            d.SetInput(k.replace("__", "."), v)
        return d
    def group(self, *items):
        g = self.S.add("Merge3D")
        for n, it in enumerate(items, 1): g.ConnectInput("SceneInput%d" % n, it)
        t = self.S.add("Transform3D"); t.ConnectInput("SceneInput", g)
        return t
    def animate(self, t, channel, pairs):
        """channel like "Translate.Y", "Rotate.Y", "Scale.X" on a Transform3D / object / camera."""
        return self.S.keys(t, "Transform3DOp." + channel, pairs)
    def camera(self, path, target=(0, 0, 0), lens=None):
        """path: [(frame, (x, y, z)), ...]; the camera always looks at target."""
        cam = self.S.add("Camera3D"); cam.SetInput("Transform3DOp.UseTarget", 1)
        for axis, v in zip("XYZ", target): cam.SetInput("Transform3DOp.Target." + axis, v)
        if lens: cam.SetInput("FLength", lens)
        for i, axis in enumerate("XYZ"):
            self.S.keys(cam, "Transform3DOp.Translate." + axis, [(f, p[i]) for f, p in path])
        return self.keep(cam)
    def lights(self, key=0.8, fill=0.4, angle=(-50, 30), shadows=True, density=0.5):
        sun = self.S.add("LightDirectional", Intensity=key)
        sun.SetInput("Transform3DOp.Rotate.X", angle[0]); sun.SetInput("Transform3DOp.Rotate.Y", angle[1])
        if shadows:
            sun.SetInput("ShadowLightInputs3D.ShadowsEnabled", 1); sun.SetInput("ShadowLightInputs3D.ShadowDensity", density)
        amb = self.S.add("LightAmbient", Intensity=fill)
        self.keep(sun); self.keep(amb)
    def render(self, supersample=None, blur=True):
        m = self.S.add("Merge3D")
        for n, p in enumerate(self.parts, 1): m.ConnectInput("SceneInput%d" % n, p)
        r = self.S.add("Renderer3D"); r.ConnectInput("SceneInput", m)
        r.SetInput("RendererType", "RendererOpenGL")
        r.SetInput("RendererOpenGL.LightingEnabled", 1); r.SetInput("RendererOpenGL.ShadowsEnabled", 1)
        ss = supersample if supersample is not None else (1 if self.S.draft else 3)
        r.SetInput("RendererOpenGL.AntiAliasing.Presets.Color.Supersampling.LowQuality.Rate", ss)
        if blur and not self.S.draft: self.S.mblur(r)
        return r

def begin(name):
    """Open the shot's comp (by its timeline name), clear what a previous attempt built."""
    tl = project.GetCurrentTimeline()
    item = None
    for n in range(1, (tl.GetTrackCount("video") or 1) + 1):
        for it in tl.GetItemListInTrack("video", n) or []:
            if it.GetName() == name:
                item = it
    if item is None:
        raise RuntimeError("No shot named %s on this timeline" % name)
    comp = item.GetFusionCompByIndex(1)
    comp.Lock(); comp.StartUndo("One Production " + name)
    for t in list((comp.GetToolList(False) or {}).values()):
        if t.ID not in ("MediaIn", "MediaOut"):
            t.Delete()
    return Shot(comp, comp.GetData("OneProduction.style"), comp.GetData("OneProduction.shot"))

def finish(S, layer, push=0.03, drift=(0.0, 0.0)):
    """Connect the final layer to the shot's output with a slow camera push (0 = none)."""
    cam = S.move(layer, blur=True)
    if push or drift[0] or drift[1]:
        S.keys(cam, "Size", [(0, 1.0), (S.last, 1.0 + push)])
        S.keys(cam, "Center", [(0, {1: 0.5, 2: 0.5}), (S.last, {1: 0.5 + drift[0], 2: 0.5 + drift[1]})])
    S.comp.FindToolByID("MediaOut").ConnectInput("Input", cam)
    S.comp.EndUndo(True); S.comp.Unlock()
    return {"tools": len(S.comp.GetToolList(False) or {}), "frames": S.frames}
# ---- end of One Production Fusion kit ----
'''


GUIDE = """Fusion kit (start every shot script with the line `from op_kit import *`; One Production adds
the fixed kit code when the owner approves the script):
  S = begin("shot-007")          opens that shot's comp, clears an earlier attempt; S.frames, S.fps (60),
                                 S.last, S.sec(seconds) -> frames, S.style (the art direction), S.info (the brief)
  S.c("accent") / S.font("heading")   colors and fonts from art.json
  S.media()                      a stock shot's footage (its Fusion MediaIn) - put graphics OVER it with S.over(S.media(),
                                 layer); None on pure motion-graphics shots
  Stage and objects (v3): S.backdrop() a lit, textured stage (use it instead of a flat S.solid);
    S.image("vault-door", x, y, h) a generated asset (name from asset_generate, transparent, soft contact shadow);
    S.float(layer) a slow bob; S.parallax([back, mid, front]) depth drift;
    S.quote_card(source, line, highlight, date, x, y, w, f0) a designed source card (plain source name, never a logo
    or an imitated page); S.person_tag(name, role, x, y, figure) a name chip (figure = a generic generated figure,
    never a real person's face)
  Layers: S.solid(color, alpha), S.gradient(tl, tr, bl, br), S.clear(), S.text(s, x, y, size, color, role, style,
    align), S.icon(name, x, y, size, color), S.masked(color, mask), S.over(back, front, fade=(f0, f1), mode, blend),
    S.grain(layer, 0.04), S.vignette(layer, color, 0.35), S.glow(layer, 0.8, 12), S.dashed(x0, x1, y, color)
  Masks: S.rect(x, y, w, h, radius, square, outline), S.circle(x, y, d, outline), S.line(x0, y0, x1, y1, color,
    width, draw=(f0, f1))
  Motion: S.keys(tool, input, [(frame, value), ...]), S.pop(layer, x, y, f0), S.rise(layer, f0, dy),
    S.typewriter(text, f0, f1), S.move(layer, pivot), S.mblur(tool), S.bars(values, x0, y0, w, h, f0=,
    highlight=i) + S.bar_x
  3D: st = S.stage3d(); st.cube(color, w, h, d, pos, rot), st.shape("Cylinder"|"Sphere"|"Plane"|"Cone"|"Torus",
    color, pos, rot, Radius=, Height=, Width=), st.text3d(s, size, color, pos, rot, font), st.icon3d(name, size, color,
    pos), st.duplicate(obj, copies, Translate__Y=0.2), st.group(*objs), st.keep(obj), st.animate(obj,
    "Translate.Y", pairs), st.camera([(frame, (x, y, z)), ...], target), st.lights(key, fill), layer = st.render()
  End: result = finish(S, layer, push=0.03, drift=(0, 0))   (connects the output, slow camera push)
  Coordinates: x, y from 0 to 1 (0, 0 = bottom left); sizes are fractions of the frame width.
  Icons (Segoe Fluent Icons): """ + ", ".join(sorted(ICONS)) + """
  Fonts installed: """ + ", ".join(FONTS) + """
  Raw Fusion is fine too: S.add("ToolID", Input=value) with real IDs (Background, TextPlus, Merge, Transform,
  RectangleMask, EllipseMask, SoftGlow, FastNoise, KD_* Krokodove tools, Shape3D, Cube3D, Camera3D, Renderer3D ...)."""


def source() -> str:
    """The kit as it is sent to Resolve."""
    return KIT.replace("%(version)d", str(KIT_VERSION)).replace("%(icons)r", repr(ICONS))


def reference() -> str:
    """The kit's functions with their signatures and first doc line, for the shot workers (v2 re-draft,
    2026-10-12: without it they went searching outside the project for op_kit and raised cards)."""
    import ast
    tree = ast.parse(source())
    lines = [f"# One Production Fusion kit v{KIT_VERSION} - what `from op_kit import *` gives a shot script", "",
             "S = begin(\"shot-NNN\") starts a shot; finish(S, layer) ends it. Methods take fractions of the frame "
             "(x, y, sizes 0-1) and frames for times.", ""]

    def sig(fn):
        text = ast.unparse(fn.args)
        return text[6:] if text.startswith("self, ") else ("" if text == "self" else text)

    def doc(node):
        d = ast.get_docstring(node) or ""
        return (" - " + d.strip().splitlines()[0]) if d.strip() else ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_") and not node.name.startswith("op_"):
            lines.append(f"{node.name}({ast.unparse(node.args)}){doc(node)}")
        elif isinstance(node, ast.ClassDef):
            lines += ["", f"## {node.name}" + doc(node)]
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_"):
                    lines.append(f"{fn.name}({sig(fn)}){doc(fn)}")
    return "\n".join(lines) + "\n"


def settings_block(settings: dict | None) -> str:
    """Per-episode values the kit reads: draft quality, the project folder and the generated assets."""
    if not settings:
        return ""
    lines = ["# ---- this episode's settings (One Production) ----"]
    if settings.get("quality"):
        lines.append(f"OP_QUALITY = {str(settings['quality'])!r}")
    if settings.get("root"):
        lines.append(f"OP_ROOT = {str(settings['root'])!r}")
    if settings.get("assets") is not None:
        lines.append(f"OP_ASSETS = {dict(settings['assets'])!r}")
    return "\n" + "\n".join(lines) + "\n"


def expand(script: str, motion: str | None = None, project: str | None = None,
           settings: dict | None = None) -> tuple[str, bool]:
    """Replace `from op_kit import *` with the kit and `from ep_motion import *` with the episode's own
    motion library (owner, 2026-10-11: new animation for every script). `project`: the script may only
    run while that Resolve project is open (a guard line after the kit). `settings`: quality / root /
    assets for the kit (settings_block). (script, whether expanded)."""
    if not isinstance(script, str) or not (MARKER.search(script) or EP_MARKER.search(script)):
        return script, False
    head = source() + settings_block(settings) + (guard(project) if project else "")
    if MARKER.search(script):
        script = MARKER.sub(lambda _m: head, script, count=1)
        head = ""                                   # the kit is in; the motion library goes after it
    if EP_MARKER.search(script) and motion is not None:
        lib = "\n# ---- this episode's motion library (motion.py) ----\n" + motion + "\n# ---- end of motion.py ----\n"
        script = EP_MARKER.sub(lambda _m: head + lib, script, count=1)
    return script, True


# ---------------------------------------------------------------- episode motion library + scope (2026-10-11)
EP_MARKER = re.compile(r"^[ \t]*from[ \t]+ep_motion[ \t]+import[ \t]+\*[ \t]*$", re.M)
SHOT_CALL = re.compile(r"""\bbegin\(\s*["'](shot-\d{3,4})["']\s*\)""")
_FORBIDDEN = re.compile(r"(?<![.\w])(exec|eval|compile|__import__|open)\s*\(|^\s*(import|from)\s+(os|sys|subprocess|shutil|socket|ctypes"
                        r"|pathlib|importlib|urllib|http)\b|run_script_unsafe", re.M)
MAX_MOTION = 60_000


def guard(project: str) -> str:
    return (f"\nif project.GetName() != {project!r}:\n"
            f"    raise RuntimeError('One Production: open the Resolve project ' + {project!r} + ' to build this shot')\n")


def shots_in(script: str) -> list[str]:
    return SHOT_CALL.findall(script or "")


def check_motion(code: str) -> str:
    """An episode's motion.py: plain helper functions on top of the kit, nothing that reaches out."""
    if not isinstance(code, str) or not code.strip():
        raise ValueError("motion.py is empty")
    if len(code) > MAX_MOTION:
        raise ValueError(f"motion.py is over {MAX_MOTION // 1000} kB - keep it to reusable helpers")
    if MARKER.search(code) or EP_MARKER.search(code):
        raise ValueError("motion.py must not import op_kit or ep_motion itself (the app puts the kit before it)")
    bad = _FORBIDDEN.search(code)
    if bad:
        raise ValueError(f"motion.py may only build Fusion tools - not allowed: {bad.group(0).strip()[:40]}")
    try:
        compile(code, "motion.py", "exec")
    except SyntaxError as e:
        raise ValueError(f"motion.py line {e.lineno}: {e.msg}") from None
    return code
