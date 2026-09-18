import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1] / 'gui-v2/backend'
sys.path.insert(0, str(BACKEND))
from api import project_utils as projects
from fastapi.testclient import TestClient
from main import app


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.original = self.base / 'original'; self.original.mkdir()
        self.selected = self.base / 'selected'; self.selected.mkdir()
        self.settings = self.base / 'settings.json'
        self.env = patch.dict(os.environ, {'AGRS_PROJECTS_ROOT': str(self.original), 'ZEUS_WORKSPACE_SETTINGS': str(self.settings)})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.env.stop(); self.temp.cleanup()

    def project(self, root, name):
        path = root / name; path.mkdir()
        (path / 'project_metadata.json').write_text(json.dumps({'project_name': name, 'project_id': name, 'status': 'active'}))
        return path

    def test_saved_roots_visible_to_new_worker_process(self):
        old = self.project(self.original, 'Old')
        new = self.project(self.selected, 'Imported')
        response = self.client.put('/api/workspace', json={'directory': str(self.selected)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(projects.get_projects_root(), self.selected)
        self.assertEqual(projects.discover_project_paths(), {'Old': old, 'Imported': new})
        output = subprocess.check_output([sys.executable, '-c',
            'import json; from api.project_utils import get_projects_root,resolve_project_path; print(json.dumps([str(get_projects_root()),str(resolve_project_path("Old"))]))'], cwd=BACKEND, text=True)
        self.assertEqual(json.loads(output), [str(self.selected), str(old)])

    def test_duplicate_names_rejected_without_changing_settings(self):
        self.project(self.original, 'Same'); self.project(self.selected, 'Same')
        response = self.client.put('/api/workspace', json={'directory': str(self.selected)})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Two projects', response.json()['detail'])
        self.assertFalse(self.settings.exists())

    def test_invalid_paths_and_project_directory_rejected(self):
        project = self.project(self.selected, 'Individual')
        for path in ['relative', str(self.base/'missing'), str(project)]:
            with self.subTest(path=path):
                self.assertEqual(self.client.put('/api/workspace', json={'directory': path}).status_code, 400)
        self.assertEqual(projects.get_projects_root(), self.original)

    def test_browse_and_nested_discovery(self):
        grouping = self.selected / 'Group'; grouping.mkdir()
        project = self.project(grouping, 'Nested')
        nested = project / 'data'; nested.mkdir()
        self.project(nested, 'ProviderMetadata')
        response = self.client.get('/api/workspace/directories', params={'path': str(self.selected)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['folders'], [{'name':'Group','path':str(grouping)}])
        projects.set_projects_directory(str(self.selected))
        self.assertEqual(projects.discover_project_paths(), {'Nested': project})

    def test_corrupt_settings_do_not_silently_change_destination(self):
        self.settings.write_text('{broken')
        with self.assertRaises(ValueError): projects.get_projects_root()

    def test_persistence_failure_keeps_previous_destination(self):
        with patch('api.project_utils.os.replace', side_effect=OSError('Disk unavailable')):
            response = self.client.put('/api/workspace', json={'directory': str(self.selected)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(projects.get_projects_root(), self.original)
        self.assertFalse(self.settings.exists())

    def test_native_picker_select_and_cancel(self):
        with patch('api.native_folder_picker.pick_directory', return_value=str(self.selected)) as picker:
            response = self.client.post('/api/workspace/pick-directory')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'directory': str(self.selected), 'cancelled': False})
        picker.assert_called_once_with(self.original)
        # Selection does not bypass the existing validated save endpoint.
        self.assertEqual(projects.get_projects_root(), self.original)
        with patch('api.native_folder_picker.pick_directory', return_value=None):
            response = self.client.post('/api/workspace/pick-directory')
        self.assertEqual(response.json(), {'directory': None, 'cancelled': True})
        self.assertEqual(projects.get_projects_root(), self.original)

    def test_create_retains_standard_layout_and_is_readable(self):
        projects.set_projects_directory(str(self.selected))
        aoi = {'type':'Polygon','coordinates':[[[-80.55,43.47],[-80.549,43.47],[-80.549,43.471],[-80.55,43.471],[-80.55,43.47]]]}
        aoi = {'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':aoi}]}
        form = {'project_name':'Saved-Here','drawn_geojson':json.dumps(aoi),'workspace_directory':str(self.selected)}
        with patch('api.projects._aoi_countries_admin0', return_value=['CAN']):
            response = self.client.post('/api/projects/create', data=form)
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.selected/'Saved-Here'
        self.assertEqual(Path(response.json()['project_path']), saved)
        for relative in ['project_metadata.json','aoi/aoi.geojson','aoi/project_aoi.json','data/rasters/raw','data/rasters/processed','data/vectors/processed','logs']:
            self.assertTrue((saved/relative).exists(), relative)
        self.assertEqual(projects.resolve_project_path('Saved-Here'), saved)
        self.assertEqual(self.client.get('/api/projects/Saved-Here/metadata').status_code, 200)
        self.assertEqual(self.client.get('/api/projects/Saved-Here/datasets').status_code, 200)
        projects.set_projects_directory(str(self.original))
        self.assertEqual(projects.resolve_project_path('Saved-Here'), saved)
        stale = self.client.post('/api/projects/create', data={**form,'project_name':'Wrong-Directory'})
        self.assertEqual(stale.status_code, 409)
        self.assertFalse((self.original/'Wrong-Directory').exists())


if __name__ == '__main__': unittest.main()
