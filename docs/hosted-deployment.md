# VULCAN website and local companion

The public website is https://vulcan.colony.tech. Vercel serves a static export;
fetching, project storage, native folder selection and the durable worker run on
the visitor's Windows computer. The GitHub repository remains private.

## Using the website

1. Download the Windows companion from the website and extract the whole ZIP.
2. Double-click `start-companion.cmd`. First run installs the pinned Python/GDAL
   runtime and verifies its package hashes; allow several minutes.
3. Open the website and click **Connect local companion**. Allow local network
   access if Chrome or Edge asks.
4. Choose a project directory in Project Index. New installations default to
   `Documents/Vulcan/Projects`. Runtime, caches and the job ledger live in
   `%LOCALAPPDATA%/Vulcan`; include the acquisition directory in backups.

The companion listens on `127.0.0.1:8000`. There is no public API tunnel.
Only the production website and the local development origins are allowed.
The first downloadable companion targets Windows 64-bit; macOS/Linux installers
are not included. A running development companion can also connect to the site.

## Redeploying

From the repository root, rebuild the allowlisted companion archive when backend
or launcher files change:

```powershell
python tests/package_companion.py
cd apps/web
npm ci
npx tsc --noEmit
npx vercel deploy --prod --yes --scope colony-technologies
```

The Vercel project is `vulcan` in team `colony-technologies`. `vercel.json`
uses the static framework preset, `node tools/build-hosted.cjs`, and output `out`.
The build generates Cesium assets and embeds the loopback API address. Do not
replace it with an API rewrite to a Vercel server. Environment files, user data,
and local build caches are excluded from uploads.

## Adding the subdomain again

In Vercel, open **colony-technologies → vulcan → Settings → Domains** and add
`vulcan.colony.tech`. Equivalently, from the linked frontend directory:

```powershell
npx vercel domains add vulcan.colony.tech vulcan --scope colony-technologies
npx vercel domains verify vulcan.colony.tech --scope colony-technologies
```

`colony.tech` already uses Vercel nameservers, and this subdomain verified without
registrar changes. If DNS must be recreated, use the exact record shown by
Vercel for this project. At setup, the recommended record was CNAME `vulcan` →
`bd740e0aac14ee2b.vercel-dns-017.com.`. Do not alter the root domain or other
subdomains. Vercel provisions HTTPS after verification.

See [Vercel's domain instructions](https://vercel.com/docs/domains/set-up-custom-domain).
Deploying this interface does not complete the outstanding scientific provider
qualification gates in `datasets/IMPLEMENTATION_STATUS.md`.
