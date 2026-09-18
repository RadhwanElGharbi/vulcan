from __future__ import annotations

import json
import os
import tempfile
import time
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
import urllib3

from .contracts import AcquisitionReceipt, Asset, file_hash, now
from .store import Store

SECRET_KEYS = {"token", "key", "api_key", "api-key", "sig", "access_token", "password", "x-amz-signature", "x-amz-security-token", "x-amz-credential"}


class Cancelled(Exception):
    pass


class ProviderResponseError(ValueError):
    """Remote availability failure; distinct from local budgets and persistence."""


class LocalResponse:
    """Response-compatible reader for a completed, bounded subprocess transfer."""
    def __init__(self, path, result):
        self.path = path
        self.status_code = result['status']
        self.headers = requests.structures.CaseInsensitiveDict(result['headers'])
        self.ok = self.status_code < 400
        self.raw = path.open('rb')
    def json(self):
        return json.load(self.raw)
    def close(self):
        self.raw.close()
        self.path.unlink(missing_ok=True)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


def safe_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Acquisition URLs must use HTTPS without embedded credentials")
    # Preserve exact non-secret query components, including bare flags and
    # duplicate/empty values: some provider URLs use `&redirect` as an operation.
    query='&'.join(part for part in parsed.query.split('&') if part and
                   all(key.lower() not in SECRET_KEYS for key,_ in parse_qsl(part,keep_blank_values=True)))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


class Transport:
    def __init__(self, store: Store, cancelled=lambda: False, max_bytes=20 * 1024**3, max_total_bytes=None, retained=()):
        self.store, self.cancelled, self.max_bytes = store, cancelled, max_bytes
        self.max_total_bytes = max_total_bytes
        self.retained_sizes = {}
        for receipt in retained:
            self.account(receipt)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ZEUS-Research/2.0 (deterministic acquisition)", "Accept-Encoding": "identity"})

    @property
    def remaining_bytes(self):
        if self.max_total_bytes is None:
            return self.max_bytes
        return min(self.max_bytes, self.max_total_bytes - sum(self.retained_sizes.values()))

    def account(self, receipt):
        if receipt.size < 0 or (receipt.sha256 in self.retained_sizes and self.retained_sizes[receipt.sha256] != receipt.size):
            raise ValueError('Retained receipts disagree about object length')
        self.retained_sizes[receipt.sha256] = receipt.size
        if self.max_total_bytes is not None and sum(self.retained_sizes.values()) > self.max_total_bytes:
            raise ValueError('Job exceeded the approved retained-input budget')
        return receipt

    def isolated_request(self, method, url, *, deadline=300, **options):
        options.pop('stream', None)
        fd, name = tempfile.mkstemp(prefix='http-', dir=self.store.root)
        os.close(fd)
        path = Path(name)
        progress_path = path.with_suffix('.progress')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('http_transfer.py'))], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        task = {'parent_pid': os.getpid(), 'method': method, 'url': url, 'options': options, 'body': str(path), 'progress': str(progress_path), 'max_bytes': self.remaining_bytes if deadline > 300 else min(self.remaining_bytes, 64*1024**2)}
        started = time.monotonic()
        last_progress, previous_size = started, 0
        try:
            process.stdin.write(json.dumps(task).encode('utf-8'))
            process.stdin.close()
            while process.poll() is None:
                self.check()
                if time.monotonic() - started > deadline:
                    raise ProviderResponseError('Provider transfer exceeded its hard deadline')
                try:
                    size = int(progress_path.read_text()) if progress_path.exists() else 0
                except (ValueError, PermissionError):
                    size = previous_size  # An operational progress update is in flight.
                if size != previous_size:
                    previous_size, last_progress = size, time.monotonic()
                if time.monotonic() - last_progress > min(90, deadline):
                    raise requests.RequestException('Provider transfer made no progress within 90 seconds')
                time.sleep(.1)
            raw = process.stdout.read(65536)
            if process.returncode != 0 or not raw:
                raise ValueError('Provider transfer process did not complete')
            result = json.loads(raw)
            if 'error' in result:
                if result.get('retryable'):
                    raise requests.RequestException(result['error'])
                if result.get('failure_kind') == 'persistence':
                    raise OSError('Cannot durably retain provider response: '+result['error'])
                raise ValueError(result['error'])
            return LocalResponse(path, result)
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            path.unlink(missing_ok=True)
            raise
        finally:
            process.stdout.close()
            progress_path.unlink(missing_ok=True)

    def check(self):
        if self.cancelled():
            raise Cancelled("Cancelled by user")

    def signed_url(self, url):
        parsed = urlsplit(url)
        if parsed.hostname == 'data-donnees.az.ec.gc.ca' and parsed.path.startswith('/public//'):
            # The published catalogue's file API issues a short-lived Azure SAS.
            # Only its unsigned object identity enters plans and receipts.
            with self.request('GET', 'https://data-donnees.az.ec.gc.ca/api/file', params={'path':'/'+parsed.path[len('/public//'):]}, allow_redirects=False) as response:
                signed = response.headers.get('Location')
                if response.status_code != 302 or not signed or urlsplit(signed)[:3] != parsed[:3]:
                    raise ValueError('ECCC file API did not authorize the exact selected public object')
            return signed
        if "blob.core.windows.net" not in urlsplit(url).netloc:
            return url
        with self.request("GET", "https://planetarycomputer.microsoft.com/api/sas/v1/sign", params={"href": url}) as response:
            signed = response.json().get("href")
        if not signed or urlsplit(signed)[:3] != urlsplit(url)[:3]:
            raise ValueError("Imagery signing did not return the exact requested asset")
        return signed

    def chunks(self, response, limit_seconds=3600):
        started = time.monotonic()
        while True:
            self.check()
            if time.monotonic()-started > limit_seconds:
                raise ValueError("Provider response exceeded its transfer deadline")
            # read1 returns available bytes after one underlying read, preventing
            # a trickling peer from indefinitely holding a large iter_content block.
            block = response.raw.read1(65536) if isinstance(response, LocalResponse) else response.raw.read1(65536, decode_content=True)
            if not block:
                return
            yield block

    def request(self, method, url, **kw):
        safe_url(url)
        for attempt in range(3):
            self.check()
            try:
                response = self.isolated_request(method, url, **kw)
                if response.status_code not in (429, 500, 502, 503, 504):
                    if not response.ok:
                        status = response.status_code
                        response.close()
                        raise ProviderResponseError(f"Provider HTTP {status}: {safe_url(url)}")
                    return response
                response.close()
                error = f"Provider HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After", "")
            except requests.RequestException as exc:
                # Request exceptions may contain signed URLs or credentials.
                error = type(exc).__name__
                retry_after = ""
            if attempt < 2:
                delay = min(60, max(2**attempt, int(retry_after) if retry_after.isdigit() else 0))
                for _ in range(10 * delay):
                    self.check()
                    time.sleep(0.1)
        raise ProviderResponseError(f"{error} after 3 attempts: {safe_url(url)}")

    def json(self, url, *, method="GET", params=None, data=None, json_body=None, allow_list=False):
        with self.request(method, url, params=params, data=data, json=json_body, stream=True) as response:
            content = bytearray()
            for chunk in self.chunks(response, 300):
                self.check()
                content.extend(chunk)
                if len(content) > 64 * 1024**2:
                    raise ValueError("Discovery response exceeds 64 MiB; partition the query")
            receipt = self.retain_bytes(bytes(content), safe_url(url), response.headers)
        value = json.loads(content)
        if not isinstance(value, (dict, list) if allow_list else dict) or (isinstance(value, dict) and (value.get("error") or value.get("remark"))):
            raise ValueError("Provider returned an error or incomplete-response remark")
        return value, {"request": {"url": safe_url(url), "method": method, "params": params, "data": data, "json": json_body}, "receipt": receipt.model_dump(mode="json")}

    def retain_bytes(self, content, url, headers=None):
        if len(content) > self.remaining_bytes:
            raise ValueError('Response exceeds the remaining retained-input budget')
        fd, name = tempfile.mkstemp(dir=self.store.root)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        sha, path = self.store.retain(Path(name))
        return self.account(AcquisitionReceipt(asset_id=sha, source_url=url, sha256=sha, size=path.stat().st_size,
                                  acquired_at=now(), headers={k.lower(): v for k, v in (headers or {}).items() if k.lower() in {"etag", "last-modified", "content-type", "content-length"}}, blob=sha))

    def inspect(self, asset: Asset, *, actual_url=None):
        with self.request("HEAD", actual_url or asset.url, allow_redirects=True) as r:
            size = r.headers.get("Content-Length")
            asset.size = int(size) if size and size.isdigit() else asset.size
            asset.etag = r.headers.get("ETag")
            asset.last_modified = r.headers.get("Last-Modified")
        return asset

    def download(self, asset: Asset, *, actual_url=None):
        cached=self.store.cached_receipt(asset)
        if cached:
            fresh=self.inspect(asset.model_copy(deep=True),actual_url=actual_url)
            if (fresh.etag,fresh.size,fresh.last_modified)!=(asset.etag,asset.size,asset.last_modified):
                raise ValueError('Source changed after confirmation; cached input cannot be reused')
            if asset.expected_hash and file_hash(self.store.verify_blob(cached.sha256),asset.hash_algorithm)!=asset.expected_hash:
                raise ValueError('Cached input does not match the publisher checksum')
            return self.account(cached.model_copy(update={'headers':{**cached.headers,'zeus-cache-revalidated-at':now()}}))
        for attempt in range(3):
            try:
                return self._download_once(asset, actual_url=actual_url)
            except (requests.RequestException, urllib3.exceptions.HTTPError) as exc:
                if attempt == 2:
                    raise ValueError(f"Asset transfer failed after 3 attempts ({type(exc).__name__})") from None
                for _ in range(10*(2**attempt)):
                    self.check()
                    time.sleep(.1)

    def _download_once(self, asset: Asset, *, actual_url=None):
        headers = {}
        if asset.etag and not asset.etag.startswith("W/"):
            headers["If-Match"] = asset.etag
        fd, name = tempfile.mkstemp(dir=self.store.root)
        path = Path(name)
        started = time.monotonic()
        try:
            with os.fdopen(fd, "wb") as f, self.request("GET", actual_url or asset.url, headers=headers, stream=True, deadline=3600) as response:
                if asset.etag and response.headers.get("ETag") != asset.etag:
                    raise ValueError("Source ETag changed after confirmation; create a new plan")
                if asset.last_modified and response.headers.get("Last-Modified") != asset.last_modified:
                    raise ValueError("Source modification time changed after confirmation")
                size = 0
                for chunk in self.chunks(response):
                    self.check()
                    if time.monotonic() - started > 3600:
                        raise ValueError("Asset exceeded the 1-hour transfer limit")
                    size += len(chunk)
                    if size > self.remaining_bytes:
                        raise ValueError("Asset exceeded the approved storage budget")
                    import shutil
                    if shutil.disk_usage(self.store.root).free < len(chunk)+16*1024**2:
                        raise ValueError("Insufficient free disk space while retaining source input")
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                hdr = dict(response.headers)
            length = hdr.get("Content-Length")
            if size == 0 or (length and size != int(length)) or (asset.size is not None and size != asset.size):
                raise ValueError("Truncated or changed asset size")
            if asset.expected_hash and file_hash(path, asset.hash_algorithm) != asset.expected_hash:
                raise ValueError("Publisher checksum mismatch")
            sha, retained = self.store.retain(path)
            receipt=self.account(AcquisitionReceipt(asset_id=asset.id, source_url=safe_url(asset.url), sha256=sha, size=size, acquired_at=now(),
                                      headers={k.lower(): v for k, v in hdr.items() if k.lower() in {"etag", "last-modified", "content-length", "content-type"}}, blob=sha))
            self.store.cache_receipt(receipt)
            return receipt
        finally:
            path.unlink(missing_ok=True)
