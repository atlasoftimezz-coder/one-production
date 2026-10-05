"""Measuring media with ffmpeg / ffprobe (CPU, a few seconds per file). No model, no GPU.

Every number a stage gate uses comes from here, so the gates, the editor and the tests agree.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class MediaError(RuntimeError):
    pass


def find_ffmpeg(data_dir: Path | None = None) -> str | None:
    """ONEPROD_FFMPEG, then the copy the setup wizard installed, then PATH."""
    for candidate in (os.environ.get('ONEPROD_FFMPEG'),
                      str(data_dir / 'modules' / 'ffmpeg' / 'bin' / 'ffmpeg.exe') if data_dir else None,
                      shutil.which('ffmpeg')):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def ffprobe_for(ffmpeg: str) -> str:
    sibling = Path(ffmpeg).with_name('ffprobe' + Path(ffmpeg).suffix)
    found = str(sibling) if sibling.is_file() else shutil.which('ffprobe')
    if not found:
        raise MediaError('ffprobe was not found next to ffmpeg or on PATH.')
    return found


def run(args: list[str], timeout: float = 3600, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, creationflags=NO_WINDOW, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise MediaError(f'{Path(args[0]).stem} took longer than {int(timeout)} s.') from None


def probe(ffmpeg: str, path: Path) -> dict:
    r = run([ffprobe_for(ffmpeg), '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)], 120)
    if r.returncode:
        raise MediaError(f'{Path(path).name} is not readable media: {r.stderr.strip()[-200:]}')
    return json.loads(r.stdout)


def duration(ffmpeg: str, path: Path) -> float:
    return float(probe(ffmpeg, path)['format']['duration'])


def loudness(ffmpeg: str, path: Path) -> dict:
    """Integrated loudness (LUFS) and true peak (dBTP), measured exactly as the P4 voice test does."""
    r = run([ffmpeg, '-hide_banner', '-nostats', '-i', str(path), '-af', 'loudnorm=print_format=json', '-f', 'null', '-'])
    text = r.stderr
    try:
        data = json.loads(text[text.rindex('{'):text.rindex('}') + 1])
        return {'lufs': float(data['input_i']), 'true_peak': float(data['input_tp'])}
    except (ValueError, KeyError):
        raise MediaError(f'Could not measure loudness of {Path(path).name}.') from None


def master(ffmpeg: str, src: Path, dst: Path, lufs: float = -14.0, true_peak: float = -1.5) -> Path:
    """Two-pass loudnorm to the channel's level (measure, then exact gain), like the voice pipeline.
    -1.5 dBTP leaves room for the AAC encoder's overshoot under the -1.0 limit."""
    target = f'loudnorm=I={lufs}:TP={true_peak}:LRA=11'
    r = run([ffmpeg, '-hide_banner', '-nostats', '-i', str(src), '-af', target + ':print_format=json', '-f', 'null', '-'])
    try:
        m = json.loads(r.stderr[r.stderr.rindex('{'):r.stderr.rindex('}') + 1])
    except ValueError:
        raise MediaError(f'Could not measure {Path(src).name} for mastering.') from None
    r = run([ffmpeg, '-y', '-hide_banner', '-v', 'error', '-i', str(src), '-af',
             f"{target}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
             f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample=48000",
             '-ar', '48000', str(dst)])
    if r.returncode:
        raise MediaError(f'Mastering failed: {r.stderr.strip()[-200:]}')
    return dst


def black_intervals(ffmpeg: str, path: Path, min_seconds: float = 0.1) -> list[tuple[float, float]]:
    r = run([ffmpeg, '-hide_banner', '-nostats', '-i', str(path), '-vf', f'blackdetect=d={min_seconds}:pic_th=0.98:pix_th=0.10',
             '-an', '-f', 'null', '-'])
    return [(float(a), float(b)) for a, b in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)', r.stderr)]


def pcm(ffmpeg: str, path: Path, rate: int = 16000, start: float = 0, seconds: float | None = None) -> np.ndarray:
    args = [ffmpeg, '-hide_banner', '-v', 'error', '-ss', str(start), '-i', str(path)]
    if seconds:
        args += ['-t', str(seconds)]
    r = subprocess.run(args + ['-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'], capture_output=True, timeout=600,
                       creationflags=NO_WINDOW)
    if r.returncode:
        raise MediaError(f'Could not decode {Path(path).name}: {r.stderr.decode(errors="replace")[-200:]}')
    return np.frombuffer(r.stdout, dtype=np.float32)


def median_f0(x: np.ndarray, rate: int = 16000, fmin: float = 55, fmax: float = 400) -> tuple[float | None, int]:
    """(median pitch in Hz of the voiced frames, voiced frame count). YIN-style: a cumulative-mean
    normalised difference from the autocorrelation, which avoids octave jumps on harmonic voices."""
    frame, hop = 1024, 320
    if len(x) < frame * 2:
        return None, 0
    frames = np.lib.stride_tricks.sliding_window_view(x, frame)[::hop].astype(np.float64)
    frames = frames - frames.mean(axis=1, keepdims=True)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    loud = energy > max(1e-4, 0.15 * np.percentile(energy, 95))
    frames = frames[loud]
    if not len(frames):
        return None, 0
    spec = np.fft.rfft(frames, n=2 * frame, axis=1)
    acf = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :frame]
    diff = 2 * (acf[:, :1] - acf)                        # d(tau), stationary approximation
    diff[:, 0] = 0
    cum = np.cumsum(diff[:, 1:], axis=1)
    norm = np.ones_like(diff)
    norm[:, 1:] = diff[:, 1:] * np.arange(1, frame) / np.maximum(cum, 1e-12)
    lo, hi = int(rate / fmax), int(rate / fmin)
    window = norm[:, lo:hi]
    pitches = []
    for row in window:
        below = np.flatnonzero(row < 0.15)
        if below.size:                                 # first dip under the threshold, then its local minimum
            i = below[0]
            while i + 1 < len(row) and row[i + 1] < row[i]:
                i += 1
        else:
            i = int(np.argmin(row))
            if row[i] > 0.35:
                continue                               # unvoiced / noisy frame
        a, b, c = row[max(i - 1, 0)], row[i], row[min(i + 1, len(row) - 1)]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        pitches.append(rate / (lo + i + shift))
    if not pitches:
        return None, 0
    return float(np.median(pitches)), len(pitches)


def pitch(ffmpeg: str, path: Path, start: float = 0, seconds: float | None = 120) -> tuple[float | None, int]:
    return median_f0(pcm(ffmpeg, path, start=start, seconds=seconds))


def srt_cues(text: str) -> list[dict]:
    def sec(stamp: str) -> float:
        h, m, s = stamp.strip().replace(',', '.').split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    cues = []
    for block in re.split(r'\n\s*\n', text.replace('\r\n', '\n').strip()):
        lines = [l for l in block.split('\n') if l.strip()]
        timing = next((l for l in lines if '-->' in l), None)
        if not timing:
            continue
        a, b = timing.split('-->')
        body = lines[lines.index(timing) + 1:]
        cues.append({'start': sec(a), 'end': sec(b.split()[0]), 'text': ' '.join(body).strip()})
    return cues
