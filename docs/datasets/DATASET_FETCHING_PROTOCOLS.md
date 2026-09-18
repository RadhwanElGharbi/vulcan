# Scientific dataset acquisition protocol

The mounted local API implements **discover → freeze → review → acquire → validate → approve exceptions → publish**. Provider qualification is still in progress; see [implementation status](IMPLEMENTATION_STATUS.md). Historical catalogue entries are references, not authorization to substitute products.

## Planning and confirmation

`GET /api/dataset-sources?project=NAME` returns versioned product identities, parameter schemas, capabilities, access requirements, findings and dated verification evidence. National defaults use project country hints; those hints do not prove AOI coverage. A project can retain multiple selections in the same category.

`POST /api/projects/NAME/dataset-fetch/plan?background=true` takes a `PlanRequest` and an `Idempotency-Key` header. It persists the exact selections, reference time, original AOI identity, target CRS and implementation identity, then returns a durable discovery job. Poll `GET /api/dataset-discovery-jobs/ID`; `DELETE` requests cancellation. `GET /api/projects/NAME/dataset-discovery-jobs` supports reconnect. Discovery is bounded by a 30-minute deadline and 2 GiB of retained responses. Restart resumes unconfirmed discovery with its original reference time; changing code or AOI requires a new discovery. The synchronous plan endpoint remains available when `background` is omitted.

Successful discovery returns the frozen `plan_id` and `plan_hash`. Review includes exact products, assets or snapshot queries, observation/release/snapshot dates, native spacing, output grid, units, coordinate operations, accuracy, coverage evidence, processing, licences and storage estimates. Unknown values include their reason. The original AOI files and dependencies are retained; its normalization recipe is part of the plan.

The existing fetch endpoint accepts only an `ExecuteRequest` containing `plan_id`, `plan_hash`, `idempotency_key` and the individual acknowledged finding IDs. It verifies the plan, current project context and pinned runtime. Execution cannot reselect a source. Acknowledgements belong to that exact plan, not a category or future job.

## Acquisition and validation

The dedicated worker uses a SQLite WAL ledger with transactional state changes, retained checkpoints and event history. Each exact input object, provider subset response, discovery response and required dependency is preserved by SHA-256 before transformation. Cache reuse verifies both content integrity and the current strong provider identity. Weak or missing identities cannot authorize byte reuse.

Analytical operations use frozen coordinate pipelines, grids, source precedence, resampling, datatype, calibration and NoData policies. Native imagery, population and climate values are retained with separately identified visualization derivatives. OSM tags and relation dependencies and vector source attributes/IDs remain traceable to retained responses.

Validation scans all required bands, variables and time steps in bounded blocks. Raster coverage accounts for AOI holes and fractional boundary cells, with downloadable masks and out-of-extent geometry. Vector checks reconcile IDs, batches, attributes, geometry and clipping losses. A verified empty result is valid; an incomplete or unquantifiable response is a blocker. Provider response completeness and real-world mapping completeness are separate claims.

Corruption, checksum mismatches, mandatory uninterpretable CRS/units, invalid classes, truncated responses and unexecuted mandatory stages prevent publication. Measurable gaps and identified provider limitations remain findings requiring explicit review. `POST /api/dataset-jobs/ID/accept` binds the individual finding IDs to the exact report hash. Acceptance never rewrites a failed check as passed. Pending approvals survive restart.

## Publication, cancellation and replay

All selected results publish together as an immutable generation. The visible inventory changes through one atomic `active-generation.json` switch after validation and required approvals. Readers verify that pointer against its immutable generation manifest and resolve artifacts from that generation. A failure before the switch leaves the previous inventory visible; a failure after the switch reconciles the committed generation with the ledger. Cancellation retains the project lock until execution stops and blocks later publication.

The ledger and content-addressed inputs are under `.runtime/acquisition`; outputs are under `Projects/NAME/data/generations/JOB`. Retain both in backups. Existing data remains readable as legacy/unverified. Present-artifact revalidation cannot reconstruct missing historical provenance.

Job status/report endpoints provide stage history, findings, validation metrics and publication state. Artifact downloads verify integrity. The provenance ZIP contains the exact plan, inputs, evidence, implementation, runtime fingerprint, report, approvals, event history and output inventory. Extract it into a fresh directory and run `python zeus_replay.py --output NEW_DIRECTORY` in its pinned reference environment. Replay verifies input integrity, reconstructs the original AOI and compares scientific-content hashes and validation decisions. Artifact byte hashes are also recorded; operational timestamps are not scientific content.

## Verification limits

Immutable fixture tests, independent format checks and dated live-provider checks are separate evidence. A successful small AOI does not qualify every region, parameter or scientific analysis. ERA5 live access requires credentials. Full catalogue/provider qualification, additional platforms and physical storage power-loss guarantees remain unfinished. Software licensing and provider redistribution terms are separate.
