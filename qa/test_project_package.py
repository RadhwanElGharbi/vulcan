import io
import json
import zipfile
import hashlib

import numpy as np
import pytest
from qa.test_research_acquisition import setup_job
from api.project_package import write_project_package
from api.dataset_fetch.research import worker
from api.dataset_fetch.research.bundle import extract_bundle, import_directory
from api.dataset_fetch.research.replay import replay
from api.dataset_fetch.research.store import Store


def test_project_download_contains_replayable_published_assets(tmp_path, monkeypatch):
    store, job, project = setup_job(tmp_path, monkeypatch, np.ones((10, 10)))
    worker.execute(store, job['id'])
    destination = tmp_path/'project.zip'
    write_project_package(destination, project, 'test', store)
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read('package-manifest.json'))
        assert manifest['replayable_jobs'] == [job['id']]
        for name, record in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == record['sha256']
        bundle = tmp_path/'replay.zip'
        bundle.write_bytes(archive.read(f"acquisitions/{job['id']}/replay.zip"))
    portable = tmp_path/'portable'
    extract_bundle(bundle, portable)
    fresh_store = Store(tmp_path/'fresh-store')
    identity = import_directory(portable, fresh_store)
    result = replay(identity, tmp_path/'replayed', fresh_store)
    assert result['identical_scientific_content'] and result['identical_validation']


def test_corrupt_published_asset_blocks_complete_download(tmp_path, monkeypatch):
    store, job, project = setup_job(tmp_path, monkeypatch, np.ones((10, 10)))
    worker.execute(store, job['id'])
    job = store.job(job['id'])
    artifact = project/'data/generations'/job['id']/job['outputs'][0]['file']
    artifact.write_bytes(b'corrupted artifact')
    with pytest.raises(ValueError, match='integrity'):
        write_project_package(tmp_path/'bad.zip', project, 'test', store)
