# P8 manual check — Package, updates, support

The package is ready: `dist\OneProduction-0.8.0-win64.zip` (28 MB).

## Quick look in your own copy (5 minutes)

Close any old black One Production window, then run `start.bat`.

- [ ] **1. Guide.** The **?** button in the top bar opens the guide — readable without asking me?
- [ ] **2. Sample.** On the start page (click the One Production name, or have no project open):
  **Try the sample** opens "Getting started" with a short script, ready to voice.
- [ ] **3. Diagnostics.** System → **Export diagnostics** saves a zip. Open it: scan, versions, recent
  errors, the log — no chat text, no keys (they show as `[REDACTED:…]`), emails shortened.
- [ ] **4. Updates panel.** System shows "This is a development copy" — correct for your git folder.

## The real test (the plan's P8 check) — one friend, their PC, from zero

- [ ] **5. A friend installs it.** Send them the zip (any way you like). They extract it anywhere,
  double-click **One Production.cmd**, go through the setup wizard (it downloads ~9 GB of voice
  pieces if they want the voice module and have an NVIDIA card), and **voice the sample project**.
- [ ] **6. Diagnostics are enough.** If anything goes wrong, they press System → Export diagnostics
  and send you the zip; give it to me and we find the problem from it.

## Updates — when you want to send a new version

1. I change things, bump the version, add the notes to `docs\CHANGELOG.md`.
2. `.venv\Scripts\python tools\build_package.py`, then
   `.venv\Scripts\python tools\publish_release.py --to "<a folder only you can write to>"`.
3. The friend sets that folder (a shared drive) or its web address once in System → Updates, then
   **Check for updates → Download → Restart into …**. **Roll back** returns to the previous version.

Where to host the release folder is your choice (shared drive, Google Drive folder, GitHub
release, a web space) — tell me which, and I'll set the address into the next build so friends
don't have to type it.

## Sign-off

Items 1–4 → you can say **"P8 approved"**; 5–6 happen when a friend tries it (that's the
"friends release" moment). Anything odd → tell me the number and what you saw.
