# P7 manual check — Setup wizard + modules

Two parts. Part A takes 10 minutes in your own Windows account. Part B is the real
"brand-new PC" test — it downloads about 9 GB, so do it when you have time (or on a friend's PC).

## Part A — in your own account (nothing gets downloaded)

Close any old black One Production window, then run `start.bat`. Your setup is never interrupted
by the wizard (you already have projects); open it from **System → Set up / modules**.

- [ ] **1. Modules.** Core shows ALWAYS ON. Voice shows READY — "your own voice app". The five
  Phase 2 modules are greyed with what they will need.
- [ ] **2. Install says nothing to do.** Install step: "Everything needed is already on this PC".
- [ ] **3. Your PC.** The scan matches your PC (RTX 5060 Ti, ffmpeg, Claude Code, Codex, C: low on space).
- [ ] **4. Tune your voice (about 3 minutes on the GPU).** Your voice step → pick `human_ref48.wav` (or
  Buster) → **Find best settings** → approve the GPU card. It shows progress and then the best
  expressiveness / guidance / temperature. Make a short voiceover with that voice afterwards:
  it uses those settings by itself (the fine-tune boxes in the voice dialog stay empty).
- [ ] **5. Finish later / reopen.** "Finish later" closes it; System → Set up / modules reopens it.

## Part B — a brand-new PC (real test of everything a friend sees)

Make a second Windows user (Windows Settings → Accounts → Other users → Add account — this is a
Windows change, so it's yours to make), sign in as that user, and start One Production from this
folder (`D:\Projects\YT project\one-production\start.bat`).

- [ ] **6. The wizard opens by itself** and shows your drives with their free space. Pick **D:**.
  One Production restarts there and opens a new tab by itself (close the old one).
- [ ] **7. Every screen is understandable without asking me.** Note anything that confused you.
- [ ] **8. The real install.** Install step lists 5 items (~9 GB: ffmpeg 115 MB, Python, PyTorch
  CUDA 12.8 ~3.4 GB, voice libraries ~650 MB, models ~4.8 GB), each needs its own tick.
  Try **Cancel** in the middle of PyTorch, then **Install** again — it continues, not restarts.
  At the end: "Voice module installed and checked" (that check runs a real calculation on the GPU).
- [ ] **9. Account.** Add one account (an API key is enough — without Claude Code it works as plain
  chat; the screen says so).
- [ ] **10. Your voice + test run.** Add a 10–30 s clip, tick the permission box, then the Test run:
  a reply to the test message ✓, and a 15-word voice test that plays ✓. Press **Finish**.

## Good to know

- Everything the wizard installs goes into the data folder (`modules\`), never into Windows itself.
  Deleting the second Windows user removes it all.
- Moving the data folder is only offered on a new install; with existing projects the screen
  explains how to move it by hand.
- Claude Code and Codex aren't installed by the wizard — it says whether they're there and where
  to get them. Plain chat with an API key works without either.

## Sign-off

Part A ticked → you can say **"P7 approved"** now and do Part B before giving the app to friends
(P8 packages it). Anything odd → tell me the number and what you saw.
