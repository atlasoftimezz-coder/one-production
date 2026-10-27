"""Read-only account-location diagnostics. Prints labels/flags, never credentials or hints."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def credential_names():
    """Limit the Windows query to One Production's Claude-token entries; no blobs are printed."""
    try:
        import win32cred
        import pywintypes
    except ImportError:
        print('Credential metadata lookup: Windows API unavailable')
        return
    names = set()
    for prefix in ('OneProduction', 'claude-token:*'):
        try:
            entries = win32cred.CredEnumerate(prefix, 0)
        except pywintypes.error as exc:
            if exc.winerror == 1168:
                continue
            print('Credential metadata lookup unavailable:', exc.winerror)
            return
        for entry in entries:
            target, username = entry.get('TargetName', ''), entry.get('UserName', '')
            if username.startswith('claude-token:') and (target == 'OneProduction' or target.endswith('@OneProduction')):
                names.add(username.removeprefix('claude-token:'))
    print('Saved One Production Claude-token account IDs:', json.dumps(sorted(names)))


def health(port):
    try:
        result = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1, trust_env=False)
        data = result.json()
        return port, {k: data[k] for k in ('status', 'version', 'data_dir') if k in data}
    except Exception:
        return None


def main():
    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = [x for x in pool.map(health, range(8730, 8740)) if x]
    print('Running app instances:', json.dumps(instances))
    roots = {Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'OneProduction',
             Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming') / 'OneProduction'}
    roots.update(Path(data['data_dir']) for _, data in instances if data.get('data_dir'))
    settings = Path(os.environ.get('ONEPROD_SETTINGS_FILE') or Path.home() / 'AppData/Roaming/OneProduction/settings.json')
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding='utf-8'))
            if data.get('data_dir'):
                roots.add(Path(data['data_dir']))
        except (OSError, ValueError):
            pass
    if os.environ.get('ONEPROD_DATA_DIR'):
        roots.add(Path(os.environ['ONEPROD_DATA_DIR']))
    for root in sorted(roots, key=str):
        file = root / 'accounts.json'
        try:
            items = json.loads(file.read_text(encoding='utf-8'))
            rows = [{k: a.get(k) for k in ('id', 'provider', 'label', 'enabled', 'status')} for a in items]
            print('Account registry:', file, json.dumps(rows))
        except FileNotFoundError:
            print('No account registry:', file)
        except (OSError, ValueError) as exc:
            print('Cannot inspect account registry:', file, type(exc).__name__)
    if '--credential-names' in sys.argv:
        credential_names()


if __name__ == '__main__':
    main()
