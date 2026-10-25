"""CPU measurements and assembly for Voiceovers. No model download or network I/O."""
from __future__ import annotations

import math
import re
import wave
from pathlib import Path

import numpy as np

from . import media

MILES_RESOLVE_PRESET = {
    'name': 'Miles · Resolve distortion reference', 'highpass_hz': 666, 'lowpass_hz': 1650,
    'mode': 'Distort', 'level': 68.2, 'ceiling_db': -4.7, 'dry_wet': 100.0,
    'output_db': 5.4, 'auto_level': True,
    'implementation': 'Portable approximation; Resolve’s proprietary distortion curve is not reproduced bit-for-bit.',
}


def word_count(text: str) -> int:
    text = re.sub(r'<#[0-9.]+#>', '', text)
    return len(re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", text, re.UNICODE))


def chunks(text: str, limit: int = 1000) -> list[str]:
    """Bound provider requests without splitting words or speaking markdown headings."""
    text = re.sub(r'^\s*#{1,6}\s+.*$', '', text, flags=re.M).strip()
    words, out, current = text.split(), [], ''
    for word in words:
        if len(word) > limit:
            raise ValueError('A word in the script is too long.')
        if current and len(current) + len(word) + 1 > limit:
            out.append(current)
            current = ''
        current = (current + ' ' + word).strip()
    if current:
        out.append(current)
    return out


def script_turns(text: str, speakers: list[dict]) -> list[dict]:
    aliases = {}
    for speaker in speakers:
        for alias in (speaker['id'], speaker['label']):
            alias = alias.strip().casefold()
            if alias in aliases and aliases[alias] != speaker['id']:
                raise ValueError('Give each speaker a different name.')
            aliases[alias] = speaker['id']
    turns, active = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.match(r'^(?:\[([^\]]{1,40})\]|([^:]{1,40}):)\s*(.*)$', line)
        name = (match.group(1) or match.group(2)).strip().casefold() if match else ''
        if name in aliases:
            active = aliases[name]
            content = match.group(3).strip()
        else:
            if len(speakers) > 1 and (active is None or match):
                raise ValueError('Label dialogue with the speaker names, for example A: Hello or [B] Hi.')
            active = active or speakers[0]['id']
            content = line
        if content:
            if turns and turns[-1]['speaker'] == active:
                turns[-1]['text'] += '\n' + content
            else:
                turns.append({'speaker': active, 'text': content})
    result = []
    for turn in turns:
        before = len(result)
        for piece in re.split(r'(<#[0-9.]+#>)', turn['text']):
            if re.fullmatch(r'<#[0-9.]+#>', piece):
                try:
                    pause = float(piece[2:-2])
                except ValueError:
                    raise ValueError('Use a pause such as <#0.5#>.') from None
                if not .01 <= pause <= 10 or len(result) == before or 'explicit_gap_ms' in result[-1]:
                    raise ValueError('Place one pause of 0.01–10 seconds between spoken text sections.')
                result[-1]['explicit_gap_ms'] = round(pause * 1000)
            else:
                for part in chunks(piece):
                    result.append({'speaker': turn['speaker'], 'text': part, 'turn_end': False})
        if len(result) > before:
            result[-1]['turn_end'] = True
    if not result:
        raise ValueError('Add some spoken script text.')
    if len(result) > 500:
        raise ValueError('This script has more than 500 sections. Split it into smaller voiceovers.')
    return result


def effect_guess(samples: np.ndarray, rate: int = 16000) -> dict:
    """A bandwidth heuristic, not proof of a particular effect or speaker identity."""
    if len(samples) < rate or np.max(np.abs(samples)) < .001:
        return {'suggested': 'clean', 'confidence': 'low', 'reason': 'Not enough audible speech to assess the effect.'}
    frames = samples[:min(len(samples), rate * 15)]
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(len(frames)))) ** 2
    frequency = np.fft.rfftfreq(len(frames), 1 / rate)
    total = float(spectrum[(frequency >= 80) & (frequency <= 7500)].sum()) + 1e-12
    low = float(spectrum[(frequency >= 80) & (frequency < 250)].sum() / total)
    high = float(spectrum[frequency > 4000].sum() / total)
    radio = low < .025 and high < .025
    return {'suggested': 'radio' if radio else 'clean', 'confidence': 'low',
            'low_band_ratio': round(low, 4), 'high_band_ratio': round(high, 4),
            'reason': 'Narrow speech bandwidth suggests radio/telephone processing; confirm by listening.' if radio
                      else 'No clear narrow-band radio signature; compression or other effects may still be present.'}


def analyze_words(words: list[dict], seconds: float) -> dict:
    clean = []
    for item in words:
        if item.get('type', 'word') != 'word' or not str(item.get('text', '')).strip():
            continue
        try:
            start, end = float(item['start']), float(item['end'])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start + end) or start < 0 or end <= start or end > seconds + 1:
            continue
        clean.append({'text': str(item['text']), 'start': start, 'end': min(end, seconds),
                      'speaker': str(item.get('speaker_id') or 'speaker_0')})
    clean.sort(key=lambda x: (x['start'], x['end']))
    if not clean:
        raise ValueError('No timed speech was found. Try a clearer reference recording.')
    turns, pauses, overlap = [], [], 0
    for word in clean:
        if turns:
            gap = word['start'] - turns[-1]['end']
            if gap < -.05:
                overlap += 1
            if gap > .12:
                pauses.append(round(gap, 3))
        if turns and turns[-1]['speaker'] == word['speaker'] and word['start'] - turns[-1]['end'] < .8:
            turns[-1]['text'] += ' ' + word['text']
            turns[-1]['end'] = max(turns[-1]['end'], word['end'])
            turns[-1]['words'] += word_count(word['text'])
        else:
            turns.append({'speaker': word['speaker'], 'start': word['start'], 'end': word['end'],
                          'text': word['text'], 'words': word_count(word['text'])})
    speakers = []
    for i, sid in enumerate(dict.fromkeys(w['speaker'] for w in clean)):
        own = [t for t in turns if t['speaker'] == sid]
        count = sum(t['words'] for t in own)
        span = sum(t['end'] - t['start'] for t in own)
        speakers.append({'id': sid, 'label': f'Speaker {chr(65 + i)}', 'words': count,
                         'speaking_seconds': round(span, 3), 'wpm': round(count * 60 / max(span, .1), 1),
                         'effect': 'clean', 'turns': len(own)})
    turn_gaps = [max(0, b['start'] - a['end']) for a, b in zip(turns, turns[1:]) if a['speaker'] != b['speaker']]
    within = [b['start'] - a['end'] for a, b in zip(clean, clean[1:])
              if a['speaker'] == b['speaker'] and .12 < b['start'] - a['end'] < 3]
    return {'seconds': round(seconds, 3), 'words': sum(s['words'] for s in speakers),
            'wpm': round(sum(s['words'] for s in speakers) * 60 / max(seconds, .1), 1),
            'speakers': speakers, 'turns': turns, 'word_timestamps': clean,
            'pauses': {'count': len(pauses), 'median_seconds': round(float(np.median(pauses)), 3) if pauses else 0,
                       'within_speaker_median_seconds': round(float(np.median(within)), 3) if within else None,
                       'longest_seconds': max(pauses, default=0),
                       'turn_gap_ms': round(float(np.median(turn_gaps)) * 1000) if turn_gaps else 250},
            'overlapping_words': overlap,
            'notes': ['Speaker labels are estimated, not real-person identification. Review labels and effects before generation.']}


def fit_radio_bandwidth(clean: np.ndarray, processed: np.ndarray, rate=16000) -> dict:
    """Fit a conservative filter starting point to relative long-term spectra.

    No waveform alignment or identical spoken text is assumed. This cannot identify
    the original plugin chain, distortion or compressor parameters uniquely.
    """
    n = 2048
    def spectrum(data):
        if len(data) < n:
            return None
        frames = np.lib.stride_tricks.sliding_window_view(data[:rate * 30], n)[::1024]
        return np.mean(np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)) ** 2, axis=0)
    a, b = spectrum(clean), spectrum(processed)
    default = {'highpass_hz': 300, 'lowpass_hz': 3400, 'compression_ratio': 4, 'fit': 'default: insufficient speech bandwidth'}
    if a is None or b is None or max(a.max(), b.max()) < 1e-10:
        return default
    frequencies = np.fft.rfftfreq(n, 1 / rate)
    centers = np.geomspace(100, 6500, 28)
    aa, bb = [], []
    for center in centers:
        mask = (frequencies >= center / 1.12) & (frequencies <= center * 1.12)
        aa.append(float(a[mask].mean()) if mask.any() else 0)
        bb.append(float(b[mask].mean()) if mask.any() else 0)
    aa, bb = np.array(aa), np.array(bb)
    usable = (aa > aa.max() * 1e-4) & (bb > bb.max() * 1e-6)
    if usable.sum() < 9:
        return default
    f = centers[usable]
    measured = np.clip(10 * np.log10((bb[usable] + 1e-12) / (aa[usable] + 1e-12)), -35, 20)
    measured -= np.median(measured)
    best = None
    for low in (80, 120, 180, 250, 300, 400, 500, 650):
        for high in (1800, 2400, 2800, 3400, 4000, 5000, 6000):
            response = 10 * np.log10(f ** 4 / (f ** 4 + low ** 4) * high ** 4 / (high ** 4 + f ** 4) + 1e-12)
            response -= np.median(response)
            error = float(np.mean(np.minimum((measured - response) ** 2, 100)))
            if best is None or error < best[0]:
                best = (error, low, high)
    return {'highpass_hz': best[1], 'lowpass_hz': best[2], 'compression_ratio': 4,
            'fit': 'relative long-term spectrum; approximate', 'fit_error_db': round(float(np.sqrt(best[0])), 2)}


def waveform(ffmpeg: str, path: Path) -> list[float]:
    data = media.pcm(ffmpeg, path, rate=1000, seconds=120)
    if not len(data):
        return []
    return [round(float(np.max(np.abs(block))), 3) for block in np.array_split(data, min(180, len(data)))]


def render_turn(ffmpeg: str, src: Path, dst: Path, words: int, target_wpm: float | None, effect: str,
                effect_settings: dict | None = None) -> tuple[float, float]:
    original = media.duration(ffmpeg, src)
    if original <= .01:
        raise ValueError('The provider returned empty audio.')
    tempo = min(2.0, max(.5, words * 60 / (target_wpm * original))) if target_wpm and words else 1.0
    filters = [f'atempo={tempo:.6f}']
    if effect == 'miles_radio':
        p = MILES_RESOLVE_PRESET
        ceiling = 10 ** (p['ceiling_db'] / 20)
        drive = 1 + 15 * p['level'] / 100
        filters += [f"highpass=f={p['highpass_hz']}", f"lowpass=f={p['lowpass_hz']}",
                    f'volume={drive}', f'asoftclip=type=tanh:threshold={ceiling}:oversample=4']
        if p['auto_level']:
            filters.append('loudnorm=I=-20:TP=-4.7:LRA=7')
        filters.append(f"volume={p['output_db']}dB")
    elif effect == 'radio':
        settings = effect_settings or {}
        low = min(800, max(80, float(settings.get('highpass_hz', 300))))
        high = min(6000, max(1800, float(settings.get('lowpass_hz', 3400))))
        ratio = min(10, max(1, float(settings.get('compression_ratio', 4))))
        filters += [f'highpass=f={low}', f'lowpass=f={high}', f'acompressor=threshold=0.12:ratio={ratio}:attack=5:release=70:makeup=1.5']
    filters.append('aresample=48000')
    result = media.run([ffmpeg, '-y', '-v', 'error', '-i', str(src), '-af', ','.join(filters),
                        '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(dst)], timeout=180)
    if result.returncode:
        raise ValueError('Audio processing failed. Check that ffmpeg supports the selected effect.')
    return original, media.duration(ffmpeg, dst)


def join_turns(turns: list[dict], output: Path, stem: str | None = None) -> None:
    with wave.open(str(output), 'wb') as dst:
        dst.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        for turn in turns:
            with wave.open(str(turn['path']), 'rb') as src:
                if stem is None or turn['speaker'] == stem:
                    while data := src.readframes(48000):
                        dst.writeframesraw(data)
                else:
                    frames = src.getnframes()
                    while frames:
                        take = min(48000, frames)
                        dst.writeframesraw(bytes(take * 2))
                        frames -= take
            dst.writeframesraw(bytes(round(turn.get('gap_ms', 0) * 48) * 2))


def shape_pauses(path: Path, pause_ms: int | None, alignment: list[dict],
                 consistent: bool = False, threshold_db: float = -42, minimum_ms: int = 120) -> float:
    """Adjust clear internal quiet gaps; retain breath/noisy intervals and word timings.

    This is conservative silence shaping, not a claim to recover performance from text.
    The model's expressive delivery and punctuation still matter.
    """
    with wave.open(str(path), 'rb') as src:
        rate, frames = src.getframerate(), src.getnframes()
        if pause_ms is None and not consistent:
            return frames / rate
        pcm = np.frombuffer(src.readframes(frames), dtype='<i2').copy()
    window = max(1, round(rate * .01))
    usable = len(pcm) // window
    if not usable:
        return len(pcm) / rate
    blocks = pcm[:usable * window].reshape(-1, window).astype(np.float64) / 32768
    quiet = np.sqrt(np.mean(blocks * blocks, axis=1)) < (10 ** (threshold_db / 20) if consistent else .005)
    changes, begin = [], None
    for i, silent in enumerate([*quiet, False]):
        if silent and begin is None:
            begin = i
        elif not silent and begin is not None:
            # Do not reshape leading/trailing silence; reject very long non-speech regions.
            if consistent and (i - begin) * 10 >= minimum_ms:
                target = 0 if begin == 0 or i >= usable else round(rate * (pause_ms if pause_ms is not None else 180) / 1000)
                changes.append((begin * window, i * window, target))
            elif not consistent and begin > 0 and i < usable and 15 <= i - begin <= 300:
                old = (i - begin) * window
                target = min(old * 2, max(round(old * .5), round(rate * pause_ms / 1000)))
                changes.append((begin * window, i * window, target))
            begin = None
    if not changes:
        return frames / rate
    pieces, cursor = [], 0
    for start, end, new in changes:
        pieces.extend((pcm[cursor:start], np.zeros(new, dtype='<i2')))
        cursor = end
    pieces.append(pcm[cursor:])
    combined = np.concatenate(pieces)
    def map_time(value):
        frame, shift = float(value) * rate, 0
        for start, end, new in changes:
            if frame >= end:
                shift += new - (end - start)
            elif frame > start:
                return (start + shift + (frame - start) * new / (end - start)) / rate
            else:
                break
        return (frame + shift) / rate
    for word in alignment:
        word['start'], word['end'] = map_time(word['start']), map_time(word['end'])
    with wave.open(str(path), 'wb') as dst:
        dst.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        dst.writeframes(combined.astype('<i2').tobytes())
    return len(combined) / rate


def alignment_words(alignment: dict | None) -> list[dict]:
    if not alignment:
        return []
    chars = alignment.get('characters') or []
    starts = alignment.get('character_start_times_seconds') or []
    ends = alignment.get('character_end_times_seconds') or []
    result, pending = [], []
    for char, start, end in zip(chars, starts, ends):
        if str(char).isspace():
            if pending:
                result.append({'text': ''.join(x[0] for x in pending), 'start': pending[0][1], 'end': pending[-1][2]})
                pending = []
        else:
            try:
                a, b = float(start), float(end)
            except (TypeError, ValueError):
                continue
            if math.isfinite(a + b) and 0 <= a <= b:
                pending.append((str(char), a, b))
    if pending:
        result.append({'text': ''.join(x[0] for x in pending), 'start': pending[0][1], 'end': pending[-1][2]})
    return result


def srt_text(turns: list[dict]) -> str:
    def timestamp(seconds):
        ms = max(0, round(seconds * 1000))
        return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'
    lines, cursor = [], 0.0
    for turn in turns:
        timed = turn.get('alignment') or []
        if not timed:
            tokens = turn['text'].split()
            step = turn['seconds'] / max(1, len(tokens))
            timed = [{'text': word, 'start': i * step, 'end': (i + 1) * step} for i, word in enumerate(tokens)]
        for i in range(0, len(timed), 8):
            group = timed[i:i + 8]
            start = max(cursor, turn['start'] + max(0, float(group[0]['start'])))
            end = min(turn['start'] + turn['seconds'], turn['start'] + float(group[-1]['end']))
            if end <= start:
                continue
            content = ' '.join(w['text'] for w in group)
            if len(content) > 48:
                tokens = content.split()
                half = max(1, len(tokens) // 2)
                content = ' '.join(tokens[:half]) + '\n' + ' '.join(tokens[half:])
            lines += [str(len(lines) // 4 + 1), f'{timestamp(start)} --> {timestamp(end)}', content, '']
            cursor = end
    return '\n'.join(lines)


def master_delivery(ffmpeg: str, source: Path, destination: Path, true_peak_margin: float = -1.5) -> dict:
    """Measure/correct short-take loudness and limit oversampled peaks explicitly.

    Two-pass loudnorm can miss integrated loudness on short, highly dynamic clips.
    Never mark mastering complete based only on the filter's requested settings.
    """
    media.master(ffmpeg, source, destination, -14, true_peak_margin)
    for attempt in range(5):
        measured = media.loudness(ffmpeg, destination)
        if not math.isfinite(measured['lufs']) or not math.isfinite(measured['true_peak']):
            raise ValueError('The result has no measurable speech loudness. Review the provider audio.')
        if abs(measured['lufs'] + 14) <= .2 and measured['true_peak'] <= -1:
            return measured
        if attempt == 4:
            break
        gain = max(-12, min(12, -14 - measured['lufs']))
        temporary = destination.with_name(destination.stem + '-corrected.wav')
        ceiling = 10 ** (true_peak_margin / 20)
        result = media.run([ffmpeg, '-y', '-v', 'error', '-i', str(destination), '-af',
            f'volume={gain:.6f}dB,aresample=192000,alimiter=limit={ceiling:.8f}:level=false:latency=true,aresample=48000',
            '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(temporary)], timeout=600)
        if result.returncode:
            raise ValueError('Measured loudness correction failed. Check ffmpeg’s limiter support.')
        temporary.replace(destination)
    return measured
