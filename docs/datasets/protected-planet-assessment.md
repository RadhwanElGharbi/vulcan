# Protected Planet source assessment

Checked 2026-09-17 against the provider's [API documentation](https://api.protectedplanet.net/documentation), [token request form](https://api.protectedplanet.net/request) and [published conditions](https://www.protectedplanet.net/en/legal). Retained responses are indexed in `catalogue-assessment.json`.

The API is currently v4; v3 is deprecated. An API token is sent after the provider approves a request describing the intended use. This is individual approval, not an immediately available free-account credential. Field permissions can omit attributes. No token was requested and no authenticated data check was attempted.

The documentation exposes pagination with a maximum of 50 records per page, but its sample response does not establish a stable, complete feature-ID inventory or snapshot. The country examples also contain inconsistent counts. An implementation must verify actual count/ID semantics before claiming a complete extract; an unknown missing feature set cannot be accepted as a measured coverage gap.

The published conditions restrict commercial use and redistribution. They separately discuss online display, attribution and the release date to show. These conditions are recorded as provider evidence; they do not establish permission for a particular user's intended use or for a public dataset-download service.

The request form points to Shapefile, CSV and geodatabase downloads as alternatives. Their automation, exact release identity, access conditions and completeness still require assessment. The API's approval requirement therefore does **not** justify excluding every Protected Planet product or country row. Catalogue entries remain unavailable / qualification outstanding until an eligible acquisition path is established and implemented.
