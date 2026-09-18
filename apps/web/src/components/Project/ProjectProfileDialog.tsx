'use client'

import { trackEvent } from '@/lib/analytics'
import {
ProjectCRSRecommendation,
fetchRecommendedCRS,
updateProjectCRS
} from '@/lib/api/dataClient'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import {
AlertTriangle,
Calendar,
Database,
ExternalLink,
FileText,
Globe,
Layers,
LayoutDashboard,
MapPin,
Mountain,
Route,
Settings,
Target,
TreePine,
X
} from 'lucide-react'
import { useEffect,useRef,useState } from 'react'
import { createPortal } from 'react-dom'
import { CRSEntry,CRSSelectorDialog } from './CRSSelectorDialog'

interface ProjectProfileDialogProps {
  open: boolean
  onClose: () => void
}

type TabId = 'overview' | 'geo_scope'

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: 'overview', label: 'Mission Brief', icon: LayoutDashboard },
  { id: 'geo_scope', label: 'Spatial Domain', icon: Globe },
]

export function ProjectProfileDialog({ open, onClose }: ProjectProfileDialogProps) {
  const { projectMetadata, currentProject, refreshProjectData, datasets } = useProject()
  const [mounted, setMounted] = useState(false)
  const prevOpenRef = useRef(open)
  const [crsSelectorOpen, setCrsSelectorOpen] = useState(false)
  const [recommendation, setRecommendation] = useState<ProjectCRSRecommendation | null>(null)
  const [isUpdating, setIsUpdating] = useState(false)
  const [isClosing, setIsClosing] = useState(false)
  const [activeTab, setActiveTab] = useState<TabId>('overview')
  
  
  
  
  
  
  
  
  
  

  useEffect(() => {
    setMounted(true)
  }, [])

  

  

  

  

  useEffect(() => {
    if (prevOpenRef.current === open) return
    trackEvent('dialog', 'ProjectProfileDialog', open ? 'open_project_profile_dialog' : 'close_project_profile_dialog', {
      project: currentProject
    })
    prevOpenRef.current = open
  }, [currentProject, open])

  useEffect(() => {
    if (open) {
      setIsClosing(false)
    }
  }, [open])

  const handleClose = () => {
    setIsClosing(true)
    setTimeout(() => {
      onClose()
    }, 150)
  }

  useEffect(() => {
    if (open && currentProject) {
      fetchRecommendedCRS(currentProject)
        .then(setRecommendation)
        .catch(err => console.warn('Failed to fetch CRS recommendation:', err))
    }
  }, [open, currentProject])

  if (!open || !mounted) return null

  const handleCRSSelect = async (crs: CRSEntry) => {
    if (!currentProject) return
    
    setIsUpdating(true)
    try {
      await updateProjectCRS(currentProject, crs.epsg, crs.name)
      // Refresh project data to get updated CRS
      await refreshProjectData()
      setCrsSelectorOpen(false)
    } catch (err) {
      console.error('Failed to update CRS:', err)
      alert(`Failed to update CRS: ${err instanceof Error ? err.message : 'Unknown error'}`)
    } finally {
      setIsUpdating(false)
    }
  }

  // Helper to format dates
  const formatDate = (dateStr?: string) => {
    if (!dateStr) return 'N/A'
    try {
      return new Date(dateStr).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return dateStr
    }
  }

  

  
  

  
  
  
  
  
  
  
  

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
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none" />
      </div>

      {/* Dialog */}
      <div className="fixed inset-0 z-[101] flex items-center justify-center p-4 pointer-events-none">
        <div className={cn(
          "relative w-[800px] max-w-[95vw] max-h-[90vh] bg-[#0a0a0a]/95 border border-white/10 rounded-sm shadow-[0_0_50px_-10px_rgba(0,0,0,0.8)] flex flex-col pointer-events-auto overflow-hidden",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}>
          
          {/* Header */}
          <header className="px-8 py-6 border-b border-white/10 flex items-center justify-between bg-black/20 shrink-0">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-[10px] text-white/40 uppercase tracking-[0.2em] font-mono">
                <Settings className="w-3 h-3" />
                <span>Configuration & Metadata</span>
              </div>
              <h2 className="text-2xl font-bold text-white uppercase tracking-wide font-mono">
                Project Profile
              </h2>
            </div>
            <button 
              onClick={handleClose}
              className="p-2 hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm text-white/50 hover:text-white transition-all"
            >
              <X className="w-5 h-5" />
            </button>
          </header>

          {/* Tabs */}
          <div className="flex items-center px-8 border-b border-white/10 bg-white/[0.02] shrink-0 overflow-x-auto no-scrollbar">
            {TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "flex items-center gap-2 px-4 py-3 text-[10px] font-mono uppercase tracking-wider transition-all border-b-2 hover:bg-white/5 whitespace-nowrap",
                  activeTab === tab.id 
                    ? "border-primary text-primary bg-primary/5" 
                    : "border-transparent text-white/40 hover:text-white"
                )}
              >
                <tab.icon className="w-3.5 h-3.5" />
                {tab.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-8 bg-[linear-gradient(to_right,#ffffff02_1px,transparent_1px),linear-gradient(to_bottom,#ffffff02_1px,transparent_1px)] bg-[size:20px_20px]">
            
            {activeTab === 'overview' && (
              <div className="space-y-8 animate-in fade-in duration-300">
                {/* Identity Section */}
            <section className="grid grid-cols-2 gap-6">
              <div className="p-4 bg-white/[0.02] border border-white/5 rounded-sm">
                <div className="flex items-center gap-3 mb-3">
                  <FileText className="w-4 h-4 text-primary" />
                  <span className="text-xs font-mono uppercase text-white/50 tracking-wider">Project Identity</span>
                </div>
                <div className="space-y-1">
                  <div className="text-lg font-bold text-white">{projectMetadata?.project_name || currentProject || 'Unknown'}</div>
                  <div className="text-xs font-mono text-white/40">{projectMetadata?.project_code || 'NO_CODE'}</div>
                </div>
              </div>

              <div className="p-4 bg-white/[0.02] border border-white/5 rounded-sm">
                <div className="flex items-center gap-3 mb-3">
                  <Calendar className="w-4 h-4 text-primary" />
                  <span className="text-xs font-mono uppercase text-white/50 tracking-wider">Timestamps</span>
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] font-mono text-white/30 uppercase">Created</span>
                    <span className="text-xs text-white/70">{formatDate(projectMetadata?.date_created)}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] font-mono text-white/30 uppercase">Status</span>
                    <span className="text-xs font-bold text-emerald-500 uppercase tracking-wider border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 rounded-sm">
                      {projectMetadata?.status || 'ACTIVE'}
                    </span>
                  </div>
                </div>
              </div>
            </section>

            {/* Spatial Configuration */}
            <section>
              <div className="flex items-center gap-2 mb-4 text-white/50">
                <Globe className="w-4 h-4" />
                <h3 className="text-sm font-bold uppercase tracking-wider">Spatial Configuration</h3>
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                
                {/* CRS Card */}
                <div className="group relative p-5 bg-black/40 border border-white/10 rounded-sm hover:border-primary/30 transition-all">
                  <div className="absolute top-0 left-0 w-1 h-full bg-primary/20 group-hover:bg-primary transition-all" />
                  
                  <div className="flex justify-between items-start mb-4">
                    <div className="flex items-center gap-2 text-xs font-mono uppercase text-white/40 tracking-wider">
                      <Target className="w-3 h-3" />
                      <span>Coordinate Reference System</span>
                    </div>
                    <div className="px-2 py-1 bg-primary/10 border border-primary/20 rounded-sm">
                      <span className="text-xs font-bold text-primary font-mono">EPSG:{projectMetadata?.crs?.epsg || '----'}</span>
                    </div>
                  </div>

                  <div className="space-y-1 mb-6">
                    <div className="text-sm font-bold text-white group-hover:text-primary transition-colors">
                      {projectMetadata?.crs?.name || 'Unknown CRS'}
                    </div>
                    <div className="text-xs text-white/40 font-mono">
                      {projectMetadata?.measurement_system || 'Metric'} Units
                    </div>
                  </div>

                  {recommendation && (
                    <div className="mb-4 p-3 bg-emerald-500/5 border border-emerald-500/20 rounded-sm relative group/rec">
                      <div className="absolute -top-2 left-2 bg-[#0a0a0a] px-1 text-[9px] font-mono uppercase text-emerald-500/70 tracking-wider">
                        AI Recommendation
                      </div>
                      <div className="flex items-center justify-between gap-3 mt-1">
                        <div className="flex-1 min-w-0">
                           <div className="text-xs text-emerald-400 font-bold truncate" title={recommendation.name}>
                             {recommendation.name}
                           </div>
                           <div className="text-[10px] text-emerald-500/40 font-mono flex items-center gap-2">
                             <span>EPSG:{recommendation.epsg}</span>
                             <span className="w-1 h-1 rounded-full bg-emerald-500/30" />
                             <span className="truncate max-w-[120px] opacity-70" title={recommendation.reason}>{recommendation.reason}</span>
                           </div>
                        </div>
                        <button
                           onClick={() => handleCRSSelect({ 
                             epsg: recommendation.epsg, 
                             name: recommendation.name, 
                             category: 'Recommended', 
                             type: 'Projected',
                             area: recommendation.reason
                           })}
                           className="px-3 py-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 text-[10px] font-bold font-mono uppercase rounded-sm transition-all border border-emerald-500/20 hover:border-emerald-500/40 flex items-center gap-1.5"
                        >
                           <Target className="w-3 h-3" />
                           Apply
                        </button>
                      </div>
                    </div>
                  )}

                  <button 
                    onClick={() => setCrsSelectorOpen(true)}
                    className="w-full py-2 border border-white/10 bg-white/5 hover:bg-white/10 hover:border-white/20 text-xs font-mono uppercase tracking-wider text-white rounded-sm transition-all flex items-center justify-center gap-2"
                  >
                    <Globe className="w-3 h-3" />
                    Select Project CRS
                  </button>
                </div>

                {/* AOI / Location Card */}
                <div className="p-5 bg-black/40 border border-white/10 rounded-sm">
                  <div className="flex items-center gap-2 text-xs font-mono uppercase text-white/40 tracking-wider mb-4">
                    <MapPin className="w-3 h-3" />
                    <span>Area of Interest</span>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <div className="text-[10px] text-white/30 font-mono mb-1 uppercase">
                        {(projectMetadata?.aoi?.countries?.length ?? 0) > 1 ? 'Countries Covered' : 'Country'}
                      </div>
                      <div className="text-sm font-bold text-white">
                        {projectMetadata?.aoi?.countries?.join(', ') || projectMetadata?.country || projectMetadata?.iso3 || 'Global / Unspecified'}
                      </div>
                      {(projectMetadata?.iso3_list?.length ?? 0) > 1 && (
                        <div className="text-[10px] text-white/40 font-mono mt-1">
                          {projectMetadata!.iso3_list!.join(' · ')} ({projectMetadata!.iso3_list!.length} countries)
                        </div>
                      )}
                    </div>
                    
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <div className="text-[10px] text-white/30 font-mono mb-1 uppercase">Source File</div>
                        <div className="text-xs text-white/60 truncate font-mono" title={projectMetadata?.aoi?.file}>
                          {projectMetadata?.aoi?.file ? projectMetadata.aoi.file.split('/').pop() : 'No AOI file'}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] text-white/30 font-mono mb-1 uppercase">Coverage</div>
                        <div className="text-xs text-white/60 font-mono">
                          {projectMetadata?.aoi?.area_km2 ? `${projectMetadata.aoi.area_km2.toLocaleString()} km²` : 'Unknown Area'}
                        </div>
                      </div>
                    </div>

                    {/* Start and End Points */}
                    <div className="grid grid-cols-2 gap-4 pt-2 border-t border-white/5">
                      <div>
                        <div className="text-[10px] text-white/30 font-mono mb-1 uppercase flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                          Start Point
                        </div>
                        <div className="text-xs text-white/70 font-mono">
                          {projectMetadata?.aoi?.start_point ? (
                            <>
                              <span className="text-emerald-400">{projectMetadata.aoi.start_point.latitude?.toFixed(6)}°</span>
                              <span className="text-white/30 mx-1">,</span>
                              <span className="text-emerald-400">{projectMetadata.aoi.start_point.longitude?.toFixed(6)}°</span>
                            </>
                          ) : (
                            <span className="text-white/40">Not defined</span>
                          )}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] text-white/30 font-mono mb-1 uppercase flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                          End Point
                        </div>
                        <div className="text-xs text-white/70 font-mono">
                          {projectMetadata?.aoi?.end_point ? (
                            <>
                              <span className="text-red-400">{projectMetadata.aoi.end_point.latitude?.toFixed(6)}°</span>
                              <span className="text-white/30 mx-1">,</span>
                              <span className="text-red-400">{projectMetadata.aoi.end_point.longitude?.toFixed(6)}°</span>
                            </>
                          ) : (
                            <span className="text-white/40">Not defined</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

              </div>
            </section>

            {/* Organization (Placeholder) */}
            <section className="pt-6 border-t border-white/5">
               <div className="grid grid-cols-3 gap-4 text-xs">
                  <div>
                    <span className="block text-[9px] font-mono uppercase text-white/30 mb-1">Client / Organization</span>
                    <span className="text-white/60">{projectMetadata?.client || projectMetadata?.organization || 'Internal'}</span>
                  </div>
                  <div>
                    <span className="block text-[9px] font-mono uppercase text-white/30 mb-1">Department</span>
                    <span className="text-white/60">{projectMetadata?.department || 'Engineering'}</span>
                  </div>
                  <div>
                    <span className="block text-[9px] font-mono uppercase text-white/30 mb-1">Creator</span>
                    <span className="text-white/60">{projectMetadata?.project_creator || 'System Admin'}</span>
                  </div>
               </div>
            </section>
              </div>
            )}

            

            {activeTab === 'geo_scope' && (
              <div className="space-y-8 animate-in fade-in duration-300">
                <section>
                  <div className="flex items-center gap-2 mb-4 text-white/50">
                    <Database className="w-4 h-4" />
                    <h3 className="text-sm font-bold uppercase tracking-wider">Geospatial Data Sources</h3>
                  </div>

                  {datasets && (datasets.rasters.length > 0 || datasets.vectors.length > 0) ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="p-4 bg-black/40 border border-white/10 rounded-sm">
                        <div className="flex items-center justify-between text-xs font-mono uppercase text-white/40 tracking-wider mb-3">
                          <div className="flex items-center gap-2">
                            <Layers className="w-3 h-3" />
                            <span>Raster Datasets</span>
                          </div>
                          <span className="text-[9px]">{datasets.rasters.length}</span>
                        </div>
                        <div className="space-y-2">
                          {datasets.rasters.length === 0 ? (
                            <div className="text-[10px] font-mono text-white/30 italic py-2">No rasters fetched yet.</div>
                          ) : datasets.rasters.map((ds) => (
                            <DataSourceItem
                              key={ds.name}
                              name={ds.metadata?.dataset_name || ds.name}
                              source={ds.metadata?.source || ds.metadata?.provider || 'Unknown source'}
                              resolution={ds.metadata?.resolution_m ? `${ds.metadata.resolution_m}m` : 'Raster'}
                              url={ds.metadata?.documentation_url || ds.metadata?.provider_url || '#'}
                            />
                          ))}
                        </div>
                      </div>

                      <div className="p-4 bg-black/40 border border-white/10 rounded-sm">
                        <div className="flex items-center justify-between text-xs font-mono uppercase text-white/40 tracking-wider mb-3">
                          <div className="flex items-center gap-2">
                            <Route className="w-3 h-3" />
                            <span>Vector Datasets</span>
                          </div>
                          <span className="text-[9px]">{datasets.vectors.length}</span>
                        </div>
                        <div className="space-y-2">
                          {datasets.vectors.length === 0 ? (
                            <div className="text-[10px] font-mono text-white/30 italic py-2">No vectors fetched yet.</div>
                          ) : datasets.vectors.map((ds) => (
                            <DataSourceItem
                              key={ds.name}
                              name={ds.metadata?.dataset_name || ds.name}
                              source={ds.metadata?.source || ds.metadata?.provider || 'Unknown source'}
                              resolution="Vector"
                              url={ds.metadata?.documentation_url || ds.metadata?.provider_url || '#'}
                            />
                          ))}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="p-6 bg-black/40 border border-white/10 rounded-sm text-center">
                      <Database className="w-6 h-6 text-white/20 mx-auto mb-2" />
                      <div className="text-xs text-white/40 font-mono uppercase">No datasets fetched yet</div>
                      <div className="text-[10px] text-white/30 mt-1">Use the Dataset Manager to fetch geospatial data for this project.</div>
                    </div>
                  )}
                </section>

                <section>
                  <div className="flex items-center gap-2 mb-4 text-white/50">
                    <Mountain className="w-4 h-4" />
                    <h3 className="text-sm font-bold uppercase tracking-wider">Terrain Characteristics</h3>
                  </div>

                  {datasets && datasets.rasters.some(r => r.metadata?.category === 'dem' && r.metadata?.statistics) ? (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                      {(() => {
                        const demDs = datasets.rasters.find(r => r.metadata?.category === 'dem')
                        const stats = demDs?.metadata?.statistics
                        return (
                          <>
                            <TerrainCard
                              label="Elevation Range"
                              value={stats?.min != null && stats?.max != null ? `${Math.round(stats.min)}m - ${Math.round(stats.max)}m` : 'N/A'}
                              subtext={stats?.mean != null ? `Mean: ${Math.round(stats.mean)}m` : ''}
                              icon={<Layers className="w-4 h-4" />}
                            />
                            <TerrainCard
                              label="DEM Source"
                              value={demDs?.metadata?.dataset_name || 'Unknown'}
                              subtext={demDs?.metadata?.resolution_m ? `${demDs.metadata.resolution_m}m resolution` : ''}
                              icon={<Mountain className="w-4 h-4" />}
                            />
                          </>
                        )
                      })()}
                      {datasets.rasters.some(r => r.metadata?.category === 'geohazard') && (
                        <TerrainCard
                          label="Seismic Data"
                          value="Available"
                          subtext={datasets.rasters.find(r => r.metadata?.category === 'geohazard')?.metadata?.dataset_name || 'GEM/USGS PGA'}
                          icon={<AlertTriangle className="w-4 h-4" />}
                          warning
                        />
                      )}
                      {datasets.rasters.some(r => r.metadata?.category === 'landcover') && (
                        <TerrainCard
                          label="Land Cover"
                          value="Available"
                          subtext={datasets.rasters.find(r => r.metadata?.category === 'landcover')?.metadata?.dataset_name || 'ESA WorldCover'}
                          icon={<TreePine className="w-4 h-4" />}
                        />
                      )}
                    </div>
                  ) : (
                    <div className="p-4 bg-black/20 border border-white/5 rounded-sm">
                      <div className="text-[10px] text-white/30 font-mono uppercase">Terrain analysis requires a DEM dataset. Fetch datasets to populate this section.</div>
                    </div>
                  )}
                </section>

                <section>
                  <div className="flex items-center gap-2 mb-4 text-white/50">
                    <AlertTriangle className="w-4 h-4" />
                    <h3 className="text-sm font-bold uppercase tracking-wider">Environmental Constraints</h3>
                  </div>

                  <div className="p-4 bg-amber-500/5 border border-amber-500/20 rounded-sm">
                    <div className="text-xs text-white/60">
                      {datasets && datasets.vectors.some(v => v.metadata?.category === 'protected_areas') ? (
                        <div>
                          <div className="text-amber-400 font-bold mb-2">Protected Areas Data Available</div>
                          <p className="text-white/50">
                            {datasets.vectors.find(v => v.metadata?.category === 'protected_areas')?.metadata?.dataset_name || 'Protected areas'} loaded.
                            Review in the map layer manager for intersection analysis.
                          </p>
                        </div>
                      ) : (
                        <div className="text-white/30 font-mono text-[10px] uppercase">
                          Environmental constraint data will appear here once protected areas and constraint datasets are fetched for this project.
                        </div>
                      )}
                    </div>
                  </div>
                </section>
              </div>
            )}

            

          </div>
        </div>
      </div>

      <CRSSelectorDialog 
        open={crsSelectorOpen} 
        onClose={() => setCrsSelectorOpen(false)}
        onSelect={handleCRSSelect}
        currentEpsg={projectMetadata?.crs?.epsg}
      />
    </>,
    document.body
  )
}

function DataSourceItem({ name, source, resolution, url }: { name: string; source: string; resolution: string; url: string }) {
  return (
    <div className="flex items-center justify-between p-2 bg-white/[0.02] border border-white/5 rounded-sm hover:bg-white/[0.05] transition-colors group">
      <div className="flex-1 min-w-0">
        <div className="text-xs text-white font-medium truncate">{name}</div>
        <div className="text-[10px] text-white/40 font-mono">{source}</div>
      </div>
      <div className="flex items-center gap-2 ml-2">
        <span className="text-[9px] font-mono text-primary/70 bg-primary/10 px-1.5 py-0.5 rounded">{resolution}</span>
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="p-1 text-white/30 hover:text-primary transition-colors opacity-0 group-hover:opacity-100"
          title="View source"
        >
          <ExternalLink className="w-3 h-3" />
        </a>
      </div>
    </div>
  )
}

function TerrainCard({ label, value, subtext, icon, warning }: { label: string; value: string; subtext: string; icon: React.ReactNode; warning?: boolean }) {
  return (
    <div className={cn(
      "p-3 border rounded-sm",
      warning ? "bg-amber-500/5 border-amber-500/20" : "bg-black/40 border-white/10"
    )}>
      <div className={cn("mb-2", warning ? "text-amber-400" : "text-white/40")}>
        {icon}
      </div>
      <div className="text-[10px] text-white/40 font-mono uppercase tracking-wider mb-1">{label}</div>
      <div className={cn("text-sm font-bold", warning ? "text-amber-400" : "text-white")}>{value}</div>
      <div className="text-[10px] text-white/40">{subtext}</div>
    </div>
  )
}


