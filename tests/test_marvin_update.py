"""A failed update restores both old artifacts and their exact configuration."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('marvin_update', Path(__file__).parents[1] / 'scripts/marvin_update.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class UpdateTests(unittest.TestCase):
    def test_checksum_preflight_failure_writes_nothing(self):
        self.run_update('checksum')

    def test_busy_preflight_failure_writes_nothing(self):
        self.run_update('busy')

    def test_second_function_failure_restores_pair(self):
        self.run_update('second')

    def test_success_preserves_all_settings(self):
        self.run_update(None)

    def run_update(self, failure):
        rows = {fid: {'function': {'id': fid, 'name': fid, 'meta': {}, 'content': 'old',
                                  'is_active': True, 'is_global': False}, 'valves': {'GATEWAY_TOKEN': 'fixture'}}
                for fid in u.FILES}
        original = copy.deepcopy(rows)
        writes = []
        failed = False

        def api(path, body=None):
            nonlocal failed
            if path == '/api/models':
                return {'data': [{'id': 'openclaw_gateway.default'}]}
            fid = path.split('/')[5]
            if body is not None:
                if failure == 'second' and fid == 'openclaw_status_action' and not failed:
                    failed = True
                    raise ValueError('test failure')
                writes.append(path)
                if path.endswith('/update') and '/valves/' not in path:
                    rows[fid]['function'].update(body)
                elif path.endswith('/valves/update'):
                    rows[fid]['valves'] = copy.deepcopy(body)
            return copy.deepcopy(rows[fid]['valves' if path.endswith('/valves') else 'function'])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = {}
            for fid, filename in u.FILES.items():
                code = 'MARVIN_RELEASE = "marvin-v1.0.0"\n'
                (root / filename).write_text(code)
                artifacts[fid] = {'file': filename, 'sha256': u.sha(code)}
            (root / 'release.json').write_text(json.dumps({'release': 'marvin-v1.0.0', 'commit': 'abc', 'artifacts': artifacts}))
            if failure == 'checksum':
                (root / 'openclaw_pipe.py').write_text('tampered')

            def idle():
                if failure == 'busy':
                    raise ValueError('busy')
            args = (api, root, {fid: u.sha('old') for fid in u.FILES}, root / 'backup', idle,
                    lambda path: path.write_bytes(b'fixture'))
            if failure:
                with self.assertRaises((ValueError, RuntimeError)):
                    u.apply_release(*args)
                self.assertEqual(rows, original)
                if failure in ('checksum', 'busy'):
                    self.assertEqual(writes, [])
            else:
                result = u.apply_release(*args)
                self.assertEqual(result['release'], 'marvin-v1.0.0')
                self.assertTrue((root / 'backup/functions.json').is_file())
                for fid in rows:
                    self.assertEqual(rows[fid]['valves'], original[fid]['valves'])
                    self.assertEqual(rows[fid]['function']['is_global'], False)
