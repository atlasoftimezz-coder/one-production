# P10 manual check — knowledge, learning and Resolve

Close any old black One Production window, then run `start.bat`. Steps 1–4 cost nothing.
Step 5 uses a real AI account. Steps 6–8 touch your real DaVinci Resolve Studio, but only to
make a NEW project in a "One Production (disposable)" folder. Save your open Resolve project first.

- [ ] **1. Import the manual.** Files sidebar → **Knowledge and learning** → set Channel and
  Resolve version (21.0) → import `DaVinci_Resolve_21.1_Reference_Manual.pdf` with Application
  `resolve`, Version `21.1`. It takes a few minutes (see the measured time in HANDOFF.md). Note: a
  21.1 manual is hidden from searches while the version says 21.0 — that is on purpose; set the
  manual's version to 21.0 if you want it searchable anyway.
- [ ] **2. Search.** Try "ripple trim" or "add marker". Does "Open cited source" open the right page?
- [ ] **3. Directions.** Save a direction, correct it, forget it. Only the newest one shows.
- [ ] **4. Resolve off by default.** Files sidebar → **Resolve plans**: the switch is off and
  **Check Resolve** says control is off.
- [ ] **5. A worker's plan.** In the chat ask for a Resolve rough cut of one audio + one video file
  in the project. The worker should call resolve_status, cite manual pages and propose a plan; it
  must say the plan waits for you, not that it ran. The plan appears under **Resolve plans**.
- [ ] **6. Check Resolve.** Start Resolve Studio, set Preferences → System → General → External
  scripting using → **Local**, turn the switch on, press **Check Resolve**: it shows the version and
  your open project's name.
- [ ] **7. Run it.** Save your open Resolve project, tick the box, press **Run on a new Resolve
  project**. In Resolve: a new project in "One Production (disposable)" with the timeline, clips
  and markers; your own project unchanged. The panel shows "Passed" and the checks.
- [ ] **8. Learned.** **Knowledge and learning** lists a verified method (kind "recipe", Resolve
  version, evidence with the run). Delete the disposable project in Resolve when done if you like.

Covered by the automatic tests only (stand-in Resolve): unsupported operations (render, scripts),
paths outside the project, version too old / plan needs a newer version, Resolve updated between
review and run, media changed after review, a failed Resolve call (no recipe), Resolve not answering,
name clashes, the run being refused until you confirm.

## Sign-off

All boxes ticked → write **"P10 knowledge approved"**. Anything odd → the number and what you saw.
