"""Offline replay: python -m api.dataset_fetch.research.replay --job ID --output DIRECTORY"""
from __future__ import annotations

import argparse
from pathlib import Path

from .contracts import AcquisitionReceipt, FetchPlan, digest
from .planning import runtime_fingerprint
from .processing import run_selection
from .store import Store, atomic_json


def replay(job_id, output, store=None):
    store = store or Store()
    job = store.job(job_id)
    plan = FetchPlan.model_validate(store.plan(job["plan_id"]))
    if plan.runtime != runtime_fingerprint():
        raise ValueError("Replay requires the exact pinned implementation and reference runtime")
    if not job.get("report"):
        raise ValueError("No completed validation report to compare")
    import socket
    previous = socket.socket.connect
    def offline(*args, **kwargs):
        raise RuntimeError("Network access is forbidden during scientific replay")
    socket.socket.connect = offline
    try:
        results, findings, files = [], [], []
        for selection in plan.selections:
            from .aoi import replay_aoi
            replay_aoi(selection, plan, store)
            receipts = [AcquisitionReceipt.model_validate(r) for r in job["receipts"][selection.id]]
            artifacts, checks, issues = run_selection(selection, receipts, plan, store, output / selection.id)
            files.extend(artifacts)
            results.extend(checks)
            findings.extend(f.model_dump(mode="json") for f in issues)
        expected = job["report"]
        body = {"schema_version": "zeus.acquisition/2.0", "policy": plan.policy, "results": results, "findings": findings}
        result = {"identical_scientific_content": [r["scientific_hash"] for r in results] == [r["scientific_hash"] for r in expected["results"]],
                  "identical_validation": digest(body) == expected["report_hash"], "report": body}
        atomic_json(output / "replay-result.json", result)
        if not result["identical_scientific_content"] or not result["identical_validation"]:
            raise ValueError("Replay differs from the retained scientific result")
        return result
    finally:
        socket.socket.connect = previous


def main(bundle_directory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Replay output directory must be empty")
    if sum(bool(x) for x in (args.job, args.bundle, bundle_directory)) != 1:
        parser.error("Provide either --job or --bundle (the portable runner uses its extracted bundle)")
    if args.job:
        result = replay(args.job, args.output)
    else:
        import tempfile
        from .bundle import extract_bundle, import_directory
        with tempfile.TemporaryDirectory(prefix="zeus-replay-") as temporary:
            directory = Path(temporary)
            source = bundle_directory or directory / "bundle"
            if args.bundle:
                extract_bundle(args.bundle, source)
            store = Store(directory / "store")
            job = import_directory(source, store)
            result = replay(job, args.output, store)
    print({key:value for key,value in result.items() if key != "report"})


if __name__ == "__main__":
    main()
