'use client'

import { BackgroundJobIndicator } from '@/components/Project/BackgroundJobIndicator'
import { DatasetFetchProgressDialog } from '@/components/Project/DatasetFetchProgressDialog'
import { ProjectLoadingDialog } from '@/components/shared/ProjectLoadingDialog'
import {
computeScaleLayout,
getStoredResolution,
setStoredResolution,
type ResolutionOption,
} from '@/components/shared/SettingsDialog'
import type { DatasetFetchJob } from '@/lib/api/dataClient'
import { getApiBase } from '@/lib/api/dataClient'
import { useMapView } from '@/lib/context/MapViewContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import { ReactNode,useCallback,useEffect,useState } from 'react'
import { Header } from './Header'
import { Sidebar } from './Sidebar'

interface MainLayoutProps {
  children: ReactNode
}

export function MainLayout({ children }: MainLayoutProps) {
  const { isProjectLoading, error, refreshProjectData } = useProject()
  const { mapUiIdle } = useMapView()
  const [devMode, setDevMode] = useState(false)
  const [activeView, setActiveView] = useState<'map' | 'digital-twin' | 'project-management'>('map')
  const [resolution, setResolution] = useState<ResolutionOption>(() => getStoredResolution())
  const [scaleLayout, setScaleLayout] = useState<ReturnType<typeof computeScaleLayout>>(null)

  const handleResolutionChange = useCallback((value: ResolutionOption) => {
    setResolution(value)
    setStoredResolution(value)
    setScaleLayout(computeScaleLayout(value))
  }, [])

  // Recompute scale on mount and window resize
  useEffect(() => {
    const update = () => setScaleLayout(computeScaleLayout(resolution))
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [resolution])
  const [isBackendOnline, setIsBackendOnline] = useState(true)

  // Shared backend reachability for sidebar indicator + topbar notifications.
  // Use hysteresis to avoid flapping during short network hiccups.
  useEffect(() => {
    const OFFLINE_FAILURE_THRESHOLD = 2
    const ONLINE_SUCCESS_THRESHOLD = 2

    let cancelled = false
    let isChecking = false
    let stableOnline = true
    let consecutiveFailures = 0
    let consecutiveSuccesses = 0

    const applyProbeResult = (isHealthy: boolean) => {
      if (isHealthy) {
        consecutiveSuccesses += 1
        consecutiveFailures = 0
        if (!stableOnline && consecutiveSuccesses >= ONLINE_SUCCESS_THRESHOLD) {
          stableOnline = true
          if (!cancelled) setIsBackendOnline(true)
        }
        return
      }

      consecutiveFailures += 1
      consecutiveSuccesses = 0
      if (stableOnline && consecutiveFailures >= OFFLINE_FAILURE_THRESHOLD) {
        stableOnline = false
        if (!cancelled) setIsBackendOnline(false)
      }
    }

    const checkBackendHealth = async () => {
      if (isChecking) return
      isChecking = true

      const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
      const timeoutHandle = setTimeout(() => {
        controller?.abort()
      }, 2500)

      try {
        const response = await fetch(`${getApiBase()}/health`, {
          method: 'GET',
          cache: 'no-store',
          signal: controller?.signal
        })
        applyProbeResult(response.ok)
      } catch {
        applyProbeResult(false)
      } finally {
        clearTimeout(timeoutHandle)
        isChecking = false
      }
    }

    void checkBackendHealth()
    const interval = setInterval(() => {
      void checkBackendHealth()
    }, 5000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  // Background dataset job state
  const [backgroundJobId, setBackgroundJobId] = useState<string | null>(null)
  const [showExpandedJobDialog, setShowExpandedJobDialog] = useState(false)

  // Handle running dataset fetch in background
  const handleRunInBackground = useCallback((jobId: string) => {
    setBackgroundJobId(jobId)
    setShowExpandedJobDialog(false)
  }, [])

  // Handle expanding background job to dialog
  const handleExpandBackgroundJob = useCallback(() => {
    setShowExpandedJobDialog(true)
  }, [])

  // Handle background job completion
  const handleBackgroundJobFinished = useCallback((result: DatasetFetchJob) => {
    // Keep indicator visible for a moment to show completion status
    setTimeout(() => {
      setBackgroundJobId(null)
    }, 3000)
    // Refresh project data
    void refreshProjectData()
  }, [refreshProjectData])

  // Handle closing expanded dialog
  const handleExpandedDialogClose = useCallback(() => {
    setShowExpandedJobDialog(false)
    // If job is still running, keep it in background
    // If job is complete, clear it
  }, [])
  const fadeMapChrome = activeView === 'map' && mapUiIdle

  return (
    <div id="zeus-workspace" aria-busy={isProjectLoading} className="h-screen w-screen overflow-hidden bg-black">
    <div
      className={cn(
        "flex overflow-hidden bg-background",
        fadeMapChrome && "cursor-none"
      )}
      style={
        scaleLayout
          ? {
              width: `${scaleLayout.width}px`,
              height: `${scaleLayout.height}px`,
              transform: `scale(${scaleLayout.scale})`,
              transformOrigin: '0 0',
            }
          : { width: '100vw', height: '100vh' }
      }
    >
      {/* Sidebar */}
      <Sidebar
        devMode={devMode}
        isBackendOnline={isBackendOnline}
        activeView={activeView}
        onViewChange={setActiveView}
        onDatasetRunInBackground={handleRunInBackground}
        resolution={resolution}
        onResolutionChange={handleResolutionChange}
        className={cn(
          "transition-all duration-700 ease-out",
          fadeMapChrome ? "w-0 min-w-0 opacity-0 pointer-events-none border-r-0" : "opacity-100"
        )}
      />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <Header 
          devMode={devMode} 
          isBackendOnline={isBackendOnline}
          onDevModeChange={setDevMode}
          activeView={activeView}
          className={cn(
            "transition-all duration-700 ease-out",
            fadeMapChrome ? "h-0 opacity-0 pointer-events-none border-b-0 px-0 overflow-hidden" : "opacity-100"
          )}
        />

        {/* Content */}
        <main className="flex-1 relative overflow-hidden">
          {activeView === 'map' && children}
          
          
        </main>
      </div>

      {/* Supplier Search Dialog */}
      

      {/* Project Loading Dialog */}
      <ProjectLoadingDialog open={isProjectLoading} />
      {error && <div role="alert" className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[100] max-w-xl border border-red-500/40 bg-black/90 px-5 py-3 text-sm text-red-300">{error}</div>}

      {/* YC Demo Onboarding */}
      
      

      {/* App Update Notification */}
      

      {/* Background Job Indicator */}
      {backgroundJobId && !showExpandedJobDialog && (
        <BackgroundJobIndicator
          jobId={backgroundJobId}
          onExpand={handleExpandBackgroundJob}
          onJobFinished={handleBackgroundJobFinished}
        />
      )}

      {/* Expanded Background Job Dialog */}
      <DatasetFetchProgressDialog
        jobId={backgroundJobId}
        open={showExpandedJobDialog && Boolean(backgroundJobId)}
        onClose={handleExpandedDialogClose}
        onJobFinished={handleBackgroundJobFinished}
        onRunInBackground={() => setShowExpandedJobDialog(false)}
      />
    </div>
    </div>
  )
}

