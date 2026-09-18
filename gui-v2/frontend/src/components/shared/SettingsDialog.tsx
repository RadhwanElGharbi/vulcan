'use client'

import { trackEvent } from '@/lib/analytics'
import {
clearProjectLocalCacheRuntime,
ensureProjectLocalCacheRuntime,
getProjectLocalCacheSettings,
patchProjectLocalCacheConfig,
patchUserSettings,
type ProjectLocalCacheConfig
} from '@/lib/api/dataClient'
import { readSession,writeSession } from '@/lib/context/MapViewContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import {
AlertTriangle,
CheckCircle2,
ChevronDown,
Download,
FolderOpen,
Globe,
HardDrive,
Map as MapIcon,
Maximize,
Minimize,
Monitor,
RefreshCw,
Settings,
X
} from 'lucide-react'
import { useCallback,useEffect,useRef,useState } from 'react'
import { createPortal } from 'react-dom'

// ---------------------------------------------------------------------------
// Resolution options
// ---------------------------------------------------------------------------
export type ResolutionOption = 'native' | '3840x2160' | '2560x1440' | '1920x1080' | '1600x900' | '1366x768' | '1280x720'

export interface ResolutionEntry {
  value: ResolutionOption
  label: string
  description: string
  width: number | null
}

export const RESOLUTIONS: ResolutionEntry[] = [
  { value: 'native',     label: 'Native',       description: 'Viewport default',  width: null },
  { value: '3840x2160',  label: '3840 × 2160',  description: '4K Ultra HD',       width: 3840 },
  { value: '2560x1440',  label: '2560 × 1440',  description: 'QHD / 2K',          width: 2560 },
  { value: '1920x1080',  label: '1920 × 1080',  description: 'Full HD',           width: 1920 },
  { value: '1600x900',   label: '1600 × 900',   description: 'HD+',               width: 1600 },
  { value: '1366x768',   label: '1366 × 768',   description: 'HD',                width: 1366 },
  { value: '1280x720',   label: '1280 × 720',   description: '720p',              width: 1280 },
]

// ---------------------------------------------------------------------------
// Persistence helpers
// ---------------------------------------------------------------------------
const SETTING_KEY = 'gui_resolution'

export function getStoredResolution(): ResolutionOption {
  return readSession<ResolutionOption>(SETTING_KEY, 'native')
}

export function setStoredResolution(value: ResolutionOption) {
  writeSession(SETTING_KEY, value)
}

export function computeScaleLayout(resolution: ResolutionOption): {
  width: number
  height: number
  scale: number
} | null {
  const entry = RESOLUTIONS.find((r) => r.value === resolution)
  if (!entry?.width) return null
  if (typeof window === 'undefined') return null
  const vw = window.innerWidth
  const vh = window.innerHeight
  const scale = vw / entry.width
  const height = Math.round(vh / scale)
  return { width: entry.width, height, scale }
}

function defaultProjectCacheConfig(): ProjectLocalCacheConfig {
  return { enabled: false, base_directory: null, last_sync_at: null, last_sync_fingerprint: null, last_check_at: null, last_discrepancy: null }
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1 }
  return `${value.toFixed(value >= 100 || index === 0 ? 0 : 1)} ${units[index]}`
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
interface SettingsDialogProps {
  open: boolean
  onClose: () => void
  resolution: ResolutionOption
  onResolutionChange: (value: ResolutionOption) => void
}

export function SettingsDialog({ open, onClose, resolution, onResolutionChange }: SettingsDialogProps) {
  const [mounted, setMounted] = useState(false)
  const [isClosing, setIsClosing] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [projectCacheConfig, setProjectCacheConfig] = useState<ProjectLocalCacheConfig>(defaultProjectCacheConfig)
  const [cacheCheckResult, setCacheCheckResult] = useState<LocalCacheCheckResult | null>(null)
  const [cacheError, setCacheError] = useState<string | null>(null)
  const [cacheBusyAction, setCacheBusyAction] = useState<'choose' | 'check' | 'sync' | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const prevOpenRef = useRef(open)
  const { currentProject, refreshProjectData } = useProject()

  const hasProject = Boolean(currentProject)
  const hasElectronBridge = typeof window !== 'undefined' && Boolean(
    window.electron &&
    typeof window.electron.pickLocalCacheDirectory === 'function' &&
    typeof window.electron.checkProjectLocalCache === 'function' &&
    typeof window.electron.syncProjectLocalCache === 'function'
  )

  useEffect(() => { setMounted(true) }, [])

  useEffect(() => {
    if (prevOpenRef.current === open) return
    trackEvent('dialog', 'SettingsDialog', open ? 'open_settings_dialog' : 'close_settings_dialog', {
      project: currentProject
    })
    prevOpenRef.current = open
  }, [currentProject, open])

  useEffect(() => {
    if (window.electron?.onFullscreenChange) {
      window.electron.isFullscreen?.().then(setIsFullscreen)
      return window.electron.onFullscreenChange(setIsFullscreen)
    }
    const handler = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', handler)
    return () => document.removeEventListener('fullscreenchange', handler)
  }, [])

  const toggleFullscreen = useCallback(() => {
    if (window.electron?.setFullscreen) {
      void window.electron.setFullscreen(!isFullscreen)
    } else {
      if (document.fullscreenElement) void document.exitFullscreen()
      else void document.documentElement.requestFullscreen()
    }
  }, [isFullscreen])

  const handleClose = useCallback(() => {
    setIsClosing(true)
    setTimeout(() => { setIsClosing(false); onClose() }, 200)
  }, [onClose])

  const handleSelect = useCallback((value: ResolutionOption) => {
    onResolutionChange(value)
    setStoredResolution(value)
    setDropdownOpen(false)
    void patchUserSettings({ resolution: value }, 'device')
  }, [onResolutionChange])

  useEffect(() => {
    if (!dropdownOpen) return
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) setDropdownOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [dropdownOpen])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { dropdownOpen ? setDropdownOpen(false) : handleClose() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, dropdownOpen, handleClose])

  useEffect(() => { if (!open) setDropdownOpen(false) }, [open])

  useEffect(() => {
    if (!open || !currentProject) {
      setProjectCacheConfig(defaultProjectCacheConfig())
      setCacheCheckResult(null)
      setCacheError(null)
      return
    }
    const cachedSettings = getProjectLocalCacheSettings()
    setProjectCacheConfig(cachedSettings[currentProject] ?? defaultProjectCacheConfig())
    setCacheCheckResult(null)
    setCacheError(null)
  }, [open, currentProject])

  const saveProjectConfig = useCallback(async (nextConfig: ProjectLocalCacheConfig) => {
    if (!currentProject) return null
    try {
      const updated = await patchProjectLocalCacheConfig(currentProject, nextConfig)
      const persisted = updated[currentProject] ?? nextConfig
      setProjectCacheConfig(persisted)
      return persisted
    } catch (error) {
      setCacheError(error instanceof Error ? error.message : 'Failed to save settings.')
      return null
    }
  }, [currentProject])

  const activateLocalRuntime = useCallback(async (config: ProjectLocalCacheConfig) => {
    if (!currentProject) return
    if (!config.enabled || !config.base_directory) {
      clearProjectLocalCacheRuntime(currentProject)
      if (window.electron?.stopLocalCacheService) {
        try { await window.electron.stopLocalCacheService() } catch {}
      }
      if (window.electron?.stopPolling) {
        try { await window.electron.stopPolling() } catch {}
      }
      return
    }
    await ensureProjectLocalCacheRuntime(currentProject, config)
    if (window.electron?.startPolling) {
      try { await window.electron.startPolling({ projectName: currentProject, baseDirectory: config.base_directory }) } catch {}
    }
  }, [currentProject])

  const handleToggle = useCallback(async (enabled: boolean) => {
    if (!currentProject) return
    setCacheError(null)
    const nextConfig: ProjectLocalCacheConfig = { ...projectCacheConfig, enabled }
    const persisted = await saveProjectConfig(nextConfig)
    if (!persisted) return
    await activateLocalRuntime(persisted)
    await refreshProjectData()
  }, [activateLocalRuntime, currentProject, projectCacheConfig, refreshProjectData, saveProjectConfig])

  const handlePickDirectory = useCallback(async () => {
    if (!currentProject || !window.electron?.pickLocalCacheDirectory) return
    setCacheError(null)
    setCacheBusyAction('choose')
    try {
      const result = await window.electron.pickLocalCacheDirectory()
      if (result.cancelled || !result.directory) return
      const nextConfig: ProjectLocalCacheConfig = { ...projectCacheConfig, base_directory: result.directory }
      const persisted = await saveProjectConfig(nextConfig)
      if (persisted?.enabled) {
        await activateLocalRuntime(persisted)
        await refreshProjectData()
      }
    } catch (error) {
      setCacheError(error instanceof Error ? error.message : 'Failed to select directory.')
    } finally {
      setCacheBusyAction(null)
    }
  }, [activateLocalRuntime, currentProject, projectCacheConfig, refreshProjectData, saveProjectConfig])

  const handleCheck = useCallback(async () => {
    if (!currentProject || !window.electron?.checkProjectLocalCache) return
    if (!projectCacheConfig.base_directory) { setCacheError('Pick a local directory first.'); return }
    setCacheError(null)
    setCacheBusyAction('check')
    try {
      const result = await window.electron.checkProjectLocalCache({
        projectName: currentProject, baseDirectory: projectCacheConfig.base_directory
      })
      setCacheCheckResult(result)
      await saveProjectConfig({ ...projectCacheConfig, last_check_at: new Date().toISOString(), last_discrepancy: result.discrepancy })
    } catch (error) {
      setCacheError(error instanceof Error ? error.message : 'Check failed.')
    } finally {
      setCacheBusyAction(null)
    }
  }, [currentProject, projectCacheConfig, saveProjectConfig])

  const handleSync = useCallback(async () => {
    if (!currentProject || !window.electron?.syncProjectLocalCache) return
    if (!projectCacheConfig.base_directory) { setCacheError('Pick a local directory first.'); return }
    setCacheError(null)
    setCacheBusyAction('sync')
    try {
      const result = await window.electron.syncProjectLocalCache({
        projectName: currentProject, baseDirectory: projectCacheConfig.base_directory
      })
      const nowIso = new Date().toISOString()
      const persisted = await saveProjectConfig({
        ...projectCacheConfig,
        last_sync_at: nowIso,
        last_sync_fingerprint: result.local_manifest.fingerprint,
        last_check_at: nowIso,
        last_discrepancy: result.discrepancy_after
      })
      setCacheCheckResult({
        project_name: result.project_name, base_directory: result.base_directory,
        project_directory: result.project_directory, remote_manifest: result.remote_manifest,
        local_manifest: result.local_manifest, discrepancy: result.discrepancy_after
      })
      if (persisted?.enabled) await activateLocalRuntime(persisted)
      await refreshProjectData()
    } catch (error) {
      setCacheError(error instanceof Error ? error.message : 'Sync failed.')
    } finally {
      setCacheBusyAction(null)
    }
  }, [activateLocalRuntime, currentProject, projectCacheConfig, refreshProjectData, saveProjectConfig])

  if (!mounted || !open) return null

  const currentEntry = RESOLUTIONS.find((r) => r.value === resolution) ?? RESOLUTIONS[0]
  const activeDiscrepancy = cacheCheckResult?.discrepancy ?? projectCacheConfig.last_discrepancy ?? null
  const cacheBusy = cacheBusyAction !== null

  return createPortal(
    <div className={cn('fixed inset-0 z-[140] flex items-center justify-center p-4 transition-opacity duration-200', isClosing ? 'opacity-0' : 'opacity-100')}>
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={handleClose} />
      <div className={cn('relative w-[780px] max-w-[96vw] max-h-[85vh] overflow-visible rounded-sm border border-white/10 bg-[#0a0a0a]/95 shadow-[0_0_60px_rgba(0,0,0,0.8)] transition-all duration-200 flex flex-col', isClosing ? 'scale-95 opacity-0' : 'scale-100 opacity-100')}>
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff03_1px,transparent_1px),linear-gradient(to_bottom,#ffffff03_1px,transparent_1px)] bg-[size:20px_20px] pointer-events-none rounded-sm overflow-hidden" />

        {/* Header */}
        <div className="relative px-6 py-5 border-b border-white/10 bg-black/40 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-sm bg-primary/10 border border-primary/20"><Settings className="w-4 h-4 text-primary" /></div>
            <div>
              <div className="text-[10px] text-white/40 uppercase tracking-[0.2em] font-mono">System</div>
              <div className="mt-0.5 text-lg font-semibold text-white tracking-wide">Settings</div>
            </div>
          </div>
          <button onClick={handleClose} className="p-2 rounded-sm text-white/40 hover:text-white hover:bg-white/5 border border-transparent hover:border-white/10 transition-all duration-200"><X className="w-4 h-4" /></button>
        </div>

        {/* Body */}
        <div className="relative p-6 space-y-6 overflow-y-auto flex-1">

          {/* Resolution */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Monitor className="w-4 h-4 text-white/40" />
              <span className="text-xs font-mono text-white/50 uppercase tracking-[0.15em]">Display Resolution</span>
            </div>
            <div className="flex items-stretch gap-2">
              <div className="relative flex-1" ref={dropdownRef}>
                <button type="button" onClick={() => setDropdownOpen(!dropdownOpen)} className={cn('w-full h-full flex items-center justify-between gap-3 px-4 py-3 rounded-sm border transition-all duration-200 text-left bg-black/40', dropdownOpen ? 'border-primary/40 shadow-[0_0_12px_rgba(var(--primary),0.1)]' : 'border-white/10 hover:border-white/20')}>
                  <div className="flex items-center gap-3">
                    <div className="text-sm font-medium text-white tracking-wide">{currentEntry.label}</div>
                    <div className="text-[10px] font-mono text-white/30 uppercase tracking-wider">{currentEntry.description}</div>
                  </div>
                  <ChevronDown className={cn('w-4 h-4 text-white/40 transition-transform duration-200', dropdownOpen && 'rotate-180')} />
                </button>
                {dropdownOpen && (
                  <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-sm border border-white/10 bg-[#0c0c0c] shadow-[0_8px_30px_rgba(0,0,0,0.6)] overflow-hidden">
                    {RESOLUTIONS.map((entry) => (
                      <button key={entry.value} onClick={() => handleSelect(entry.value)} className={cn('w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left transition-all duration-150', resolution === entry.value ? 'bg-primary/10 text-white' : 'hover:bg-white/5 text-white/70 hover:text-white')}>
                        <div className="flex items-center gap-3">
                          <span className="text-sm font-medium tracking-wide">{entry.label}</span>
                          <span className="text-[10px] font-mono text-white/30 uppercase tracking-wider">{entry.description}</span>
                        </div>
                        {resolution === entry.value && <div className="w-1.5 h-1.5 rounded-full bg-primary shadow-[0_0_6px_rgba(var(--primary),0.8)]" />}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button type="button" onClick={toggleFullscreen} title={isFullscreen ? 'Exit full screen' : 'Enter full screen'} className={cn('flex-shrink-0 flex items-center justify-center w-12 rounded-sm border transition-all duration-200 bg-black/40', isFullscreen ? 'border-primary/40 text-primary hover:bg-primary/10' : 'border-white/10 text-white/40 hover:text-white hover:border-white/20')}>
                {isFullscreen ? <Minimize className="w-4.5 h-4.5" /> : <Maximize className="w-4.5 h-4.5" />}
              </button>
            </div>
          </div>

          <div className="p-3 rounded-sm bg-white/[0.02] border border-white/5">
            <div className="text-[10px] font-mono text-white/30 leading-relaxed">
              The interface scales to render at the chosen resolution while maintaining your screen&apos;s native aspect ratio. Choose <span className="text-white/50">Native</span> for no scaling.
            </div>
          </div>

          <div className="border-t border-white/5" />

          {/* Map Projection */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Globe className="w-4 h-4 text-white/40" />
              <span className="text-xs font-mono text-white/50 uppercase tracking-[0.15em]">Map Projection Style</span>
            </div>
            <div className="flex rounded-sm border border-white/10 bg-black/40 overflow-hidden">
              <div className="flex items-center gap-2.5 px-4 py-3 text-white">
                <Globe className="w-4 h-4" />
                <span className="text-sm font-medium tracking-wide">3D Globe</span>
                <span className="text-[10px] font-mono text-white/30 uppercase tracking-wider">Atmosphere &amp; Star Field</span>
              </div>
            </div>
            <div className="mt-3 p-3 rounded-sm bg-white/[0.02] border border-white/5">
              <div className="text-[10px] font-mono text-white/30 leading-relaxed">
                Left-drag to pan, middle-drag to rotate and tilt, right-drag to zoom. The 2D map is available only when picking an AOI for a new project.
              </div>
            </div>
          </div>

          <div className="border-t border-white/5" />

          {/* Project Local Data Cache */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <HardDrive className="w-4 h-4 text-white/40" />
              <span className="text-xs font-mono text-white/50 uppercase tracking-[0.15em]">Project Local Data Cache</span>
            </div>

            {!hasProject && (
              <div className="p-3 rounded-sm bg-white/[0.02] border border-white/5 text-[10px] font-mono text-white/30 leading-relaxed">
                Select a project to configure local cache settings.
              </div>
            )}

            {hasProject && (
              <div className="space-y-3">
                {/* Project name + toggle */}
                <div className="flex items-center justify-between p-3 rounded-sm border border-white/10 bg-black/30">
                  <div>
                    <div className="text-[10px] font-mono text-white/30 uppercase tracking-wider">Project</div>
                    <div className="text-sm text-white/80 mt-1">{currentProject}</div>
                  </div>
                  <button type="button" onClick={() => { void handleToggle(!projectCacheConfig.enabled) }} disabled={cacheBusy} className={cn('px-3 py-1.5 rounded-sm text-[10px] font-mono uppercase tracking-wider border transition-all', projectCacheConfig.enabled ? 'border-emerald-500/40 text-emerald-300 bg-emerald-500/10' : 'border-white/10 text-white/50 hover:text-white/70 hover:border-white/20', cacheBusy && 'opacity-60 pointer-events-none')}>
                    {projectCacheConfig.enabled ? 'Enabled' : 'Disabled'}
                  </button>
                </div>

                {projectCacheConfig.enabled && (
                  <>
                    {/* Directory display + picker */}
                    <div className="flex gap-2">
                      <div className="flex-1 px-3 py-2.5 rounded-sm border border-white/10 bg-black/40 text-xs text-white/80 font-mono truncate">
                        {projectCacheConfig.base_directory || <span className="text-white/30">No directory selected</span>}
                      </div>
                      <button type="button" onClick={() => { void handlePickDirectory() }} disabled={!hasElectronBridge || cacheBusy} className={cn('px-3 py-2.5 rounded-sm border border-white/10 text-xs font-mono text-white/70 hover:text-white hover:border-white/20 transition-all flex items-center gap-2', (!hasElectronBridge || cacheBusy) && 'opacity-50 pointer-events-none')}>
                        <FolderOpen className="w-3.5 h-3.5" />
                        Choose
                      </button>
                    </div>

                    {/* Check + Sync buttons */}
                    <div className="flex gap-2">
                      <button type="button" onClick={() => { void handleCheck() }} disabled={!hasElectronBridge || !projectCacheConfig.base_directory || cacheBusy} className={cn('flex-1 px-3 py-2 rounded-sm border border-white/10 text-xs font-mono text-white/70 hover:text-white hover:border-white/20 transition-all flex items-center justify-center gap-2', (!hasElectronBridge || !projectCacheConfig.base_directory || cacheBusy) && 'opacity-50 pointer-events-none')}>
                        <RefreshCw className={cn('w-3.5 h-3.5', cacheBusyAction === 'check' && 'animate-spin')} />
                        Check
                      </button>
                      <button type="button" onClick={() => { void handleSync() }} disabled={!hasElectronBridge || !projectCacheConfig.base_directory || cacheBusy} className={cn('flex-1 px-3 py-2 rounded-sm border border-primary/40 text-xs font-mono text-primary hover:bg-primary/10 transition-all flex items-center justify-center gap-2', (!hasElectronBridge || !projectCacheConfig.base_directory || cacheBusy) && 'opacity-50 pointer-events-none')}>
                        <Download className={cn('w-3.5 h-3.5', cacheBusyAction === 'sync' && 'animate-spin')} />
                        Sync
                      </button>
                    </div>

                    {/* Discrepancy info */}
                    {(cacheCheckResult || projectCacheConfig.last_sync_at || activeDiscrepancy) && (
                      <div className="p-3 rounded-sm bg-white/[0.02] border border-white/5 space-y-1.5">
                        {cacheCheckResult && (
                          <div className="text-[10px] font-mono text-white/35">
                            Remote: {cacheCheckResult.remote_manifest.file_count} files ({formatBytes(cacheCheckResult.remote_manifest.total_size_bytes)})
                            {' | '}
                            Local: {cacheCheckResult.local_manifest.file_count} files ({formatBytes(cacheCheckResult.local_manifest.total_size_bytes)})
                          </div>
                        )}
                        {activeDiscrepancy && (
                          <div className="text-[10px] font-mono text-white/40 flex items-center gap-2">
                            {activeDiscrepancy.in_sync
                              ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                              : <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />}
                            <span>Missing {activeDiscrepancy.missing_count} | Changed {activeDiscrepancy.changed_count} | Extra {activeDiscrepancy.extra_count}</span>
                          </div>
                        )}
                        {projectCacheConfig.last_sync_at && (
                          <div className="text-[10px] font-mono text-white/30">Last sync: {new Date(projectCacheConfig.last_sync_at).toLocaleString()}</div>
                        )}
                      </div>
                    )}

                    {/* Bridge missing warning */}
                    {!hasElectronBridge && (
                      <div className="p-2.5 rounded-sm border border-amber-500/20 bg-amber-500/10 text-[10px] font-mono text-amber-100">
                        Local cache requires ZEUS Desktop v2.3.0+. Update the app to use this feature.
                      </div>
                    )}
                  </>
                )}

                {!projectCacheConfig.enabled && (
                  <div className="p-3 rounded-sm border border-white/10 bg-black/30 text-[10px] font-mono text-white/40">
                    Enable local cache to sync project data to your workstation for faster loading.
                  </div>
                )}

                {cacheError && (
                  <div className="p-2.5 rounded-sm border border-red-500/20 bg-red-500/10 text-[10px] font-mono text-red-200">{cacheError}</div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}

