"""ffmpeg editor (P10, the default video editor): an edit list in the project -> a finished mp4.

edit.json (all paths are project files):
    {"voiceover": "vo.wav", "size": "1920x1080", "fps": 30, "output": "episode.mp4",
     "visuals": [{"file": "art/01.png", "seconds": 3.5, "motion": "push"},
                 {"file": "clips/river.mp4", "seconds": 4, "from": 12.0},
                 {"card": {"text": "1986", "style": "date"}, "seconds": 2}],
     "music": {"file": "bed.mp3", "db": -22, "duck": true},
     "subtitles": {"file": "vo.srt", "burn": false}}

Visuals play back to back with hard cuts (never a fade to black) and are fitted to the voiceover to
the frame: the last one absorbs the difference when the list is within FIT_SLACK seconds of it.
Rendering is CPU work for minutes, so it goes through the heavy-job approval (tools.TOOLS).
Every render ends with the video gate; its report sits beside the gates' other reports.
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import gates, media
from .storage import NotFound, safe_filename

AUDIO = {'.wav', '.mp3', '.m4a', '.aac', '.flac'}
STILLS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
CLIPS = {'.mp4', '.mov', '.mkv', '.webm', '.avi'}
SIZES = {'1920x1080', '1280x720', '3840x2160'}
RATES = {24, 25, 30, 50, 60}
MOTIONS = {'none', 'push', 'pull', 'pan_left', 'pan_right'}
# title cards are dark navy, not near-black: the video gate (rightly) flags near-black frames
CARD_STYLES = {'title': ('0x1B2A4A', 'white', None), 'date': ('white', 'black', '0xFFD400'), 'label': ('white', '0xD62828', None)}
FIT_SLACK = 2.0
FONT_DIRS = (Path(r'C:\Windows\Fonts'),)
FONTS = ('arialbd.ttf', 'segoeuib.ttf', 'bahnschrift.ttf', 'arial.ttf')


class EditError(ValueError):
    pass


def font() -> Path:
    for folder in FONT_DIRS:
        for name in FONTS:
            if (folder / name).is_file():
                return folder / name
    raise EditError('No usable font for text cards (Arial or Segoe UI) was found.')


def plan(edit: dict, file_of, vo_seconds: float) -> dict:
    """Validate an edit list and work out the exact frame count of every visual."""
    if not isinstance(edit, dict):
        raise EditError('The edit list must be a JSON object.')
    size = str(edit.get('size') or '1920x1080')
    fps = int(edit.get('fps') or 30)
    if size not in SIZES:
        raise EditError(f'size must be one of {", ".join(sorted(SIZES))}.')
    if fps not in RATES:
        raise EditError(f'fps must be one of {", ".join(map(str, sorted(RATES)))}.')
    visuals = edit.get('visuals')
    if not isinstance(visuals, list) or not visuals:
        raise EditError('visuals must list at least one picture, clip or card.')
    if len(visuals) > 3000:
        raise EditError('At most 3000 visuals per edit.')
    out, total = [], 0.0
    for n, v in enumerate(visuals, 1):
        if not isinstance(v, dict):
            raise EditError(f'Visual {n} must be an object.')
        try:
            seconds = float(v.get('seconds'))
        except (TypeError, ValueError):
            raise EditError(f'Visual {n} needs seconds.') from None
        if not 0.2 <= seconds <= 600:
            raise EditError(f'Visual {n}: seconds must be between 0.2 and 600.')
        if ('file' in v) == ('card' in v):
            raise EditError(f'Visual {n} needs either a file or a card.')
        if 'card' in v:
            card = v['card'] if isinstance(v['card'], dict) else {}
            text, style = str(card.get('text') or '').strip(), str(card.get('style') or 'title')
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            text = chr(10).join(lines)
            if not text or len(text) > 160 or len(lines) > 4:
                raise EditError(f'Visual {n}: card text must be 1-160 characters on at most 4 lines.')
            if style not in CARD_STYLES:
                raise EditError(f'Visual {n}: card style must be one of {", ".join(CARD_STYLES)}.')
            out.append({'kind': 'card', 'text': text, 'style': style, 'seconds': seconds})
        else:
            path = file_of(str(v['file']))
            suffix = path.suffix.lower()
            if suffix in STILLS:
                motion = str(v.get('motion') or 'push')
                if motion not in MOTIONS:
                    raise EditError(f'Visual {n}: motion must be one of {", ".join(sorted(MOTIONS))}.')
                out.append({'kind': 'still', 'file': str(v['file']), 'path': str(path), 'motion': motion, 'seconds': seconds})
            elif suffix in CLIPS:
                start = float(v.get('from') or 0)
                if start < 0:
                    raise EditError(f'Visual {n}: from must be 0 or more.')
                out.append({'kind': 'clip', 'file': str(v['file']), 'path': str(path), 'from': start, 'seconds': seconds})
            else:
                raise EditError(f'Visual {n}: {v["file"]} is not a picture or video clip.')
        total += seconds
    if abs(total - vo_seconds) > FIT_SLACK:
        raise EditError(f'The visuals last {total:.1f} s but the voiceover is {vo_seconds:.1f} s; they must be within {FIT_SLACK:.0f} s.')
    frames_total, cumulative, previous = round(vo_seconds * fps), 0.0, 0
    for i, item in enumerate(out):
        cumulative += item['seconds']
        boundary = frames_total if i == len(out) - 1 else min(round(cumulative * fps), frames_total - (len(out) - 1 - i))
        item['frames'] = boundary - previous
        if item['frames'] < 1:
            raise EditError(f'Visual {i + 1} ends up shorter than one frame after fitting to the voiceover.')
        previous = boundary
    result = {'size': size, 'fps': fps, 'visuals': out, 'frames': frames_total, 'seconds': vo_seconds,
              'fitted_by': round(vo_seconds - total, 3)}
    music = edit.get('music')
    if music:
        db = float(music.get('db', -22))
        if not -40 <= db <= -6:
            raise EditError('music db must be between -40 and -6.')
        path = file_of(str(music.get('file') or ''))
        if path.suffix.lower() not in AUDIO:
            raise EditError('music must be an audio file.')
        result['music'] = {'file': str(music['file']), 'path': str(path), 'db': db, 'duck': bool(music.get('duck', True))}
    subs = edit.get('subtitles')
    if subs and subs.get('burn'):
        path = file_of(str(subs.get('file') or ''))
        if path.suffix.lower() != '.srt':
            raise EditError('subtitles must be an .srt file.')
        result['subtitles'] = {'file': str(subs['file']), 'path': str(path)}
    return result


def segment_args(item: dict, w: int, h: int, fps: int, i: int) -> tuple[list[str], str]:
    """(ffmpeg input args, filter) for one visual; the run's cwd is the work folder."""
    n = item['frames']
    if item['kind'] == 'card':
        bg, colour, box = CARD_STYLES[item['style']]
        size, lines = h // 9, item['text'].splitlines()
        step = int(size * 1.35)                          # one centred drawtext per line (text files, no escaping)
        top = (h - step * len(lines)) // 2
        draw = ','.join(f"drawtext=fontfile=card.ttf:textfile=card{i}_{k}.txt:fontcolor={colour}:fontsize={size}"
                        f":x=(w-text_w)/2:y={top + k * step}" + (f":box=1:boxcolor={box}:boxborderw={h // 30}" if box else '')
                        for k in range(len(lines)))
        return ['-f', 'lavfi', '-i', f'color=c={bg}:s={w}x{h}:r={fps}'], draw
    if item['kind'] == 'still':
        z = {'none': '1', 'push': f'1+0.12*on/{n}', 'pull': f'1.12-0.12*on/{n}'}.get(item['motion'], '1.12')
        x = {'pan_right': f'(iw-iw/zoom)*on/{n}', 'pan_left': f'(iw-iw/zoom)*(1-on/{n})'}.get(item['motion'], 'iw/2-(iw/zoom/2)')
        return ['-i', item['path']], (f"scale={2 * w}:{2 * h}:force_original_aspect_ratio=increase,crop={2 * w}:{2 * h},"
                                      f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps={fps}")
    return (['-ss', str(item['from']), '-i', item['path']],
            f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"tpad=stop_mode=clone:stop_duration={item['seconds'] + 1}")


def render(ffmpeg: str, p: dict, voiceover: Path, work: Path, out: Path, progress=lambda share, step: None,
           cancelled=lambda: False) -> None:
    w, h = map(int, p['size'].split('x'))
    fps = p['fps']
    work.mkdir(parents=True, exist_ok=True)
    if any(v['kind'] == 'card' for v in p['visuals']):
        shutil.copyfile(font(), work / 'card.ttf')
    names = []
    for i, item in enumerate(p['visuals']):
        if cancelled():
            raise EditError('Render cancelled.')
        progress(0.85 * i / len(p['visuals']), f'visual {i + 1} of {len(p["visuals"])}')
        if item['kind'] == 'card':
            for k, line in enumerate(item['text'].splitlines()):
                (work / f'card{i}_{k}.txt').write_text(line, encoding='utf-8')
        inputs, vf = segment_args(item, w, h, fps, i)
        name = f'seg{i:05d}.mp4'
        r = media.run([ffmpeg, '-y', '-hide_banner', '-v', 'error', *inputs, '-vf', f'{vf},format=yuv420p,setsar=1',
                       '-frames:v', str(item['frames']), '-r', str(fps), '-an', '-c:v', 'libx264', '-preset', 'veryfast',
                       '-crf', '20', '-g', str(fps * 2), '-video_track_timescale', str(fps * 1000), name], cwd=work)
        if r.returncode:
            raise EditError(f'Visual {i + 1} ({item.get("file") or item["text"][:30]}) failed: {r.stderr.strip()[-300:]}')
        names.append(name)
    (work / 'list.txt').write_text(''.join(f"file '{n}'\n" for n in names), encoding='utf-8')
    audio = voiceover
    if p.get('music'):
        progress(0.88, 'mixing the music under the voice')
        m = p['music']
        duck = (f"[0:a]aresample=48000,asplit=2[v][sc];[m][sc]sidechaincompress=threshold=0.02:ratio=10:attack=20:release=500[d];"
                f"[v][d]amix=inputs=2:duration=first:normalize=0[a]") if m['duck'] else "[0:a]aresample=48000[v];[v][m]amix=inputs=2:duration=first:normalize=0[a]"
        r = media.run([ffmpeg, '-y', '-hide_banner', '-v', 'error', '-i', str(voiceover), '-stream_loop', '-1', '-i', m['path'],
                       '-filter_complex', f"[1:a]aresample=48000,volume={m['db']}dB[m];{duck}", '-map', '[a]',
                       '-t', f"{p['seconds']:.3f}", 'mix.wav'], cwd=work)
        if r.returncode:
            raise EditError(f'Mixing the music failed: {r.stderr.strip()[-300:]}')
        audio = media.master(ffmpeg, work / 'mix.wav', work / 'mix-mastered.wav')
    progress(0.92, 'joining the visuals with the sound')
    video = ['-c:v', 'copy']
    if p.get('subtitles'):
        shutil.copyfile(p['subtitles']['path'], work / 'subs.srt')
        video = ['-vf', "subtitles=subs.srt:force_style='FontName=Arial,FontSize=20,Outline=2,MarginV=40'",
                 '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p']
    tmp = work / 'final.mp4'
    r = media.run([ffmpeg, '-y', '-hide_banner', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', 'list.txt', '-i', str(audio),
                   '-map', '0:v', '-map', '1:a', *video, '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
                   '-movflags', '+faststart', tmp.name], cwd=work)
    if r.returncode:
        raise EditError(f'Joining failed: {r.stderr.strip()[-300:]}')
    shutil.move(str(tmp), out)
    progress(1.0, 'done')


class Editor:
    """Render jobs (one at a time per project), each ending with the video gate."""

    def __init__(self, store, data_dir: Path, gate_reports):
        self.store, self.data_dir, self.gate_reports = store, data_dir, gate_reports
        self.jobs: dict[str, dict] = {}
        self.cancel_flags: set[str] = set()
        self.lock = threading.Lock()

    def ffmpeg(self) -> str:
        found = media.find_ffmpeg(self.data_dir)
        if not found:
            raise EditError('ffmpeg was not found. Install it from Setup (free) or put it on PATH.')
        return found

    def file_of(self, pid: str):
        def find(name: str) -> Path:
            try:
                return self.store.file_path(pid, name)
            except NotFound:
                raise EditError(f'{name} is not a file in this project.') from None
        return find

    def prepare(self, pid: str, edit_name: str) -> tuple[dict, dict, Path]:
        file_of = self.file_of(pid)
        source = file_of(edit_name)
        try:
            edit = json.loads(source.read_text(encoding='utf-8-sig'))
        except ValueError as exc:
            raise EditError(f'{edit_name} is not valid JSON: {exc}') from None
        voiceover = file_of(str(edit.get('voiceover') or ''))
        if voiceover.suffix.lower() not in AUDIO:
            raise EditError('voiceover must be an audio file.')
        p = plan(edit, file_of, media.duration(self.ffmpeg(), voiceover))
        return edit, p, voiceover

    def start(self, pid: str, edit_name: str) -> dict:
        edit, p, voiceover = self.prepare(pid, edit_name)
        with self.lock:
            if any(j['project'] == pid and j['status'] == 'rendering' for j in self.jobs.values()):
                raise EditError('A render is already running in this project.')
            out_name = safe_filename(str(edit.get('output') or Path(edit_name).stem + '.mp4'))
            if not out_name.lower().endswith('.mp4'):
                out_name += '.mp4'
            job = {'id': secrets.token_hex(6), 'project': pid, 'edit': edit_name, 'status': 'rendering', 'progress': 0.0,
                   'step': 'starting', 'output': '', 'error': '', 'gate': None, 'started': time.time(), 'finished': None,
                   'visuals': len(p['visuals']), 'seconds': p['seconds'], 'fitted_by': p['fitted_by']}
            self.jobs[job['id']] = job
        threading.Thread(target=self._run, args=(job, p, voiceover, out_name), daemon=True).start()
        return dict(job)

    def _run(self, job, p, voiceover, out_name):
        pid, work = job['project'], self.data_dir / 'cache' / 'render' / job['id']
        try:
            ff = self.ffmpeg()
            out = self.store.unique_path(pid, out_name)
            render(ff, p, voiceover, work, out, progress=lambda share, step: job.update(progress=round(share, 3), step=step),
                   cancelled=lambda: job['id'] in self.cancel_flags)
            job.update(output=out.name, step='checking the video')
            report = gates.video_gate(ff, out, voiceover)
            job['gate'] = self.gate_reports.save(pid, report, {'video': out.name, 'voiceover': voiceover.name, 'edit': job['edit']})
            job.update(status='done', step='done' if report['passed'] else 'rendered, but the video gate failed')
        except Exception as exc:                         # noqa: BLE001 - shown to the user
            job.update(status='cancelled' if job['id'] in self.cancel_flags else 'failed', error=str(exc)[:500], step='stopped')
        finally:
            job['finished'] = time.time()
            shutil.rmtree(work, ignore_errors=True)

    def get(self, pid: str, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if not job or job['project'] != pid:
            raise EditError('No such render in this project.')
        return dict(job)

    def list(self, pid: str) -> list[dict]:
        return sorted((dict(j) for j in self.jobs.values() if j['project'] == pid), key=lambda j: j['started'], reverse=True)

    def cancel(self, pid: str, job_id: str) -> dict:
        self.get(pid, job_id)
        self.cancel_flags.add(job_id)
        return self.get(pid, job_id)
