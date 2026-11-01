#!/usr/bin/env python3
"""Create a reproducible pair of Functions and its source manifest. No image build."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

FILES = {'openclaw_gateway': 'openclaw_pipe.py', 'openclaw_status_action': 'openclaw_status_action.py'}
UPSTREAM_BASE = 'ec8b8ba51be564b0ca29981fef41eaa4f43d8a68'
IMAGE = 'ghcr.io/open-webui/open-webui:v0.12.5-slim@sha256:9a4db3e6c8d6174d4ed8952304115e90dcc36e9a8b98c2f8984eab321f29505e'


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def package(root, destination):
    subprocess.run(['python3', 'build.py', '--check-all'], cwd=root, check=True)
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip():
        raise ValueError('Commit all changes before packaging a release')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    destination.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    releases = set()
    for fid, filename in FILES.items():
        content = (root / filename).read_text()
        tree = ast.parse(content)
        value = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'MARVIN_RELEASE' for t in n.targets))
        releases.add(value)
        (destination / filename).write_text(content)
        artifacts[fid] = {'file': filename, 'sha256': digest(content)}
    if len(releases) != 1:
        raise ValueError('Pipe and Action versions differ')
    manifest = {'release': releases.pop(), 'repository': 'https://github.com/Ixian/openclaw-openwebui-integration',
                'commit': commit, 'upstream_base': UPSTREAM_BASE, 'artifacts': artifacts,
                'tested': {'openwebui': '0.12.5', 'openclaw': '2026.10.9', 'python': '3.12',
                           'websockets': '17.3', 'image': IMAGE}}
    (destination / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], args.out), indent=2))
