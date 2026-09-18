"""Portable, integrity-checked replay bundles. Import never runs bundled code."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from .contracts import canonical, digest, file_hash


def write_bundle(path, plan, job, report, store, base):
    inventory = {}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        def content(name, value):
            raw = value if isinstance(value, bytes) else canonical(value)
            archive.writestr(name, raw)
            import hashlib
            inventory[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
        def add(name, source):
            inventory[name] = {"sha256": file_hash(source), "size": source.stat().st_size}
            archive.write(source, name)
        content("plan.json", plan)
        content("job.json", job)
        content("report.json", report)
        hashes = {r["sha256"] for rs in job.get("receipts", {}).values() for r in rs}
        hashes.update(d["receipt"]["sha256"] for s in plan["selections"] for d in s["discovery"] if "receipt" in d)
        for sha in sorted(hashes):
            add(f"blobs/{sha[:2]}/{sha}", store.verify_blob(sha))
        for name, sha in plan["runtime"]["implementation_files"].items():
            source = store.blob_path(sha)
            if not source.exists():
                source = Path(__file__).parent / name
            if file_hash(source) != sha:
                raise ValueError("The exact replay implementation is unavailable")
            add("implementation/"+name, source)
        for item in job.get("outputs", []):
            for key, hash_key in (("file", "sha256"), ("gap_mask", "gap_sha256"), ("extent_gap", "extent_gap_sha256")):
                if item.get(key):
                    source = (base / item[key]).resolve()
                    if not source.is_relative_to(base.resolve()) or file_hash(source) != item[hash_key]:
                        raise ValueError("Published artifact integrity failure")
                    add("outputs/"+item[key], source)
        content("runtime-lock.json", plan["runtime"])
        from .store import ROOT
        reference = ROOT / 'docs/datasets/reference-runtime'
        if reference.is_dir():
            for source in sorted(reference.iterdir()):
                if source.is_file():
                    add('reference-runtime/'+source.name, source)
        content("zeus_replay.py", b'from pathlib import Path\nfrom implementation.replay import main\nif __name__ == "__main__":\n    main(bundle_directory=Path(__file__).resolve().parent)\n')
        content("README.txt", b'ZEUS scientific replay\nExtract this ZIP into a new directory. Use the reference runtime in runtime-lock.json.\nRun: python zeus_replay.py --output REPLAY_DIRECTORY\nNo provider connection or credentials are needed. The runner checks every bundle file before processing.\nOperational timestamps are excluded from scientific hashes; the preserved evidence remains unchanged.\n')
        archive.writestr("bundle-index.json", canonical({"schema_version": "zeus.bundle/1", "files": inventory}))


def import_directory(directory, store):
    index = json.loads((directory / "bundle-index.json").read_text(encoding="utf-8"))
    if index.get("schema_version") != "zeus.bundle/1":
        raise ValueError("Unsupported provenance bundle")
    for name, record in index["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or path.stat().st_size != record["size"] or file_hash(path) != record["sha256"]:
            raise ValueError("Missing or corrupt bundle member: "+name)
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    if digest({k: v for k, v in plan.items() if k not in ("plan_id", "plan_hash")}) != plan["plan_hash"] or plan["plan_id"] != job["plan_id"]:
        raise ValueError("Bundle plan integrity failure")
    report = job.get("report")
    if not report or digest({k:v for k,v in report.items() if k != "report_hash"}) != report["report_hash"]:
        raise ValueError("Bundle validation report integrity failure")
    hashes = {r["sha256"] for rows in job.get("receipts", {}).values() for r in rows}
    hashes.update(d["receipt"]["sha256"] for s in plan["selections"] for d in s["discovery"] if "receipt" in d)
    import shutil
    for sha in hashes:
        source = directory / "blobs" / sha[:2] / sha
        if file_hash(source) != sha:
            raise ValueError("Corrupt preserved provider response")
        target = store.blob_path(sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        store.verify_blob(sha)
    from .contracts import FetchPlan
    store.save_plan(FetchPlan.model_validate(plan))
    with store.connect() as connection:
        connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?)", (job["id"], job["project"], job["plan_id"], "offline_replay", canonical(job).decode(), "bundle-import", digest(job)))
    return job["id"]


def extract_bundle(path, destination):
    with zipfile.ZipFile(path) as archive:
        seen = set()
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename or info.filename in seen:
                raise ValueError("Unsafe or duplicate bundle member")
            seen.add(info.filename)
            if not (destination / info.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError("Unsafe bundle member")
        archive.extractall(destination)
