'use client'

import { DatasetCoverageDialog } from '@/components/Project/DatasetCoverageDialog'
import { ProjectProfileDialog } from '@/components/Project/ProjectProfileDialog'
import { ProjectSelector } from '@/components/Project/ProjectSelector'
import { SettingsDialog,type ResolutionOption } from '@/components/shared/SettingsDialog'
import { ZeusLoadingDialog } from '@/components/shared/ZeusLoadingDialog'
import { readSession,useMapView,writeSession } from '@/lib/context/MapViewContext'
import { TourAction,useOnboarding } from '@/lib/context/OnboardingContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import { getApiBase } from '@/lib/api/dataClient'
import {
AlertTriangle,
ChevronLeft,
ChevronRight,
Download,
Github,
Layers,
Map,
Settings,
Target,
Terminal
} from 'lucide-react'
import { useCallback,useEffect,useState } from 'react'
import { createPortal } from 'react-dom'
// removed DigitalTwinView import

interface SidebarProps {
  className?: string
  devMode?: boolean
  isBackendOnline: boolean
  activeView: 'map' | 'digital-twin' | 'project-management'
  onViewChange: (view: 'map' | 'digital-twin' | 'project-management') => void
  onDatasetRunInBackground?: (jobId: string) => void
  resolution: ResolutionOption
  onResolutionChange: (value: ResolutionOption) => void
}

export function Sidebar({ className, devMode = false, isBackendOnline, activeView, onViewChange, onDatasetRunInBackground, resolution, onResolutionChange }: SidebarProps) {
  const { currentProject } = useProject()
  const { registerGisActions, gis } = useMapView()
  const { reportAction } = useOnboarding()
  const [collapsed, _setCollapsed] = useState(false)
  useEffect(() => { _setCollapsed(readSession<boolean>('sidebar_collapsed', false)) }, [])
  const setCollapsed = useCallback((v: boolean) => {
    _setCollapsed(v)
    writeSession('sidebar_collapsed', v)
  }, [])
  const [showDatasets, setShowDatasets] = useState(false)
  const [showProfile, setShowProfile] = useState(false)
  const [showProjectControls, setShowProjectControls] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [collapsedNavTooltip, setCollapsedNavTooltip] = useState<{
    label: string
    description: string
    left: number
    top: number
  } | null>(null)
  const [localChangesDetected, setLocalChangesDetected] = useState(false)
  const [driftDiscrepancy, setDriftDiscrepancy] = useState<LocalCacheDiscrepancy | null>(null)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.electron?.onDriftDetected) return
    const unsubscribe = window.electron.onDriftDetected((data) => {
      if (data.direction === 'local-ahead' || data.direction === 'both') {
        setLocalChangesDetected(true)
        setDriftDiscrepancy(data.discrepancy)
      } else {
        setLocalChangesDetected(false)
        setDriftDiscrepancy(data.discrepancy)
      }
    })
    return () => { unsubscribe() }
  }, [])
  useEffect(() => {
    if (!collapsed) {
      setCollapsedNavTooltip(null)
    }
  }, [collapsed])
  const [isZeusAnalyzing, setIsZeusAnalyzing] = useState(false)

  useEffect(() => {
    if (currentProject && !collapsed) {
      setCollapsed(true)
    }
  }, [currentProject]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDatasetsClick = useCallback(() => {
    reportAction('click-datasets')
    if (devMode) {
      setShowDatasets(true)
      return
    }
    setShowDatasets(true)
  }, [devMode, reportAction])

  const handleAnalysisComplete = useCallback(() => {
    setIsZeusAnalyzing(false)
    setShowDatasets(true)
  }, [])

  // Archived standalone fetch action. Retained with its dialog for future restoration.
  useEffect(() => {
    registerGisActions({
      openFetchDatasets: () => {
        if (!currentProject) return
        handleDatasetsClick()
      }
    })
  }, [currentProject, handleDatasetsClick, registerGisActions])

  const handleCollapsedTooltipOpen = useCallback((target: HTMLButtonElement, label: string, description: string) => {
    if (!collapsed) return
    const rect = target.getBoundingClientRect()
    setCollapsedNavTooltip({
      label,
      description,
      left: rect.right + 12,
      top: rect.top + rect.height / 2
    })
  }, [collapsed])

  const handleCollapsedTooltipClose = useCallback(() => {
    setCollapsedNavTooltip(null)
  }, [])

  const handleNavClick = (onClick: (() => void) | undefined, tourAction?: TourAction) => {
    if (tourAction) {
      reportAction(tourAction)
    }
    setCollapsedNavTooltip(null)
    onClick?.()
    if (!collapsed) {
      setCollapsed(true)
    }
  }

  const navigationItems = [
    { icon: Target, label: 'Project Profile', onClick: () => currentProject && setShowProfile(true), description: 'Metadata & CRS', disabled: !currentProject, tourId: 'sidebar-profile' },
    { icon: Map, label: 'Map View', active: activeView === 'map', onClick: () => onViewChange('map'), description: 'Main Interface', tourId: 'sidebar-map' },


    { icon: Layers, label: 'Digital Twin', onClick: () => currentProject && gis.openDatasetDigitalTwin(), description: 'Explore & fetch datasets', disabled: !currentProject, tourId: 'sidebar-datasets' },
    { icon: Download, label: 'Download', onClick: () => {
      if (currentProject) window.open(`${getApiBase()}/projects/${encodeURIComponent(currentProject)}/package`, '_blank', 'noopener,noreferrer')
    }, description: 'Download loaded project assets', disabled: !currentProject, tourId: 'sidebar-download' },


    { icon: Settings, label: 'Settings', description: 'System Config', onClick: () => setShowSettings(true), tourId: 'sidebar-settings' },
  ]
  const backendStatusLabel = isBackendOnline ? 'Online' : 'Offline'
  const backendStatusDotClass = isBackendOnline
    ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]'
    : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)]'
  const backendStatusPulseClass = isBackendOnline ? 'bg-emerald-500' : 'bg-red-500'
  const backendStatusTooltip = isBackendOnline ? 'System Operational' : 'Backend Offline'

  return (
    <div
      className={cn(
        "relative flex flex-col bg-[#0a0a0a]/95 backdrop-blur-xl border-r border-white/10 transition-all duration-300 overflow-hidden shadow-[10px_0_30px_-10px_rgba(0,0,0,0.5)]",
        collapsed ? "w-20" : "w-80",
        className
      )}
      data-sidebar="main"
      data-tour="sidebar"
    >
      {/* Technical Background Grid */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff03_1px,transparent_1px),linear-gradient(to_bottom,#ffffff03_1px,transparent_1px)] bg-[size:20px_20px] pointer-events-none" />
      
      {/* Header */}
      <div className="relative flex items-center justify-between px-3 py-6 border-b border-white/10 bg-black/20 z-10 h-[88px]">
        <span
          className={cn('leading-none text-[#902316] whitespace-nowrap', collapsed ? 'text-[18px]' : 'text-[44px]')}
          style={{ fontFamily: '"Baskerville Old Face", "Baskerville", "Times New Roman", serif' }}
        >
          Vulcan
        </span>

        {/* Futuristic Collapse Button */}
        <button
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          onClick={() => setCollapsed(!collapsed)}
          className="absolute -right-3 top-10 p-1.5 bg-[#0a0a0a] border border-white/10 text-white/40 hover:text-primary hover:border-primary/50 hover:bg-primary/5 rounded-sm rotate-45 transition-all duration-300 shadow-lg z-50 group"
        >
          <div className="-rotate-45">
            {collapsed ? (
              <ChevronRight className="w-3 h-3 group-hover:translate-x-0.5 transition-transform" />
            ) : (
              <ChevronLeft className="w-3 h-3 group-hover:-translate-x-0.5 transition-transform" />
            )}
          </div>
        </button>
      </div>

      {/* Project Selector */}
      {!collapsed && (
        <div className="p-4 border-b border-white/5 bg-white/[0.01] relative z-10" data-tour="project-selector">
          <div className="flex items-center gap-2 mb-2 pl-1">
            <Terminal className="w-3 h-3 text-primary/50" />
            <span className="text-[10px] text-white/30 font-mono uppercase tracking-widest">Active Operation</span>
          </div>
          <ProjectSelector />
        </div>
      )}

      {/* Navigation */}
      <nav className="flex-1 p-3 space-y-1.5 overflow-y-auto relative z-10">
        {navigationItems.map((item, index) => {
          const Icon = item.icon
          return (
            <button
              aria-label={item.label}
              key={index}
              className={cn(
                "group relative w-full flex items-center gap-3 px-3 py-2.5 rounded-sm transition-all duration-300 border border-transparent",
                item.active
                  ? "bg-primary/10 border-primary/20 text-white"
                  : item.disabled
                  ? "cursor-not-allowed opacity-50"
                  : "hover:bg-white/5 hover:border-white/10 text-muted-foreground hover:text-white"
              )}
              onClick={() => handleNavClick(item.onClick, undefined)}
              onMouseEnter={(event) => handleCollapsedTooltipOpen(event.currentTarget, item.label, item.description)}
              onMouseLeave={handleCollapsedTooltipClose}
              onFocus={(event) => handleCollapsedTooltipOpen(event.currentTarget, item.label, item.description)}
              onBlur={handleCollapsedTooltipClose}
              disabled={item.disabled}
              data-tour={item.tourId}
            >
              {/* Active Indicator Line */}
              {item.active && (
                <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-primary shadow-[0_0_10px_rgba(var(--primary),0.8)]" />
              )}

              <div className={cn(
                "p-1.5 rounded-sm transition-colors duration-300",
                item.active ? "text-primary" : "text-white/40 group-hover:text-white/80"
              )}>
                <Icon className="w-5 h-5 flex-shrink-0" />
              </div>
              
              {!collapsed && (
                <div className="flex flex-col items-start text-left">
                  <span className={cn(
                    "text-sm font-medium tracking-wide transition-colors",
                    item.active ? "text-white" : "group-hover:text-white"
                  )}>{item.label}</span>
                  <span className="text-[10px] font-mono text-white/30 uppercase tracking-wider group-hover:text-white/50 transition-colors">
                    {item.description}
                  </span>
                </div>
              )}

              {/* Status Tag */}
              {(item as { tag?: string }).tag && !collapsed && (
                 <div className="ml-auto pl-2">
                    <div className="px-1.5 py-0.5 rounded-sm bg-red-500/10 border border-red-500/30 shadow-[0_0_5px_rgba(239,68,68,0.4)] animate-pulse">
                      <span className="text-[8px] font-bold font-mono text-red-500 uppercase tracking-wider whitespace-nowrap block transform scale-90">{(item as { tag?: string }).tag}</span>
                    </div>
                 </div>
              )}

              {/* Hover Glow Effect */}
              {!item.active && (
                <div className="absolute inset-0 bg-white/5 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none" />
              )}
            </button>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-white/10 bg-black/20 relative z-10 backdrop-blur-sm">
        <a href={process.env.NEXT_PUBLIC_GITHUB_URL || 'https://github.com/RadhwanElGharbi/vulcan'}
          target="_blank" rel="noopener noreferrer" aria-label="Vulcan on GitHub" title="Vulcan on GitHub"
          className={cn('mb-4 flex items-center gap-2 rounded-sm p-2 text-white/40 transition-colors hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/40', collapsed && 'justify-center')}>
          <Github className="h-5 w-5 shrink-0" strokeWidth={1.5} />
          {!collapsed && <span className="text-xs">GitHub</span>}
        </a>
        {!collapsed ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2">
                <div className="relative">
                  <div className={cn('w-2 h-2 rounded-full animate-pulse', backendStatusDotClass)} />
                  <div className={cn('absolute inset-0 w-2 h-2 rounded-full opacity-50 animate-ping', backendStatusPulseClass)} />
                </div>
                <span className="text-white/60 font-mono uppercase tracking-widest text-[10px]">{backendStatusLabel}</span>
              </div>
              {localChangesDetected && (
                <button
                  type="button"
                  onClick={() => setShowProjectControls(true)}
                  className="flex items-center gap-1.5 px-1.5 py-0.5 rounded-sm bg-red-500/10 border border-red-500/30 hover:bg-red-500/20 transition-all group"
                  title="Local changes detected — open Project Controls"
                >
                  <AlertTriangle className="w-3 h-3 text-red-500 animate-pulse" />
                  <span className="text-[8px] font-bold font-mono text-red-500 uppercase tracking-wider">Local Changes</span>
                </button>
              )}
              <div className="text-[10px] text-white/30 font-mono">
                VULCAN
              </div>
            </div>
          </div>
        ) : (
          <div className="flex justify-center">
             <div className="relative group cursor-help">
                <div className={cn('w-2 h-2 rounded-full animate-pulse', backendStatusDotClass)} />
                <div className="absolute left-full ml-2 top-1/2 -translate-y-1/2 px-2 py-1 bg-black border border-white/20 text-[10px] text-white opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-50 pointer-events-none">
                    {backendStatusTooltip}
                </div>
            </div>
          </div>
        )}
      </div>

      {!devMode && (
        <ZeusLoadingDialog open={isZeusAnalyzing} onComplete={handleAnalysisComplete} />
      )}
      <DatasetCoverageDialog
        open={showDatasets}
        onClose={() => setShowDatasets(false)}
        onRunInBackground={onDatasetRunInBackground}
      />
      <ProjectProfileDialog open={showProfile} onClose={() => setShowProfile(false)} />
      
      
      <SettingsDialog
        open={showSettings}
        onClose={() => setShowSettings(false)}
        resolution={resolution}
        onResolutionChange={onResolutionChange}
      />
      {collapsedNavTooltip && typeof document !== 'undefined' && createPortal(
        <div
          className="fixed z-[120] pointer-events-none -translate-y-1/2"
          style={{ left: `${collapsedNavTooltip.left}px`, top: `${collapsedNavTooltip.top}px` }}
        >
          <div className="relative">
            <div className="absolute -left-1 top-1/2 h-2 w-2 -translate-y-1/2 rotate-45 border-l border-b border-primary/40 bg-[#05070a]/95" />
            <div className="relative overflow-hidden rounded-sm border border-primary/40 bg-[#05070a]/95 px-3 py-2 shadow-[0_0_18px_-6px_rgba(var(--primary),0.8)] backdrop-blur-xl">
              <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff08_1px,transparent_1px),linear-gradient(to_bottom,#ffffff08_1px,transparent_1px)] bg-[size:12px_12px] opacity-30" />
              <div className="relative flex flex-col items-start">
                <span className="text-[10px] font-mono uppercase tracking-widest text-white/90 whitespace-nowrap">
                  {collapsedNavTooltip.label}
                </span>
                <span className="text-[9px] font-mono uppercase tracking-wide text-white/50 whitespace-nowrap">
                  {collapsedNavTooltip.description}
                </span>
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}
