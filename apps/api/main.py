"""Local ZEUS dataset workspace. Projects and downloads stay on this computer."""
import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('AGRS_PROJECTS_ROOT', str(ROOT / 'Projects'))
os.environ.setdefault('AGRS_DBS_ROOT', str(ROOT / 'DBs'))
os.environ.setdefault('AGRS_DBS_MATERIALIZE_MODE', 'copy')
os.environ.setdefault('ZEUS_AGENT_ENABLED', '0')

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from api.projects import router as projects_router
from api.data import router as data_router
from api.workspace import router as workspace_router
from api.project_package import router as package_router
from api.dataset_fetch import router as dataset_router
from api.dataset_fetch.research.worker import start_worker
from api.cloud_workspace import CloudMiddleware, Sessions, enabled as cloud_enabled, supervise_cloud

@asynccontextmanager
async def lifespan(app):
    if cloud_enabled():
        app.state.cloud_sessions = Sessions()
        app.state.cloud_leases = {}
        supervisor = asyncio.create_task(supervise_cloud(app))
        app.state.cloud_supervisor = supervisor
        try:
            yield
        finally:
            supervisor.cancel()
            try:
                await supervisor
            except asyncio.CancelledError:
                pass
        return
    Path(os.environ['AGRS_PROJECTS_ROOT']).mkdir(parents=True, exist_ok=True)
    worker = start_worker()
    async def supervise():
        nonlocal worker
        while True:
            await asyncio.sleep(3)
            if worker.poll() is not None:
                worker = start_worker()
    supervisor=asyncio.create_task(supervise())
    try:
        yield
    finally:
        supervisor.cancel()
        try:
            await supervisor
        except asyncio.CancelledError:
            pass
        # The dedicated worker may finish a confirmed job independently of HTTP.

app = FastAPI(title='ZEUS Dataset API', version='0.1.0', lifespan=lifespan,
              docs_url='/api/docs', openapi_url='/api/openapi.json')
ALLOWED_ORIGINS = ['http://localhost:3001', 'http://127.0.0.1:3001', 'https://vulcan.colony.tech']
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=['*'], allow_headers=['*'])

@app.middleware('http')
async def local_companion_origin_guard(request, call_next):
    if cloud_enabled():
        return await call_next(request)
    # CORS alone does not prevent cross-site forms from causing local mutations.
    origin = request.headers.get('origin')
    if origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({'detail': 'This website is not allowed to access the local companion.'}, status_code=403)
    if request.url.hostname not in {'localhost', '127.0.0.1', '::1', 'testserver'}:
        return JSONResponse({'detail': 'The companion accepts loopback hosts only.'}, status_code=403)
    response = await call_next(request)
    if origin in ALLOWED_ORIGINS and request.headers.get('access-control-request-private-network') == 'true':
        response.headers['Access-Control-Allow-Private-Network'] = 'true'
    return response
app.add_middleware(CloudMiddleware)
app.include_router(projects_router, prefix='/api')
app.include_router(data_router, prefix='/api')
app.include_router(workspace_router, prefix='/api')
app.include_router(package_router, prefix='/api')
app.include_router(dataset_router, prefix='/api')

@app.get('/api/health')
def health():
    import shutil
    return {'status': 'healthy', 'version': '0.1.0', 'application': 'VULCAN', 'companion_protocol': 1, 'services': {
        tool: 'available' if shutil.which(tool) else 'missing'
        for tool in ['gdalinfo', 'gdalwarp', 'ogr2ogr', 'ogrinfo']}}

@app.get('/api/config')
def config():
    return {'mapbox_token': '', 'api_version': '0.1.0', 'features': ['projects', 'datasets', 'map']}
