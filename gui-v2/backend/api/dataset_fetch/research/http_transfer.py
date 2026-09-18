"""One isolated HTTP transfer. No ledger writes, transformations or publication.

The parent owns the deadline and terminates this process before releasing a job.
Request details travel through stdin; signed URLs never appear in process arguments.
"""
import json
import os
import shutil
import sys
import time
import threading

import requests


def watch_parent(parent_pid):
    """Exit if the owning worker dies; the Windows handle also prevents PID reuse."""
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(72)
        def monitor():
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel.CloseHandle(handle)
            os._exit(72)
    else:
        def monitor():
            while os.getppid() == parent_pid:
                time.sleep(.2)
            os._exit(72)
    threading.Thread(target=monitor, daemon=True).start()


def main():
    task = json.load(sys.stdin)
    watch_parent(task['parent_pid'])
    try:
        with requests.Session() as session:
            session.headers.update({'User-Agent': 'ZEUS-Research/2.0', 'Accept-Encoding': 'identity'})
            with session.request(task['method'], task['url'], timeout=(20, 60), stream=True, **task['options']) as response:
                headers = {k: v for k, v in response.headers.items() if k.lower() in {'etag', 'last-modified', 'content-length', 'content-type', 'retry-after', 'location'}}
                result = {'status': response.status_code, 'headers': headers}
                with open(task['body'], 'wb') as stream:
                    size = 0
                    last_progress = 0
                    if response.ok and task['method'] != 'HEAD':
                        for block in response.iter_content(65536):
                            size += len(block)
                            if size > task['max_bytes']:
                                raise ValueError('Provider response exceeded the approved byte limit')
                            if shutil.disk_usage(task['body']).free < len(block) + 16*1024**2:
                                raise OSError('Insufficient free disk space while retaining source input')
                            stream.write(block)
                            if time.monotonic() - last_progress > 1:
                                with open(task['progress'], 'w') as progress:
                                    progress.write(str(size))
                                last_progress = time.monotonic()
                    stream.flush()
                    os.fsync(stream.fileno())
                result['size'] = size
        print(json.dumps(result), flush=True)
    except Exception as exc:
        # Exception messages from requests can contain authentication credentials.
        message = str(exc) if type(exc) is ValueError else type(exc).__name__
        remote = isinstance(exc, requests.RequestException)
        print(json.dumps({'error': message, 'retryable': remote,
                          'failure_kind': 'provider' if remote else 'persistence' if isinstance(exc, OSError) else 'local'}), flush=True)


if __name__ == '__main__':
    main()
