/**
 * AGRS ZEUS GUI v2 - Project Selection Dialog
 * 
 * High-fidelity interface for project selection with "futuristic/exclusive" aesthetic.
 * Local project selection and creation.
 */

'use client'

import { trackEvent } from '@/lib/analytics'
import {
ProjectMetadata
} from '@/lib/api/dataClient'
import { useOnboarding } from '@/lib/context/OnboardingContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import {
Calendar,
ChevronRight,
Database,
Folder,
Globe,
Loader2,
MapPin,
Plus,
RefreshCw,
ShieldCheck,
Terminal,
User,
X
} from 'lucide-react'
import React,{ useEffect,useRef,useState } from 'react'
import { createPortal } from 'react-dom'
import { CreateProjectWizard } from './CreateProjectWizard'
import { WorkspaceDirectory } from './WorkspaceDirectory'

interface ProjectSelectionDialogProps {
  open: boolean
  projects: ProjectMetadata[]
  isLoading: boolean
  onSelect: (projectName: string) => void
  onClose: () => void
  onRefresh: () => void
}

export function ProjectSelectionDialog({
  open,
  projects,
  isLoading,
  onSelect,
  onClose,
  onRefresh
}: ProjectSelectionDialogProps) {
  const [selectedProject, setSelectedProject] = useState<string | null>(null)
  const [mounted, setMounted] = useState(false)
  const [isClosing, setIsClosing] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(false)
  const { refreshProjects, setCurrentProject } = useProject()
  const { reportAction } = useOnboarding()
  const prevOpenRef = useRef(open)
  

  // Folder state
  
   // null = "All"
  
  
  
  

  useEffect(() => { setMounted(true) }, [])

  

  useEffect(() => {
    if (prevOpenRef.current !== open) {
      if (open) {
        trackEvent('dialog', 'ProjectSelectionDialog', 'open_project_index_dialog')
      }
      prevOpenRef.current = open
    }
    if (open) {
      setSelectedProject(null)
      setIsClosing(false)

    }
  }, [open])

  // Close context menu on outside click
  

  const handleClose = () => {
    trackEvent('dialog', 'ProjectSelectionDialog', 'close_project_index_dialog')
    setIsClosing(true)
    setTimeout(() => { onClose() }, 150)
  }

  const handleProjectCreated = async (projectName: string) => {
    trackEvent('project', 'ProjectSelectionDialog', 'project_created_from_wizard', { project_name: projectName })
    await refreshProjects()
    setCurrentProject(projectName)
    onSelect(projectName)
    setWizardOpen(false)
    handleClose()
  }

  if (!open || !mounted) return null

  const handleProjectClick = (projectName: string) => { setSelectedProject(projectName) }

  const handleConfirm = () => {
    if (selectedProject) onSelect(selectedProject)
  }

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return 'N/A'
    try {
      return new Date(dateStr).toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric'
      }).toUpperCase()
    } catch { return dateStr }
  }

  const getProjectLocation = (project: ProjectMetadata): string => {
    if (project.aoi?.countries && project.aoi.countries.length > 0) return project.aoi.countries.join(', ')
    if (project.country) return project.country
    return 'UNDEFINED'
  }

  // --- Folder-based filtering ---
  const filteredProjects = projects

  

  // --- Superadmin: visibility settings dialog ---
  

  // --- Superadmin: folder CRUD ---
  

  

  

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className={cn(
          "fixed inset-0 bg-black/80 backdrop-blur-md z-[100]",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}
        onClick={handleClose}
      >
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:40px_40px]" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_0%,black_100%)]" />
      </div>

      {/* Dialog */}
      <div className="fixed inset-0 flex items-center justify-center z-[101] p-4 md:p-8 pointer-events-none">
        <div
          className={cn(
            "relative bg-[#0a0a0a]/90 border border-white/10 rounded-sm shadow-[0_0_50px_-12px_rgba(0,0,0,0.9)] w-full max-w-6xl max-h-[90vh] flex flex-col pointer-events-auto overflow-hidden",
            isClosing ? "animate-fade-out" : "animate-fade-in"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Decorative Top Line */}
          <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-primary/50 to-transparent" />

          {/* Header */}
          <div className="flex items-center justify-between px-8 py-6 border-b border-white/10 bg-black/20">
            <div className="flex items-center gap-4">
              <div className="relative p-3 bg-primary/5 border border-primary/20 rounded-sm">
                <Database className="w-6 h-6 text-primary" />
                <div className="absolute -top-1 -right-1 w-2 h-2 bg-primary rounded-full shadow-[0_0_10px_rgba(var(--primary),1)] animate-pulse" />
              </div>
              <div>
                <h2 className="text-xl font-bold tracking-widest uppercase text-white font-mono">
                  Project Index
                </h2>
                <div className="flex items-center gap-2 text-[10px] text-muted-foreground font-mono uppercase tracking-wider mt-1">
                  <span>{process.env.NEXT_PUBLIC_WEB_PREVIEW === '1' ? 'Preview workspace' : process.env.NEXT_PUBLIC_CLOUD_MODE === '1' ? 'Temporary workspace' : 'Local workspace'}</span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button onClick={() => setWizardOpen(true)} data-tour="create-project-btn" className="flex items-center gap-2 px-3 py-2 text-xs font-mono uppercase tracking-wider border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20">
                <Plus className="w-4 h-4" /> Create Project
              </button>
              {/* Stats Pill */}
              <div className="hidden md:flex items-center gap-4 px-4 py-2 bg-white/5 border border-white/5 rounded-sm mr-4">
                <div className="flex flex-col items-end">
                  <span className="text-[10px] text-muted-foreground font-mono uppercase">Total Projects</span>
                  <span className="text-sm font-mono font-bold text-white">{projects.length.toString().padStart(2, '0')}</span>
                </div>
                <div className="w-px h-6 bg-white/10" />
                <div className="flex flex-col items-end">
                  <span className="text-[10px] text-muted-foreground font-mono uppercase">Status</span>
                  <span className="text-sm font-mono font-bold text-white/60">{process.env.NEXT_PUBLIC_WEB_PREVIEW === '1' ? 'PREVIEW' : 'ACTIVE'}</span>
                </div>
              </div>

              <button
                onClick={onRefresh}
                disabled={isLoading}
                className="group p-2 hover:bg-primary/10 border border-transparent hover:border-primary/20 rounded-sm transition-all disabled:opacity-50"
                title="Refresh Index"
              >
                <RefreshCw className={cn("w-5 h-5 text-muted-foreground group-hover:text-primary transition-colors", isLoading && "animate-spin")} />
              </button>
              <button
                onClick={handleClose}
                className="group p-2 hover:bg-red-500/10 border border-transparent hover:border-red-500/20 rounded-sm transition-all"
              >
                <X className="w-5 h-5 text-muted-foreground group-hover:text-red-400 transition-colors" />
              </button>
            </div>
          </div>

          <WorkspaceDirectory onChanged={() => { setSelectedProject(null); onRefresh() }} />

          {/* Folder Tabs */}
          

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-8 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:20px_20px]">
            {isLoading && projects.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full py-20 text-muted-foreground">
                <div className="relative">
                  <div className="w-16 h-16 border-4 border-primary/20 border-t-primary rounded-full animate-spin" />
                  <div className="absolute inset-0 flex items-center justify-center">
                    <Loader2 className="w-6 h-6 text-primary animate-pulse" />
                  </div>
                </div>
                <p className="mt-6 text-sm font-mono tracking-widest uppercase animate-pulse">Retrieving Project Data...</p>
              </div>
            ) : filteredProjects.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full py-20 text-muted-foreground">
                <Terminal className="w-16 h-16 mb-6 opacity-20" />
                <p className="text-xl font-light tracking-wider uppercase text-white/50">
                  No Projects Detected
                </p>
                <p className="text-xs font-mono mt-2 text-white/30">
                  Create a project to choose an area and fetch datasets.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {filteredProjects.map((project) => (
                  <ProjectCard
                    key={project.project_name}
                    project={project}
                    isSelected={selectedProject === project.project_name}
                    onClick={() => handleProjectClick(project.project_name)}
                    onDoubleClick={() => onSelect(project.project_name)}
                    formatDate={formatDate}
                    getProjectLocation={getProjectLocation}
                    
                    
                    
                  />
                ))}

                {/* Create New Project Card */}
                <button
                  onClick={() => {
                    reportAction('click-create-project')
                    setWizardOpen(true)
                  }}
                  data-tour="create-project-btn"
                  className="group relative p-6 border border-dashed border-white/10 bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/20 rounded-sm flex flex-col items-center justify-center gap-4 transition-all min-h-[240px]"
                >
                  <div className="p-4 rounded-full bg-white/5 border border-white/10 group-hover:border-primary/50 group-hover:bg-primary/10 transition-all">
                    <Plus className="w-8 h-8 text-white/30 group-hover:text-primary transition-colors" />
                  </div>
                  <div className="text-center">
                    <div className="text-sm font-bold text-white uppercase tracking-wide group-hover:text-primary transition-colors">Create New Project</div>
                    <div className="text-[10px] font-mono text-white/30 mt-1">Initiate Setup Sequence</div>
                  </div>
                </button>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end px-8 py-6 border-t border-white/10 bg-black/40 backdrop-blur-sm">
            <div className="flex items-center gap-4">
              <button
                onClick={handleClose}
                className="px-6 py-2.5 text-xs font-mono uppercase tracking-wider text-white/60 hover:text-white hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm transition-all"
              >
                Abort
              </button>
              <button
                onClick={handleConfirm}
                disabled={!selectedProject}
                className={cn(
                  "relative group px-8 py-2.5 text-xs font-mono uppercase tracking-wider font-bold transition-all duration-300 rounded-sm",
                  selectedProject
                    ? "bg-primary text-black hover:bg-primary/90 shadow-[0_0_20px_-5px_rgba(var(--primary),0.5)]"
                    : "bg-white/5 text-white/20 cursor-not-allowed"
                )}
              >
                <span className="relative z-10 flex items-center gap-2">
                  Initialize Project
                  <ChevronRight className={cn("w-3 h-3 transition-transform", selectedProject && "group-hover:translate-x-1")} />
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Context Menu (superadmin: folder assignment) */}
      

      {/* Folder Create/Edit Dialog */}
      

      {/* Visibility Dialog */}
      

      <CreateProjectWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={handleProjectCreated}
      />
    </>,
    document.body
  )
}


// ============================================================================
// Folder Bar (horizontal tabs above project grid)
// ============================================================================










// ============================================================================
// Project Card Component
// ============================================================================

interface ProjectCardProps {
  project: ProjectMetadata
  isSelected: boolean
  onClick: () => void
  onDoubleClick: () => void
  formatDate: (date?: string) => string
  getProjectLocation: (project: ProjectMetadata) => string
  
  
  
}

function ProjectCard({
  project, isSelected, onClick, onDoubleClick, formatDate, getProjectLocation,

}: ProjectCardProps) {
  

  return (
    <div
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      
      className={cn(
        "group relative p-6 border cursor-pointer transition-all duration-300 overflow-hidden",
        isSelected
          ? "bg-primary/5 border-primary/50 shadow-[0_0_30px_-10px_rgba(var(--primary),0.3)]"
          : "bg-black/20 border-white/5 hover:border-white/20 hover:bg-white/5"
      )}
    >
      {/* Technical Corner Markers */}
      <div className={cn("absolute top-0 left-0 w-2 h-2 border-t border-l transition-colors", isSelected ? "border-primary" : "border-white/20 group-hover:border-white/40")} />
      <div className={cn("absolute top-0 right-0 w-2 h-2 border-t border-r transition-colors", isSelected ? "border-primary" : "border-white/20 group-hover:border-white/40")} />
      <div className={cn("absolute bottom-0 left-0 w-2 h-2 border-b border-l transition-colors", isSelected ? "border-primary" : "border-white/20 group-hover:border-white/40")} />
      <div className={cn("absolute bottom-0 right-0 w-2 h-2 border-b border-r transition-colors", isSelected ? "border-primary" : "border-white/20 group-hover:border-white/40")} />

      {/* Top-right badges */}
      <div className="absolute top-2 right-2 flex items-center gap-1.5">
        {/* Visibility badge */}
        

        {/* Folder badge */}
        

        {/* Superadmin: visibility toggle */}
        
      </div>

      {/* Header Section */}
      <div className="flex justify-between items-start mb-6">
        <div className="flex items-start gap-4">
          <div className={cn(
            "p-2.5 rounded-sm border transition-all duration-300",
            isSelected
              ? "bg-primary/10 border-primary/30 text-primary"
              : "bg-white/5 border-white/10 text-muted-foreground group-hover:text-white group-hover:border-white/20"
          )}>
            <Folder className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h3 className={cn(
                "font-bold text-sm tracking-wide uppercase transition-colors",
                isSelected ? "text-white" : "text-white/80 group-hover:text-white"
              )}>
                {project.project_name}
              </h3>
              {isSelected && <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" />}
            </div>
            <p className="text-[10px] text-muted-foreground font-mono uppercase tracking-wider">
              {project.client || 'Internal Project'}
            </p>
          </div>
        </div>
        {project.aoi?.area_km2 && (
          <div className={cn(
            "px-2 py-1 text-[10px] font-mono border rounded-sm transition-colors mt-4",
            isSelected ? "border-primary/30 text-primary bg-primary/5" : "border-white/10 text-muted-foreground bg-white/5"
          )}>
            {project.aoi.area_km2.toFixed(0)} KM²
          </div>
        )}
      </div>

      {/* Data Grid */}
      <div className="grid grid-cols-2 gap-y-4 gap-x-8">
        <InfoBlock
          label="Location"
          value={getProjectLocation(project)}
          icon={MapPin}
          colSpan={2}
        />
        <InfoBlock
          label="CRS / EPSG"
          value={project.crs ? `${project.crs.name}` : 'N/A'}
          subValue={project.crs ? `EPSG:${project.crs.epsg}` : undefined}
          icon={Globe}
        />
        <InfoBlock
          label="Created"
          value={formatDate(project.date_created)}
          icon={Calendar}
        />

        <div className="col-span-2 h-px bg-white/5 my-1 group-hover:bg-white/10 transition-colors" />

        <InfoBlock
          label="Creator"
          value={project.project_creator || 'UNASSIGNED'}
          icon={User}
          dim={!project.project_creator}
        />
        <InfoBlock
          label="Division"
          value={project.department || 'R&D'}
          icon={ShieldCheck}
          dim={!project.department}
        />
      </div>

      {/* Selection Overlay (Scan effect) */}
      {isSelected && (
        <div className="absolute inset-0 bg-gradient-to-b from-primary/0 via-primary/5 to-primary/0 animate-scan pointer-events-none" />
      )}
    </div>
  )
}


// ============================================================================
// Info Block Component
// ============================================================================

interface InfoBlockProps {
  label: string
  value: string
  subValue?: string
  icon: React.ElementType
  colSpan?: number
  dim?: boolean
}

function InfoBlock({ label, value, subValue, icon: Icon, colSpan, dim }: InfoBlockProps) {
  return (
    <div className={cn("space-y-1", colSpan && `col-span-${colSpan}`)}>
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground font-mono uppercase tracking-wider">
        <Icon className="w-3 h-3 opacity-70" />
        <span>{label}</span>
      </div>
      <div className={cn(
        "font-mono text-xs truncate",
        dim ? "text-white/30 italic" : "text-white/90"
      )}>
        {value}
      </div>
      {subValue && (
        <div className="text-[10px] font-mono text-primary/80 truncate">
          {subValue}
        </div>
      )}
    </div>
  )
}

