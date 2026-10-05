# P10 manual check — production engine (one full episode)

Close any old black One Production window, then run `start.bat`. You need: a finished script in
the project (from Script Studio or your own, with `##` headings), the voice module, and one
signed-in Claude / Codex account. A run uses your GPU for the voiceover (unless you reuse one),
your processor for the render, and real AI usage for the visuals and the package (the drawings
are the big part: roughly one SVG per 3 seconds of video).

- [ ] **1. Start.** Files sidebar → **Make an episode**. Pick the script, a voice (or "Use …" an
  existing voiceover), optionally a music bed and directions for the look. The dialog lists the
  heavy work; tick **OK for this episode** → **Start the episode**.
- [ ] **2. Watch the stages.** The chat shows Script → Voiceover → Visuals → Edit → Package, each
  with its status, the worker's account and review rounds, and a **gate report** link. A stage
  that fails its measured check says why with the numbers (e.g. "loudness -15.2 LUFS").
- [ ] **3. The visuals.** Open `work/<name>/frames/` — do the drawings fit what is said, and do
  they change often enough? Open `work/<name>/shots.json` to see the anchor words.
- [ ] **4. Watch the whole video** (`work/<name>/<name>.mp4`) end to end: picture in sync with the
  voice, no black frames, the shots change on the right words. **Would you publish it?**
- [ ] **5. The bundle.** `episode/<name>/`: video, subtitles, thumbnail.png, description.txt with
  chapters, title-options.txt, metadata.json, package-gate.json. Are the titles honest and the
  thumbnail readable small?
- [ ] **6. Cost.** Usage → "By role" (Worker, Master). Acceptable for one episode?

Covered by the automatic tests only (stand-in AI turns, synthetic voice): a seeded bad take
stopping the run at the voice gate; a 6-semitone pitch shift failing the voice gate; loudness,
true peak and subtitle checks; video = voiceover +/- 0.5 s, A/V drift < 40 ms, no black frames;
a failed check going back to the worker with its numbers; capped retries and review rounds;
YouTube title/description/tag/chapter/thumbnail limits.

Not built yet: stock footage (Storyblocks / free sources) and research workers; Resolve stays an
optional editor through the Resolve plans panel.

## Sign-off

All boxes ticked → write **"P10 production approved"**. Anything odd → the number and what you saw.
