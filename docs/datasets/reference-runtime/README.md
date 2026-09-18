# Reference environment

The current reference is Windows x86-64. `lock.json` records exact package builds, artifact hashes, Python/GDAL/PROJ versions and transformation databases/grids. Other platforms require separate qualification.

From the repository root, recreate it in a new directory using a conda-compatible manager, verify the archive hashes, then install the hashed pip overlay. The example isolates micromamba from user configuration and uses a temporary working directory:

```powershell
$referenceRepo = (Get-Location).Path
$referencePrefix = Join-Path $env:LOCALAPPDATA 'zeus-runtime-recreated'
$referenceCache = Join-Path $env:LOCALAPPDATA 'zeus-reference-build-cache'
$env:MAMBA_ROOT_PREFIX = $referenceCache
Push-Location $env:TEMP
try {
  micromamba --no-rc -r $referenceCache create -y -p $referencePrefix -f "$referenceRepo\docs\datasets\reference-runtime\conda-win-64.txt"
} finally { Pop-Location }
$referencePython = Join-Path $referencePrefix 'python.exe'
& $referencePython qa/verify_runtime_archives.py --prefix $referencePrefix --cache "$referenceCache\pkgs" --record
& $referencePython -m pip install --no-deps --no-build-isolation --require-hashes -r docs/datasets/reference-runtime/pip-win-64.txt
$env:PATH = "$referencePrefix\Library\bin;" + $env:PATH
$env:GDAL_DATA = "$referencePrefix\Library\share\gdal"
$env:PROJ_DATA = "$referencePrefix\Library\share\proj"
& $referencePython qa/lock_reference_runtime.py
```

Check each command's exit status before continuing. The conda explicit file uses exact archive URLs and MD5, while `lock.json` also records their SHA-256. `verify_runtime_archives.py` verifies every downloaded archive against that SHA-256 lock and the installed package identity before adding the verified SHA-256 to metadata records where micromamba omitted it. Pip distributions use SHA-256. The local verification workspace retains pip archives in `.runtime/reference-packages`; those archives are not automatically committed or included in every dataset bundle.

The 2026-09-17 isolated reconstruction used [micromamba 2.3.3-0](https://github.com/mamba-org/micromamba-releases/releases/tag/2.3.3-0); its Windows executable SHA-256 is `90085767d811e08a0fe240f7cb7a713915e1e5f24e3b0acfad9b37daa64c98fe`. All 86 conda archive hashes and 47 pip overlay distributions were checked. Evidence is recorded in [reference-recreation-artifacts.json](../../../qa/reference-recreation-artifacts.json). The recreated installation matched the reference fingerprint and reproduced the downloaded USGS browser job's scientific content and validation report.

The `reverse_geocoder` package has only a source distribution. It is installed using the locked environment's build tools; acquisition numerical libraries use pinned binary packages. Re-run all fixture gates after recreation. Package records alone do not qualify another machine or operating system.

Each plan separately pins and preserves its acquisition implementation. An environment check passing does not authorize executing an old plan with new code. The offline replay command rejects a different runtime fingerprint or implementation and compares scientific content and the entire validation report.

netCDF4 1.7.4 currently needs the narrow upstream Cython declaration workaround in `netcdf_compat.py`; all other runtime warnings stay enabled. See [upstream PR 1471](https://github.com/Unidata/netcdf4-python/pull/1471).

The HWSD extension pins access-parser 0.0.6, construct 2.10.70 and tabulate 0.10.0. Both installations match the updated lock; 147 fixtures pass in each environment. The native Access reader is verified against retained original-source tables and does not require the host ODBC driver during acquisition or replay.

The portable HWSD bundle also reproduces identical scientific content and validation under the independently installed runtime; evidence is in `qa/reference-recreation-verification.json`.
