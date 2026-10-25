# First test on your friend’s PC

Use this order: **install → one voice → both voices → a short final → passage repair → agents → a longer script**.

This is the first real RTX 5070/voice-fidelity validation of these changes. Automated tests use synthetic audio and simulated providers. They do not prove that your recordings sound correct on the real model.

## 1. Get the code and launch

Use a GitHub account with access to this repository. Download **Code → Download ZIP** from `main` and extract it, or clone it:

```powershell
git clone https://github.com/atlasoftimezz-coder/one-production.git
cd one-production
```

If the repository is already on that PC, close One Production and its Chatterbox engine, then run `git pull --ff-only` in its folder. Preserve any local edits first. Use the updated `voice/` engine from this checkout rather than an older external voice app.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed:

```powershell
winget install --id astral-sh.uv -e
```

Open a new PowerShell window **inside the extracted/cloned repository folder**, then:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_dev.ps1
.\start.bat
```

The setup script creates the core Python environment. It does not install Chatterbox into that core environment. The launcher opens the authenticated app in your browser, normally at `http://127.0.0.1:8730/`. Use that launched page and choose **Voiceovers**; opening a bare Voiceovers URL in a different session can show a session warning.

## 2. Install the voice module on the RTX 5070

1. Complete the app’s setup wizard. Choose a data drive with room for the downloaded models, Python libraries and generated audio.
2. Open **System → Modules → Voice**, choose the graphics-card mode, and install the requested voice components. The installer shows download sizes and asks for each component.
3. The repository’s RTX 50-series recipe selects **PyTorch 2.8.0 with CUDA 12.8**. This is a supported [PyTorch build](https://pytorch.org/get-started/previous-versions/). Let the app use its isolated voice environment; do not replace it with a random global torch installation.
4. Wait for the module’s final verification to pass. The installer performs an actual GPU operation, rather than relying only on a “CUDA available” flag.
5. In **Voiceovers → Connections**, choose **Chatterbox → Connect / start engine**.

For the first trial, close other GPU-heavy applications. If GPU detection/install fails, run `nvidia-smi` in PowerShell and check that it identifies the RTX 5070 and an installed NVIDIA driver. Correct a driver/install failure before testing voice quality. First model loading can take longer than subsequent generations.

**Pass:** the voice module’s final check passes and Chatterbox connects. A connection alone is not proof that generation works; continue below.

## 3. Test one clean clone, then the second

Create a project named **Voiceovers PC Test**.

1. In **Voices & references**, save Peter’s clean single-speaker recording as **Peter**. Confirm permission to clone it. Use your roughly one-minute recording.
2. Choose **Chatterbox**, expand **Clone & check voice match**, select **1 take**, approve and start.
3. Wait for the result. Expand the saved comparison and choose **Load reference & test audio**. Play both recordings.
4. Repeat for **Miles**, using his clean sample. Add the processed radio recording to Miles’s separate radio/effect reference field afterward.

**Pass:** both samples and test takes play; each clone sounds recognizably like its own source; the comparison has a real score or clearly explains why scoring is unavailable. A score is not a percentage match or proof of natural delivery. “Scorer unavailable” means the automatic comparison path has not passed this test.

Once that works, run **Improve the match automatically** with a limit of five takes. Check that it stops for progress/target/limit conditions, keeps completed takes and retains its best eligible settings. Leave the optional identity target unset until calibrated against your examples; do not enter an arbitrary “perfect” value.

## 4. Generate both speakers from one script

Open **Create voiceover**. Set **Speakers → 2**, choose Peter’s and Miles’s saved voices, and use Chatterbox for both. Keep **Peter → Clean** and **Miles → your Resolve distortion preset**. Leave the production pipeline defaults on.

Paste this script, or upload/select `docs/examples/voiceovers-smoke-test.txt` from the repository:

```text
Miles: Peter, can you hear me?
Peter: Loud and clear. This is a two speaker voiceover test.
Miles: My voice should have the radio effect.
Peter: Mine should stay clean. <#0.5#> This sentence follows a half-second pause.
Miles: The score is twenty-one to seven. Let's check the pacing.
Peter: Both voices should finish in the order written.
```

1. Set episode number **1**.
2. First leave **Short preview** on. Click **Review**, approve and generate. It generates only the first two sections, up to 40 words each.
3. Listen: Miles must speak first, Peter second. Routing labels must not be spoken. “Peter” within Miles’s actual sentence should be spoken normally.
4. Turn **Short preview off**, Review again, approve and generate the complete conversation.

**Pass:** all six turns appear in order; Peter and Miles have distinct assigned voices; only Miles has the selected radio processing; words are not cut off; the deliberate half-second pause remains; other pauses sound consistent. Compare the Miles effect with your processed recording. The portable effect approximates your Resolve settings and may need adjustment after listening.

## 5. Check the final files and repair

In **History**, select the full take and listen from beginning to end. Download:

- `#1 VO Final.wav` for the editor.
- `#1 VO Final.mp3` as a listening copy.
- The delivery report and subtitles for inspection.
- Preprod and speaker stems if useful for comparison/editing.

The measured WAV target is **−14 LUFS integrated, within ±0.5**, with **true peak no higher than −1 dBTP**. Check the MP3 measurements too. Estimated subtitle timing is a review note, not verified alignment. Speaker stems are before final mix mastering and need not sum to the mastered final.

Expand **Review & repair a passage**, select just one section, record an issue and approve regeneration. A new take should appear while the old one remains. Only the selected section should receive new synthesis; the mix is then rebuilt and mastered. Recheck the result’s words and levels.

**Pass:** the full WAV is playable and correctly named; delivery measurements pass or clearly flag a miss; the earlier take remains after repair. Restart the app and confirm completed voices/jobs/files remain available.

## 6. Test Claude and Codex

Add/sign in to your chosen assistant through the app’s **Accounts** page. Test one assistant at a time in the same project.

For cloning, ask:

> Use my saved Peter and Miles voices. Clone and compare them with Chatterbox, improving the match automatically with up to five test takes each. Reuse existing clones. Keep the best eligible settings and report why each search stopped.

For the full dialogue, set up the script/voices, turn preview off and click **Review → Continue in Claude / Codex**. Send the prepared request from studio chat.

**Pass:** the correct project and reviewed script are used; the assistant asks for generation approval; it reports actual job progress/output and review flags; the completed take appears in Voiceovers History. Repeat with the other assistant if you want to validate both integrations. The handoff button only prepares a chat request; it does not send it automatically.

## 7. Expand the test only after the short flow works

- Try a normal Shorts script, then a 2–5 minute conversation, then a longer script.
- Check voice consistency toward the end, names/numbers, silence edits, effects, measured levels and subtitles.
- Test ElevenLabs or MiniMax one at a time after saving that provider’s key. Clone/test generation uses credits, and provider verification can require your action. Chatterbox’s local scorer is still needed for automatic similarity measurements of cloud-generated takes.
- Add your 20 delivery examples after establishing this baseline, keeping their scripts and expectation notes alongside them.

## If something fails, collect this

- Which step failed and the exact error text.
- The app’s **System** information, GPU/driver information from `nvidia-smi`, and selected provider/model.
- The failed job’s status and delivery report when available.
- For an audio defect: the reference/test audio, script passage and timestamp of the issue.
- For startup failures: the terminal output. App logs are under the chosen data folder’s `logs` directory; the default data folder is `%LOCALAPPDATA%\OneProduction`.

Report what actually happened rather than repeatedly starting more jobs. Automatic identity comparison and bounded repair are implemented; autonomous listening judgment of pronunciation, emotion and artifacts against your examples is still pending. A completed generation is not production approval.
