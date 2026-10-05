"""Packaging (P10): the finished video + everything the upload needs, in one folder.

package.json (project files), written by the packaging worker:
    {"name": "ep01", "video": "episode.mp4", "subtitles": "vo.srt", "script": "scripts/ep01.md",
     "title": "...", "title_options": ["...", "..."], "description": "...", "tags": ["...", "..."],
     "thumbnail_svg": "art/thumb.svg"   (or "thumbnail": "thumb.png"),
     "chapters": [{"time": "0:00", "title": "..."}]   (optional: otherwise from the script's headings)}

Chapters come from the script's sections: each starts when the section's first words are spoken
(the voiceover's subtitles). Sections under 10 s merge into the one before, as YouTube requires.
The bundle goes to episode/<name>/ and is checked by the package gate.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import gates, media
from .animation import anchor_times, check_svg, find_browser, rasterise, words
from .editor import EditError
from .scripts import read_script, sections
from .storage import NotFound, safe_filename


def chapters_from(script_text: str, cues: list[dict], video_seconds: float) -> list[dict]:
    secs = sections(script_text)
    if not secs:
        return []
    anchors = [None] + [' '.join(words(s['body'])[:6]) for s in secs[1:]]
    starts, missing = anchor_times(anchors, cues)
    out = []
    for s, start in zip(secs, starts):
        if start is None:
            continue                                   # a section whose opening words weren't heard stays merged
        title = s['title'] or ('Intro' if not out else '')
        if not title:
            continue
        if out and (start - out[-1]['seconds'] < gates.CHAPTER_SECONDS):
            continue                                   # too short: merged into the chapter before
        out.append({'seconds': start, 'title': title[:80]})
    while out and video_seconds - out[-1]['seconds'] < gates.CHAPTER_SECONDS:
        out.pop()
    if len(out) < gates.CHAPTERS_MIN:
        return []
    out[0]['seconds'] = 0.0
    return [{'time': gates._stamp(c['seconds']), 'title': c['title']} for c in out]


class Packaging:
    def __init__(self, store, data_dir: Path, gate_reports):
        self.store, self.data_dir, self.gate_reports = store, data_dir, gate_reports

    def file_of(self, pid: str):
        def find(name: str) -> Path:
            try:
                return self.store.file_path(pid, name)
            except NotFound:
                raise EditError(f'{name} is not a file in this project.') from None
        return find

    def build(self, pid: str, spec_name: str) -> dict:
        file_of = self.file_of(pid)
        try:
            spec = json.loads(file_of(spec_name).read_text(encoding='utf-8-sig'))
        except ValueError as exc:
            raise EditError(f'{spec_name} is not valid JSON: {exc}') from None
        ffmpeg = media.find_ffmpeg(self.data_dir)
        if not ffmpeg:
            raise EditError('ffmpeg was not found. Install it from Setup (free) or put it on PATH.')
        video = file_of(str(spec.get('video') or ''))
        seconds = media.duration(ffmpeg, video)
        srt = file_of(str(spec['subtitles'])) if spec.get('subtitles') else None
        chapters = spec.get('chapters')
        if chapters is None and spec.get('script') and srt:
            text = read_script(file_of(str(spec['script'])))
            chapters = chapters_from(text, media.srt_cues(srt.read_text(encoding='utf-8-sig', errors='replace')), seconds)
        chapters = chapters or []
        description = str(spec.get('description') or '').strip()
        if chapters:
            description += '\n\nChapters\n' + gates.chapters_text(chapters)
        name = safe_filename(str(spec.get('name') or Path(spec_name).stem))[:60] or 'episode'
        root = self.store.files_dir(pid) / 'episode'
        folder, n = root / name, 1
        while folder.exists():                         # never overwrite an earlier bundle
            n += 1
            folder = root / f'{name} ({n})'
        folder.mkdir(parents=True)
        thumb = None
        if spec.get('thumbnail_svg'):
            svg = file_of(str(spec['thumbnail_svg']))
            check_svg(svg)
            browser = find_browser()
            if not browser:
                raise EditError('Drawing the thumbnail needs Chrome or Edge on this PC.')
            thumb = rasterise(browser, [svg], folder, '1280x720')[0].rename(folder / 'thumbnail.png')
        elif spec.get('thumbnail'):
            src = file_of(str(spec['thumbnail']))
            thumb = folder / ('thumbnail' + src.suffix.lower())
            shutil.copyfile(src, thumb)
        target = folder / ('video' + video.suffix.lower())
        try:
            os.link(video, target)                     # same drive: no second copy of a large file
        except OSError:
            shutil.copyfile(video, target)
        if srt:
            shutil.copyfile(srt, folder / 'subtitles.srt')
        meta = {'title': str(spec.get('title') or '').strip(), 'title_options': [str(t) for t in spec.get('title_options') or []][:10],
                'description': description, 'tags': [str(t).strip() for t in spec.get('tags') or [] if str(t).strip()],
                'chapters': chapters, 'seconds': round(seconds, 3)}
        (folder / 'metadata.json').write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding='utf-8')
        (folder / 'description.txt').write_text(description + '\n', encoding='utf-8')
        if meta['title_options']:
            (folder / 'title-options.txt').write_text('\n'.join(meta['title_options']) + '\n', encoding='utf-8')
        report = gates.package_gate(meta, video_seconds=seconds, thumbnail=thumb)
        report = self.gate_reports.save(pid, report, {'package': spec_name, 'folder': folder.name})
        (folder / 'package-gate.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
        rel = folder.relative_to(self.store.files_dir(pid)).as_posix()
        return {'folder': rel, 'files': sorted(p.name for p in folder.iterdir()), 'chapters': chapters,
                'gate': {k: report[k] for k in ('passed', 'summary', 'report')}}
