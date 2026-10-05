"""Import a large manual into a THROWAWAY library and report timings and known-question retrieval.

    python tools/manual_probe.py <manual.pdf> <scratch data folder> [version]

Never touches the owner's app library: the data folder given here is created fresh and can be
deleted afterwards. CPU only (pypdf + SQLite FTS5); no model, network or GPU.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.knowledge import Knowledge  # noqa: E402

QUESTIONS = [
    ('ripple trim', 'Edit page trimming'),
    ('timeline frame rate project settings', 'Project settings / timeline frame rate'),
    ('add marker timeline', 'Markers'),
    ('append to timeline media pool', 'Editing clips into a timeline'),
    ('render queue add job', 'Deliver page render queue'),
    ('normalize audio levels', 'Fairlight / audio normalization'),
    ('generate proxy media', 'Proxy media'),
    ('external scripting preferences', 'Scripting access preference'),
    ('Text+ title Fusion', 'Fusion Text+'),
    ('subtitle track import srt', 'Subtitles'),
]


def main():
    pdf, data = Path(sys.argv[1]), Path(sys.argv[2])
    version = sys.argv[3] if len(sys.argv) > 3 else '21.1'
    if data.exists() and any(data.iterdir()):
        raise SystemExit(f'{data} is not empty; give a fresh folder.')
    lib = Knowledge(data)
    started = time.perf_counter()
    try:
        doc = lib.add(pdf, pdf.name, 'probe', scope='project', application='resolve', version=version, background=False)
        took = time.perf_counter() - started
        report = {'status': doc['status'], 'error': doc['error'], 'pages': doc['total'], 'processed': doc['processed'],
                  'passages': doc['chunks'], 'pages_needing_inspection': len(doc['warnings']),
                  'warning_samples': doc['warnings'][:5], 'seconds': round(took, 1),
                  'library_mb': round(sum(p.stat().st_size for p in (data / 'knowledge').rglob('*') if p.is_file()) / 2**20, 1),
                  'questions': []}
        for query, expected in QUESTIONS:
            t = time.perf_counter()
            result = lib.search('probe', query, version='')
            report['questions'].append({'query': query, 'expect': expected, 'ms': round((time.perf_counter() - t) * 1000),
                                        'top': [{'page': m['page'], 'section': m['section'][-120:], 'text': m['text'][:160]}
                                                for m in result['matches'][:3]]})
        print(json.dumps(report, indent=1, ensure_ascii=False))
    finally:
        lib.close()


if __name__ == '__main__':
    main()
