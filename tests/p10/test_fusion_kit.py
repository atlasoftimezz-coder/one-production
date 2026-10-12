"""The Fusion kit (app/fusion_kit.py) run in plain Python against a stand-in Fusion object model.
Locks in what was learned on Resolve Studio 21.1 (F1/F1b): circle masks W = H, rectangle heights in
frame-height units, premultiplied colors, Cube3D SizeLock off + per-face colors, lit/shadowed OpenGL
renderer, BezierSpline keys, and the app-side `from op_kit import *` expansion."""
import pytest

from app import fusion_kit


class Tool:
    def __init__(self, comp, tool_id, n):
        self.comp, self.ID, self.Name = comp, tool_id, f"{tool_id}{n}"
        self.inputs, self.keys, self.links, self.mods, self.deleted = {}, {}, {}, {}, False

    def SetInput(self, name, value, time=None):
        if time is None:
            self.inputs[name] = value
        else:
            assert name in self.mods, f"{self.Name}.{name} keyed without a spline"
            self.keys.setdefault(name, {})[time] = value

    def GetInput(self, name, time=None):
        return self.inputs.get(name)

    def AddModifier(self, name, kind):
        self.mods[name] = kind
        return True

    def ConnectInput(self, name, tool):
        self.links[name] = tool
        return True

    def Delete(self):
        self.deleted = True
        self.comp.tools.remove(self)


class Comp:
    def __init__(self, data):
        self.tools, self.data, self.locked, self.n = [], dict(data), 0, 0
        self.AddTool("MediaIn", 0, 0); self.AddTool("MediaOut", 0, 0)

    def AddTool(self, tool_id, x, y):
        self.n += 1
        t = Tool(self, tool_id, self.n)
        self.tools.append(t)
        return t

    def GetToolList(self, selected=False, regid=None):
        return {i + 1: t for i, t in enumerate(t for t in self.tools if regid in (None, t.ID))}

    def FindToolByID(self, tool_id):
        return next((t for t in self.tools if t.ID == tool_id), None)

    def Lock(self): self.locked += 1
    def Unlock(self): self.locked -= 1
    def StartUndo(self, name): pass
    def EndUndo(self, keep): pass
    def GetData(self, key): return self.data.get(key)


class Item:
    def __init__(self, name, comp):
        self.name, self.comp = name, comp

    def GetName(self): return self.name
    def GetFusionCompByIndex(self, i): return self.comp


class Timeline:
    def __init__(self, items):
        self.items = items

    def GetTrackCount(self, kind): return 1
    def GetItemListInTrack(self, kind, n): return self.items


class Project:
    def __init__(self, tl):
        self.tl = tl

    def GetCurrentTimeline(self): return self.tl


STYLE = {"palette": {"bg": "#101418", "ink": "#EEF2F6", "accent": "#FFB547", "muted": "#7C8796"}, "fonts": {"heading": "Bahnschrift"}}


def kit(shot_code, frames=180, style=STYLE, settings=None):
    comp = Comp({"OneProduction.style": style, "OneProduction.shot": {"frames": frames, "fps": 60, "idea": "test"}})
    old = comp.AddTool("Background", 0, 0)                      # what an earlier attempt left behind
    env = {"project": Project(Timeline([Item("shot-001", Tool(comp, "x", 0)), Item("shot-007", comp)]))}
    script, used = fusion_kit.expand("from op_kit import *\n" + shot_code, settings=settings)
    assert used
    exec(compile(script, "shot", "exec"), env)
    return comp, env, old


def by(comp, tool_id):
    return [t for t in comp.tools if t.ID == tool_id]


def test_expand_only_replaces_the_marker_line():
    s, used = fusion_kit.expand("print(1)\n")
    assert s == "print(1)\n" and not used
    s, used = fusion_kit.expand("x = 1\n  from op_kit import *  \nS = begin('shot-001')\n")
    assert used and "class Shot" in s and s.startswith("x = 1\n") and "S = begin('shot-001')" in s
    assert "from op_kit import" not in s
    compile(fusion_kit.source(), "kit", "exec")                 # the kit itself is valid Python


def test_begin_clears_old_work_reads_the_style_and_finish_wires_the_output():
    comp, env, old = kit("S = begin('shot-007')\nresult = finish(S, S.solid(S.c('bg')), push=0.04)\n", frames=240)
    assert old.deleted and {t.ID for t in comp.tools} >= {"MediaIn", "MediaOut", "Background", "Transform"}
    S, res = env["S"], env["result"]
    assert S.frames == 240 and S.last == 239 and S.sec(0.5) == 30 and S.font("heading") == "Bahnschrift"
    out = comp.FindToolByID("MediaOut")
    cam = out.links["Input"]
    assert cam.ID == "Transform" and cam.inputs["MotionBlur"] == 1
    assert cam.keys["Size"] == {0: 1.0, 239: pytest.approx(1.04)} and comp.locked == 0
    assert res == {"tools": len(comp.tools), "frames": 240}
    with pytest.raises(RuntimeError, match="No shot named shot-999"):
        kit("S = begin('shot-999')\n")


def test_masks_and_colors_follow_the_resolve_rules():
    comp, env, _ = kit("S = begin('shot-007')\n"
                       "c = S.circle(0.3, 0.5, 0.1, outline=0.002)\n"
                       "r = S.rect(0.5, 0.5, 0.1, square=True, radius=0.2)\n"
                       "s = S.solid('#FF0000', 0.25)\n"
                       "line = S.line(0.1, 0.2, 0.5, 0.2, draw=(10, 40))\n")
    c = by(comp, "EllipseMask")[0].inputs
    assert c["Width"] == c["Height"] == 0.1 and c["Solid"] == 0 and c["BorderWidth"] == 0.002
    r = by(comp, "RectangleMask")[0].inputs
    assert r["Height"] == pytest.approx(0.1 * 1920 / 1080)       # square: height in frame-height units
    s = env["s"].inputs
    assert s["TopLeftAlpha"] == 0.25 and s["TopLeftRed"] == pytest.approx(0.25) and s["TopLeftGreen"] == 0  # premultiplied
    lm = by(comp, "RectangleMask")[1]
    assert lm.inputs["Width"] == pytest.approx(0.4) and lm.inputs["Angle"] == pytest.approx(0.0, abs=1e-6)
    assert lm.keys["WriteLength"] == {10: 0.0, 40: 1.0}


def test_text_icons_typewriter_and_pop():
    comp, env, _ = kit("S = begin('shot-007')\n"
                       "t = S.typewriter(S.text('Hello', 0.5, 0.8, 0.05, role='heading', style='Bold'), 4, 40)\n"
                       "i = S.icon('bank', 0.5, 0.5, 0.06)\n"
                       "p = S.pop(i, 0.5, 0.5, 20)\n")
    t, i, p = env["t"].inputs, env["i"].inputs, env["p"]
    assert t["Font"] == "Bahnschrift" and t["Style"] == "Bold" and env["t"].keys["End"] == {4: 0.0, 40: 1.0}
    assert i["Font"] == "Segoe Fluent Icons" and i["StyledText"] == chr(0xE825)
    assert t["Red1"] == pytest.approx(0xEE / 255)                # ink from the art direction
    assert p.keys["Size"][20] == 0.0 and max(p.keys["Size"].values()) == pytest.approx(1.08) and p.inputs["Pivot"] == {1: 0.5, 2: 0.5}


def test_bars_grow_from_the_baseline_and_only_the_highlight_glows():
    comp, env, _ = kit("S = begin('shot-007')\nb = S.bars([4, 2, 1], 0.2, 0.25, 0.6, 0.4, f0=10, highlight=0)\n")
    S = env["S"]
    masks = [m for m in by(comp, "RectangleMask") if "Height" in m.keys]
    assert len(masks) == 3 and len(S.bar_x) == 3 and S.bar_x == sorted(S.bar_x)
    first = masks[0].keys["Height"]
    assert min(first.values()) == 0.0 and max(first.values()) == pytest.approx(0.4)
    assert len(by(comp, "SoftGlow")) == 1


def test_3d_stage_sets_size_lock_off_per_face_colors_and_a_lit_renderer():
    comp, env, _ = kit("S = begin('shot-007')\n"
                       "st = S.stage3d()\n"
                       "cube = st.keep(st.cube('#2E8F4F', 0.9, 0.25, 0.45, pos=(0, 0.13, 0)))\n"
                       "cyl = st.keep(st.shape('Cylinder', '#FFB547', Radius=0.6, Height=0.2))\n"
                       "st.camera([(0, (0, 2, 6)), (179, (1, 3, 7))], target=(0, 0.5, 0))\n"
                       "st.lights(0.9, 0.35)\n"
                       "r = st.render()\n")
    cube = by(comp, "Cube3D")[0].inputs
    assert cube["SurfaceCubeInputs.SizeLock"] == 0 and cube["SurfaceCubeInputs.Width"] == 0.9 and cube["SurfaceCubeInputs.Height"] == 0.25
    for face in ("Front", "Back", "Left", "Right", "Top", "Bottom"):
        assert cube[face + ".MtlStdInputs.Diffuse.Color.Green"] == pytest.approx(0x8F / 255)
    cyl = by(comp, "Shape3D")[0].inputs
    assert cyl["Shape"] == "SurfaceCylinderInputs" and cyl["SurfaceCylinderInputs.Radius"] == 0.6
    cam = by(comp, "Camera3D")[0]
    assert cam.inputs["Transform3DOp.UseTarget"] == 1 and cam.keys["Transform3DOp.Translate.Z"] == {0: 6, 179: 7}
    r = env["r"].inputs
    assert r["RendererType"] == "RendererOpenGL" and r["RendererOpenGL.LightingEnabled"] == 1 and r["RendererOpenGL.ShadowsEnabled"] == 1
    merge = by(comp, "Merge3D")[-1]
    assert len(merge.links) == 5                                 # cube, cylinder, camera, sun, ambient


ASSETS = {"vault-door": {"file": "assets/vault-door.png", "w": 1200, "h": 1600}}


def test_draft_quality_blurs_only_the_camera_move_and_lightly():
    """Owner's first draft rendered ~15 min for 1:46: every pop/rise carried 6-sample motion blur."""
    code = ("S = begin('shot-007')\n"
            "p = S.pop(S.icon('bank', 0.5, 0.5, 0.06), 0.5, 0.5, 10)\n"
            "st = S.stage3d(); st.keep(st.cube('#FFB547', 1, 1, 1)); st.lights(); r = st.render()\n"
            "result = finish(S, S.over(p, r))\n")
    comp, env, _ = kit(code, settings={"quality": "draft"})
    blurred = [t for t in comp.tools if t.inputs.get("MotionBlur") == 1]
    assert [t.ID for t in blurred] == ["Transform"] and blurred[0] is comp.FindToolByID("MediaOut").links["Input"]
    assert blurred[0].inputs["Quality"] == 2
    assert env["r"].inputs["RendererOpenGL.AntiAliasing.Presets.Color.Supersampling.LowQuality.Rate"] == 1
    comp, env, _ = kit(code)                                       # final quality: as before
    assert env["p"].inputs["MotionBlur"] == 1 and env["p"].inputs["Quality"] == 6
    assert env["r"].inputs["RendererOpenGL.AntiAliasing.Presets.Color.Supersampling.LowQuality.Rate"] == 3


def test_generated_assets_load_from_the_project_at_their_height():
    comp, env, _ = kit("S = begin('shot-007')\nv = S.image('vault-door', 0.4, 0.5, 0.6)\n",
                       settings={"root": r"C:\proj\files", "assets": ASSETS})
    ld = by(comp, "Loader")[0]
    assert ld.Clip == r"C:\proj\files\assets\vault-door.png"
    merge = [m for m in by(comp, "Merge") if m.links.get("Foreground") is ld][0]
    assert merge.inputs["Center"] == {1: 0.4, 2: 0.5} and merge.inputs["Size"] == pytest.approx(0.6 * 1080 / 1600)
    assert by(comp, "Blur")                                        # a soft contact shadow
    with pytest.raises(RuntimeError, match="No generated asset called gold-bar"):
        kit("S = begin('shot-007')\nS.image('gold-bar')\n", settings={"root": "C:\\p", "assets": ASSETS})


def test_quote_card_person_tag_backdrop_and_depth():
    comp, env, _ = kit("S = begin('shot-007')\n"
                       "q = S.quote_card('Reuters', 'Central banks bought more than one thousand tonnes of gold in 2022', "
                       "highlight='more than one thousand tonnes', date='Jan 2023', f0=10)\n"
                       "t = S.person_tag('Treasury Secretary', 'United States')\n"
                       "b = S.backdrop()\n"
                       "f = S.float(q)\n"
                       "px = S.parallax([b, q, t])\n", settings={"root": "C:\\p", "assets": ASSETS})
    texts = [t.inputs.get("StyledText") for t in by(comp, "TextPlus")]
    assert "REUTERS" in texts and "more than one thousand tonnes" in texts and "TREASURY SECRETARY" in texts
    body = next(s for s in texts if s and s.startswith("Central banks"))
    assert "\n" in body and all(len(row) <= 40 for row in body.split("\n"))     # wrapped for the card
    hl = [m for m in by(comp, "RectangleMask") if "Width" in m.keys]
    assert hl and min(hl[0].keys["Width"].values()) == 0.0                     # the highlighter wipes in
    assert not by(comp, "Loader")                                              # no image of a real person
    assert by(comp, "FastNoise") and len(env["f"].keys["Center"]) >= 3
    drifts = [t for t in by(comp, "Transform") if "Center" in t.keys and len(t.keys["Center"]) == 2]
    assert len(drifts) == 3


def test_settings_block_and_shot_scripts_read_the_asset_index(tmp_path):
    from app.fusion import ShotScripts
    import json
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index.json").write_text(json.dumps({"vault-door": {**ASSETS["vault-door"], "prompt": "x"}}), encoding="utf-8")
    s = ShotScripts("P", {"shot-001"}, None, None, auto=True, root=tmp_path, quality="draft")
    out = s.auto("from op_kit import *\nS = begin('shot-001')\n")
    assert "OP_QUALITY = 'draft'" in out and "'vault-door'" in out and repr(str(tmp_path)) in out
    assert out.index("OP_QUALITY = 'draft'") > out.index("class Shot")      # settings after the kit's defaults


def test_guide_lists_every_icon_and_font():
    for name in fusion_kit.ICONS:
        assert name in fusion_kit.GUIDE
    for font in fusion_kit.FONTS:
        assert font in fusion_kit.GUIDE
