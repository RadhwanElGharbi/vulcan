export {}

declare global {
  interface LocalCacheCheckPayload {
    projectName: string
    baseDirectory: string
    token?: string | null
  }

  interface LocalCacheDiscrepancy {
    missing_count: number
    changed_count: number
    extra_count: number
    in_sync: boolean
    missing_paths: string[]
    changed_paths: string[]
    extra_paths: string[]
  }

  interface LocalCacheManifestSummary {
    file_count: number
    total_size_bytes: number
    fingerprint: string
  }

  interface LocalCacheCheckResult {
    project_name: string
    base_directory: string
    project_directory: string
    remote_manifest: LocalCacheManifestSummary
    local_manifest: LocalCacheManifestSummary
    discrepancy: LocalCacheDiscrepancy
  }

  interface LocalCacheSyncResult {
    project_name: string
    base_directory: string
    project_directory: string
    downloaded_count: number
    deleted_count: number
    remote_manifest: LocalCacheManifestSummary
    local_manifest: LocalCacheManifestSummary
    discrepancy_before: LocalCacheDiscrepancy
    discrepancy_after: LocalCacheDiscrepancy
  }

  interface LocalCacheServicePayload {
    baseDirectory: string
  }

  interface LocalCacheServiceStatus {
    running: boolean
    port: number
    api_base_url: string
    base_directory: string | null
    gdal_bin_dir: string | null
  }

  interface GdalStatus {
    available: boolean
    version: string | null
  }

  interface DriftEvent {
    project_name: string
    direction: 'server-ahead' | 'local-ahead' | 'both'
    discrepancy: LocalCacheDiscrepancy
  }

  interface PollingPayload {
    projectName: string
    baseDirectory: string
    token?: string | null
  }

  interface PushPayload {
    projectName: string
    baseDirectory: string
    filePaths: string[]
    token?: string | null
  }

  interface PushResult {
    project_name: string
    results: Array<{
      path: string
      status: string
      reason?: string
    }>
  }

  interface RouteExportFile {
    relativePath: string
    content: string
    encoding?: 'utf8' | 'base64'
  }

  interface RouteExportPayload {
    projectName: string
    routeId: string
    files: RouteExportFile[]
    manifest?: Record<string, unknown> | null
  }

  interface RouteExportResult {
    cancelled: boolean
    directory?: string
    folder?: string
    files_written?: number
  }

  interface Window {
    electron?: {
      getAppVersion: () => Promise<string>
      getAppPath: () => Promise<string>
      platform: string
      getGdalStatus: () => Promise<GdalStatus>
      pickLocalCacheDirectory: () => Promise<{ cancelled: boolean; directory?: string }>
      checkProjectLocalCache: (payload: LocalCacheCheckPayload) => Promise<LocalCacheCheckResult>
      syncProjectLocalCache: (payload: LocalCacheCheckPayload) => Promise<LocalCacheSyncResult>
      ensureLocalCacheService: (payload: LocalCacheServicePayload) => Promise<LocalCacheServiceStatus>
      stopLocalCacheService: () => Promise<LocalCacheServiceStatus>
      getLocalCacheStatus: () => Promise<LocalCacheServiceStatus>
      getLocalCacheApiBase: () => Promise<string>
      startPolling: (payload: PollingPayload) => Promise<{ polling: boolean }>
      stopPolling: () => Promise<{ polling: boolean }>
      onDriftDetected: (callback: (data: DriftEvent) => void) => () => void
      pushFilesToServer: (payload: PushPayload) => Promise<PushResult>
      exportRouteBundle: (payload: RouteExportPayload) => Promise<RouteExportResult>
      setFullscreen: (isFullscreen: boolean) => Promise<boolean>
      isFullscreen: () => Promise<boolean>
      onFullscreenChange: (callback: (isFullscreen: boolean) => void) => () => void
    }
  }
}

