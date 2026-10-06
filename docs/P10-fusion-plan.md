# Fusion motion graphics - plan (2026-10-06)

Owner: "Animation, motion graphics should done in davinci fusion page not drawing ... learn about
fusion page ... and proceed how it should be worked."

Owner's choices (2026-10-06):
- **The AI writes Fusion scripts per shot.** They run through Resolve's MCP `run_script`, and each
  script shows on an approval card.
- **Fusion only.** Episode visuals no longer use drawn SVGs. Episodes need Resolve Studio, because
  the free Resolve has no external scripting.
- **The owner renders.** The app builds the Resolve project and timeline and stops. The owner
  renders in Resolve and points the episode at the file.
- **Style (updated by the owner, 2026-10-06):** "No reference for each and every script it should implement new style for easy understanding perspective super duper high quality with 1080p 60fps. not always same templeted style." Every script gets its OWN art direction, chosen for clarity. The Bravos Research video (https://www.youtube.com/watch?v=u70oUWgVoYU) is one thing learned from, not a template. Output is 1920x1080 at 60 fps.

## What was learned (sources)

### Resolve 21.1 Reference Manual

1. **Fusion and the timeline** (p1279-1280). Every timeline clip can carry a Fusion composition
   ("comp"). Fusion clips combine several layers. The Fusion page renders straight back into the
   timeline.
2. **Templates** (Ch. 67, p1433-1449). Fusion Titles, Generators, Effects and Transitions are
   macros (`.setting`) with chosen published controls.
   - They live in `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Templates\Edit\
     {Titles,Generators,Effects,Transitions}`, and Resolve must be restarted to see them.
   - **Keyframe Stretcher** and **Anim Curves** keep animation timing when a clip's duration
     changes.
   - Templates ship as a `.drfx` bundle (a zip with the folder tree).
3. **Animation:**
   - keyframes (Ch. 71, p1518), the Spline Editor (Ch. 72, p1535), motion paths (p1563);
   - modifiers and expressions (Ch. 74, p1581);
   - Text+ Write On, Character Level Styling, and the Follower modifier for per-character
     ripples (p2381-2396).
4. **3D** (Ch. 84, p1819-1871). The minimal lit scene is geometry (Text3D / Shape3D / Cube3D /
   ImagePlane3D / FBXMesh3D) → Merge3D (+ Camera3D, SpotLight) → Renderer3D, which outputs a 2D
   image.
   - **Models:** FBXMesh3D imports FBX, OBJ and DAE (Collada). Mind the scale, using the Size
     slider. USD comes in through the uLoader. Models texture through the material input.
   - **Camera:** Camera3D with depth of field (p1839-1841).
   - Duplicate3D / Mapped Duplicate 3D repeat objects (stacks of cash).
5. **Krokodove** (Ch. 105, p2433-2438, new in Resolve 21):
   - shape tools: sWriteOn (draws outlines on), sPrimitiveCreate, sTrace Create;
   - Duplicate, Grow and Fragments;
   - the Beat modifier, which drives animation from BPM;
   - text modifiers: Write (typewriter with cursor) and Juggle.

### Resolve's own MCP

`get_scripting_api` / `search_scripting_api` / `get_whats_new` give API 21.1.

- **Timeline and clips:**
  - `Timeline.InsertFusionCompositionIntoTimeline()`, `InsertFusionTitleIntoTimeline(name)`,
    `InsertFusionGeneratorIntoTimeline(name)`, `CreateFusionClip(items)`;
  - `TimelineItem.GetFusionCompByIndex(i)`, `AddFusionComp()`, `ImportFusionComp(path)`,
    `ExportFusionComp(path, i)`;
  - `Timeline.SetCurrentTimecode`, `AddTrack`, `GetItemListInTrack`.
- **Comp:**
  - `Composition.AddTool(id, …)`, `FindTool(name)`, `Paste(settings)`, `CopySettings()`,
    `StartUndo`;
  - `Operator.SetInput(id, value, time)`, `ConnectInput`, `AddModifier(input, "BezierSpline")`,
    `LoadSettings(file|table)`;
  - `BezierSpline.SetKeyFrames`; `TextPlus.UpdateWordAnimation(style)`.
- **Review and render:** `Project.ExportCurrentFrameAsStill(path)` gives frames for the reviewer;
  render jobs exist (`AddRenderJob` / `StartRendering`), but the owner renders.
- **New in 21.0** (get_whats_new): Krokodove plus 100+ motion-graphics tools, a new Macro Editor
  that publishes to Edit effects, audio-driven Fusion animation, native Lottie and OGraf HTML
  graphics, emoji and color fonts in Text+, and keyframing of Fusion titles and generators on
  the Edit timeline.
- **Safety:** `run_script` runs sandboxed Python 3.14 inside Resolve. `run_script_unsafe` stays
  blocked.

### Reference video style

80 frames every 15 s, plus 2 fps samples of two scenes, studied in `build/reference/`.

- **Look:**
  - charcoal background (~#16171B) with a soft vignette;
  - one crimson accent (~#E0234E), with red glow and dashed red outlines on the element in focus;
  - a small logo top-right.
- **2D language:**
  - rounded dark icon tiles (icon + label below) joined by dashed connector lines into
    flowcharts, popping in one after another while the whole layout slowly reframes;
  - red pill labels as section titles;
  - name cards (photo + name + red role pill);
  - news screenshots with red highlighted lines;
  - world maps with highlighted countries and dashed arcs;
  - flags in circles around a hub.
- **Data:** bar charts (red, glow on the key bar, dashed reference lines, value callouts),
  line charts (orange / blue / red with gradient fill), a 3D pie chart with callout pills,
  stacked horizontal bars.
- **3D scenes:** low-poly white/grey buildings (Capitol, Treasury) on dark ground; green
  cash-stack walls flying in; a paper document flipping in; a slow camera dolly back.

## How it will work

1. **Art direction per episode.** Before any shot, the master designs a style for this script:
   - palette, typography (installed: Inter, Bahnschrift, Cambria, Sitka, Consolas, Segoe UI);
   - icon treatment, background, 2D/3D mix, motion language (eases, stagger, camera), and the
     transitions between sections;
   - all chosen so the ideas are easiest to follow.
   It renders 3 style frames for review before production.
2. **Style-agnostic building blocks**, not a fixed look:
   - code helpers for backgrounds, cards, icon nodes, connectors with draw-on, labels with
     typewriter, charts, maps, 3D stage and camera rigs, highlight and focus treatments;
   - each takes the episode's style values (colors, fonts, radii, stroke weight, timing).
   - The worker's per-shot scripts call them, so shots stay consistent within an episode and
     differ between episodes.
3. **Episode visuals stage (Fusion only, 1080p60):**
   1. A fixed runner creates the episode's Resolve project (60 fps) and imports the voiceover,
      music and stock.
   2. It places one Fusion Composition clip per shot at its spoken-word time.
   3. The worker writes one Fusion script per shot. Each script is an approval card (the
      owner's choice; "allow for this chat" exists).
   4. It exports stills, and the master reviews them against the art direction (quality bar:
      motion blur, eased keyframes, supersampled 3D, clean type, no overlaps).
4. **The owner renders** in Resolve and picks the file in the episode. The video gate and
   packaging then run as now.
5. **3D models** (buildings, objects, props) come from a CC0/licensed model folder (FBX/OBJ),
   still to be decided. Primitives alone look crude (F1).

## F1 prototype results (2026-10-06, done)

Built by script through Resolve's MCP in Resolve project "One Production (disposable)/OP Fusion
prototype". Stills are in `build/reference/proto_*.png`.

- **2D flowchart shot** (62 nodes, 5 s):
  - background + vignette, red pill title, three rounded tiles with icons and labels, dashed
    connectors (sRectangle → sDuplicate → sRender);
  - tiles pop in one after another (Transform Size keyframes 0 → 1.08 → 1), a red glow highlight
    (RectangleMask outline + SoftGlow) fades in, and a slow push over the whole frame.
  - It already reads like the reference.
- **3D shot:**
  - ground plane, a Capitol-like building from primitives (Cube3D, 10 cylinders via Duplicate3D,
    hemisphere dome), two cash-stack walls (nested Duplicate3D);
  - the walls drop in with keyframed overshoot; the Camera3D is targeted, dollies back and rises;
  - directional + ambient light with shadows, OpenGL renderer, comp over a charcoal background.
  - It works, but primitives look crude next to the reference's textured models.
- **API facts verified:**
  - `InsertFusionCompositionIntoTimeline()` inserts at the playhead with a 150-frame default.
  - `GetFusionCompByIndex(1)`, `comp.AddTool(id, -32768, -32768)`, `ConnectInput`.
  - Animate with `AddModifier(input, "BezierSpline")`, then `SetInput(input, value, frame)`.
  - `ExportCurrentFrameAsStill(path)` writes a PNG from the Edit page. Timeline timecodes start at
    00:00:00:00 in a new project.
- **Mask units:** mask Width is relative to frame width and Height to frame height, so a square
  needs h = w × 16/9.
- **Cube3D:** has `SurfaceCubeInputs.SizeLock` = 1 by default (Width sets all sides; set it to 0),
  and its color is per face (`Front.|Back.|Left.|Right.|Top.|Bottom.MtlStdInputs.Diffuse.Color.*`).
  Shape3D uses `MtlStdInputs.Diffuse.Color.*`.
- **Renderer3D:** set `RendererType` to `RendererOpenGL`, then `RendererOpenGL.LightingEnabled` and
  `RendererOpenGL.ShadowsEnabled` to 1.
- **Text+:** `StyledText`, `Font`, `Style`, `Size`, `Center`, `Red1/Green1/Blue1`. Color emoji work
  ("Segoe UI Emoji").
- **Tool IDs** include `KD_*` (Krokodove), `sRectangle`/`sDuplicate`/`sRender`/`sOutline`,
  `SurfaceFBXMesh`, `uLoader`, `Duplicate3D`, `Camera3D`, `LightDirectional`, `LightAmbient`.

## F1b style study (2026-10-06, done)

Project "One Production (disposable)/OP Fusion style study 60p", **1080p 60 fps**. The same
3-step idea is built in 3 unrelated art directions, each 5 s, by script. Stills are in
`build/reference/styleA|B|C_*.png`.

- **A, editorial paper:**
  - warm corner gradient + FastNoise grain (Overlay, 5 %);
  - Cambria headline + Inter kicker, black Segoe Fluent line icons in drawn rings;
  - ink connectors that grow (RectangleMask Width + Center keys) with arrowheads;
  - an ochre bloom, staggered rise + fade with motion blur, a slow drift.
- **B, isometric 3D clay:**
  - pastel Cube3D blocks rise with overshoot, Text3D icon glyphs on their top faces, Text3D
    floor labels, growing connector bars;
  - soft shadows, a slow camera orbit, OpenGL 3× supersampling.
- **C, blueprint:**
  - navy + drafting grid, outlines drawing themselves on (RectangleMask `WriteLength` 0 → 1);
  - typewriter Consolas labels (Text+ `End` 0 → 1), cyan icons, dashed dimension lines,
    registration marks, a slow pull-out.
- **Learned:**
  - **EllipseMask** measures Width and Height both in frame-width units (circle: W = H).
    **RectangleMask** measures Height in frame-height units (square: h = w × 16/9).
  - **Colors are premultiplied:** a translucent solid needs RGB × alpha.
  - Text+ write-on is `Start` / `End`. Background `Type` = "Corner" takes
    `TopLeft|TopRight|BottomLeft|BottomRight` + `Red/Green/Blue`.
  - Merge `ApplyMode` = "Overlay" works.
  - `Transform` `MotionBlur` = 1, `Quality` = 6, `ShutterAngle` = 180 gives motion blur.
  - Shadows on thin 3D bars show speckle; shadow bias needs tuning.
  - **Icon font "Segoe Fluent Icons"** (built into Windows 11) has codepoints verified by
    render:
    - E716 people, E77B person, E80F home, E825 bank, E9D2 area chart, E909 globe, E8C7 card,
      E7BA warning, E8A1 news;
    - E9F9 report, EAFC trend up, E8EF calculator, E73E check, E711 cross, E710 plus,
      E72A arrow, E7C3 document;
    - E8F1 library, EA80 lightbulb, E8B7 folder, E8EC tag, E81C history, E9CE help,
      E821 briefcase, E8CB sort;
    - E7B8 archive, E80A grid, E95E heartbeat, E713 gear, E8BD chat, E7C1 flag, E8E1 thumbs up,
      E8E0 thumbs down, E7F4 monitor;
    - EB51 heart, EC05 antenna, E9E9 sliders, E9CA thermometer, E8AB swap.

## Milestones

- **F1 Prototype**, in a disposable project, needing the owner's OK to launch Resolve: one 2D
  flowchart shot (tiles + connectors + pill + reframing camera) and one 3D shot (stage, cash
  stacks via Duplicate3D, Camera3D dolly) built by script. Export stills and compare with the
  reference. This proves the API and look before anything is rebuilt.
- **F2 done (2026-10-06):** `app/fusion_kit.py`, the Fusion kit v1.
  - It is style-agnostic: values come from the episode's art.json stored in each comp. It has
    layers, masks, text, icons, typewriter, pop/rise, draw-on lines, dashed lines, glow, grain,
    vignette, bars, and a 3D stage with camera, lights and renderer.
  - **Resolve's sandbox has no `exec`**, so a shot script starts with `from op_kit import *` and
    the app swaps in the kit when the owner approves the card (`expand`, in the Claude agent's
    `can_use_tool`). The card shows the AI's code plus "kit v1".
  - It was run live on Resolve 21.1 and has 7 unit tests against a stand-in Fusion object model.
  - The art direction is written by the worker as art.json (concept, palette, installed fonts,
    motion, 3D yes/no) and checked by the master on the stills. The "3 style frames before the
    shots" idea folded into the stills review: the worker builds, exports stills and fixes,
    then the master reviews.
- **F3 done:** `resolve_runner.fusion_build` / `fusion_stills` + `app/fusion.py`, verified live.
  - Measured on 21.1: `InsertFusionCompositionIntoTimeline` is a **300-frame ripple insert at
    the playhead**. Inserting each shot in order at its start frame gives exact cuts; the
    leftovers past the end are deleted with `DeleteClips(…, False)`.
  - A Fusion comp **added to a video clip** (`AddFusionComp`) renders black in stills, and
    `CreateFusionClip` gives no comp handle, so shots are inserted compositions.
  - Still clips ignore `startFrame`/`endFrame`. Video clips honor them (end exclusive), and
    `recordFrame` works.
  - The voiceover goes on A1 via `AppendToTimeline([{mediaPoolItem, trackIndex: 1, recordFrame,
    mediaType: 2}])`.
  - `SetName` on timeline items works.
  - Projects go into the "One Production episodes" folder. 11 tests use an in-process stand-in
    Resolve with that behavior.
- **F4 done:** the episode's visuals stage is Fusion only.
  - The worker must be a Claude or API account (`Task.providers`), because Codex has no Resolve
    MCP.
  - Edit = "waiting for your render". `POST /api/projects/{pid}/episode/render {video}` resumes,
    also after a restart (`EpisodeRun.from_snapshot`). The video gate runs on the rendered file,
    then packaging.
  - The SVG `build_animation` tool is no longer offered to agents.
  - 8 episode tests.
- F5 Real episode (manual gate, `docs/gates/P10-production-manual.md`): needs the owner, Resolve
  open, both switches on, and the GPU for the voiceover.

Open: the 3D model source (owner folder vs CC0 downloads), fonts (the reference uses a geometric
sans similar to Inter / Montserrat), and the logo file.
