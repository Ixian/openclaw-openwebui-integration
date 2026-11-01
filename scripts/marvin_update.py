#!/usr/bin/env python3
"""Update the matched Functions through the supported API, with paired rollback.

Import apply_release from a host-specific wrapper after an idle Gateway preflight.
Credentials, code and valve exports are never printed. No service controls here.
"""
import hashlib
import json
import os
from pathlib import Path
import time

FILES = {'openclaw_gateway': 'openclaw_pipe.py', 'openclaw_status_action': 'openclaw_status_action.py'}


def sha(content):
    return hashlib.sha256(content.encode()).hexdigest()


def read_pair(api):
    return {fid: {'function': api('/api/v1/functions/id/' + fid),
                  'valves': api('/api/v1/functions/id/' + fid + '/valves')}
            for fid in FILES}


def write_function(api, fid, function, valves):
    path = '/api/v1/functions/id/' + fid
    api(path + '/update', {k: function[k] for k in ('id', 'name', 'content', 'meta')})
    api(path + '/valves/update', valves)
    current = api(path)
    for key, toggle in [('is_active', '/toggle'), ('is_global', '/toggle/global')]:
        if bool(current.get(key)) != bool(function.get(key)):
            api(path + toggle, {})


def verify_pair(api, before, expected):
    actual = read_pair(api)
    for fid, old in before.items():
        current = actual[fid]
        if sha(current['function']['content']) != expected[fid]:
            raise ValueError('Installed artifact checksum mismatch: ' + fid)
        if current['valves'] != old['valves']:
            raise ValueError('Valve preservation failed: ' + fid)
        for key in ('name', 'is_active', 'is_global'):
            if current['function'].get(key) != old['function'].get(key):
                raise ValueError('Function settings changed: ' + fid)
    models = api('/api/models')
    if not any(m['id'].startswith('openclaw_gateway.') for m in models.get('data', [])):
        raise ValueError('Gateway models did not load')


def apply_release(api, release_dir, expected_hashes, backup_dir, idle_check, database_backup):
    """All preflights and a durable backup precede the first API mutation."""
    release_dir, backup_dir = Path(release_dir), Path(backup_dir)
    manifest = json.loads((release_dir / 'release.json').read_text())
    if set(manifest['artifacts']) != set(FILES):
        raise ValueError('Release must contain the matched Pipe and Action')
    contents = {}
    for fid, filename in FILES.items():
        entry = manifest['artifacts'][fid]
        if entry['file'] != filename:
            raise ValueError('Unexpected release filename')
        contents[fid] = (release_dir / filename).read_text()
        if sha(contents[fid]) != entry['sha256']:
            raise ValueError('Release artifact checksum mismatch')
        # A release marker is checked with the hash, never accepted in isolation.
        if 'MARVIN_RELEASE = ' + json.dumps(manifest['release']) not in contents[fid]:
            raise ValueError('Mixed bridge releases')
        compile(contents[fid], filename, 'exec')
    before = read_pair(api)
    old_hashes = {fid: sha(row['function']['content']) for fid, row in before.items()}
    if old_hashes != expected_hashes:
        raise ValueError('Installed Functions differ from the deployment lock')
    idle_check()
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(backup_dir, 0o700)
    backup = {'created_at': int(time.time()), 'before': before, 'release': manifest, 'hashes': old_hashes}
    target = backup_dir / 'functions.json'
    with open(target, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
        json.dump(backup, handle)
        handle.flush()
        os.fsync(handle.fileno())
    database_backup(backup_dir / 'webui.db')
    idle_check()
    try:
        for fid in FILES:
            row = before[fid]
            write_function(api, fid, {**row['function'], 'content': contents[fid]}, row['valves'])
        verify_pair(api, before, {fid: manifest['artifacts'][fid]['sha256'] for fid in FILES})
    except BaseException as original:
        failures = []
        for fid, row in before.items():
            try:
                write_function(api, fid, row['function'], row['valves'])
            except Exception:
                failures.append(fid)
        if not failures:
            verify_pair(api, before, old_hashes)
        if failures:
            raise RuntimeError('Paired rollback requires recovery: ' + ', '.join(failures)) from None
        raise RuntimeError('Update failed; both Functions restored from backup') from original
    return {'release': manifest['release'], 'commit': manifest['commit'],
            'backup': str(backup_dir), 'hashes': {fid: manifest['artifacts'][fid]['sha256'] for fid in FILES}}
