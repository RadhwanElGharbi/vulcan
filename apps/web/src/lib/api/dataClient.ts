import { API_BASE_URL } from '@/lib/api-client'

function getApiBaseSync(): string {
  return API_BASE_URL
}

async function getApiBaseAsync(): Promise<string> {
  return API_BASE_URL
}

export function getApiBase(): string {
  return API_BASE_URL
}

export const PROJECT_LOCAL_CACHE_SETTING_KEY = 'project_local_cache'

const PROJECT_LOCAL_CACHE_STORAGE_KEY = 'zeus_project_local_cache_settings'

const PROJECT_LOCAL_CACHE_RUNTIME_STORAGE_KEY = 'zeus_project_local_cache_runtime'

export interface ProjectLocalCacheDiscrepancySnapshot {
  missing_count: number
  changed_count: number
  extra_count: number
  in_sync: boolean
  missing_paths?: string[]
  changed_paths?: string[]
  extra_paths?: string[]
}

export interface ProjectLocalCacheConfig {
  enabled: boolean
  base_directory: string | null
  last_sync_at?: string | null
  last_sync_fingerprint?: string | null
  last_check_at?: string | null
  last_discrepancy?: ProjectLocalCacheDiscrepancySnapshot | null
}

export type ProjectLocalCacheSettingsMap = Record<string, ProjectLocalCacheConfig>

export interface LocalCacheServiceStatus {
  running: boolean
  port?: number
  api_base_url: string
  base_directory?: string | null
  gdal_bin_dir?: string | null
  projects_root?: string | null
}

interface LocalCacheRuntimeState {
  project_name: string
  api_base_url: string
  projects_root: string | null
  gdal_available: boolean
  updated_at: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function normalizeDiscrepancy(value: unknown): ProjectLocalCacheDiscrepancySnapshot | null {
  if (!isRecord(value)) return null
  const missing = Number(value.missing_count)
  const changed = Number(value.changed_count)
  const extra = Number(value.extra_count)
  const inSync = Boolean(value.in_sync)
  return {
    missing_count: Number.isFinite(missing) ? missing : 0,
    changed_count: Number.isFinite(changed) ? changed : 0,
    extra_count: Number.isFinite(extra) ? extra : 0,
    in_sync: inSync,
    missing_paths: Array.isArray(value.missing_paths)
      ? value.missing_paths.map((entry) => String(entry))
      : [],
    changed_paths: Array.isArray(value.changed_paths)
      ? value.changed_paths.map((entry) => String(entry))
      : [],
    extra_paths: Array.isArray(value.extra_paths)
      ? value.extra_paths.map((entry) => String(entry))
      : []
  }
}

function normalizeProjectLocalCacheConfig(value: unknown): ProjectLocalCacheConfig | null {
  if (!isRecord(value)) return null
  const enabled = Boolean(value.enabled)
  const baseDirectoryRaw = value.base_directory
  const base_directory =
    typeof baseDirectoryRaw === 'string' && baseDirectoryRaw.trim().length > 0
      ? baseDirectoryRaw.trim()
      : null
  return {
    enabled,
    base_directory,
    last_sync_at: typeof value.last_sync_at === 'string' ? value.last_sync_at : null,
    last_sync_fingerprint:
      typeof value.last_sync_fingerprint === 'string' ? value.last_sync_fingerprint : null,
    last_check_at: typeof value.last_check_at === 'string' ? value.last_check_at : null,
    last_discrepancy: normalizeDiscrepancy(value.last_discrepancy)
  }
}

export function normalizeProjectLocalCacheSettings(value: unknown): ProjectLocalCacheSettingsMap {
  if (!isRecord(value)) return {}
  const out: ProjectLocalCacheSettingsMap = {}
  for (const [projectName, configRaw] of Object.entries(value)) {
    const config = normalizeProjectLocalCacheConfig(configRaw)
    if (config) out[projectName] = config
  }
  return out
}

function readProjectLocalCacheSettingsFromStorage(): ProjectLocalCacheSettingsMap {
  if (typeof window === 'undefined') return {}
  try {
    const raw = localStorage.getItem(PROJECT_LOCAL_CACHE_STORAGE_KEY)
    if (!raw) return {}
    return normalizeProjectLocalCacheSettings(JSON.parse(raw))
  } catch {
    return {}
  }
}

function readLocalCacheRuntimeFromStorage(): LocalCacheRuntimeState | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(PROJECT_LOCAL_CACHE_RUNTIME_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as LocalCacheRuntimeState
    if (!parsed || typeof parsed.project_name !== 'string' || typeof parsed.api_base_url !== 'string') {
      return null
    }
    return {
      project_name: parsed.project_name,
      api_base_url: parsed.api_base_url,
      projects_root: typeof parsed.projects_root === 'string' ? parsed.projects_root : null,
      gdal_available: Boolean((parsed as any).gdal_available),
      updated_at: typeof parsed.updated_at === 'string' ? parsed.updated_at : new Date().toISOString()
    }
  } catch {
    return null
  }
}

function writeLocalCacheRuntimeToStorage(runtime: LocalCacheRuntimeState | null): void {
  if (typeof window === 'undefined') return
  try {
    if (!runtime) {
      localStorage.removeItem(PROJECT_LOCAL_CACHE_RUNTIME_STORAGE_KEY)
      return
    }
    localStorage.setItem(PROJECT_LOCAL_CACHE_RUNTIME_STORAGE_KEY, JSON.stringify(runtime))
  } catch {
    // Non-fatal cache write failure.
  }
}

export function getProjectLocalCacheConfig(projectName: string): ProjectLocalCacheConfig | null {
  const settings = readProjectLocalCacheSettingsFromStorage()
  return settings[projectName] ?? null
}

export function getActiveLocalCacheApiBase(projectName: string): string | null {
  if (typeof window === 'undefined') return null

  // Local cache only works when Electron bridge is available to run the local server.
  // Without it, tile URLs would point to a non-running localhost server and fail silently.
  if (!window.electron || typeof window.electron.ensureLocalCacheService !== 'function') {
    return null
  }

  const config = getProjectLocalCacheConfig(projectName)
  if (!config || !config.enabled || !config.base_directory) return null

  const runtime = readLocalCacheRuntimeFromStorage()
  if (!runtime) return null
  if (runtime.project_name !== projectName) return null
  return runtime.api_base_url
}

export function getActiveLocalCacheTileBase(projectName: string): string | null {
  if (typeof window === 'undefined') return null
  if (!window.electron || typeof window.electron.ensureLocalCacheService !== 'function') return null

  const config = getProjectLocalCacheConfig(projectName)
  if (!config || !config.enabled || !config.base_directory) return null

  const runtime = readLocalCacheRuntimeFromStorage()
  if (!runtime) return null
  if (runtime.project_name !== projectName) return null
  if (!runtime.gdal_available) return null  // No GDAL = go directly to remote for tiles
  return runtime.api_base_url
}

export function clearProjectLocalCacheRuntime(projectName?: string): void {
  const runtime = readLocalCacheRuntimeFromStorage()
  if (!runtime) return
  if (!projectName || runtime.project_name === projectName) {
    writeLocalCacheRuntimeToStorage(null)
  }
}

export async function ensureProjectLocalCacheRuntime(
  projectName: string,
  config?: ProjectLocalCacheConfig | null
): Promise<LocalCacheServiceStatus | null> {
  if (
    typeof window === 'undefined' ||
    !window.electron ||
    typeof window.electron.ensureLocalCacheService !== 'function'
  ) {
    return null
  }
  const effectiveConfig = config ?? getProjectLocalCacheConfig(projectName)

  if (!effectiveConfig || !effectiveConfig.enabled || !effectiveConfig.base_directory) {
    clearProjectLocalCacheRuntime(projectName)
    return null
  }

  try {
    const status = await window.electron.ensureLocalCacheService({
      baseDirectory: effectiveConfig.base_directory
    })
    if (status?.running && typeof status.api_base_url === 'string') {
      writeLocalCacheRuntimeToStorage({
        project_name: projectName,
        api_base_url: status.api_base_url,
        projects_root: status.base_directory ?? null,
        gdal_available: Boolean((status as any).gdal_available),
        updated_at: new Date().toISOString()
      })
      return status
    }
  } catch {
    // Ignore: caller falls back to remote API.
  }

  clearProjectLocalCacheRuntime(projectName)
  return null
}

async function fetchProjectAware(
  projectName: string,
  relativePath: string,
  init?: RequestInit,
  options?: { requireGdal?: boolean; localTimeoutMs?: number }
): Promise<Response> {
  const localBase = options?.requireGdal
    ? getActiveLocalCacheTileBase(projectName)
    : getActiveLocalCacheApiBase(projectName)

  if (localBase) {
    const localTimeoutMs = Math.max(250, Number(options?.localTimeoutMs ?? 2500))
    const canUseTimeout = typeof AbortController !== 'undefined' && !init?.signal
    const controller = canUseTimeout ? new AbortController() : null
    let timeoutHandle: ReturnType<typeof setTimeout> | null = null

    try {
      if (controller) {
        timeoutHandle = setTimeout(() => controller.abort(), localTimeoutMs)
      }

      const localResponse = await fetch(
        `${localBase}${relativePath}`,
        controller ? { ...init, signal: controller.signal } : init
      )
      if (localResponse.ok) return localResponse
    } catch {
      // Local runtime likely stale/unreachable. Clear it so subsequent requests
      // skip localhost until runtime is re-established.
      clearProjectLocalCacheRuntime(projectName)
    } finally {
      if (timeoutHandle !== null) clearTimeout(timeoutHandle)
    }
  }

  const remoteBase = await getApiBaseAsync()
  return fetch(`${remoteBase}${relativePath}`, init)
}

export interface ProjectMetadata {
  project_name: string;
  project_id?: string;
  project_code?: string;
  client?: string;
  date_created?: string;
  status?: string;
  crs?: {
    epsg: number;
    proj4: string;
    name: string;
    units: string;
  };
  aoi?: {
    file: string;
    area_km2: number;
    countries?: string[];  // Countries the AOI covers
    start_point?: {
      latitude: number;
      longitude: number;
      name?: string;
    };
    end_point?: {
      latitude: number;
      longitude: number;
      name?: string;
    };
  };
  measurement_system?: string;
  units?: Record<string, string>;
  // Extended metadata fields
  project_creator?: string;
  project_type?: string;
  organization?: string;
  department?: string;
  country?: string;
  iso3?: string;
  iso3_list?: string[];
  countries?: { iso3: string; name: string }[];
  // Folder / visibility (populated from DB)
  folder_id?: string | null;
  folder_name?: string | null;
  folder_color?: string | null;
  visibility?: string;
}

export interface DatasetInfo<T extends string = 'raster' | 'vector' | 'climate' | 'table'> {
  name: string;
  type: T;
  path: string;
  metadata?: any;
}

export interface ProjectDatasets {
  rasters: DatasetInfo<'raster'>[];
  vectors: DatasetInfo<'vector'>[];
  multidimensional?: DatasetInfo<'climate'>[];
  tables?: DatasetInfo<'table'>[];
}

export interface DatasetCoverageEntry {
  dataset: string;
  source?: string | null;
  data_type?: string | null;
  access?: string | null;
  coverage?: string | null;
  temporal_start?: string | null;
  temporal_end?: string | null;
  frequency?: string | null;
  applies_globally: boolean;
  url?: string | null;
}

export interface DatasetCoverageResponse {
  iso3: string;
  country?: string | null;
  entries: DatasetCoverageEntry[];
  summary?: string | null;
  protocol_reference: string;
}

export type DatasetCategory =
  | 'dem'
  | 'landcover'
  | 'soil'
  | 'roads'
  | 'railways'
  | 'powerlines'
  | 'waterways'
  | 'geohazard'
  | 'pipelines'
  | 'protected_areas'
  | 'indigenous_lands'
  | 'imagery'
  | 'climate'
  | 'population'

export interface DatasetCategoryStatus {
  provenance_status?: 'validated' | 'legacy_unverified' | 'absent';
  category: DatasetCategory;
  label: string;
  dataset_type: 'raster' | 'vector';
  required: boolean;
  present: boolean;
  raw_path?: string | null;
  processed_path?: string | null;
  metadata_path?: string | null;
  last_modified?: string | null;
  description?: string | null;
}

export interface DatasetStatusResponse {
  project: string;
  target_epsg: number;
  minimum_requirements_met: boolean;
  categories: DatasetCategoryStatus[];
  protocol_reference: string;
}

export interface ProjectCRSRecommendation {
  epsg: number;
  name: string;
  reason: string;
  utm_zone?: number;
  hemisphere?: string;
}

export interface AOIPreviewResponse {
  area_km2: number;
  countries: string[];
  iso3?: string | null;
  country?: string | null;
  centroid: {
    lat: number;
    lon: number;
  };
  recommended_crs: ProjectCRSRecommendation;
  start_point_within?: boolean | null;
  end_point_within?: boolean | null;
}

export interface CreateProjectResponse {
  status: string;
  project_name: string;
  project_id: string;
  iso3?: string | null;
  country?: string | null;
}

export interface DatasetStageState {
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'cancelled';
  message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface LayerDescriptor {
  category: DatasetCategory;
  dataset_type: 'raster' | 'vector';
  label: string;
  processed_path: string;
  symlink_path: string;
  epsg: number;
}

export interface DatasetFetchJobState {
  label?: string;
  status?: string | null;
  message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  stages?: Record<string, DatasetStageState>;
  layer?: LayerDescriptor | null;
}

export interface DatasetFetchJob {
  id: string;
  project: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'partial' | 'awaiting_approval' | 'publishing' | 'cancelling' | 'cancelled';
  progress: number;
  current_category?: DatasetCategory | null;
  started_at?: string | null;
  updated_at: string;
  completed_at?: string | null;
  categories: Record<string, DatasetFetchJobState>;
  layers: Record<string, LayerDescriptor>;
  logs: string[];
  total_log_count?: number;
  force: boolean;
  error?: string | null;
  overrides?: Record<string, string>;
  report?: import('./researchClient').ResearchValidation;
  outputs?: { file: string; gap_mask?: string; extent_gap?: string; name: string }[];
}

export interface ActiveDatasetJobsResponse {
  active_jobs: Record<
    string,
    {
      job_id: string;
      status: string;
      progress: number;
      current_category?: DatasetCategory | null;
      started_at?: string | null;
      updated_at?: string | null;
    }
  >;
  count: number;
}

export interface GeoJSON {
  type: string;
  features: any[];
  [key: string]: any;
}

export async function fetchProjects(): Promise<ProjectMetadata[]> {
  if (process.env.NEXT_PUBLIC_WEB_PREVIEW === '1') return []
  const base = await getApiBaseAsync();
  const headers: HeadersInit = {};
  const response = await fetch(`${base}/projects`, { headers });
  
  if (!response.ok) {
    throw new Error(`Failed to fetch projects: ${response.statusText}`);
  }
  
  return response.json();
}

export async function fetchProjectMetadata(project: string): Promise<ProjectMetadata> {
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/projects/${encodeURIComponent(project)}/metadata`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch metadata for ${project}: ${response.statusText}`);
  }
  
  return response.json();
}

export async function fetchProjectDatasets(project: string): Promise<ProjectDatasets> {
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/projects/${encodeURIComponent(project)}/datasets`);

  if (!response.ok) {
    throw new Error(`Failed to fetch datasets for ${project}: ${response.statusText}`);
  }

  return response.json();
}

export interface DatasetFingerprint {
  raster_count: number;
  vector_count: number;
  latest_modified: string | null;
  fingerprint: string;
}

export async function fetchDatasetFingerprint(project: string): Promise<DatasetFingerprint> {
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/projects/${project}/datasets/fingerprint`);

  if (!response.ok) {
    throw new Error(`Failed to fetch dataset fingerprint for ${project}: ${response.statusText}`);
  }

  return response.json();
}

export async function fetchActiveDatasetJobs(): Promise<ActiveDatasetJobsResponse> {
  const base = await getApiBaseAsync()
  const response = await fetch(`${base}/dataset-jobs/active`)

  if (!response.ok) {
    throw new Error(`Failed to fetch active dataset jobs: ${response.statusText}`)
  }

  return response.json()
}

export async function fetchVectorData(project: string, layer: string): Promise<GeoJSON> {
  const response = await fetchProjectAware(
    project,
    `/data/${encodeURIComponent(project)}/vectors/${encodeURIComponent(layer)}`,
    undefined,
    { requireGdal: true }
  )
  
  if (!response.ok) {
    throw new Error(`Failed to fetch vector layer ${layer}: ${response.statusText}`);
  }
  
  return response.json();
}

export async function fetchDatasetCoverage(project: string): Promise<DatasetCoverageResponse> {
  if (!project) {
    throw new Error('Project name is required to load dataset coverage');
  }
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/projects/${project}/dataset-coverage`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch dataset coverage for ${project}: ${response.statusText}`);
  }
  
  return response.json();
}

export async function fetchProjectDatasetStatus(project: string): Promise<DatasetStatusResponse> {
  if (!project) {
    throw new Error('Project name is required to load dataset status');
  }
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/projects/${project}/dataset-status`);
  if (!response.ok) {
    throw new Error(`Failed to fetch dataset status for ${project}: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchRecommendedCRS(project: string): Promise<ProjectCRSRecommendation> {
  if (!project) {
    throw new Error('Project name is required to load CRS recommendation');
  }
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/projects/${project}/crs/recommend`);
  if (!response.ok) {
    throw new Error(`Failed to fetch recommended CRS for ${project}: ${response.statusText}`);
  }
  return response.json();
}

export async function updateProjectCRS(project: string, epsg: number, name: string): Promise<{ status: string; epsg: number; name: string }> {
  if (!project) {
    throw new Error('Project name is required to update CRS');
  }
  const base = await getApiBaseAsync();
  const headers: HeadersInit = { 'Content-Type': 'application/json' }
  const response = await fetch(`${base}/projects/${project}/crs`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ epsg, name })
  });
  if (!response.ok) {
    throw new Error(`Failed to update CRS for ${project}: ${response.statusText}`);
  }
  return response.json();
}

export async function startDatasetFetch(
  project: string,
  categories: DatasetCategory[],
  force = false,
  overrides?: Partial<Record<DatasetCategory, string | null>>
): Promise<{ job_id: string }> {
  if (!project) {
    throw new Error('Project name is required to start dataset fetch');
  }
  if (!categories || categories.length === 0) {
    throw new Error('Select at least one dataset category to fetch');
  }
  const base = await getApiBaseAsync();
  const payload: { categories: DatasetCategory[]; force: boolean; overrides?: Record<string, string> } = {
    categories,
    force
  };
  if (overrides) {
    const cleaned: Record<string, string> = {};
    Object.entries(overrides).forEach(([key, value]) => {
      if (value) cleaned[key] = value;
    });
    if (Object.keys(cleaned).length > 0) {
      payload.overrides = cleaned;
    }
  }

  const headers: HeadersInit = { 'Content-Type': 'application/json' }

  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
  const timeoutMs = 20000
  const timeout = controller ? setTimeout(() => controller.abort(), timeoutMs) : null

  try {
    const response = await fetch(`${base}/projects/${project}/dataset-fetch`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      ...(controller ? { signal: controller.signal } : {})
    })
    if (!response.ok) {
      const message = await response.text()
      let detail = message
      try {
        const parsed = JSON.parse(message)
        if (parsed && typeof parsed === 'object' && typeof (parsed as any).detail === 'string') {
          detail = String((parsed as any).detail)
        }
      } catch {
        // ignore (non-JSON)
      }
      throw new Error(detail || `Failed to start dataset fetch: ${response.statusText}`)
    }
    return response.json()
  } catch (err) {
    // If the backend is deadlocked/busy, the request can hang forever without a client timeout.
    if (err && typeof err === 'object' && (err as any).name === 'AbortError') {
      throw new Error(
        `Dataset fetch request timed out after ${Math.round(timeoutMs / 1000)}s. The backend may be stuck—restart the backend server and try again.`
      )
    }
    throw err
  } finally {
    if (timeout) clearTimeout(timeout)
  }
}

export async function previewAoi(formData: FormData): Promise<AOIPreviewResponse> {
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/projects/aoi/preview`, {
    method: 'POST',
    body: formData
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || 'Failed to preview AOI');
  }
  return response.json();
}

export async function createProject(formData: FormData): Promise<CreateProjectResponse> {
  const base = await getApiBaseAsync();
  const headers: HeadersInit = {};
  const response = await fetch(`${base}/projects/create`, {
    method: 'POST',
    headers,
    body: formData
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || 'Failed to create project');
  }
  return response.json();
}

export async function fetchDatasetJob(jobId: string): Promise<DatasetFetchJob> {
  const base = await getApiBaseAsync();
  const response = await fetch(`${base}/dataset-jobs/${jobId}`);
  if (!response.ok) {
    throw new Error(`Failed to load dataset job ${jobId}: ${response.statusText}`);
  }
  return response.json();
}

export function subscribeToDatasetJob(
  jobId: string,
  onUpdate: (job: DatasetFetchJob) => void,
  onError?: (error: Error) => void
): () => void {
  let stopped = false;
  let lastJob: DatasetFetchJob | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let stream: EventSource | undefined;
  const isTerminal = (status?: DatasetFetchJob['status'] | null) =>
    status === 'succeeded' || status === 'failed' || status === 'partial' || status === 'cancelled';

  // Polling function as fallback
  const poll = async () => {
    if (stopped) return;
    try {
      const payload = await fetchDatasetJob(jobId);
      if (stopped) return;
      if (!lastJob || payload.updated_at >= lastJob.updated_at) {
        lastJob = payload;
        onUpdate(payload);
      }
      if (isTerminal(payload.status)) {
        stopped = true;
        stream?.close();
        return;
      }
    } catch (err) {
      if (stopped) return;
      // Only report error if we haven't received any updates yet
      if (!lastJob) {
        onError?.(err instanceof Error ? err : new Error('Failed to poll dataset job.'));
      }
      // Otherwise, keep polling silently
      console.warn('[DatasetJob] Poll failed, retrying...', err);
    }
    if (!stopped) {
      pollTimer = setTimeout(poll, 2000);
    }
  };

  // Try SSE first if available
  if (typeof window !== 'undefined' && typeof EventSource !== 'undefined') {
    const streamUrl = `${getApiBaseSync()}/dataset-jobs/${jobId}/stream`;
    console.log('[DatasetJob] Connecting to SSE stream:', streamUrl);

    const source = new EventSource(streamUrl);
    stream = source;
    // Proxies can keep an SSE connection open while buffering every event.
    // Initial retrieval and periodic polling make reconnect independent of SSE.
    poll();
    let receivedFirstMessage = false;

    source.onopen = () => {
      console.log('[DatasetJob] SSE connection opened');
    };

    source.onmessage = (event) => {
      if (stopped) return;
      try {
        receivedFirstMessage = true;
        const payload = JSON.parse(event.data) as DatasetFetchJob;
        if (!lastJob || payload.updated_at >= lastJob.updated_at) {
          lastJob = payload;
          onUpdate(payload);
        }

        // Close connection when job is complete
        if (isTerminal(payload.status)) {
          console.log('[DatasetJob] Job complete, closing SSE');
          source.close();
          stopped = true;
          clearTimeout(pollTimer);
        }
      } catch (err) {
        console.error('[DatasetJob] Failed to parse SSE message:', err);
        onError?.(err instanceof Error ? err : new Error('Failed to parse dataset job update.'));
      }
    };

    source.onerror = (event) => {
      console.warn('[DatasetJob] SSE error, falling back to polling:', event);
      source.close();

      // If we already have a terminal state, treat the disconnect as normal.
      if (!stopped && lastJob && isTerminal(lastJob.status)) {
        stopped = true;
        return;
      }

      // If we never received a message, fall back to polling
      if (!receivedFirstMessage && !stopped) {
        console.log('[DatasetJob] Falling back to polling mode');
      } else if (!stopped && lastJob && !isTerminal(lastJob.status)) {
        // SSE disconnected mid-stream, fall back to polling
        console.log('[DatasetJob] SSE disconnected, continuing with polling');
      } else if (!stopped) {
        onError?.(new Error('Dataset job stream disconnected.'));
      }
    };

    return () => {
      stopped = true;
      source.close();
      clearTimeout(pollTimer);
    };
  }

  // No SSE support, use polling
  poll();
  return () => {
    stopped = true;
    clearTimeout(pollTimer);
  };
}

export async function cancelDatasetJob(jobId: string): Promise<void> {
  const base = await getApiBaseAsync();
  const headers: HeadersInit = {}
  const response = await fetch(`${base}/dataset-jobs/${jobId}`, { method: 'DELETE', headers });
  if (!response.ok) {
    throw new Error(`Failed to cancel dataset job ${jobId}: ${response.statusText}`);
  }
}

export function getTileUrl(project: string, layer: string): string {
  const base = getActiveLocalCacheTileBase(project) || getApiBaseSync()
  return `${base}/tiles/${encodeURIComponent(project)}/${encodeURIComponent(layer)}/{z}/{x}/{y}.png`;
}

export function getTerrainTileUrl(project: string, layer: string, revision?: string): string {
  const base = getActiveLocalCacheTileBase(project) || getApiBaseSync()
  return `${base}/terrain/${encodeURIComponent(project)}/${encodeURIComponent(layer)}/{z}/{x}/{y}.png?display=native-coverage-v2${revision ? `&revision=${encodeURIComponent(revision)}` : ''}`;
}

export function getAoiFileUrl(project: string, filename: string): string {
  const base = getActiveLocalCacheApiBase(project) || getApiBaseSync()
  return `${base}/data/${encodeURIComponent(project)}/aoi/${encodeURIComponent(filename)}`;
}

export interface UserSettings {
  [key: string]: unknown
}

export async function fetchUserSettings(): Promise<UserSettings> {
  if (typeof window === 'undefined') return {}
  try { return JSON.parse(localStorage.getItem('zeus_settings') || '{}') } catch { return {} }
}

export async function patchUserSettings(patch: Record<string, unknown>, _options?: unknown): Promise<UserSettings> {
  const current = await fetchUserSettings()
  const updated = {...current, ...patch}
  localStorage.setItem('zeus_settings', JSON.stringify(updated))
  return updated
}

export function getProjectLocalCacheSettings(): ProjectLocalCacheSettingsMap {
  return readProjectLocalCacheSettingsFromStorage()
}

export async function patchProjectLocalCacheConfig(
  projectName: string,
  config: ProjectLocalCacheConfig | null
): Promise<ProjectLocalCacheSettingsMap> {
  const current = readProjectLocalCacheSettingsFromStorage()
  const next: ProjectLocalCacheSettingsMap = { ...current }

  if (config) {
    next[projectName] = config
  } else {
    delete next[projectName]
  }

  await patchUserSettings(
    { [PROJECT_LOCAL_CACHE_SETTING_KEY]: next },
    'device'
  )
  return readProjectLocalCacheSettingsFromStorage()
}

export function subscribeToProjectEvents(
  project: string,
  onEvent: (event: { type: string; [key: string]: any }) => void,
  onError?: (error: Error) => void
): () => void {
  let stopped = false;
  const streamUrl = `${getApiBaseSync()}/projects/${project}/events/stream`;

  if (typeof window === 'undefined' || typeof EventSource === 'undefined') {
    return () => {};
  }

  const source = new EventSource(streamUrl);

  source.onmessage = (ev) => {
    if (stopped) return;
    try {
      const payload = JSON.parse(ev.data);
      onEvent(payload);
    } catch (err) {
      console.warn('[ProjectEvents] Failed to parse SSE message:', err);
    }
  };

  source.onerror = () => {
    if (stopped) return;
    source.close();
    onError?.(new Error('Project event stream disconnected.'));
  };

  return () => {
    stopped = true;
    source.close();
  };
}
