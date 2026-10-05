"""Numeric quality gates per production stage (P10). No AI: every verdict is a measured number
against a fixed limit, so a stage can't pass because a reviewer liked it.

Each check is pass / fail / skip. A gate passes only when no check failed and no REQUIRED check
was skipped; optional checks (e.g. pitch without a reference clip) are reported as skipped.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from . import media

WER_LIMIT = 0.05
SEMITONES = 1.0
LUFS, LUFS_TOL, TRUE_PEAK = -14.0, 0.5, -1.0
DURATION_TOL, DRIFT_LIMIT = 0.5, 0.040
SUB_TAIL = 1.5
TITLE_MAX, DESCRIPTION_MAX, TAGS_MAX = 100, 5000, 500
CHAPTERS_MIN, CHAPTER_SECONDS = 3, 10
THUMB_SIZE, THUMB_BYTES = (1280, 720), 2 * 1024 * 1024


def check(name, ok, value, limit, detail='', required=True):
    status = 'skip' if ok is None else 'pass' if ok else 'fail'
    return {'name': name, 'status': status, 'value': value, 'limit': limit, 'detail': detail, 'required': required}


def verdict(stage: str, checks: list[dict], **extra) -> dict:
    failed = [c for c in checks if c['status'] == 'fail' or (c['status'] == 'skip' and c['required'])]
    return {'stage': stage, 'passed': not failed, 'checks': checks, 'measured_at': time.time(),
            'summary': 'All checks passed.' if not failed else
                       'Failed: ' + '; '.join(f"{c['name']} ({c['detail'] or c['value']})" for c in failed), **extra}


def subtitles(cues: list[dict], audio_seconds: float) -> list[dict]:
    out = []
    if not cues:
        return [check('subtitles complete', False, 0, '>= 1 cue', 'no cues found')]
    empty = [i + 1 for i, c in enumerate(cues) if not c['text']]
    backwards = [i + 1 for i, c in enumerate(cues) if c['end'] <= c['start']]
    overlaps = [i + 1 for i in range(1, len(cues)) if cues[i]['start'] < cues[i - 1]['end'] - 0.001]
    out.append(check('subtitles have text', not empty, len(empty), 0, f'empty cues {empty[:5]}' if empty else ''))
    out.append(check('subtitle timings valid', not backwards, len(backwards), 0, f'cues end before they start {backwards[:5]}' if backwards else ''))
    out.append(check('subtitles do not overlap', not overlaps, len(overlaps), 0, f'cues overlapping the one before {overlaps[:5]}' if overlaps else ''))
    tail = audio_seconds - cues[-1]['end']
    out.append(check('subtitles cover the audio', tail <= SUB_TAIL and cues[0]['start'] <= SUB_TAIL, round(tail, 2), f'<= {SUB_TAIL} s uncovered',
                     f'last cue ends {tail:.1f} s before the audio' if tail > SUB_TAIL else
                     f'first cue starts at {cues[0]["start"]:.1f} s' if cues[0]['start'] > SUB_TAIL else ''))
    return out


def voice_gate(ffmpeg: str, audio: Path, *, srt: Path | None = None, reference: Path | None = None,
               sections: list[dict] | None = None, pitch_required: bool = False) -> dict:
    """sections: the voice job's read-check results ({title, wer}); None = voiced outside the app.
    pitch_required: a clone made here must be measured against its reference clip."""
    checks = []
    if sections is None:
        checks.append(check('read-check (WER)', None, None, f'<= {WER_LIMIT}', 'not voiced in One Production, no read-check', required=False))
    else:
        worst = max((float(s.get('wer') or 0) for s in sections), default=None)
        bad = [s.get('title') or f'section {i + 1}' for i, s in enumerate(sections) if float(s.get('wer') or 0) > WER_LIMIT]
        missing = [s.get('title') for s in sections if s.get('wer') is None]
        checks.append(check('read-check (WER)', not bad and not missing and worst is not None, worst, f'<= {WER_LIMIT}',
                            f'misread: {", ".join(map(str, bad[:5]))}' if bad else 'sections without a read-check' if missing else ''))
    seconds = media.duration(ffmpeg, audio)
    loud = media.loudness(ffmpeg, audio)
    checks.append(check('loudness', abs(loud['lufs'] - LUFS) <= LUFS_TOL, loud['lufs'], f'{LUFS} +/- {LUFS_TOL} LUFS'))
    checks.append(check('true peak', loud['true_peak'] <= TRUE_PEAK + 0.05, loud['true_peak'], f'<= {TRUE_PEAK} dBTP'))
    if reference:
        start = min(20.0, max(0.0, seconds - 60))          # skip the intro, like the P4 measurement
        out_f0, n_out = media.pitch(ffmpeg, audio, start=start, seconds=120)
        ref_f0, n_ref = media.pitch(ffmpeg, reference, seconds=None)
        if out_f0 and ref_f0:
            off = round(12 * abs(math.log2(out_f0 / ref_f0)), 2)
            checks.append(check('pitch matches the reference', off <= SEMITONES, off, f'<= {SEMITONES} semitone',
                                f'{out_f0:.0f} Hz vs reference {ref_f0:.0f} Hz'))
        else:
            checks.append(check('pitch matches the reference', None, None, f'<= {SEMITONES} semitone', 'no voiced audio found to measure'))
    else:
        checks.append(check('pitch matches the reference', None, None, f'<= {SEMITONES} semitone', 'no reference clip',
                            required=pitch_required))
    if srt:
        checks += subtitles(media.srt_cues(Path(srt).read_text(encoding='utf-8-sig', errors='replace')), seconds)
    else:
        checks.append(check('subtitles complete', None, None, 'srt present', 'no subtitle file'))
    return verdict('voice', checks, seconds=round(seconds, 3))


def video_gate(ffmpeg: str, video: Path, voiceover: Path) -> dict:
    info = media.probe(ffmpeg, video)
    streams = {s['codec_type']: s for s in info['streams'] if s.get('codec_type') in ('video', 'audio')}
    checks = [check('has video and audio', len(streams) == 2, sorted(streams), 'video + audio')]
    total = float(info['format']['duration'])
    vo = media.duration(ffmpeg, voiceover)
    checks.append(check('duration matches the voiceover', abs(total - vo) <= DURATION_TOL, round(total - vo, 3),
                        f'+/- {DURATION_TOL} s', f'video {total:.2f} s, voiceover {vo:.2f} s'))
    if len(streams) == 2:
        def span(s):
            start = float(s.get('start_time') or 0)
            return start, start + float(s.get('duration') or total)
        (vs, ve), (as_, ae) = span(streams['video']), span(streams['audio'])
        drift = max(abs(vs - as_), abs(ve - ae))
        checks.append(check('audio/video drift', drift < DRIFT_LIMIT, round(drift * 1000, 1), f'< {int(DRIFT_LIMIT * 1000)} ms',
                            f'video {vs:.3f}-{ve:.3f} s, audio {as_:.3f}-{ae:.3f} s'))
        v = streams['video']
        checks.append(check('picture size', int(v.get('width', 0)) >= 1280 and int(v.get('height', 0)) >= 720,
                            f"{v.get('width')}x{v.get('height')}", '>= 1280x720'))
        black = media.black_intervals(ffmpeg, video)
        checks.append(check('no black frames', not black, len(black), 0,
                            ', '.join(f'{a:.1f}-{b:.1f} s' for a, b in black[:5])))
    return verdict('video', checks, seconds=round(total, 3))


def _stamp(seconds: float) -> str:
    seconds = int(seconds)
    return f'{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60}:{seconds % 60:02d}'


def parse_stamp(text: str) -> float:
    parts = [int(p) for p in str(text).strip().split(':')]
    if not 2 <= len(parts) <= 3:
        raise ValueError(text)
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def package_gate(meta: dict, *, video_seconds: float | None = None, thumbnail: Path | None = None) -> dict:
    """meta: {title, description, tags: [...], chapters: [{time: 'm:ss', title}]} (YouTube's limits)."""
    title, description = str(meta.get('title') or ''), str(meta.get('description') or '')
    tags = [str(t) for t in meta.get('tags') or []]
    checks = [check('title length', 1 <= len(title) <= TITLE_MAX and '<' not in title and '>' not in title, len(title),
                    f'1-{TITLE_MAX} characters, no < or >'),
              check('description length', len(description) <= DESCRIPTION_MAX and '<' not in description and '>' not in description,
                    len(description), f'<= {DESCRIPTION_MAX} characters, no < or >'),
              check('tags length', len(','.join(tags)) <= TAGS_MAX, len(','.join(tags)), f'<= {TAGS_MAX} characters')]
    chapters = meta.get('chapters') or []
    checks.append(chapter_check(chapters, video_seconds) if chapters else
                  check('chapters', None, 0, f'>= {CHAPTERS_MIN}, from 0:00, each >= {CHAPTER_SECONDS} s', 'no chapters',
                        required=False))                 # chapters are optional on YouTube; checked when present
    if thumbnail:
        size, dims = Path(thumbnail).stat().st_size, png_size(Path(thumbnail))
        checks.append(check('thumbnail', dims == THUMB_SIZE and size <= THUMB_BYTES, f'{dims[0]}x{dims[1]}, {size // 1024} KB' if dims else 'unreadable',
                            '1280x720, <= 2 MB'))
    else:
        checks.append(check('thumbnail', None, None, '1280x720, <= 2 MB', 'no thumbnail yet', required=False))
    return verdict('package', checks)


def chapter_check(chapters: list[dict], video_seconds: float | None) -> dict:
    try:
        times = [parse_stamp(c['time']) for c in chapters]
    except (KeyError, ValueError, TypeError):
        return check('chapters', False, None, 'm:ss times', 'a chapter time is not m:ss or h:mm:ss')
    problems = []
    if len(times) < CHAPTERS_MIN:
        problems.append(f'{len(times)} chapters, YouTube needs {CHAPTERS_MIN}')
    if times[0] != 0:
        problems.append('the first chapter must start at 0:00')
    ends = times[1:] + ([video_seconds] if video_seconds else [])
    short = [chapters[i]['title'] for i, (a, b) in enumerate(zip(times, ends)) if b - a < CHAPTER_SECONDS]
    if short:
        problems.append(f'shorter than {CHAPTER_SECONDS} s: {", ".join(map(str, short[:3]))}')
    if any(b <= a for a, b in zip(times, times[1:])):
        problems.append('chapters out of order')
    if video_seconds and times[-1] >= video_seconds:
        problems.append('a chapter starts after the video ends')
    return check('chapters', not problems, len(times), f'>= {CHAPTERS_MIN}, from 0:00, each >= {CHAPTER_SECONDS} s', '; '.join(problems))


def png_size(path: Path) -> tuple[int, int] | None:
    """Width/height of a PNG or JPEG without an image library."""
    data = path.read_bytes()[:65536]
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if data[:2] == b'\xff\xd8':
        i = 2
        while i + 9 < len(data):
            marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], 'big')
            if marker in (0xC0, 0xC1, 0xC2):
                return int.from_bytes(data[i + 7:i + 9], 'big'), int.from_bytes(data[i + 5:i + 7], 'big')
            i += 2 + length
    return None


def chapters_text(chapters: list[dict]) -> str:
    return '\n'.join(f"{_stamp(parse_stamp(c['time']))} {c['title']}" for c in chapters)
