# Temporary cloud workspaces

Implementation is opt-in and not deployed to production yet. The existing
Vercel static website cannot run this long-lived Python/GDAL worker by itself.
An actual backend host and a qualified runtime must be configured before switching
the production website away from companion mode.

Each browser gets an opaque, HttpOnly, Secure, SameSite=Strict cookie. Only its
SHA-256 is persisted in the session ledger. Projects, raw inputs, acquisition
ledgers and map caches are isolated beneath a random workspace directory.
Native folder-picker and arbitrary server-directory APIs are disabled in cloud mode.
No sign-in or companion installation is needed in the cloud interface.

The default lifetime is 24 hours from creation, not an automatically renewing
lease. Project Index shows the expiration. Expired sessions lose access immediately;
the supervisor stops their worker processes and removes their dedicated directory
after active responses have released it. Event streams are bounded by expiry.
Use one API process (`--workers 1`) per cloud instance; multiple API replicas
require shared session coordination and a separate worker scheduler first.

## Configuration

Run the backend in the pinned Python/GDAL environment, as an unprivileged service:

```text
VULCAN_CLOUD_MODE=1
VULCAN_CLOUD_ROOT=/srv/vulcan/temporary-workspaces
VULCAN_PUBLIC_ORIGIN=https://vulcan.colony.tech
VULCAN_WORKSPACE_TTL_SECONDS=86400
VULCAN_WORKSPACE_MAX_BYTES=5368709120
VULCAN_MAX_WORKSPACES=20
VULCAN_MAX_WORKERS=2
VULCAN_PACKAGE_MAX_BYTES=21474836480
```

Start `python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1`
from `gui-v2/backend`, behind an HTTPS reverse proxy. Give the service a dedicated
volume with an actual disk quota and CPU/memory limits. The application checks
workspace usage periodically; this is not a substitute for filesystem quotas.
Rate-limit workspace creation and expensive operations at ingress, and cap upload
bodies there. Public release also needs a review of untrusted geospatial uploads
and outbound network access; fixture isolation checks are not a security audit.

Proxy `https://vulcan.colony.tech/api/*` to the backend while preserving cookies
and the original Origin header. Do not cache any API response. Configure streaming
and large download timeouts on the proxy. Set `VULCAN_DEPLOY_MODE=cloud` for the
frontend build only once this same-origin API is ready. The compiled frontend uses
`/api`, automatically creates/reuses a workspace, and bypasses the companion gate.

`VULCAN_INSECURE_TEST_COOKIE=1` is exclusively for a local HTTP test server; never
set it in production. Default secure cookies require HTTPS.

## Downloads

The sidebar **Download** action requests `/api/projects/{project}/package`.
It contains `project/`, an integrity manifest, job evidence and replay bundles for
published acquisitions. It does not distribute the application UI or an installer.
Active acquisitions/discovery block export; project changes during export also
invalidate the archive. Each project file is hashed while streamed into the ZIP.
Uncommitted staging and map caches are excluded. Published artifact or preserved
input corruption blocks the download instead of silently omitting provenance.

The export is bounded in size and uses temporary disk rather than holding datasets
in browser or server memory. Temporary archive files are removed after response
completion. Downloaded data retains its provider-specific licence, independently
of the software licence.

Before production: configure and test the chosen host, hard storage/resource limits,
ingress limits, Linux replay qualification if applicable, fresh-browser expiry,
restart recovery, and a real hosted fetch-to-download flow.
