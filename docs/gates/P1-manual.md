# P1 manual check — Chat screen, files, security

Tick each box. If one fails, write what you saw next to it and tell me.
**Close any old black One Production window first**, then start with `start.bat`
(the old window is still running the previous version of the page).

- [ ] **1. The new screen opens.** You see the welcome screen: "Make your next video with
  One Production", three cards, projects on the left, files on the right. The address bar
  shows just `http://127.0.0.1:8730/` — no `#t=...` at the end.

- [ ] **2. Make a project.** Click **New project** (gradient button, top left), type a name,
  press Enter. It appears in the list with a coloured badge and is selected; the name shows
  at the top next to "One Production". (An empty name is refused.)

- [ ] **3. Chat streams.** Click a suggestion chip or type a message and press Enter.
  Three dots appear, then the reply appears word by word (a test reply for now — real AI
  replies arrive in P2).

- [ ] **4. Upload any files.** Drag 3 different files onto "Drop files or click" (or use the
  paperclip in the message box). A progress bar runs, then all 3 appear with coloured type
  badges and sizes.

- [ ] **5. Download them back.** Hover a file and click the ↓ arrow. Open the downloaded
  copy — it opens normally and is identical to the original.

- [ ] **6. Same name twice.** Upload the same file again → a second copy named
  `name (1).ext` appears; the first is not overwritten.

- [ ] **7. History is kept.** Close the black window, run `start.bat` again, click your
  project. Messages and files are still there.

- [ ] **8. The lock works.** Press **F5**. You see "Open One Production from start.bat".
  That is correct — only the tab the launcher opens can use the app. Run `start.bat`
  again to get back in.

- [ ] **9. Looks right.** No odd characters (like `Â`), nothing overlapping or cut off,
  in your normal Windows theme.

- [ ] **10. Automatic check is green.** `docs\gates\P1-auto.md` ends with
  **RESULT: PASS** — 67 tests, including all of P0 and a real-browser run of
  everything above.

## Sign-off

All ticked → tell me **"P1 approved"**. Anything odd → tell me which number and what you saw.
