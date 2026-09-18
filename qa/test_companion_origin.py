import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gui-v2/backend'))
from main import app

class CompanionOriginTests(unittest.TestCase):
    def setUp(self): self.client = TestClient(app)
    def tearDown(self): self.client.close()
    def test_public_site_can_connect(self):
        response = self.client.get('/api/health', headers={'Origin':'https://vulcan.colony.tech'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'https://vulcan.colony.tech')
        self.assertEqual(response.json()['companion_protocol'], 1)
    def test_other_sites_cannot_mutate_local_workspace(self):
        response = self.client.post('/api/workspace/pick-directory', headers={'Origin':'https://untrusted.example'})
        self.assertEqual(response.status_code, 403)
    def test_rebinding_host_rejected(self):
        response = self.client.get('/api/health', headers={'Host':'attacker.example'})
        self.assertEqual(response.status_code, 403)
    def test_preflight(self):
        response = self.client.options('/api/workspace', headers={'Origin':'https://vulcan.colony.tech','Access-Control-Request-Method':'PUT','Access-Control-Request-Headers':'content-type','Access-Control-Request-Private-Network':'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-private-network'], 'true')

if __name__ == '__main__': unittest.main()
