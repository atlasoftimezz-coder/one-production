"""Tiny synthetic media for the production tests: a voice-like harmonic signal, mastered audio,
subtitles and short videos, all generated here with numpy + ffmpeg (no owner files)."""
import json
import struct
import subprocess
import wave
import zlib
from pathlib import Path

import numpy as np
import pytest

from app import media

FF = media.find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FF is None, reason='ffmpeg is not installed')


def ff(*args):
    r = subprocess.run([FF, '-y', '-hide_banner', '-v', 'error', *map(str, args)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


def voice(path: Path, f0: float = 120, seconds: float = 8, rate: int = 48000) -> Path:
    """Harmonics + vibrato + syllable envelope: enough like speech for pitch and loudness."""
    t = np.arange(int(seconds * rate)) / rate
    inst = f0 * (1 + 0.02 * np.sin(2 * np.pi * 5 * t))
    phase = 2 * np.pi * np.cumsum(inst) / rate
    sig = sum((0.6 / k) * np.sin(k * phase) for k in range(1, 15)) * (0.5 + 0.5 * np.sin(2 * np.pi * 3.1 * t)) ** 2
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((np.clip(sig * 0.3, -1, 1) * 32767).astype('<i2').tobytes())
    return path


def master(src: Path, dst: Path, lufs: float = -14.0, tp: float = -1.5) -> Path:
    """Two-pass loudnorm, the way the voice pipeline masters."""
    r = subprocess.run([FF, '-hide_banner', '-nostats', '-i', str(src), '-af', f'loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json',
                        '-f', 'null', '-'], capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex('{'):r.stderr.rindex('}') + 1])
    ff('-i', src, '-af', f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
       f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample=48000",
       '-ar', 48000, dst)
    return dst


def srt(path: Path, seconds: float, cues: list[tuple[float, float, str]] | None = None) -> Path:
    if cues is None:
        step = seconds / 4
        cues = [(i * step, (i + 1) * step - 0.05, f'Line {i + 1}') for i in range(4)]
    def stamp(s):
        ms = int(round(s * 1000))
        return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'
    path.write_text('\n\n'.join(f'{i + 1}\n{stamp(a)} --> {stamp(b)}\n{t}' for i, (a, b, t) in enumerate(cues)) + '\n', encoding='utf-8')
    return path


def video(path: Path, audio: Path, seconds: float, *, black_at: float | None = None, audio_delay_ms: int = 0,
          size: str = '1280x720') -> Path:
    """Test pattern + the given audio. black_at: 1 s of black from that time. audio_delay_ms: audio starts late."""
    src = f'testsrc2=size={size}:rate=30:duration={seconds}'
    vf = f'drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable=between(t\\,{black_at}\\,{black_at + 1})' if black_at is not None else 'null'
    args = ['-f', 'lavfi', '-i', src]
    if audio_delay_ms:
        args += ['-itsoffset', audio_delay_ms / 1000]
    args += ['-i', audio, '-vf', vf, '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
             '-c:a', 'aac', '-b:a', '128k', '-t', seconds, path]
    ff(*args)
    return path


def png(path: Path, width: int, height: int) -> Path:
    raw = b''.join(b'\x00' + b'\x80\x40\x20' * width for _ in range(height))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
    return path
