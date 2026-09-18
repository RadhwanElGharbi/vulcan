/**
 * AGRS ZEUS GUI v2 - Project Selector
 * 
 * Button component that opens the ProjectSelectionDialog.
 */

'use client'

import { useOnboarding } from '@/lib/context/OnboardingContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import { ChevronDown,Database,Folder,Loader2 } from 'lucide-react'
import { useState } from 'react'
import { ProjectSelectionDialog } from './ProjectSelectionDialog'

export function ProjectSelector() {
  const {
    currentProject,
    setCurrentProject,
    projects,
    projectMetadata,
    isLoading,
    refreshProjects
  } = useProject()
  const { reportAction } = useOnboarding()

  const [dialogOpen, setDialogOpen] = useState(false)

  const handleOpenDialog = async () => {
    setDialogOpen(true)
    // Report action for tour auto-advance
    reportAction('click-project-selector')
    // Refresh projects when opening dialog
    await refreshProjects()
  }

  const handleSelect = (projectName: string) => {
    setCurrentProject(projectName)
    setDialogOpen(false)
  }

  const currentProjectData = projects.find(p => p.project_name === currentProject)

  return (
    <>
      {/* Selector Button */}
      <button
        onClick={handleOpenDialog}
        data-tour="project-selector-btn"
        className="w-full flex items-center justify-between p-3 bg-black/40 border border-white/10 rounded-sm hover:bg-primary/5 hover:border-primary/30 transition-all duration-300 group relative overflow-hidden"
      >
        {/* Active Marker */}
        <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-white/10 group-hover:bg-primary transition-colors duration-300" />

        <div className="flex items-center gap-3 min-w-0 pl-2">
          <div className={cn(
            "p-1.5 rounded-sm border transition-colors duration-300",
            currentProjectData ? "bg-primary/10 border-primary/20 text-primary" : "bg-white/5 border-white/10 text-muted-foreground group-hover:text-white"
          )}>
            <Folder className="w-4 h-4 flex-shrink-0" />
          </div>
          
          <div className="text-left min-w-0">
            <div className={cn(
              "text-sm font-medium truncate transition-colors",
              currentProjectData ? "text-white" : "text-muted-foreground group-hover:text-white"
            )}>
              {currentProjectData?.project_name || 'Select Project'}
            </div>
            <div className="text-[10px] font-mono text-white/30 uppercase tracking-wider truncate group-hover:text-white/50 transition-colors">
              {currentProjectData?.organization || 'NO DATA LOADED'}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isLoading ? (
             <Loader2 className="w-3 h-3 animate-spin text-primary" />
          ) : (
             <ChevronDown className="w-4 h-4 text-white/30 group-hover:text-primary transition-colors" />
          )}
        </div>
      </button>

      {/* Project Metadata Summary (when project is selected) */}
      {projectMetadata && (
        <div className="mt-2 p-3 bg-black/20 border border-white/5 rounded-sm relative overflow-hidden">
          {/* Decorator */}
          <div className="absolute top-0 right-0 p-1">
            <div className="w-2 h-2 border-t border-r border-white/10" />
          </div>

          <div className="space-y-2">
            {projectMetadata.crs && (
              <div className="flex justify-between items-center">
                <div className="flex items-center gap-1.5 text-[10px] text-white/40 font-mono uppercase tracking-wider">
                    <Database className="w-3 h-3" />
                    <span>CRS Reference</span>
                </div>
                <span className="text-[10px] font-mono text-primary/80">EPSG:{projectMetadata.crs.epsg}</span>
              </div>
            )}
            {projectMetadata.aoi && projectMetadata.aoi.area_km2 != null && (
              <div className="flex justify-between items-center">
                <span className="text-[10px] text-white/40 font-mono uppercase tracking-wider">AOI Coverage</span>
                <span className="text-[10px] font-mono text-white/80">{projectMetadata.aoi.area_km2.toFixed(1)} KM²</span>
              </div>
            )}
            {projectMetadata.date_created && (
              <div className="flex justify-between items-center border-t border-white/5 pt-1.5 mt-1">
                <span className="text-[10px] text-white/40 font-mono uppercase tracking-wider">Timestamp</span>
                <span className="text-[10px] font-mono text-white/60">{new Date(projectMetadata.date_created).toLocaleDateString()}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Selection Dialog */}
      <ProjectSelectionDialog
        open={dialogOpen}
        projects={projects}
        isLoading={isLoading}
        onSelect={handleSelect}
        onClose={() => setDialogOpen(false)}
        onRefresh={refreshProjects}
      />
    </>
  )
}
