# P10 rest: production engine (gates, visuals, edit, packaging)

Owner, 2026-10-05: "start P10 rest". Master plan block P10 in `PLAN.txt`. Manual approvals for
P7-P10 part 1 and the knowledge extension remain pending; nothing here declares them approved.

Principles carried over: free-first (ffmpeg default; Resolve/Storyblocks optional behind their
switches), CPU only unless the owner OKs GPU, nothing paid, no model downloads, every heavy job
through the R12 gate, art drawn by the AI in code (SVG) rather than image models.

## Steps (each: build, focused tests, commit)

R1 **Numeric stage gates** (`app/gates.py`, no AI, CPU):
   - Voice: every read-check section WER <= 0.05; median pitch within 1 semitone of the
     reference clip; integrated loudness -14 LUFS +/- 0.5; true peak <= -1.0 dBTP; subtitles
     complete (cues ordered, none overlapping or empty, covering the audio to within 1.5 s).
   - Video: duration = voiceover +/- 0.5 s; audio/video stream drift < 40 ms; no black frames.
   - Package: YouTube limits (title 1-100 chars, description <= 5000, tags <= 500 chars),
     chapters start at 0:00, at least 3, each >= 10 s, inside the video; thumbnail 1280x720
     and under 2 MB when present.
   - Routes + a report saved beside the checked files; failures name the number that missed.
   - New dependency: numpy (pitch by autocorrelation). No model, no GPU.
R2 **ffmpeg editor** (`app/editor.py`): an edit list (`edit.json`) of voiceover, ordered
   visuals (stills with slow push/pan, clips, cards) covering the voiceover exactly, optional
   music bed ducked under the voice, optional burned subtitles -> 1080p H.264/AAC mp4. CPU
   render is a heavy job (approval card). The video gate runs on every render.
R3 **Animation worker** (CPU): shots JSON anchored to script phrases and voiceover timings; the
   worker draws each shot as SVG (stickvid style: full-bleed scenes, close-up narrator, labels),
   rasterised with the installed Chrome headless, moved with ffmpeg; output feeds the edit list.
R4 **Packaging worker**: title options, description, tags, chapters from section timings,
   thumbnail drawn as SVG -> PNG; bundle `episode/<name>/`; package gate.
R5 **Episode flow + UI**: Episode panel runs stages script -> voice -> visuals -> edit ->
   package. Each stage = Sonnet worker, Opus review (2 rounds, budgets as P9) and its numeric
   gate; a failed gate blocks the next stage and says why. Resolve stays an optional editor.

## Gates (AUTO, from PLAN.txt)

Seeded bad take rejected; 6-semitone pitch shift fails the voice gate; final video duration =
VO +/- 0.5 s, A/V drift < 40 ms, no black frames. Plus per step: tests with tiny synthetic media
made by ffmpeg (lavfi), never the owner's files or accounts.

## MANUAL

Watch one full episode end to end - would you publish it?
