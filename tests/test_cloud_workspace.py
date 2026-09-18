import asyncio
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api'))
from api.cloud_workspace import Sessions, CURRENT_WORKSPACE, supervise_cloud
from api.project_package import write_project_package
from api.dataset_fetch.research.store import Store
from api.dataset_fetch.research.contracts import canonical
from fastapi.testclient import TestClient
from main import app


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'VULCAN_CLOUD_MODE': '1', 'VULCAN_CLOUD_ROOT': str(self.root/'cloud')})
        self.env.start()
        app.state.cloud_sessions = Sessions()
        app.state.cloud_leases = {}
        self.a = TestClient(app, base_url='https://vulcan.colony.tech')
        self.b = TestClient(app, base_url='https://vulcan.colony.tech')
        for client in (self.a, self.b):
            response = client.post('/api/session')
            self.assertEqual(response.status_code, 200)
            self.assertIn('HttpOnly', response.headers['set-cookie'])
            self.assertIn('Secure', response.headers['set-cookie'])

    def tearDown(self):
        self.a.close(); self.b.close(); self.env.stop(); self.temp.cleanup()

    def directory(self, client):
        session = app.state.cloud_sessions.lookup(client.cookies.get('vulcan_workspace'))
        return app.state.cloud_sessions.directory(session['id'])

    def project(self, client, name='SameName'):
        p = self.directory(client)/'Projects'/name
        p.mkdir(parents=True)
        (p/'project_metadata.json').write_text(json.dumps({'project_name': name, 'project_id': name}))
        return p

    def test_anonymous_session_isolation_package_and_native_routes(self):
        p = self.project(self.a)
        (p/'example.txt').write_text('private A')
        self.assertEqual(len(self.a.get('/api/projects').json()), 1)
        self.assertEqual(self.b.get('/api/projects').json(), [])
        self.assertEqual(self.b.get('/api/projects/SameName/package').status_code, 404)
        response = self.a.get('/api/projects/SameName/package')
        self.assertEqual(response.status_code, 200, response.text[:300] if response.status_code != 200 else '')
        self.assertIn('no-store', response.headers['cache-control'])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(archive.read('project/example.txt'), b'private A')
            manifest = json.loads(archive.read('package-manifest.json'))
            for name, record in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), record['sha256'])
        self.assertFalse(list((self.directory(self.a)/'acquisition').glob('vulcan-project-*.zip')))
        self.assertEqual(self.a.post('/api/workspace/pick-directory').status_code, 403)
        self.assertEqual(self.a.put('/api/workspace', json={'directory': str(self.root)}).status_code, 403)
        self.assertEqual(self.a.get('/api/workspace').json()['mode'], 'temporary_cloud')

    def test_ledger_and_cache_separation_even_with_identical_project_names(self):
        from api.data import tile_cache_root
        roots = []
        for client in (self.a, self.b):
            self.project(client)
            marker = CURRENT_WORKSPACE.set(self.directory(client))
            try:
                roots.append((Store().root, tile_cache_root()))
            finally:
                CURRENT_WORKSPACE.reset(marker)
        self.assertNotEqual(roots[0][0], roots[1][0])
        self.assertNotEqual(roots[0][1], roots[1][1])

    def test_expiry_denies_old_token_and_cleanup_removes_only_expired_session(self):
        expired = self.directory(self.a)
        self.project(self.a); self.project(self.b)
        sessions = app.state.cloud_sessions
        with sessions.connect() as db:
            db.execute('UPDATE sessions SET expires=? WHERE id=?', (time.time()-1, expired.name))
        self.assertEqual(self.a.get('/api/projects').status_code, 401)
        self.assertEqual(self.a.get('/api/session').status_code, 401)
        async def cleanup():
            task = asyncio.create_task(supervise_cloud(app))
            for _ in range(100):
                if not expired.exists(): break
                await asyncio.sleep(.01)
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        asyncio.run(cleanup())
        self.assertFalse(expired.exists())
        self.assertTrue(self.directory(self.b).exists())

    def test_invalid_and_foreign_sessions_fail_closed(self):
        self.assertEqual(self.a.post('/api/session', headers={'Origin': 'https://evil.example'}).status_code, 403)
        anonymous = TestClient(app, base_url='https://vulcan.colony.tech')
        self.assertEqual(anonymous.get('/api/projects').status_code, 401)
        self.assertEqual(anonymous.get('/api/projects', headers={'Cookie': 'vulcan_workspace=invalid'}).status_code, 401)
        with self.assertRaises(RuntimeError): Store()
        anonymous.close()

    def test_active_jobs_block_incomplete_export(self):
        root = self.project(self.a)
        marker = CURRENT_WORKSPACE.set(self.directory(self.a))
        try:
            store = Store()
            job = {'id': 'job', 'project': 'SameName', 'status': 'running'}
            with store.connect() as db:
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)', ('job','SameName','plan','running',canonical(job).decode(),'key','hash'))
            with self.assertRaisesRegex(ValueError, 'Finish, approve'):
                write_project_package(self.root/'out.zip', root, 'SameName', store)
        finally: CURRENT_WORKSPACE.reset(marker)

    def test_capacity_bounded_and_existing_session_reused(self):
        token = self.a.cookies.get('vulcan_workspace')
        self.assertEqual(self.a.post('/api/session').status_code, 200)
        self.assertEqual(self.a.cookies.get('vulcan_workspace'), token)
        with patch.dict(os.environ, {'VULCAN_MAX_WORKSPACES': '2'}):
            with self.assertRaises(Exception): app.state.cloud_sessions.create()


if __name__ == '__main__': unittest.main()
