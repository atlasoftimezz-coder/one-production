# Voiceovers workspace

Open `start.bat`, then **Voiceovers** in the studio’s top bar. Each coworker runs their own installation and Chatterbox engine. This page does not install models or start remote workers.

For a shorter introduction, read [Voiceovers: start here](VOICEOVERS_USER_GUIDE.md), or use **Page guide** in the app. Each page has its own guide, with separate topics for Settings, History, assistant handoff and Review & repair.

## Your normal workflow

1. In **Voices & references**, save **Peter** with his clean single-speaker recording and **Miles** with his clean recording. One-minute samples are accepted. Confirm your permission to clone them.
2. Add the processed/radio recording to **Miles’s existing card**. It remains separate from the clean identity recording. The comparison provides an approximate bandwidth profile, not proof of the original plugin chain.
3. Prepare each clean voice for Chatterbox, ElevenLabs or MiniMax. Chatterbox uses up to 30 seconds of the clean sample; cloud preparation uses up to 120 seconds. The original upload is retained. Provider cloning may require an appropriate plan or verification. API keys are saved on that coworker’s PC in Windows Credential Manager.
4. In **Create voiceover**, select **2 speakers** at the top right. Choose Peter/Miles in the Settings panel and pick their providers. Their models and delivery settings are independent.
   If you already have Peter/Miles clones in ElevenLabs or MiniMax, choose **Use a provider voice ID** and **Load** to select the existing clone. A new cloud clone is unnecessary.
5. Paste the **complete dialogue** once:

   ```text
   Peter: Do you hear the difference?
   Miles: I do. My voice has the radio processing.
   Peter: Both of us are generated and assembled in order.
   ```

   `A:` / `B:` or `[Peter]` / `[Miles]` labels work too. Labels are stripped before provider synthesis; either speaker may speak first. Names within the dialogue remain ordinary spoken words. For one narrator, choose **1 speaker** and paste ordinary prose. Both Shorts and long-form scripts are supported; longer text is split into bounded provider requests.
6. Set an episode number, such as `19`. Choose a short preview first or untick **Short preview** for the whole script. **Review** shows the approximate duration and number of synthesis requests. Approve that request, then generate.
7. The app generates the turns in order, applies each speaker’s effect, saves a pre-production mix, trims dead air, makes internal pauses and speaker gaps consistent, assembles the conversation and masters the final mix. It measures both WAV and MP3 levels.
8. Listen in the bottom player. **History** on the right lets you inspect a take, restore its original text, restore its settings separately, and download its outputs.

## Miles’s saved Resolve reference

The user supplied these values in the Resolve Distortion inspector:

| Parameter | Value |
|---|---|
| Low cut | 666 Hz |
| High cut | 1.65 kHz |
| Mode | Distort |
| Level | 68.2 |
| Ceiling | −4.7 dB |
| Dry/wet | 100% |
| Output level | +5.4 dB |
| Auto level | On |

Choose **Miles · your Resolve distortion preset** for Miles. Peter stays **Clean** by default. These parameters are preserved in the delivery report. The portable implementation uses filtering, driven oversampled soft clipping and level control. Resolve’s proprietary distortion curve is not reproduced bit for bit; the first production setup needs an A/B listen against the user’s actual clean and processed Miles samples. No live Resolve project is changed by the Voiceovers workflow.

The generic/learned **Radio** option is separate from this explicit Miles preset. Voice identity always comes from the clean sample or selected provider voice, not from the radio reference.

## Pauses, pacing and provider settings

- Default production settings: quiet internal gaps become **180 ms**, speaker gaps **250 ms**, with a **−42 dBFS** silence threshold and a minimum detected quiet interval of 120 ms. Leading/trailing dead air is removed per section. These are adjustable production settings, not a claim to reproduce Resolve’s Ripple Delete Silence implementation.
- Explicit pauses such as `<#0.5#>` are assembled as 500 ms gaps between generated sections. They are removed from the text sent to TTS and survive the silence edit.
- Provider speed is the normal control. **Advanced pacing → match a target WPM** can override it through bounded time stretching. Unreachable targets and review concerns are reported.
- ElevenLabs exposes speed, stability, similarity and style. MiniMax exposes speed, pitch and volume. Model IDs are saved with the take and restored from history. `(laughs)` is a MiniMax-specific editor shortcut; other engines may pronounce such text literally.
- **Reference analysis** uses ElevenLabs Scribe after one-request approval. It estimates who speaks when, per-speaker WPM, pauses/turn gaps, and possible radio processing. Names/effect suggestions need review. This stores reusable delivery context; it does not train model weights.
- Competitor uploads are delivery references. The named clean voice library and clone actions are for permitted channel samples.

## Files for your editor

For episode 19, a full run produces:

- `#19 VO Final.wav` — mono 48 kHz final voiceover.
- `#19 VO Final.mp3` — 192 kbps listening copy.
- `#19 VO Final.srt` — subtitles.
- `#19 VO Final-delivery.json` — script, speaker/model settings, timeline, effect reference, measured levels and review notes.
- `#19 VO Preprod.wav` — assembled processed turns before the silence edit and final mastering.
- Optional Peter/Miles `-stem.wav` files — separate, aligned speaker tracks after processing/silence edits, **before final mix mastering**. They are editing aids and do not necessarily sum to the mastered final.

Existing filenames are preserved; another take gets a numbered filename suffix. Preview filenames use `VO Preview`. Generated output is also registered in the existing **Make an episode** voiceover picker.

The delivery target is **−14 LUFS integrated (±0.5)** and **true peak no higher than −1 dBTP**. Mastering leaves a small peak margin for encoding. WAV and MP3 are both measured; misses appear in review notes. Do not interpret a completed generation as an independent pronunciation/read-check pass.

ElevenLabs character timings are used when supplied. Otherwise, a configured local Chatterbox runtime can align the combined result with its speech recognizer. If neither succeeds, subtitles are explicitly marked estimated; no extra paid transcription is started automatically.

## Claude and Codex

Both adapters expose the same tools:

`voiceover_studio_status`, `voiceover_provider_voices`, `voiceover_reference_analyze`, `voiceover_save_voice`, `voiceover_prepare_voice`, `voiceover_clone_and_check`, `voiceover_studio_plan`, `voiceover_studio_generate`, `voiceover_studio_job`, `voiceover_studio_repair`.

For example: “Use Peter and Miles in Voiceovers for episode 19. Keep Peter clean and use Miles’s saved Resolve preset. Make a short preview, then the complete dialogue after I approve it.”

Status/planning do not generate audio. Analysis, cloning/preparation and generation require fresh approval; session or old “always allow” permissions cannot silently authorize new paid requests. Failed cloud calls are not automatically retried. Stopping a job does not reverse charges for requests already completed by a provider.

The page’s **Review** action saves the last reviewed script/settings in the project. Both assistants can retrieve that plan through `voiceover_studio_status`; unsaved typing in a browser is not automatically exposed to them.

**Continue in Claude / Codex** returns to the same project and prefills a request without sending it. Agents can call plan/generate with only `use_reviewed_plan=true`, preserving the reviewed text snapshot, providers, effects, pipeline and preview/full mode. This flag rejects simultaneous overrides. Generation still requires fresh approval.

## Passage repair and the listening reviewer

Completed takes expose numbered sections with speaker, spoken text, timing and available Chatterbox transcript checks. In History, **Review & repair a passage** lets the user select sections and approve a new take. The equivalent agent tool is `voiceover_studio_repair(job_id, sections, reason)`, with 1-based section numbers, up to 20 per request. The API is `POST /api/projects/{pid}/voice-studio/jobs/{jid}/repair`.

Repair reuses unselected raw provider audio and raw alignment, resynthesizes selected sections, then reruns effects, silence shaping, assembly, subtitles and measured mastering. The original job and exports remain unchanged. Each new take records its parent job, repaired sections and feedback. Chatterbox repair uses a fresh initial seed; its existing bounded ASR retry loop remains active. Cloud providers receive only selected synthesis requests. Missing reuse assets fail before any provider request.

The feedback note is provenance, not a pronunciation/acting prompt. Repair is another take with the same script and voice settings. `review_status=needs_listening_review` is retained. Chatterbox transcript checks help detect misreads; neither those checks nor mastering establish natural pronunciation, clean artifacts or the intended performance. No audio-capable listening evaluator is connected. The owner's requested autonomous review/repair loop against 20 annotated examples remains a required next implementation, including bounded attempts, evidence per issue, and listening approval for uncertain passages. Do not advertise current output as AI-listened or production-approved.

The 20-example intake is described in the short user guide: finished audio, exact labeled script, ownership/reference role, expected delivery notes and useful timestamps. Keep permitted clean identity samples separate from processed and competitor delivery references. Validate real voice fidelity and the Miles effect before accepting a production baseline.

## Clone identity comparison

`voiceover_clone_and_check(channel_voice_id, provider="chatterbox", test_text="", attempts=5, auto_retry=true, target_similarity=null)` is shared by Claude and Codex and requires fresh approval. The same workflow is available on each saved voice card and at `POST /api/projects/{pid}/voice-studio/voices/{vid}/clone-check`. It reuses an existing provider clone; otherwise it prepares the clean source with the selected provider. It honors provider verification and never substitutes another provider. Approved comparison takes are bounded to 1–10. An ordinary project upload can first be saved with `voiceover_save_voice(file, name, consent)`; source roles and project paths are checked, and competitor/paired processed files are rejected even through path aliases.

The workflow synthesizes clean test speech, retains each completed WAV, and compares it against up to 60 seconds of the original clean recording through the coworker’s local voice module. `voice/server.py` exposes a bounded-upload `/api/compare-voice`; `voice/similarity.py` uses the resident trained Chatterbox encoder or strictly loads already-cached `ve.safetensors` / older `ve.pt` weights. `local_files_only=True` prevents downloads; an untrained default encoder is never scored. Multiple reference windows provide identity evidence. This follows Chatterbox’s [trained encoder loading](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/tts.py) and [speaker embedding/cosine methods](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/models/voice_encoder/voice_encoder.py).

Reports preserve all comparison takes, cosine scores, native Chatterbox transcript checks when available, relative recommendation and limitations. They always retain `review_status=needs_listening_review`, `automatically_accepted=false` and `acceptance_threshold=null`. Cosine is not a percent match, no universal acceptance cutoff is assumed, and using Chatterbox’s own encoder is not an independent listening evaluation. If scoring is unavailable, retain the first test audio for A/B listening and stop requesting more takes. Completed previews remain available even if a later request fails. Never apply Miles’s radio effect to an identity check.

Automatic refinement searches supported Chatterbox exaggeration/CFG/temperature settings with fresh seeds and ElevenLabs similarity/stability settings; MiniMax retries use its existing identity and supported native settings, without an invented identity-fidelity control. The first candidate reuses previously selected settings if present. It retains the highest-scoring eligible candidate, excluding known Chatterbox transcript failures. After two trials without at least 0.005 cosine progress from the last meaningful improvement, it stops with `no_improvement`; this is a search heuristic, not an acceptance threshold. Other stop reasons are `identity_target_reached`, `attempt_limit` and `scorer_unavailable`. A supplied target must come from user-approved calibration; no target is invented. Target attainment never sets production acceptance. Agents must report the stop reason rather than chaining fresh jobs to bypass the approved cap.

The best eligible take’s `generation_settings` and provisional provenance are saved on its provider binding. Production planning loads these by default with `use_clone_settings=true`, and the UI displays them. Custom native controls disable that default; Chatterbox’s hidden voice-specific controls remain inherited. Reviewed-plan snapshots retain their original parameters even if a later comparison changes the library defaults. Each report records settings, scores, target, search limits and recommendation. No source quality or provider identity can be made perfect merely by unlimited regeneration.

The older `voice/tune.py` also now reuses `model.ve`: its former bare `VoiceEncoder()` had not loaded trained weights and could not support meaningful fidelity ranking. Update/restart the coworker’s voice module to receive the comparison endpoint and this correction. No real models or provider accounts were exercised during this implementation; actual clone fidelity must be validated with the owner’s samples.

## Implementation and validation

- Service: `app/voice_studio.py`; voice identities: `app/voice_studio_library.py`; clone/check orchestration: `app/voice_clone_check.py`; DSP/timing: `app/voice_studio_audio.py`; local speaker evidence: `voice/similarity.py`.
- HTTP routes: `app/voice_studio_api.py`; agent tools: `app/agent/voiceover_tools.py`; page: `web/voiceovers.*`.
- State is project scoped under `voice-studio/`; audio exports are ordinary project files. There is no cross-PC queue or synchronization. Give each coworker the samples and configure each installation independently.
- Tests use synthetic audio and stand-ins for Chatterbox/cloud services. Real model fidelity, the user’s samples, provider accounts and the Resolve A/B comparison remain to be validated.
- Run focused tests with `.venv\Scripts\python.exe -m pytest tests/p12/test_voice_studio.py`.
  Run the disposable Chrome walkthrough with `.venv\Scripts\python.exe tools/voiceovers_ui_check.py`.
- The older full P12 gate stalled in P4 fixture teardown during initial takeover. Focused Voiceovers/agent/security/media checks and the dedicated disposable browser walkthrough are recorded separately; they are not presented as a fresh full-suite pass.
- Latest focused verification: **104 tests passed in separate runs** (83 core/Voiceovers/policy/page/file checks, 8 Codex checks, 13 Claude checks). The combined run stalled in the existing Windows TestClient/AnyIO teardown and was stopped; it is not reported as a full-suite pass. The dedicated Voiceovers/clone suite passed 43 tests. The **29-check Chrome walkthrough passed**, including retry controls, best-settings selection/manual overrides, playable comparisons, assistant handoff, passage repair and measured delivery targets. Existing fake-CLI teardown warnings remain. All provider calls/audio were stand-ins or synthetic; real voice fidelity and auditory performance review have not been validated.

Provider wire formats were checked against [ElevenLabs TTS timing](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps), [Scribe transcription](https://elevenlabs.io/docs/api-reference/speech-to-text/convert), [instant voice cloning](https://elevenlabs.io/docs/api-reference/voices/ivc/create), [MiniMax TTS](https://platform.minimax.io/docs/api-reference/speech-t2a-http), [clone upload](https://platform.minimax.io/docs/api-reference/voice-cloning-uploadcloneaudio), and [voice clone](https://platform.minimax.io/docs/api-reference/voice-cloning-clone). MiniMax documents deletion of an unused clone after seven days; the app surfaces this provider note after preparation.
