# P10 manual check: one full episode (Fusion, since F4)

**Before you start:**

- Close any old black One Production window, then run `start.bat`.
- You need:
  - a finished script in the project, from Script Studio or your own, with `##` headings;
  - the voice module;
  - **DaVinci Resolve Studio 21.1 running**;
  - one signed-in **Claude** account. Codex can't drive Resolve, so it can't do the visuals.
- In **Resolve plans**, both switches must be on:
  - "Let One Production control DaVinci Resolve Studio";
  - "Let the AI use DaVinci Resolve's own MCP".
- A run uses:
  - your GPU for the voiceover, unless you reuse one;
  - Resolve for the visuals, with one approval card per shot script;
  - real AI usage for the art direction, the shot scripts and the package.

## Steps

- [ ] **1. Start.** Files sidebar → **Make an episode**.
  - Pick the script, and a voice (or "Use …" an existing voiceover).
  - Optionally add a music bed and directions for the look. Left empty, the AI designs a look
    for this script.
  - Tick **OK for this episode**, then **Start the episode**.
- [ ] **2. Art direction.** When the Visuals stage runs, open `work/<name>/art.json`. Does the
  concept fit this script and make it easier to follow? It should not be a template you've seen
  before.
- [ ] **3. Resolve.** A new project "<name> - One Production …" appears in the Resolve folder
  "One Production episodes".
  - It has 60 fps, the voiceover on A1, and one Fusion composition per shot, cut where its words
    are spoken.
  - Your own projects are not touched.
- [ ] **4. The shot cards.** Each shot comes as an approval card showing the AI's code. The card
  reads "+ One Production Fusion kit v1"; the kit is added only when you approve.
  - **Approve once** for each shot.
  - "Allow for this chat" approves all the remaining ones.
- [ ] **5. The stills.** Open `work/<name>/stills/`. Check:
  - is every shot clear;
  - does every shot follow the art direction;
  - is nothing overlapping or cut off;
  - would you put it in a video?
- [ ] **6. Watch it in Resolve.** Play the "Episode" timeline. Check motion at 60 fps, that shots
  change on the right words, and that it is readable on a phone.
- [ ] **7. Render.** Deliver page: H.264, 1920x1080, 60 fps. Render **into this project's
  folder**, then in the episode panel pick the file and press **Check and package**. The video
  gate checks:
  - the length matches the voiceover;
  - picture and sound are in sync;
  - there are no black frames.
- [ ] **8. The bundle.** `episode/<name>/` holds the video, subtitles, thumbnail.png, description.txt
  with chapters, title-options.txt, metadata.json and package-gate.json.
  - Are the titles honest?
  - Is the thumbnail readable small?
- [ ] **9. Cost.** Usage → "By role" (Worker, Master). Is this acceptable for one episode?

**Covered by the automatic tests only** (stand-in AI turns, stand-in Resolve answers, synthetic
voice):

- **Voice gate:**
  - a seeded bad take stops the run at the voice gate;
  - a 6-semitone pitch shift fails the voice gate.
- **Resolve setup:**
  - shot timing from the spoken words, with no gaps or overlaps;
  - the 60 fps Resolve project build, with exact cuts and the style and brief in every comp.
- **Visuals check:**
  - an unbuilt shot is sent back to the worker;
  - the visuals worker is always a Claude account;
  - the kit is added only on approval.
- **Waiting for the render:**
  - the run waits for your render and resumes after an app restart;
  - a too-short render is stopped by the video gate.
- **Limits:**
  - capped retries and review rounds;
  - YouTube title, description, tag, chapter and thumbnail limits.

**Not built yet:** Storyblocks (no key) and research workers. Pexels stock works.

## Draft episode (2026-10-11): competitor style, no stops

Do this once with a 30-second script first, then with a 3-minute one.

- [ ] **D1. Learn a style.** Top bar → **Competitors** → paste one competitor video → Watch and
  learn. When it finishes, open "See what it learned". Do the contact sheets show that video, and
  does the profile describe how it is edited? Press **Use as guidance**.
- [ ] **D2. Keys (optional).** Set up / modules: Pexels key and Epidemic Sound key. Read the
  Epidemic licence note; your plan must cover YouTube.
- [ ] **D3. Start a draft.** Make an episode → "Make a draft with no stops" stays ticked → tick
  the style → every required line in the list shows ✓ → OK → Start.
- [ ] **D4. No cards for shots.** While the visuals run, no approval card should appear for
  `begin("shot-…")` scripts. Each script that ran is in `work/<name>/scripts/`.
- [ ] **D5. The render.** Resolve's Deliver page renders by itself into `work/<name>/draft-….mp4`.
  Your other Resolve projects are untouched.
- [ ] **D6. Watch the draft.** Is the pacing close to the competitor? Is the footage under the
  graphics where you expected it? Are the sound effects on the cuts at a sensible level (A3 in
  Resolve)?
- [ ] **D7. Review report.** The chat shows the stages, amber "check" items and a report. Do the
  flagged points match what you saw?

## Sign-off

All boxes ticked → write **"P10 production approved"**. Anything odd → the number and what you saw.
