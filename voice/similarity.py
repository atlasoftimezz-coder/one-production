"""Speaker-identity evidence from trained, already installed Chatterbox weights.

This is not a pronunciation/performance judge. Scoring never downloads a model.
"""
import numpy as np


def score_embeddings(reference, candidate):
    ref, take = np.atleast_2d(reference).astype(float), np.atleast_2d(candidate).astype(float)
    if ref.shape[1] != take.shape[1] or not np.isfinite(ref).all() or not np.isfinite(take).all():
        raise ValueError('Invalid speaker embeddings.')
    if np.any(np.linalg.norm(ref, axis=1) < 1e-8) or np.any(np.linalg.norm(take, axis=1) < 1e-8):
        raise ValueError('No usable speaker embedding was produced.')
    ref /= np.linalg.norm(ref, axis=1, keepdims=True)
    take /= np.linalg.norm(take, axis=1, keepdims=True)
    a, b = ref.mean(0), take.mean(0)
    if min(np.linalg.norm(a), np.linalg.norm(b)) < 1e-8:
        raise ValueError('Inconsistent speaker embeddings; use a cleaner single-speaker sample.')
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    scores = np.clip(ref @ b, -1, 1)
    return {'speaker_cosine': round(float(np.clip(a @ b, -1, 1)), 4),
            'reference_window_scores': [round(float(v), 4) for v in scores],
            'reference_windows': len(ref), 'candidate_windows': len(take),
            'reference_consistency': round(float((ref @ ref.T)[np.triu_indices(len(ref), 1)].mean()), 4)
                                     if len(ref) > 1 else None,
            'metric': 'cosine similarity, not a percentage or a calibrated acceptance probability',
            'status': 'needs_listening_review', 'automatically_accepted': False}


def cached_encoder():
    """A default/random VoiceEncoder is never used as a scorer."""
    from chatterbox.models.voice_encoder import VoiceEncoder
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file
    import torch
    for filename in ('ve.safetensors', 've.pt'):
        try:
            path = hf_hub_download('ResembleAI/chatterbox', filename, local_files_only=True)
        except Exception:
            continue
        encoder = VoiceEncoder()
        weights = load_file(path, device='cpu') if filename.endswith('.safetensors') else torch.load(path, map_location='cpu', weights_only=True)
        encoder.load_state_dict(weights, strict=True)
        return encoder.to('cpu').eval()
    raise ValueError('Trained Chatterbox speaker-encoder weights are not installed locally. Install the voice module on the coworker PC first; no model was downloaded for this comparison.')


def compare_files(reference, candidate, encoder=None):
    import librosa
    import torch
    encoder = encoder if encoder is not None else cached_encoder()

    def windows(path):
        data, _ = librosa.load(str(path), sr=16000, mono=True, duration=60)
        if not np.isfinite(data).all():
            raise ValueError('Audio contains invalid samples.')
        data, _ = librosa.effects.trim(data, top_db=35)
        if len(data) < 24000 or float(np.sqrt(np.mean(data ** 2))) < .0005:
            raise ValueError('Use at least 1.5 seconds of audible speech for comparison.')
        count = min(3, max(1, len(data) // 80000))
        length = min(len(data), 160000)
        return [data[start:start + length] for start in np.linspace(0, len(data) - length, count, dtype=int)]

    refs, takes = windows(reference), windows(candidate)
    with torch.inference_mode():
        embeddings = encoder.embeds_from_wavs(refs + takes, sample_rate=16000)
    result = score_embeddings(embeddings[:len(refs)], embeddings[len(refs):])
    result.update(model='ResembleAI/chatterbox trained voice encoder',
                  checks=['speaker identity evidence'],
                  limitations=['No universal pass threshold is configured.',
                               'This encoder belongs to the Chatterbox model family; it is not an independent evaluator.',
                               'Does not verify pronunciation, emotion, script accuracy or absence of artifacts.'])
    return result
