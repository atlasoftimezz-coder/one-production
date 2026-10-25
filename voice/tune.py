"""Which Chatterbox settings reproduce a reference voice most closely?

    python tune.py --ref <voice file in VOICES or a path> --out result.json

Renders the same short text with a grid of (exaggeration, cfg_weight, temperature) and scores
each take against the reference with Chatterbox's own speaker encoder. Pitch (median F0) is
the tie-breaker. The embedding is centred on unrelated speakers - every other clip in the
voices folder plus Chatterbox's built-in voice - and never on the candidates themselves:
centring on a set that contains them drags the mean toward the target and flattens every
score. With no other speakers available the raw similarity is used.

Prints "STEP i/N <what>" lines so One Production can show progress, and writes the ranking
and the best settings as JSON. Adapted from the studio's fidelity.py for any voice on any PC.
"""
import argparse
import json
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

TEXT = ("Most people never notice the moment a city goes quiet. The shops close one by one, "
        "the trains stop running, and the last families leave with whatever they can carry.")
GRID_GPU = [(0.40, 0.50, 0.80), (0.50, 0.50, 0.80), (0.50, 0.30, 0.80), (0.65, 0.40, 0.80),
            (0.65, 0.30, 0.85), (0.80, 0.30, 0.85), (0.80, 0.50, 0.80)]
GRID_CPU = [(0.50, 0.50, 0.80), (0.50, 0.30, 0.80), (0.65, 0.40, 0.80)]   # CPU is ~20x slower
MAX_BASIS = 6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = ap.parse_args()

    import numpy as np
    import librosa
    import torch
    import torchaudio
    import config
    import pipeline
    from chatterbox.tts import ChatterboxTTS

    ref = pathlib.Path(args.ref)
    if not ref.is_file():
        ref = config.VOICES / args.ref
    if not ref.is_file():
        sys.exit(f"reference clip not found: {args.ref}")
    device = ("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device
    grid = GRID_GPU if device == "cuda" else GRID_CPU
    basis_files = [p for p in sorted(config.VOICES.iterdir())
                   if p.suffix.lower() in (".wav", ".mp3", ".flac") and p != ref
                   and not p.name.startswith("_upload_")][:MAX_BASIS]
    total = len(grid) + 2                       # the grid, the built-in voice, the scoring
    started = time.time()
    print(f"STEP 0/{total} loading the voice model on {device}", flush=True)
    model = ChatterboxTTS.from_pretrained(device=device)
    work = pathlib.Path(tempfile.mkdtemp(prefix="tune-"))

    def render(path, prompt, ex, cf, tp):
        parts = []
        for i, g in enumerate(pipeline.chunk_text(TEXT)):
            torch.manual_seed(4242 + i * 101)
            kw = dict(exaggeration=ex, cfg_weight=cf, temperature=tp)
            if prompt:
                kw["audio_prompt_path"] = str(prompt)
            w = model.generate(g, **kw)
            parts.append(w.squeeze(0).cpu())
            parts.append(torch.zeros(int(0.20 * model.sr)))
        torchaudio.save(str(path), torch.cat(parts[:-1]).unsqueeze(0), model.sr)

    takes = []
    for n, (ex, cf, tp) in enumerate(grid, 1):
        dst = work / ("e%02d_c%02d_t%02d.wav" % (ex * 100, cf * 100, tp * 100))
        render(dst, ref, ex, cf, tp)
        takes.append((ex, cf, tp, dst))
        print(f"STEP {n}/{total} rendered exaggeration {ex} cfg {cf} temperature {tp}", flush=True)
    builtin = work / "builtin.wav"
    render(builtin, None, 0.5, 0.5, 0.8)
    print(f"STEP {len(grid) + 1}/{total} rendered the built-in voice for comparison", flush=True)

    # The bare VoiceEncoder constructor has random weights. Reuse the trained
    # encoder loaded with Chatterbox, otherwise the ranking is meaningless.
    ve = model.ve.eval()

    def emb(p):
        w, _ = librosa.load(str(p), sr=16000)
        return np.asarray(ve.embeds_from_wavs([w], sample_rate=16000)).reshape(-1)

    basis = [emb(p) for p in basis_files] + [emb(builtin)]
    centred = len(basis) >= 2
    mu = np.stack(basis + [emb(ref)]).mean(0) if centred else 0
    ref_e = emb(ref)

    def cos(a, b):
        a, b = a - mu, b - mu
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    def f0(p):
        y, _ = librosa.load(str(p), sr=22050, mono=True)
        f, _, _ = librosa.pyin(y, fmin=55, fmax=340, sr=22050, frame_length=1024)
        v = f[~np.isnan(f)]
        return float(np.median(v)) if len(v) else 0.0

    ref_f0 = f0(ref)
    rows = []
    for ex, cf, tp, dst in takes:
        take_f0 = f0(dst)
        semis = 12 * abs(np.log2(take_f0 / ref_f0)) if take_f0 and ref_f0 else 99.0
        rows.append({"exaggeration": ex, "cfg_weight": cf, "temperature": tp,
                     "similarity": round(cos(ref_e, emb(dst)), 4), "f0": round(take_f0, 1),
                     "semitones_off": round(float(semis), 2)})
    # most similar first; a take more than a semitone off pitch drops below the in-pitch ones
    rows.sort(key=lambda r: (r["semitones_off"] > 1.0, -r["similarity"], r["semitones_off"]))
    best = rows[0]
    print(f"STEP {total}/{total} scored {len(rows)} takes", flush=True)
    result = {"voice": ref.name, "device": device, "centred_on": len(basis), "reference_f0": round(ref_f0, 1),
              "best": {k: best[k] for k in ("exaggeration", "cfg_weight", "temperature")},
              "ranking": rows, "seconds": round(time.time() - started, 1)}
    pathlib.Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result["best"]), flush=True)


if __name__ == "__main__":
    main()
