# Voiceovers: start here

In the app, open **Voiceovers → Page guide**. The guide changes with the page you are viewing. Use its **Help with** menu for Settings, History, Claude / Codex, and Review & repair.

For installation and an ordered first test on another computer, follow the [PC test checklist](VOICEOVERS_PC_TEST.md).

## Set up once on each coworker’s PC

1. Choose or create a project at the top.
2. **Connections:** connect Chatterbox on that PC, or save an ElevenLabs / MiniMax API key.
3. **Voices & references:** save Peter’s clean sample and Miles’s clean sample separately. Add the radio recording to Miles’s card. Choose a provider and use **Clone & check voice match** for each clean voice. You can also use existing provider voice IDs.

## Clone and check your voices

Upload each clean voice under its name, then ask Claude / Codex:

> Clone Peter and Miles using Chatterbox. Automatically improve and compare each voice with its clean sample, using up to five test takes each. Keep the best settings. Keep Miles’s radio effect separate.

Name **ElevenLabs** or **MiniMax** instead when you want that provider. If you uploaded the recordings as ordinary project files, tell the assistant which file belongs to each speaker and confirm your permission to clone them. It can save the named voices and continue from there.

The assistant requests approval, creates or reuses each provider clone, generates clean test speech, and runs speaker comparison on the coworker’s installed voice module. Automatic refinement tries supported voice controls and keeps the highest-scoring eligible take. It also saves that take’s settings for future narration. Chatterbox takes flagged for misread words are excluded from the recommendation.

On a saved voice card: choose a provider, expand **Clone & check voice match**, leave **Improve the match automatically** on, approve and start. The default limit is **five takes**; you can select up to ten. The loop stops when it reaches an optional calibrated identity target, when two takes show no meaningful progress, when scoring is unavailable, or when it reaches the approved limit. Completed takes are kept. It does not keep recreating provider clone IDs.

Use one take for a quick comparison. Turning automatic improvement off generates the selected number of comparison takes with fixed voice controls. In Create voiceover, **Use best clone match settings** loads the selected settings; changing native controls lets you customize them. Review your plan again before generation.

The saved voice card shows **Load reference & test audio**, the best eligible take, why the search stopped and a speaker similarity score when available. Compare the recordings by ear. A cosine score is **not a percentage match** and does not prove perfect pronunciation, emotion or sound quality. The optional target needs calibration with your examples; there is no universal “perfect clone” value. Even a take that reaches that identity target still needs listening review. Provider verification must be completed when requested.

The comparison requires trained speaker-encoder weights already installed on the coworker’s PC, including when the clone comes from a cloud API. It does not download a model during checking. Verify the clean voice first; apply Miles’s radio sound afterward.

## Make an episode

In **Create voiceover**, choose two speakers and assign their prepared voices in Settings. Keep Peter clean and select Miles’s radio preset. Paste the whole conversation:

```text
Miles: Pete, are you there?
Peter: Yes, Miles. What happened?
Miles: You need to hear this.
```

The labels choose the voices; they are **never spoken**. Either speaker may start. Names mentioned inside a sentence remain spoken normally.

Enter the episode number. Turn **Short preview off** for the full script. Click **Review**, check the plan, approve, then generate. The app creates both voices in script order, applies effects, edits silence and masters the mix. The target is −14 LUFS with true peak no higher than −1 dBTP; check the measured result.

For Claude / Codex, click **Continue in Claude / Codex** after Review. Select your connected assistant and send the prepared request. It uses the reviewed script and settings; typing alone does not share editor changes. The handoff does not submit a chat message or start generation automatically.

## Listen, repair and export

- **History** on Create voiceover: select a take, listen, restore its text or restore its settings.
- **Review & repair a passage** inside a completed take: listen from a section, select the passages to regenerate, write the issue and approve. The app reuses other source audio and creates a new mastered take. Your earlier take remains available.
- Claude / Codex can also inspect the sections and repair selected section numbers after approval.
- **Generated audio:** find project jobs and downloads. Give your editor **#Number VO Final.wav**. MP3 is a listening copy; stems and Preprod are optional editing files.

Repair currently makes another take of the same passage with the same voice/settings. The issue note records your feedback; it is not yet a pronunciation or emotion instruction to the model.

## Your production review requirement

The intended core loop is: **generate → listen and compare → identify a faulty passage → regenerate only that passage → rebuild and check again**.

Selected-passage replacement is implemented. Chatterbox also has a transcript check and bounded retries for misreads. An autonomous audio reviewer that judges artifacts, pronunciation and the intended performance against your examples is **not connected yet**. “Done” or “Levels checked” does not mean a take has passed that listening review.

For your **20 examples**, send each finished audio with its exact labeled script. Add a short note about what you want copied, and timestamps for useful moments: emotion, pronunciation, pace, pauses, interruptions or Miles’s radio tone. Mark competitor clips as delivery references. Keep permitted clean voice samples separate. We will use these to define and validate the delivery standard; uploading them is not model-weight training.

Miles’s radio processing currently approximates your supplied Resolve settings. We still need an A/B listen using your real clean and processed recordings.
