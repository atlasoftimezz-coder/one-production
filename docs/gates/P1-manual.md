# P1 manual check — Chat screen, files, security

Tick each box. If one fails, write what you saw next to it and tell me.
Start the app with `start.bat` as before.

- [ ] **1. The new screen opens.** After `start.bat`, the tab shows three columns:
  projects (left), chat (middle), files (right). The address bar shows just
  `http://127.0.0.1:8730/` — **no `#t=...` at the end** (the app removed it).

- [ ] **2. Make a project.** Type a name in "New project" (bottom left), click Add.
  It appears in the list and is selected.

- [ ] **3. Chat streams.** Type a message and press Enter. The reply appears word by
  word (it's a test reply for now — "no AI account connected yet"). Real AI replies
  arrive in P2.

- [ ] **4. Upload any files.** Drag 3 different files onto "Drop files here"
  (e.g. a .docx, an .mp3, a .png — any you like). All 3 appear with their sizes.

- [ ] **5. Download them back.** Click Download on each. Open the downloaded copies —
  they open normally and look identical to the originals.

- [ ] **6. Same name twice.** Upload the same file again. You get a second copy named
  `name (1).ext` — the first one is not overwritten.

- [ ] **7. History is kept.** Close the black window, run `start.bat` again, click your
  project. Your earlier messages and files are still there.

- [ ] **8. The lock works.** With the app running, press **F5** to reload the tab.
  You now see "This page needs to be opened from start.bat". That is correct — only
  the tab the launcher opens can use the app, so no other web page can.
  Close the black window and run `start.bat` again to get back in.

- [ ] **9. Looks usable.** On your screen, nothing overlaps or is cut off.

- [ ] **10. Automatic check is green.** `docs\gates\P1-auto.md` ends with
  **RESULT: PASS** (63 tests, including all of P0 again).

## Sign-off

All ticked → tell me **"P1 approved"**. Anything odd → tell me which number and what you saw.
