#!/usr/bin/env python3
"""Read-only live Gateway preflight, using the installed paired device.

Run inside Open WebUI's interpreter. No pairing, chat.send, repair or reset.
"""
import ast
import asyncio
import base64
import json
from pathlib import Path
import sqlite3
import uuid

import websockets
from cryptography.hazmat.primitives import serialization

SCOPES = ['operator.admin', 'operator.read', 'operator.write']


async def check(database='/app/backend/data/webui.db'):
    with sqlite3.connect('file:' + database + '?mode=ro', uri=True) as db:
        code, raw = db.execute("SELECT content,valves FROM function WHERE id='openclaw_gateway'").fetchone()
    valves = json.loads(raw)
    state = Path(valves['STATE_DIR'])
    identity = json.loads((state / 'identity.json').read_text())
    device = json.loads((state / 'device-token.json').read_text())['deviceToken']
    token = valves['GATEWAY_TOKEN']
    namespace = {'base64': base64, 'serialization': serialization, 'default_backend': lambda: None,
                 'GATEWAY_SCOPES': SCOPES}
    signing = next(n for n in ast.parse(code).body if isinstance(n, ast.FunctionDef) and n.name == '_sign_challenge')
    exec(compile(ast.Module(body=[signing], type_ignores=[]), 'installed_signer', 'exec'), namespace)
    url = valves['GATEWAY_URL']
    if not url.startswith(('ws://', 'wss://')):
        url = 'ws://' + url
    async with websockets.connect(url, max_size=16_777_216, open_timeout=10) as ws:
        async def request(method, params):
            rid = str(uuid.uuid4())
            await ws.send(json.dumps({'type': 'req', 'id': rid, 'method': method, 'params': params}))
            deadline = asyncio.get_running_loop().time() + 15
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                response = json.loads(await asyncio.wait_for(ws.recv(), remaining))
                if response.get('type') == 'res' and response.get('id') == rid:
                    if not response.get('ok'):
                        raise RuntimeError('Gateway preflight read refused: ' + method)
                    return response.get('payload') or {}
        challenge = json.loads(await asyncio.wait_for(ws.recv(), 15))['payload']
        signed = namespace['_sign_challenge'](identity, challenge['nonce'], challenge['ts'], token, SCOPES)
        hello = await request('connect', {'minProtocol': 4, 'maxProtocol': 4,
            'client': {'id': 'webchat', 'version': '1', 'platform': 'linux', 'mode': 'cli'},
            'role': 'operator', 'scopes': SCOPES, 'auth': {'token': token, 'deviceToken': device},
            'device': signed, 'caps': ['tool-events']})
        required = {'progressCard.get', 'chat.history', 'chat.message.get', 'sessions.describe'}
        if not required.issubset(set(hello.get('features', {}).get('methods', []))):
            raise RuntimeError('Gateway lacks release capabilities')
        sessions = (await request('sessions.list', {'limit': 1000})).get('sessions')
        if not isinstance(sessions, list) or len(sessions) >= 1000:
            raise RuntimeError('Could not enumerate all sessions for the idle check')
        prefix = 'agent:' + valves['AGENT_ID'] + ':openwebui-'
        ours = [row['key'] for row in sessions if str(row.get('key', '')).startswith(prefix)]
        for key in ours:
            row = (await request('sessions.describe', {'key': key})).get('session') or {}
            if not isinstance(row.get('hasActiveRun'), bool):
                raise RuntimeError('Gateway did not report a definite run state')
            if row['hasActiveRun']:
                raise RuntimeError('An Open WebUI session is active; update deferred')
        return {'capabilities': 'ok', 'idle_sessions_checked': len(ours)}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(check())))
