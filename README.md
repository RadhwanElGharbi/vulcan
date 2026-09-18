# VULCAN geospatial data workspace

VULCAN provides project-based geospatial data acquisition, an AOI picker and a CesiumJS globe. It extracts the acquisition workflow from ZEUS; engineering and drone-planning workflows are outside this project. The website is at https://vulcan.colony.tech.

The local/companion deployment is live. Temporary cloud workspaces are implemented behind an opt-in deployment mode and are not yet enabled on the public website. See [cloud hosting](docs/cloud-workspaces.md) and [deployment instructions](docs/hosted-deployment.md).

The scientific acquisition implementation is in progress. The replacement pipeline requires a frozen plan, explicit provider acknowledgements, complete validation and any measured-gap approval before an atomic generation becomes visible. **The complete provider qualification and catalogue delivery gates are not finished.** See [implementation status](docs/datasets/IMPLEMENTATION_STATUS.md).

## Run locally

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-Zeus.ps1
```

Open **http://localhost:3001**. The API listens on `127.0.0.1:8000`. The launcher uses `%LOCALAPPDATA%\zeus-runtime` and Node.js 22. Logs are under `.runtime/`.

For a fresh environment, follow [the reference runtime instructions](docs/datasets/reference-runtime/README.md), then run `npm ci` in `gui-v2/frontend`. `environment.yml` is a convenient development solve; it is not the reference lock. Linux and macOS have not been qualified for scientific replay equality.

## Acquire and review

1. Open or create a project. The new-project AOI picker remains a 2D map; the project viewer uses CesiumJS 1.139.0 with native globe controls, skybox and atmosphere.
2. Open Datasets, select products and enter explicit dates, bands, years or properties. Multiple selections from one category are supported.
3. Start background discovery and review the frozen source inventory, scientific meaning, metadata, processing choices and storage estimates. Discovery reconnects when the project review is reopened after refresh; cancellation is available while it runs. Unknown dates and accuracy remain visible.
4. Acknowledge each provider limitation before confirming. Execution uses that plan and cannot silently substitute another source.
5. Review any measured coverage gaps. Mandatory failures cannot be accepted. Approved datasets appear together through one generation-manifest switch.
6. Reopen acquisition history to inspect reports, download artifacts, or obtain a provenance bundle. Existing data stays labelled **legacy / unverified**.
7. Use **Download** in the left sidebar to save the loaded project's assets, metadata and acquisition evidence. This control is disabled until a project is loaded. The GitHub icon links to the software repository separately.

Acquisition validity does not establish fitness for a specific analysis. For example, Copernicus elevation is a surface model; WorldPop values are modelled people per native cell; WorldClim is a climatology rather than current observations. Projected map tiles are display derivatives.

The [API and preservation protocol](docs/datasets/DATASET_FETCHING_PROTOCOLS.md) describes discovery, confirmation, approval, cancellation and publication contracts.

## Preservation and replay

The SQLite ledger and content-addressed raw inputs live under `.runtime/acquisition`. Project outputs live under `Projects/<project>/data/generations/`; `active-generation.json` identifies the visible inventory. Keep both locations when backing up this workspace. Raw inputs are retained until explicitly deleted.

Download a provenance bundle from a job report and extract it into a new directory. In its pinned reference environment:

```powershell
python zeus_replay.py --output replay-result
```

The bundle includes exact provider objects/responses, discovery evidence, plan, validation report, event history, artifacts and the preserved replay implementation. The command verifies integrity, performs offline processing and compares scientific-content hashes and validation decisions. Operational timestamps and container bytes can differ; every stored artifact also has its own SHA-256.

To inspect an older artifact without changing its provenance status, run from `gui-v2/backend` in the reference environment:

```powershell
python -m api.dataset_fetch.research.legacy --project PROJECT --artifact data/rasters/processed/FILE.tif --output NEW_REPORT_DIRECTORY
```

The command preserves a snapshot and reports present grid, value, geometry and coverage properties. It also accepts legacy GeoPackages. It does not publish a generation, infer missing scientific units, establish publisher completeness, or reconstruct original acquisition inputs. The report remains **legacy / unverified**.

## Verification

```powershell
& "$env:LOCALAPPDATA\zeus-runtime\python.exe" -m pytest qa/test_research_acquisition.py -W error::RuntimeWarning -q
& "$env:LOCALAPPDATA\zeus-runtime\python.exe" qa/lock_reference_runtime.py
# In gui-v2/frontend, without disturbing the development build:
$env:ZEUS_BUILD_DIR = '.next-build'
npm run build
```

`qa/research-provider-discovery.json` records dated discovery checks. `qa/research-live-acquisition.json` records separate live acquisitions and two offline replays, with full evidence under `.runtime/qualification/`. Immutable fixture tests do not depend on provider availability. Missing ERA5 credentials and service errors remain unresolved verification items.

No software licence has been chosen for this extraction. Provider data terms and attribution are recorded independently of any eventual software licence.