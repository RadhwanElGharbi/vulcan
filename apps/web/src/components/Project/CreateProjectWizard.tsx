'use client'

import { trackEvent } from '@/lib/analytics'
import { fetchWorkspace } from '@/lib/api/workspace'
import {
AOIPreviewResponse,
createProject,
CreateProjectResponse,
previewAoi,
ProjectCRSRecommendation
} from '@/lib/api/dataClient'
import { TourAction,useOnboarding } from '@/lib/context/OnboardingContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import MapboxDraw from '@mapbox/mapbox-gl-draw'
import {
AlertTriangle,
CheckCircle2,
ChevronLeft,
ChevronRight,
FileText,
Globe,
MapPin,
X
} from 'lucide-react'
import * as maplibregl from 'maplibre-gl'
import { Map as MapLibreMap } from 'maplibre-gl'
import { useEffect,useMemo,useRef,useState } from 'react'
import { createPortal } from 'react-dom'
import { CRSSelectorDialog } from './CRSSelectorDialog'

interface CreateProjectWizardProps {
  open: boolean
  onClose: () => void
  onCreated: (projectName: string) => void
}

type MeasurementSystem = 'SI' | 'Imperial'
type AOIMode = 'upload' | 'draw'

const STEPS = [
  { id: 'identity', label: 'Identity & Units' },
  { id: 'aoi', label: 'AOI Capture' },
  { id: 'crs', label: 'Coordinate System' },
  { id: 'review', label: 'Review' },
]

const UNIT_TABLE: Record<MeasurementSystem, { quantity: string; unit: string }[]> = {
  SI: [
    { quantity: 'Length', unit: 'Meter (m)' },
    { quantity: 'Area', unit: 'Square meter (m²)' },
    { quantity: 'Volume', unit: 'Cubic meter (m³)' },
    { quantity: 'Pressure', unit: 'Pascal (Pa)' },
    { quantity: 'Mass', unit: 'Kilogram (kg)' },
    { quantity: 'Temperature', unit: 'Celsius (°C)' },
    { quantity: 'Velocity', unit: 'Meters/second (m/s)' },
    { quantity: 'Density', unit: 'kg/m³' },
  ],
  Imperial: [
    { quantity: 'Length', unit: 'Foot (ft)' },
    { quantity: 'Area', unit: 'Square foot (ft²)' },
    { quantity: 'Volume', unit: 'Cubic foot (ft³)' },
    { quantity: 'Pressure', unit: 'Pounds per square inch (psi)' },
    { quantity: 'Mass', unit: 'Pound (lb)' },
    { quantity: 'Temperature', unit: 'Fahrenheit (°F)' },
    { quantity: 'Velocity', unit: 'Feet/second (ft/s)' },
    { quantity: 'Density', unit: 'lb/ft³' },
  ],
}

export function CreateProjectWizard({ open, onClose, onCreated }: CreateProjectWizardProps) {
  const { projects, refreshProjects, setCurrentProject } = useProject()
  const { reportAction } = useOnboarding()
  const [saveDirectory, setSaveDirectory] = useState<string | null>(null)
  const [directoryError, setDirectoryError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    let stopped = false
    setSaveDirectory(null); setDirectoryError(null)
    fetchWorkspace().then(value => { if (!stopped) setSaveDirectory(value.directory) }).catch(error => { if (!stopped) setDirectoryError(error.message) })
    return () => { stopped = true }
  }, [open])
  const [mounted, setMounted] = useState(false)
  const [stepIndex, setStepIndex] = useState(0)
  const [projectName, setProjectName] = useState('')
  const [measurementSystem, setMeasurementSystem] = useState<MeasurementSystem>('SI')
  const [projectIdPreview, setProjectIdPreview] = useState('ORG_PROJECT_ISO_YEAR_SEQ')
  const [aoiMode, setAoiMode] = useState<AOIMode>('upload')
  const [aoiFile, setAoiFile] = useState<File | null>(null)
  const [drawOverlayOpen, setDrawOverlayOpen] = useState(false)
  const [drawnAoi, setDrawnAoi] = useState<any>(null)
  const [aoiSummary, setAoiSummary] = useState<AOIPreviewResponse | null>(null)
  const [crsSelection, setCrsSelection] = useState<ProjectCRSRecommendation | null>(null)
  const [crsDialogOpen, setCrsDialogOpen] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [submitState, setSubmitState] = useState<{ loading: boolean; error: string | null }>({
    loading: false,
    error: null,
  })
  const [isClosing, setIsClosing] = useState(false)
  const drawOverlayPrevRef = useRef(false)
  const crsDialogPrevRef = useRef(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  const handleClose = () => {
    trackEvent('dialog', 'CreateProjectWizard', 'close_create_project_wizard')
    setIsClosing(true)
    setTimeout(() => onClose(), 150)
  }

  useEffect(() => {
    if (open) {
      trackEvent('dialog', 'CreateProjectWizard', 'open_create_project_wizard')
      setIsClosing(false)
    } else {
      const resetTimer = setTimeout(() => {
        setStepIndex(0)
        setAoiSummary(null)
        setCrsSelection(null)
        setPreviewError(null)


        setSubmitState({ loading: false, error: null })
        setDrawnAoi(null)


        setAoiFile(null)


      }, 200)
      return () => clearTimeout(resetTimer)
    }
  }, [open])

  useEffect(() => {
    if (drawOverlayPrevRef.current === drawOverlayOpen) return
    trackEvent('dialog', 'CreateProjectWizard', drawOverlayOpen ? 'open_aoi_draw_overlay' : 'close_aoi_draw_overlay')
    drawOverlayPrevRef.current = drawOverlayOpen
  }, [drawOverlayOpen])

  useEffect(() => {
    if (crsDialogPrevRef.current === crsDialogOpen) return
    trackEvent('dialog', 'CreateProjectWizard', crsDialogOpen ? 'open_crs_selector_dialog' : 'close_crs_selector_dialog')
    crsDialogPrevRef.current = crsDialogOpen
  }, [crsDialogOpen])

  useEffect(() => {
    const iso = (aoiSummary?.iso3 || 'ISO').toUpperCase()
    const sanitizedName = sanitizeProjectName(projectName)
    const orgSlug = 'ORG' // Preserve the existing project ID format without an organization field.
    const year = new Date().getFullYear()
    const prefix = `${orgSlug}_${sanitizedName || 'PROJECT'}_${iso || 'ISO'}_${year}_`
    let seq = 1
    projects.forEach((proj) => {
      if (proj.project_id && proj.project_id.startsWith(prefix)) {
        const suffix = proj.project_id.slice(prefix.length)
        const parsed = parseInt(suffix, 10)
        if (!Number.isNaN(parsed)) {
          seq = Math.max(seq, parsed + 1)
        }
      }
    })
    setProjectIdPreview(`${prefix}${seq.toString().padStart(3, '0')}`)
  }, [projectName, aoiSummary?.iso3, projects])


  const isStepValid = useMemo(() => {
    switch (stepIndex) {
      case 0:
        return Boolean(sanitizeProjectName(projectName))
      case 1:
        return Boolean(aoiSummary)
      case 2:
        return Boolean(aoiSummary) // CRS step valid if AOI summary exists (which provides default)
      case 3:
        return true
      default:
        return false
    }
  }, [stepIndex, projectName, aoiSummary])

  const handleAoiPreview = async (options?: {geojson?: any; file?: File}) => {
    if (process.env.NEXT_PUBLIC_WEB_PREVIEW === '1') {
      setPreviewError('AOI captured for this session. Connect cloud processing to determine its country and CRS and create the project.')
      return
    }
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const form = new FormData()
      if (options?.file) form.append('aoi_file', options.file)
      else if (options?.geojson || (aoiMode === 'draw' && drawnAoi)) form.append('drawn_geojson', JSON.stringify(options?.geojson || drawnAoi))
      else if (aoiFile) form.append('aoi_file', aoiFile)
      else throw new Error('Upload an AOI file or draw an area to continue.')
      const summary = await previewAoi(form)
      setAoiSummary(summary)
      setCrsSelection(null)
    } catch (error) {
      setAoiSummary(null)
      setPreviewError(error instanceof Error ? error.message : 'Could not read area of interest.')
    } finally { setPreviewLoading(false) }
  }

  const handleDrawSave = (payload: {geojson: any}) => {
    setDrawnAoi(payload.geojson)
    setDrawOverlayOpen(false)
    setAoiMode('draw')
    void handleAoiPreview({geojson: payload.geojson})
  }

  const handleSubmit = async () => {
    if (!saveDirectory) { setSubmitState({ loading: false, error: directoryError || 'Save directory is still loading.' }); return }
    if (!aoiSummary) {
      setSubmitState({ loading: false, error: 'AOI summary missing.' })
      return
    }
    trackEvent('project', 'CreateProjectWizard', 'project_create_attempted', {
      project_name: sanitizeProjectName(projectName),
      aoi_mode: aoiMode
    })
    setSubmitState({ loading: true, error: null })
    try {
      const formData = new FormData()
      const sanitizedName = sanitizeProjectName(projectName)
      formData.append('project_name', sanitizedName)
      formData.append('workspace_directory', saveDirectory)
      formData.append('measurement_system', measurementSystem)
      if (aoiMode === 'upload' && aoiFile) {
        formData.append('aoi_file', aoiFile)
      } else if (drawnAoi) {
        formData.append('drawn_geojson', JSON.stringify(drawnAoi))
      }

      if (crsSelection) {
        formData.append('crs_epsg', String(crsSelection.epsg))
        formData.append('crs_name', crsSelection.name)
      }

      const response: CreateProjectResponse = await createProject(formData)
      trackEvent('project', 'CreateProjectWizard', 'project_create_succeeded', {
        project_name: response.project_name
      })
      await refreshProjects()
      setCurrentProject(response.project_name)

      // Report project created for tour auto-advance
      reportAction('project-created')

      setIsClosing(true)
      setTimeout(() => {
        trackEvent('dialog', 'CreateProjectWizard', 'close_create_project_wizard', {
          reason: 'project_created',
          project_name: response.project_name
        })
        onCreated(response.project_name)
        onClose()
      }, 150)
    } catch (error) {
      trackEvent('error', 'CreateProjectWizard', 'project_create_failed', {
        project_name: sanitizeProjectName(projectName),
        error: error instanceof Error ? error.message : String(error)
      })
      setSubmitState({
        loading: false,
    error: error instanceof Error ? error.message : 'Failed to create project.'
      })
      return
    }
    setSubmitState({ loading: false, error: null })
  }

  const body = (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6">
      <div 
        className={cn(
          "absolute inset-0 bg-black/90 backdrop-blur-xl overflow-hidden",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}
        onClick={handleClose}
      >
        {/* Dynamic Aurora Background */}
        <div className="absolute inset-0 bg-[linear-gradient(90deg,#581c8733_0%,#1e3a8a33_20%,#064e3b33_40%,#14532d33_60%,#713f1233_80%,#7f1d1d33_100%)] bg-[length:200%_100%] animate-aurora" />
        
        {/* Moving Grid */}
        <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(255,255,255,0.1)_1px,transparent_1px),linear-gradient(to_bottom,rgba(255,255,255,0.1)_1px,transparent_1px)] bg-[size:40px_40px]" />
        
        {/* Vignette */}
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_10%,#000000_100%)]" />
      </div>

      <div 
        className={cn(
          "relative z-10 w-full max-w-6xl max-h-[90vh] overflow-hidden bg-[#050505]/95 border border-white/10 rounded-sm shadow-[0_0_60px_rgba(0,0,0,0.8)] flex flex-col",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}
      >
        {/* Decorative Top Line */}
        <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-primary/50 to-transparent" />

        <header className="flex items-center justify-between px-8 py-6 border-b border-white/10 bg-black/30">
          <div>
            <div className="text-xs font-mono uppercase tracking-[0.25em] text-white/50">Project Creator</div>
            <h2 className="text-2xl font-bold text-white uppercase tracking-wide">New Dataset Project</h2>
          </div>
          <button onClick={handleClose} className="p-2 rounded-sm border border-transparent hover:border-white/20 hover:bg-white/5 text-white/60 hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </header>

        <div className="flex flex-1 overflow-hidden">
          <aside className="w-64 border-r border-white/5 bg-black/40 px-4 py-6 space-y-4">
            {STEPS.map((step, idx) => (
              <button
                key={step.id}
                onClick={() => setStepIndex(idx)}
                className={cn(
                  'w-full text-left px-4 py-3 rounded-sm border transition-all font-mono text-xs uppercase tracking-wider',
                  idx === stepIndex
                    ? 'border-primary text-primary bg-primary/10 shadow-[0_0_15px_rgba(var(--primary),0.3)]'
                    : 'border-white/10 text-white/60 hover:text-white hover:border-white/30'
                )}
              >
                {idx + 1}. {step.label}
              </button>
            ))}
          </aside>

          <section className="flex-1 overflow-y-auto p-8 space-y-8">
            <div key={stepIndex} className="animate-slide-up-fade" data-tour={`wizard-step-${stepIndex + 1}`}>
              {stepIndex === 0 && (
                <IdentityStep
                  projectName={projectName}
                  measurementSystem={measurementSystem}
                  projectIdPreview={projectIdPreview}
                  onProjectNameChange={(value) => setProjectName(sanitizeProjectName(value))}
                  onMeasurementSystemChange={(value) => setMeasurementSystem(value)}
                />
              )}

              {stepIndex === 1 && (
                <AOIStep
                  mode={aoiMode}
                  onModeChange={(mode) => {
                    setAoiMode(mode)
                    if (mode === 'draw') {
                      reportAction('click-draw-tab')
                    }
                  }}
                  aoiFile={aoiFile}
                  onFileChange={(file) => {
                    setAoiFile(file)
                    setDrawnAoi(null)
                    if (file) {
                      void handleAoiPreview({ file })
                    } else {
                      setAoiSummary(null)
                    }
                  }}
                  
                  
                  
                  
                  
                  
                  
                  
                  summary={aoiSummary}
                  crsSelection={crsSelection}
                  onLaunchCrsSelector={() => setCrsDialogOpen(true)}
                  previewError={previewError}
                  previewLoading={previewLoading}
                  onLaunchDraw={() => {
                    setDrawOverlayOpen(true)
                    setAoiMode('draw')
                    reportAction('click-launch-drawing')
                  }}
                />
              )}

              {stepIndex === 2 && (
                <CRSStep
                  summary={aoiSummary}
                  crsSelection={crsSelection}
                  onLaunchCrsSelector={() => setCrsDialogOpen(true)}
                />
              )}

              

              {stepIndex === 3 && (
                <ReviewStep
                  projectName={projectName}
                  measurementSystem={measurementSystem}
                  units={UNIT_TABLE[measurementSystem]}
                  aoiSummary={aoiSummary}
                  crsSelection={crsSelection}
                  
                  
                  
                  
                  
                  projectIdPreview={projectIdPreview}
                />
              )}
            </div>
          </section>
        </div>

        <div className="px-8 py-3 border-t border-white/10 text-xs text-white/50">
          <span className="text-white/35">{process.env.NEXT_PUBLIC_CLOUD_MODE === '1' ? 'Storage ' : 'Save location '}</span>
          <span className="break-all">{directoryError || (process.env.NEXT_PUBLIC_CLOUD_MODE === '1' ? 'Temporary workspace — use Download to keep your project assets.' : saveDirectory ? `${saveDirectory}/${sanitizeProjectName(projectName) || 'Project name'}` : 'Loading directory…')}</span>
        </div>
        <footer className="flex items-center justify-between px-8 py-5 border-t border-white/10 bg-black/40">
          <div className="text-xs font-mono text-white/40 uppercase tracking-widest">
            Step {stepIndex + 1} of {STEPS.length}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => setStepIndex(Math.max(0, stepIndex - 1))}
              disabled={stepIndex === 0 || submitState.loading}
              className="flex items-center gap-2 px-5 py-2 text-xs font-mono uppercase tracking-wider text-white/50 border border-white/15 rounded-sm hover:text-white hover:border-white/40 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" />
              Back
            </button>
            {stepIndex < STEPS.length - 1 ? (
              <button
                onClick={() => {
                  // Report wizard step completion for tour auto-advance
                  const stepActions: Record<number, TourAction> = {
                    0: 'wizard-step-1-complete',
                    1: 'wizard-step-2-complete',
                    2: 'wizard-step-3-complete',
                    3: 'wizard-step-4-complete',
                  }
                  const action = stepActions[stepIndex]
                  if (action) {
                    reportAction(action)
                  }
                  setStepIndex(Math.min(STEPS.length - 1, stepIndex + 1))
                }}
                disabled={!isStepValid || submitState.loading}
                className={cn(
                  'flex items-center gap-2 px-6 py-2 text-xs font-mono uppercase tracking-wider rounded-sm border transition-all',
                  isStepValid
                    ? 'bg-primary text-black border-primary hover:bg-primary/90'
                    : 'bg-white/5 text-white/30 border-white/10 cursor-not-allowed'
                )}
              >
                Next
                <ChevronRight className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={handleSubmit}
                disabled={!isStepValid || submitState.loading || !saveDirectory}
                className="flex items-center gap-2 px-6 py-2 text-xs font-mono uppercase tracking-wider rounded-sm bg-primary text-black border border-primary hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {submitState.loading ? (
                  <>
                    <LoaderDots />
                    Creating...
                  </>
                ) : (
                  <>
                    Deploy Project
                    <CheckCircle2 className="w-4 h-4" />
                  </>
                )}
              </button>
            )}
          </div>
        </footer>

        {submitState.error && (
          <div className="border-t border-red-500/30 bg-red-500/10 text-red-300 px-8 py-3 text-xs font-mono flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {submitState.error}
          </div>
        )}
      </div>

      <AOIDrawOverlay
        open={drawOverlayOpen}
        onClose={() => setDrawOverlayOpen(false)}
        onSave={handleDrawSave}
      />
      
      <CRSSelectorDialog
        open={crsDialogOpen}
        onClose={() => setCrsDialogOpen(false)}
        onSelect={(crs) => {
          setCrsSelection({
            epsg: crs.epsg,
            name: crs.name,
            reason: 'Manual Selection',
            utm_zone: undefined,
            hemisphere: undefined
          })
          setCrsDialogOpen(false)
        }}
      />
    </div>
  )

  if (!mounted || !open) return null
  return createPortal(body, document.body)
}

function IdentityStep(props: {
  projectName: string
  measurementSystem: MeasurementSystem
  projectIdPreview: string
  onProjectNameChange: (val: string) => void
  onMeasurementSystemChange: (val: MeasurementSystem) => void
}) {
  const units = UNIT_TABLE[props.measurementSystem]
  return (
    <div className="space-y-8">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-2">
          <label className="text-xs font-mono uppercase text-white/50 tracking-widest">Project Name</label>
          <input
            value={props.projectName}
            onChange={(event) => props.onProjectNameChange(event.target.value)}
            maxLength={48}
            placeholder="e.g. WATERLOO-DATASETS"
            className="w-full bg-black/50 border border-white/10 rounded-sm px-4 py-2 text-sm text-white focus:outline-none focus:border-primary"
          />
          <p className="text-[11px] text-white/40 font-mono">Letters, numbers, and hyphen only.</p>
        </div>
        <div className="space-y-2">
          <label className="text-xs font-mono uppercase text-white/50 tracking-widest">Measurement System</label>
          <div className="flex gap-2">
            {(['SI', 'Imperial'] as MeasurementSystem[]).map((system) => (
              <button
                key={system}
                onClick={() => props.onMeasurementSystemChange(system)}
                className={cn(
                  'flex-1 border rounded-sm px-3 py-2 text-sm font-mono uppercase tracking-widest transition-all',
                  props.measurementSystem === system
                    ? 'border-primary text-primary bg-primary/10'
                    : 'border-white/10 text-white/60 hover:border-white/30 hover:text-white'
                )}
              >
                {system}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="border border-white/10 rounded-sm p-4 bg-black/40">
          <div className="text-xs font-mono uppercase text-white/50 tracking-widest mb-2">Project ID</div>
          <div className="text-lg font-bold text-white tracking-wide">{props.projectIdPreview}</div>
          <p className="text-[11px] text-white/40 font-mono mt-1">
            ISO code and sequence update once AOI is processed.
          </p>
        </div>
        <div className="border border-white/10 rounded-sm p-4 bg-black/40">
          <div className="text-xs font-mono uppercase text-white/50 tracking-widest mb-3">
            Measurement Units Snapshot
          </div>
          <div className="grid grid-cols-2 gap-2 text-[11px] font-mono text-white/70">
            {units.map((entry) => (
              <div key={entry.quantity} className="flex flex-col border border-white/5 rounded-sm px-3 py-2">
                <span className="text-white/40 uppercase tracking-widest">{entry.quantity}</span>
                <span>{entry.unit}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function AOIStep(props: {
  mode: AOIMode
  onModeChange: (mode: AOIMode) => void
  aoiFile: File | null
  onFileChange: (file: File | null) => void
  
  
  
  
  
  
  
  
  summary: AOIPreviewResponse | null
  crsSelection: ProjectCRSRecommendation | null
  onLaunchCrsSelector: () => void
  previewLoading: boolean
  previewError: string | null
  onLaunchDraw: () => void
}) {
  return (
    <div className="space-y-8">
      <div className="flex gap-4">
        {(['upload', 'draw'] as AOIMode[]).map((mode) => (
          <button
            key={mode}
            onClick={() => props.onModeChange(mode)}
            data-tour={mode === 'draw' ? 'aoi-draw-tab' : undefined}
            className={cn(
              'flex-1 border rounded-sm px-4 py-3 text-sm font-mono uppercase tracking-widest transition-all',
              props.mode === mode
                ? 'border-primary text-primary bg-primary/10'
                : 'border-white/10 text-white/60 hover:border-white/30 hover:text-white'
            )}
          >
            {mode === 'upload' ? 'Upload Geometry' : 'Draw on Map'}
          </button>
        ))}
      </div>

      

      {props.mode === 'upload' ? (
        <label className="block border border-dashed border-white/20 rounded-sm p-8 bg-black/40 space-y-4">
          <span className="block text-sm font-mono uppercase tracking-widest text-white/80">Area of interest</span>
          <span className="block text-xs text-white/50">Upload a polygon as GeoJSON, GeoPackage, KML, or zipped Shapefile.</span>
          <input aria-label="AOI geometry file" type="file" accept=".geojson,.json,.gpkg,.kml,.zip" onChange={event => props.onFileChange(event.target.files?.[0] || null)} className="block w-full text-sm text-white/70 file:mr-4 file:border file:border-primary/40 file:bg-primary/10 file:px-4 file:py-2 file:text-primary" />
          {props.aoiFile && <span className="block text-xs text-primary">{props.aoiFile.name}</span>}
        </label>
      ) : (
        <button onClick={props.onLaunchDraw} className="w-full border border-dashed border-primary/40 rounded-sm p-8 bg-primary/5 text-primary font-mono uppercase tracking-widest">Open AOI Map</button>
      )}
      {props.previewError && <p role="alert" className="text-sm text-red-400">{props.previewError}</p>}
      {props.previewLoading && (
        <div className="text-xs font-mono text-white/50 uppercase tracking-widest flex items-center gap-2">
          <LoaderDots />
          Analyzing AOI...
        </div>
      )}

      {props.summary && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <SummaryCard
            icon={<FileText className="w-5 h-5 text-primary" />}
            title="AOI Area"
            value={`${props.summary.area_km2.toLocaleString()} km²`}
            subtitle="Computed via WGS84 ellipsoid"
          />
          <SummaryCard
            icon={<MapPin className="w-5 h-5 text-primary" />}
            title="Countries"
            value={props.summary.country || 'Pending'}
            subtitle="Derived from centroid"
          />
        </div>
      )}
    </div>
  )
}

function CRSStep(props: {
  summary: AOIPreviewResponse | null
  crsSelection: ProjectCRSRecommendation | null
  onLaunchCrsSelector: () => void
}) {
  const displayCRS = props.crsSelection || props.summary?.recommended_crs

  return (
    <div className="space-y-6">
      <div className="border border-white/10 rounded-sm p-6 bg-black/40">
        <div className="flex items-start gap-4 mb-6">
          <div className="p-3 border border-white/10 rounded-sm bg-black/30 text-primary">
            <Globe className="w-8 h-8" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-white">Coordinate Reference System</h3>
            <p className="text-sm text-white/60 max-w-lg mt-1">
              The system automatically recommends a UTM zone based on your AOI&apos;s geographic centroid. You can override this if your project requires a specific projection.
            </p>
          </div>
        </div>

        <div className="bg-black/50 border border-white/10 rounded-sm p-6 flex flex-col md:flex-row items-center justify-between gap-6">
          <div>
            <div className="text-xs font-mono uppercase text-white/50 tracking-widest mb-1">Selected System</div>
            <div className="text-xl font-bold text-white mb-1">{displayCRS?.name || 'Pending Analysis'}</div>
            <div className="flex items-center gap-3 text-sm font-mono">
              <span className="text-primary bg-primary/10 px-2 py-0.5 rounded-sm">
                EPSG:{displayCRS?.epsg}
              </span>
              <span className="text-white/40">
                • {props.crsSelection ? 'Manual Selection' : 'Auto-Recommended'}
              </span>
            </div>
          </div>

          <button
            onClick={props.onLaunchCrsSelector}
            className="px-6 py-3 text-sm font-mono uppercase tracking-wider bg-white/5 border border-white/20 text-white hover:bg-white/10 hover:border-white/40 hover:text-primary transition-all rounded-sm flex items-center gap-2"
          >
            <Globe className="w-4 h-4" />
            Change System
          </button>
        </div>

        {displayCRS?.reason && (
          <div className="mt-4 flex items-center gap-2 text-xs text-white/40 font-mono px-2">
            <div className="w-1.5 h-1.5 rounded-full bg-primary/50" />
            {displayCRS.reason}
          </div>
        )}
      </div>
    </div>
  )
}



function ReviewStep(props: {
  projectName: string
  measurementSystem: MeasurementSystem
  units: { quantity: string; unit: string }[]
  aoiSummary: AOIPreviewResponse | null
  crsSelection: ProjectCRSRecommendation | null
  
  
  
  
  
  projectIdPreview: string
}) {
  const displayCRS = props.crsSelection || props.aoiSummary?.recommended_crs

  return (
    <div className="space-y-6">
      <div className="border border-white/10 rounded-sm p-4 bg-black/40">
        <div className="text-xs font-mono uppercase text-white/40 tracking-widest mb-3">Project Overview</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm text-white/80">
          <div>
            <div className="text-white/50 text-[11px] uppercase font-mono">Name</div>
            <div className="font-semibold">{props.projectName || '—'}</div>
          </div>
          <div>
            <div className="text-white/50 text-[11px] uppercase font-mono">Measurement System</div>
            <div className="font-semibold">{props.measurementSystem}</div>
          </div>
          <div>
            <div className="text-white/50 text-[11px] uppercase font-mono">Project ID</div>
            <div className="font-semibold">{props.projectIdPreview}</div>
          </div>
        </div>
      </div>

      <div className="border border-white/10 rounded-sm p-4 bg-black/40 space-y-2">
        <div className="text-xs font-mono uppercase text-white/40 tracking-widest">AOI Summary</div>
        {props.aoiSummary ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm text-white/80">
            <div>
              <div className="text-white/50 text-[11px] uppercase font-mono">Area</div>
              <div className="font-semibold">{props.aoiSummary.area_km2.toLocaleString()} km²</div>
            </div>
            <div>
              <div className="text-white/50 text-[11px] uppercase font-mono">Country</div>
              <div className="font-semibold">{props.aoiSummary.country || 'Pending'}</div>
            </div>
            <div>
              <div className="text-white/50 text-[11px] uppercase font-mono">Coordinate System</div>
              <div className="font-semibold">{displayCRS?.name || 'Pending'}</div>
              <div className="text-[10px] text-white/40 font-mono">{displayCRS?.epsg ? `EPSG:${displayCRS.epsg}` : ''}</div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-white/50 font-mono">Awaiting AOI analysis.</div>
        )}
      </div>

      

      <div className="border border-white/10 rounded-sm p-4 bg-black/40">
        <div className="text-xs font-mono uppercase text-white/40 tracking-widest mb-3">Units Reference</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] font-mono text-white/70">
          {props.units.map((entry) => (
            <div key={entry.quantity} className="border border-white/5 rounded-sm px-3 py-2">
              <div className="text-white/40 uppercase tracking-widest">{entry.quantity}</div>
              <div>{entry.unit}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function SummaryCard(props: {
  icon: React.ReactNode
  title: string
  value: string
  subtitle: string
}) {
  return (
    <div className="border border-white/10 rounded-sm p-4 bg-black/40 flex items-start gap-3">
      <div className="p-2 border border-white/10 rounded-sm bg-black/30">{props.icon}</div>
      <div className="flex flex-col">
        <div className="text-xs font-mono uppercase text-white/50 tracking-widest">{props.title}</div>
        <div className="text-lg font-bold text-white">{props.value}</div>
        <div className="text-[11px] text-white/40 font-mono">{props.subtitle}</div>
      </div>
    </div>
  )
}



function LoaderDots() {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" />
      <span className="w-1.5 h-1.5 bg-primary/70 rounded-full animate-pulse delay-150" />
      <span className="w-1.5 h-1.5 bg-primary/40 rounded-full animate-pulse delay-300" />
    </span>
  )
}

function sanitizeProjectName(value: string): string {
  return value.replace(/[^A-Za-z0-9-]+/g, '-').replace(/-+/g, '-')
}



interface AOIDrawOverlayProps {
  open: boolean
  onClose: () => void
  onSave: (payload: { geojson: any; startPoint?: { lat: number; lon: number }; endPoint?: { lat: number; lon: number } }) => void
}

// Calculate area of a GeoJSON polygon in square kilometers using spherical geometry
function calculatePolygonAreaKm2(geojson: any): number {
  if (!geojson?.features?.length) return 0

  let totalArea = 0
  for (const feature of geojson.features) {
    if (feature.geometry?.type === 'Polygon') {
      const coords = feature.geometry.coordinates[0]
      if (coords && coords.length >= 4) {
        // Use spherical excess formula for geodesic area calculation
        const toRadians = (deg: number) => (deg * Math.PI) / 180
        let area = 0
        for (let i = 0; i < coords.length - 1; i++) {
          const p1 = coords[i]
          const p2 = coords[i + 1]
          area += toRadians(p2[0] - p1[0]) * (2 + Math.sin(toRadians(p1[1])) + Math.sin(toRadians(p2[1])))
        }
        // Earth radius in km
        const R = 6371
        area = Math.abs(area * R * R / 2)
        totalArea += area
      }
    }
  }
  return totalArea
}

// Helper function to check if a point is inside a polygon using ray casting
function isPointInPolygon(point: [number, number], polygon: [number, number][]): boolean {
  const [x, y] = point
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i]
    const [xj, yj] = polygon[j]
    if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
      inside = !inside
    }
  }
  return inside
}

// Helper to get polygon coordinates from draw data
function getPolygonCoords(drawData: any): [number, number][] | null {
  if (!drawData?.features?.length) return null
  const feature = drawData.features.find((f: any) => f.geometry?.type === 'Polygon')
  if (!feature?.geometry?.coordinates?.[0]) return null
  return feature.geometry.coordinates[0] as [number, number][]
}

// Calculate area from raw coordinate array (for live preview)
function calculateAreaFromCoords(coords: [number, number][]): number {
  if (coords.length < 3) return 0

  // Close the polygon if not already closed
  const closedCoords = coords[0][0] === coords[coords.length - 1][0] &&
                       coords[0][1] === coords[coords.length - 1][1]
    ? coords
    : [...coords, coords[0]]

  const toRadians = (deg: number) => (deg * Math.PI) / 180
  let area = 0
  for (let i = 0; i < closedCoords.length - 1; i++) {
    const p1 = closedCoords[i]
    const p2 = closedCoords[i + 1]
    area += toRadians(p2[0] - p1[0]) * (2 + Math.sin(toRadians(p1[1])) + Math.sin(toRadians(p2[1])))
  }
  const R = 6371 // Earth radius in km
  return Math.abs(area * R * R / 2)
}

interface CoordinateVertex {
  lat: string
  lng: string
}

function AOIDrawOverlay({ open, onClose, onSave }: AOIDrawOverlayProps) {
  const { reportAction } = useOnboarding()
  const mapContainerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const drawRef = useRef<MapboxDraw | null>(null)
  const startMarkerRef = useRef<maplibregl.Marker | null>(null)
  const endMarkerRef = useRef<maplibregl.Marker | null>(null)
  const selectModeRef = useRef<'start' | 'end' | null>(null)
  const [selectMode, setSelectMode] = useState<'start' | 'end' | null>(null)
  const [statusMessage, setStatusMessage] = useState('Draw polygon to define AOI.')
  const [currentArea, setCurrentArea] = useState<number>(0)
  const [areaExceeded, setAreaExceeded] = useState(false)
  const [isDrawing, setIsDrawing] = useState(false)
  const [drawReady, setDrawReady] = useState(false)
  const [hasValidAOI, setHasValidAOI] = useState(false)
  const [polygonCoords, setPolygonCoords] = useState<[number, number][] | null>(null)
  const polygonCoordsRef = useRef<[number, number][] | null>(null)
  const [livePreviewArea, setLivePreviewArea] = useState<number | null>(null)
  const [livePreviewExceeded, setLivePreviewExceeded] = useState(false)
  const isDrawingRef = useRef(false)

  // Keep polygonCoordsRef in sync
  useEffect(() => {
    polygonCoordsRef.current = polygonCoords
  }, [polygonCoords])

  // Coordinate entry panel state
  const [showCoordPanel, setShowCoordPanel] = useState(false)
  const [coordPanelTab, setCoordPanelTab] = useState<'polygon' | 'points'>('polygon')
  const [vertexInputs, setVertexInputs] = useState<CoordinateVertex[]>([
    { lat: '', lng: '' },
    { lat: '', lng: '' },
    { lat: '', lng: '' },
  ])

  // Sync vertex inputs when polygon coordinates change (from drawing)
  useEffect(() => {
    if (polygonCoords && polygonCoords.length >= 3) {
      // Remove the closing point if it's the same as the first (GeoJSON polygons close themselves)
      const vertices = polygonCoords.slice(0, -1)
      const newInputs: CoordinateVertex[] = vertices.map(([lng, lat]) => ({
        lat: lat.toFixed(6),
        lng: lng.toFixed(6)
      }))
      // Ensure at least 3 inputs
      while (newInputs.length < 3) {
        newInputs.push({ lat: '', lng: '' })
      }
      setVertexInputs(newInputs)
    }
  }, [polygonCoords])

  // Keep selectModeRef in sync
  useEffect(() => {
    selectModeRef.current = selectMode
  }, [selectMode])

  // Handle ESC key to cancel select mode
  useEffect(() => {
    if (!open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectMode) {
          setSelectMode(null)
        } else if (isDrawing && drawRef.current) {
          drawRef.current.changeMode('simple_select')
          setIsDrawing(false)
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, selectMode, isDrawing])

  useEffect(() => {
    if (!open || !mapContainerRef.current) return

    setDrawReady(false)
    setStatusMessage('Preparing drawing tools…')
    maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs')
    // Mapbox Draw's hit testing, keyboard handling and control CSS must use
    // MapLibre's class names. See MapLibre's official Draw integration.
    Object.assign(MapboxDraw.constants.classes, {
      CANVAS: 'maplibregl-canvas', CONTROL_BASE: 'maplibregl-ctrl',
      CONTROL_PREFIX: 'maplibregl-ctrl-', CONTROL_GROUP: 'maplibregl-ctrl-group',
      ATTRIBUTION: 'maplibregl-ctrl-attrib',
    })
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: {
        version: 8,
        sources: {
          // Use ESRI World Imagery for better satellite view like Project Management
          esriImagery: {
            type: 'raster',
            tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
            tileSize: 256,
            // Avoid Esri "Map Data not available" placeholder tiles by overzooming the last
            // available imagery tiles when users zoom in further.
            maxzoom: 17,
            attribution: 'Esri, Maxar, Earthstar Geographics',
          },
          esriLabels: {
            type: 'raster',
            tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'],
            tileSize: 256,
            maxzoom: 17,
            attribution: 'Esri',
          },
        },
        layers: [
          {
            id: 'esri-imagery',
            type: 'raster',
            source: 'esriImagery',
          },
          {
            id: 'esri-labels',
            type: 'raster',
            source: 'esriLabels',
            paint: {
              'raster-opacity': 0.8,
            },
          },
        ],
      },
      center: [-98, 39], // Center of continental US
      zoom: 4,
      pitch: 0,
      bearing: 0,
      maxPitch: 0, // Disable pitch completely for 2D drawing
      minPitch: 0, // Also set minPitch to 0 to prevent any pitch changes
      attributionControl: false,
      trackResize: true,
      fadeDuration: 0, // Disable fade animations
      renderWorldCopies: true,
      interactive: true,
    })

    mapRef.current = map

    // Workaround: @mapbox/mapbox-gl-draw passes an unsupported 3rd options arg
    // (e.g. { passive: true }) to map.on() which MapLibre v5 does not accept,
    // causing crashes on touch devices. Wrap map.on to silently drop the extra arg.
    const origOn = map.on.bind(map) as (...args: any[]) => any
    ;(map as any).on = (type: any, layerOrFn: any, fn?: any, opts?: any) => {
      if (typeof fn === 'function') return origOn(type, layerOrFn, fn)
      return origOn(type, layerOrFn)
    }

    // Disable all rotation and pitch manipulation immediately
    map.dragRotate.disable()
    map.touchZoomRotate.disableRotation()
    map.keyboard.disableRotation()

    // Enforce 2D view - prevent any pitch changes
    const enforce2D = () => {
      if (map.getPitch() !== 0) {
        map.setPitch(0)
      }
      if (map.getBearing() !== 0) {
        map.setBearing(0)
      }
    }

    map.on('pitch', enforce2D)
    map.on('pitchstart', enforce2D)
    map.on('rotate', enforce2D)
    map.on('rotatestart', enforce2D)

    // Draw connects its input listeners when the map is loaded. Keep the
    // toolbar disabled until that connection is established.
    map.once('load', () => {
      const draw = new MapboxDraw({
        displayControlsDefault: false,
        controls: {
          polygon: true,
          trash: true,
        },
        styles: [
          // Polygon fill
          {
            id: 'gl-draw-polygon-fill',
            type: 'fill',
            filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
            paint: {
              'fill-color': '#ef4444',
              'fill-outline-color': '#ef4444',
              'fill-opacity': 0.25,
            },
          },
          // Polygon outline stroke - active
          {
            id: 'gl-draw-polygon-stroke-active',
            type: 'line',
            filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
            layout: {
              'line-cap': 'round',
              'line-join': 'round',
            },
            paint: {
              'line-color': '#ef4444',
              'line-width': 3,
            },
          },
          // Polygon midpoints
          {
            id: 'gl-draw-polygon-midpoint',
            type: 'circle',
            filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'midpoint']],
            paint: {
              'circle-radius': 5,
              'circle-color': '#fbbf24',
            },
          },
          // Polygon vertices
          {
            id: 'gl-draw-polygon-and-line-vertex-inactive',
            type: 'circle',
            filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point'], ['!=', 'mode', 'static']],
            paint: {
              'circle-radius': 7,
              'circle-color': '#ffffff',
              'circle-stroke-color': '#ef4444',
              'circle-stroke-width': 2,
            },
          },
          // Line stroke - active (while drawing)
          {
            id: 'gl-draw-line-active',
            type: 'line',
            filter: ['all', ['==', '$type', 'LineString'], ['!=', 'mode', 'static']],
            layout: {
              'line-cap': 'round',
              'line-join': 'round',
            },
            paint: {
              'line-color': '#ef4444',
              'line-width': 3,
              'line-dasharray': [0.2, 2],
            },
          },
          // Static polygon fill
          {
            id: 'gl-draw-polygon-fill-static',
            type: 'fill',
            filter: ['all', ['==', 'mode', 'static'], ['==', '$type', 'Polygon']],
            paint: {
              'fill-color': '#ef4444',
              'fill-outline-color': '#ef4444',
              'fill-opacity': 0.15,
            },
          },
          // Static polygon outline
          {
            id: 'gl-draw-polygon-stroke-static',
            type: 'line',
            filter: ['all', ['==', 'mode', 'static'], ['==', '$type', 'Polygon']],
            layout: {
              'line-cap': 'round',
              'line-join': 'round',
            },
            paint: {
              'line-color': '#ef4444',
              'line-width': 3,
            },
          },
        ],
      })

      drawRef.current = draw
      map.addControl(draw as any, 'top-left')
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
      map.addControl(new maplibregl.ScaleControl({ maxWidth: 150, unit: 'metric' }), 'bottom-left')

      // Update area when polygon is created/updated
      const updateArea = () => {
        const data = draw.getAll()
        const area = calculatePolygonAreaKm2(data)
        const coords = getPolygonCoords(data)
        setCurrentArea(area)
        setPolygonCoords(coords)
        const validAOI = area > 0 && coords !== null && coords.length >= 4
        setHasValidAOI(validAOI)
        // Report polygon drawn for tour when valid AOI is created
        if (validAOI) {
          reportAction('aoi-polygon-drawn')
        }
        setAreaExceeded(false)
        if (area > 0) {
          setStatusMessage(`Area: ${area.toFixed(1)} km²`)
        } else {
          setStatusMessage('Draw polygon to define AOI.')
        }
      }

      // Mapbox Draw emits custom events outside MapLibre's built-in event union.
      const drawEvents = map as unknown as {
        on(type: 'draw.modechange', listener: (event: { mode: string }) => void): unknown
        on(type: 'draw.create' | 'draw.update' | 'draw.delete', listener: () => void): unknown
      }
      // Track drawing mode changes
      drawEvents.on('draw.modechange', (e) => {
        const drawing = e.mode === 'draw_polygon'
        setIsDrawing(drawing)
        isDrawingRef.current = drawing
        if (!drawing) {
          // Clear live preview when done drawing
          setLivePreviewArea(null)
          setLivePreviewExceeded(false)
        }
      })

      // Live preview area calculation during drawing
      const handleMouseMove = (e: maplibregl.MapMouseEvent) => {
        if (!isDrawingRef.current || !draw) return

        // Get the current feature being drawn
        const featureIds = draw.getSelectedIds()
        const allFeatures = draw.getAll()

        // Find any feature in draw_polygon mode (it's typically a LineString while drawing)
        let currentVertices: [number, number][] = []

        for (const feature of allFeatures.features) {
          if (feature.geometry.type === 'LineString' && feature.geometry.coordinates) {
            // LineString while drawing - coordinates are the vertices so far
            currentVertices = feature.geometry.coordinates as [number, number][]
            break
          } else if (feature.geometry.type === 'Polygon' && feature.geometry.coordinates?.[0]) {
            // Could be a completed polygon being edited
            currentVertices = (feature.geometry.coordinates[0] as [number, number][]).slice(0, -1)
            break
          }
        }

        // Need at least 2 vertices to form a triangle with cursor
        if (currentVertices.length >= 2) {
          // Add cursor position as the "next" vertex
          const cursorPos: [number, number] = [e.lngLat.lng, e.lngLat.lat]
          const previewCoords = [...currentVertices, cursorPos]

          const previewArea = calculateAreaFromCoords(previewCoords)
          setLivePreviewArea(previewArea)
          setLivePreviewExceeded(false)
        } else if (currentVertices.length === 1) {
          // Only one vertex - show 0 area
          setLivePreviewArea(0)
          setLivePreviewExceeded(false)
        }
      }

      map.on('mousemove', handleMouseMove)

      // Clear live preview when polygon is created
      drawEvents.on('draw.create', () => {
        setLivePreviewArea(null)
        setLivePreviewExceeded(false)
        updateArea()
      })
      drawEvents.on('draw.update', updateArea)
      drawEvents.on('draw.delete', updateArea)
      setDrawReady(true)
      draw.changeMode('draw_polygon')
      setIsDrawing(true)
      isDrawingRef.current = true
      setStatusMessage('Click to add vertices. Click the first vertex or double-click to finish.')
    })

    // Note: Start/end point click handling is done via a React overlay div
    // that appears when selectMode is active. This bypasses MapboxDraw's
    // click interception on the polygon.

    return () => {
      map.remove()
      mapRef.current = null
      drawRef.current = null
      startMarkerRef.current = null
      endMarkerRef.current = null
      setSelectMode(null)
      setCurrentArea(0)
      setAreaExceeded(false)
      setIsDrawing(false)
      setDrawReady(false)
      isDrawingRef.current = false
      setHasValidAOI(false)
      setPolygonCoords(null)
      setLivePreviewArea(null)
      setLivePreviewExceeded(false)
      setStatusMessage('Draw polygon to define AOI.')
    }
  }, [open])

  if (!open) return null

  const handleSave = () => {
    const draw = drawRef.current
    if (!draw) return
    const data = draw.getAll()
    if (!data.features.length) {
      setStatusMessage('Please draw the AOI polygon before saving.')
      return
    }

    const payload: { geojson: any; startPoint?: { lat: number; lon: number }; endPoint?: { lat: number; lon: number } } = {
      geojson: data,
    }
    if (startMarkerRef.current) {
      const lngLat = startMarkerRef.current.getLngLat()
      payload.startPoint = { lat: lngLat.lat, lon: lngLat.lng }
    }
    if (endMarkerRef.current) {
      const lngLat = endMarkerRef.current.getLngLat()
      payload.endPoint = { lat: lngLat.lat, lon: lngLat.lng }
    }
    reportAction('click-save-geometry')
    onSave(payload)
  }

  // Custom draw control functions
  const startDrawPolygon = () => {
    const draw = drawRef.current
    if (draw) {
      setSelectMode(null)
      draw.changeMode('draw_polygon')
      setIsDrawing(true)
      isDrawingRef.current = true
      setStatusMessage('Click on map to add polygon vertices. Double-click to finish.')
      reportAction('click-draw-polygon')
    }
  }

  const deleteAllDrawings = () => {
    const draw = drawRef.current
    if (draw) {
      draw.deleteAll()
      setCurrentArea(0)
      setAreaExceeded(false)
      setHasValidAOI(false)
      setPolygonCoords(null)
      // Also remove start/end markers since they were inside the deleted AOI
      startMarkerRef.current?.remove()
      startMarkerRef.current = null
      endMarkerRef.current?.remove()
      endMarkerRef.current = null
      // Clear coordinate inputs
      setVertexInputs([{ lat: '', lng: '' }, { lat: '', lng: '' }, { lat: '', lng: '' }])


      setStatusMessage('Draw polygon to define AOI.')
    }
  }

  // Place marker at given coordinates (used by both click handler and manual entry)
  const placeStartMarker = (lng: number, lat: number) => {
    const map = mapRef.current
    if (!map) return false

    // Validate point is inside AOI
    const coords = polygonCoordsRef.current
    if (!coords || coords.length < 4) {
      setStatusMessage('Please create an AOI polygon first.')
      return false
    }

    const point: [number, number] = [lng, lat]
    if (!isPointInPolygon(point, coords)) {
      setStatusMessage('Start point must be inside the AOI polygon.')
      return false
    }

    // Remove existing and create new marker
    startMarkerRef.current?.remove()
    const marker = new maplibregl.Marker({ color: '#10b981' })
      .setLngLat([lng, lat])
      .addTo(map)
    startMarkerRef.current = marker

    setStatusMessage('Start point set inside AOI.')
    return true
  }

  const placeEndMarker = (lng: number, lat: number) => {
    const map = mapRef.current
    if (!map) return false

    // Validate point is inside AOI
    const coords = polygonCoordsRef.current
    if (!coords || coords.length < 4) {
      setStatusMessage('Please create an AOI polygon first.')
      return false
    }

    const point: [number, number] = [lng, lat]
    if (!isPointInPolygon(point, coords)) {
      setStatusMessage('End point must be inside the AOI polygon.')
      return false
    }

    // Remove existing and create new marker
    endMarkerRef.current?.remove()
    const marker = new maplibregl.Marker({ color: '#ef4444' })
      .setLngLat([lng, lat])
      .addTo(map)
    endMarkerRef.current = marker

    setStatusMessage('End point set inside AOI.')
    return true
  }

  // Handle map container click directly (bypasses MapboxDraw)
  const handleMapContainerClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const mode = selectModeRef.current
    if (!mode) return

    const map = mapRef.current
    if (!map) return

    // Get the map container's bounding rect
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top

    // Convert pixel coordinates to lng/lat
    const lngLat = map.unproject([x, y])

    if (mode === 'start') {
      if (placeStartMarker(lngLat.lng, lngLat.lat)) {
        setSelectMode(null)
      }
    } else if (mode === 'end') {
      if (placeEndMarker(lngLat.lng, lngLat.lat)) {
        setSelectMode(null)
      }
    }
  }

  // Add a vertex input row
  const addVertexInput = () => {
    setVertexInputs([...vertexInputs, { lat: '', lng: '' }])
  }

  // Remove a vertex input row
  const removeVertexInput = (index: number) => {
    if (vertexInputs.length <= 3) return // Minimum 3 vertices
    setVertexInputs(vertexInputs.filter((_, i) => i !== index))
  }

  // Update a specific vertex input
  const updateVertexInput = (index: number, field: 'lat' | 'lng', value: string) => {
    const updated = [...vertexInputs]
    updated[index] = { ...updated[index], [field]: value }
    setVertexInputs(updated)
  }

  // Apply polygon from coordinate inputs
  const applyPolygonFromCoords = () => {
    const map = mapRef.current
    const draw = drawRef.current
    if (!map || !draw) return

    // Parse and validate coordinates
    const validCoords: [number, number][] = []
    for (const vertex of vertexInputs) {
      const lat = parseFloat(vertex.lat)
      const lng = parseFloat(vertex.lng)
      if (isNaN(lat) || isNaN(lng)) continue
      if (lat < -90 || lat > 90 || lng < -180 || lng > 180) continue
      validCoords.push([lng, lat]) // GeoJSON uses [lng, lat]
    }

    if (validCoords.length < 3) {
      setStatusMessage('Need at least 3 valid coordinates to create a polygon.')
      return
    }

    // Close the polygon by adding first point at the end
    const closedCoords = [...validCoords, validCoords[0]]

    // Delete existing drawings
    draw.deleteAll()
    startMarkerRef.current?.remove()
    startMarkerRef.current = null
    endMarkerRef.current?.remove()
    endMarkerRef.current = null

    // Create new polygon feature
    const polygon: GeoJSON.Feature<GeoJSON.Polygon> = {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'Polygon',
        coordinates: [closedCoords]
      }
    }

    // Add to draw
    draw.add(polygon)

    // Calculate bounds and fly to the polygon
    const bounds = new maplibregl.LngLatBounds()
    for (const coord of validCoords) {
      bounds.extend(coord as [number, number])
    }
    map.fitBounds(bounds, { padding: 50, maxZoom: 15 })

    // Update area
    const data = draw.getAll()
    const area = calculatePolygonAreaKm2(data)
    const coords = getPolygonCoords(data)
    setCurrentArea(area)
    setPolygonCoords(coords)
    const isValid = area > 0 && coords !== null && coords.length >= 4
    setHasValidAOI(isValid)
    if (isValid) {
      reportAction('aoi-polygon-drawn')
    }

    setAreaExceeded(false)
    setStatusMessage(`Polygon created: ${area.toFixed(1)} km²`)
  }

  return createPortal(
    <div className="fixed inset-0 z-[300] bg-black flex flex-col">
      {/* Header toolbar */}
      <div className="flex h-16 shrink-0 items-center justify-between gap-4 px-4 py-3 border-b border-white/10 bg-black/80 backdrop-blur-sm">
        <div className="flex min-w-0 items-center gap-4">
          <div className="min-w-0">
            <div className="text-xs font-mono uppercase text-white/50 tracking-widest">AOI Drawing Console</div>
            <div className={cn(
              "text-sm mt-0.5 truncate",
              areaExceeded ? "text-red-400 font-semibold" : "text-white/70"
            )} title={statusMessage}>{statusMessage}</div>
          </div>
          {/* Area indicator badge - show live preview during drawing, otherwise show current area */}
          {(livePreviewArea !== null || currentArea > 0) && (
            <div className={cn(
              "shrink-0 whitespace-nowrap px-3 py-1.5 text-sm font-mono rounded border flex items-center gap-2",
              livePreviewArea !== null
                ? livePreviewExceeded
                  ? "border-red-500 bg-red-500/20 text-red-400 animate-pulse"
                  : "border-amber-500 bg-amber-500/20 text-amber-400"
                : areaExceeded
                  ? "border-red-500 bg-red-500/20 text-red-400"
                  : "border-emerald-500 bg-emerald-500/20 text-emerald-400"
            )}>
              {livePreviewArea !== null ? (
                <>
                  <span className="text-white/50">~</span>
                  {livePreviewArea.toFixed(1)} km²
                </>
              ) : (
                <>{currentArea.toFixed(1)} km²</>
              )}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* Draw Polygon Button */}
          <button
            onClick={startDrawPolygon}
            disabled={!drawReady}
            data-tour="draw-polygon-btn"
            className={cn(
              'px-3 py-2 text-xs font-mono uppercase tracking-widest rounded border transition-all flex items-center gap-2',
              isDrawing
                ? 'border-primary bg-primary/20 text-primary'
                : 'border-white/20 text-white/70 hover:border-primary hover:text-primary hover:bg-primary/10'
            )}
            title="Draw Polygon"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="12 2 2 7 2 17 12 22 22 17 22 7 12 2"></polygon>
            </svg>
            Draw Polygon
          </button>
          {/* Delete Button */}
          <button
            onClick={deleteAllDrawings}
            disabled={!drawReady}
            className="px-3 py-2 text-xs font-mono uppercase tracking-widest rounded border border-white/20 text-white/70 hover:border-red-500 hover:text-red-400 hover:bg-red-500/10 transition-all flex items-center gap-2"
            title="Delete All"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            </svg>
            Clear
          </button>
          {/* Enter Coordinates Button */}
          <button
            onClick={() => setShowCoordPanel(!showCoordPanel)}
            className={cn(
              'px-3 py-2 text-xs font-mono uppercase tracking-widest rounded border transition-all flex items-center gap-2',
              showCoordPanel
                ? 'border-amber-500 text-amber-400 bg-amber-500/20'
                : 'border-white/20 text-white/70 hover:border-amber-500 hover:text-amber-400 hover:bg-amber-500/10'
            )}
            title="Enter coordinates manually"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 22s-8-4.5-8-11.8A8 8 0 0 1 12 2a8 8 0 0 1 8 8.2c0 7.3-8 11.8-8 11.8z"/>
              <circle cx="12" cy="10" r="3"/>
            </svg>
            Coordinates
          </button>
          <div className="w-px h-6 bg-white/10 mx-1" />
          {/* Start Point Button */}
          
          {/* End Point Button */}
          
          <div className="w-px h-6 bg-white/10 mx-1" />
          {/* Save Button */}
          <button
            onClick={handleSave}
            disabled={areaExceeded || currentArea === 0}
            data-tour="save-geometry-btn"
            className={cn(
              "px-4 py-2 text-xs font-mono uppercase tracking-widest rounded transition-all font-bold",
              areaExceeded || currentArea === 0
                ? "bg-white/10 text-white/30 cursor-not-allowed border border-white/10"
                : "bg-primary text-black hover:bg-primary/90"
            )}
          >
            Save Geometry
          </button>
          {/* Cancel Button */}
          <button
            onClick={onClose}
            className="px-3 py-2 text-xs font-mono uppercase tracking-widest border border-white/20 text-white/60 rounded hover:border-white/40 hover:text-white transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
      {/* Instructions bar */}
      {selectMode && (
        <div className="px-4 py-2 bg-primary/10 border-b border-primary/30 text-sm font-mono text-primary">
          Click on the map to place the {selectMode === 'start' ? 'START' : 'END'} point. Press ESC or click the button again to cancel.
        </div>
      )}
      {/* Main content area with map and optional coordinate panel */}
      <div className="flex-1 flex relative overflow-hidden">
        {/* Map container */}
        <div
          className="flex-1 relative"
          style={{
            position: 'relative',
            width: '100%',
            height: '100%',
            overflow: 'hidden'
          }}
        >
          <div
            ref={mapContainerRef}
            data-tour="aoi-map"
            className="absolute inset-0"
            style={{
              width: '100%',
              height: '100%',
            }}
          />
          {/* Click capture overlay - appears when in select mode to bypass MapboxDraw */}
          {selectMode && (
            <div
              className="absolute inset-0 cursor-crosshair z-10"
              style={{ backgroundColor: 'transparent' }}
              onClick={handleMapContainerClick}
            />
          )}
        </div>
        {/* Coordinate Entry Panel */}
        {showCoordPanel && (
          <div className="w-96 min-w-[384px] border-l border-white/10 bg-black/90 backdrop-blur-sm flex flex-col overflow-hidden shrink-0">
            {/* Panel header with tabs */}
            <div className="flex border-b border-white/10">
              <button
                onClick={() => setCoordPanelTab('polygon')}
                className={cn(
                  'flex-1 px-4 py-3 text-xs font-mono uppercase tracking-widest transition-all',
                  'bg-white/5 text-primary border-b-2 border-primary'
                )}
              >
                Polygon Vertices
              </button>
              
            </div>
            {/* Panel content */}
            <div className="flex-1 overflow-y-auto p-4">
              {(
                <div className="space-y-4">
                  <div className="text-xs text-white/50 font-mono">
                    Enter at least 3 vertex coordinates (Lat, Lng) to define the AOI polygon.
                  </div>
                  {/* Vertex inputs */}
                  <div className="space-y-2">
                    {vertexInputs.map((vertex, index) => (
                      <div key={index} className="flex items-center gap-2">
                        <div className="w-6 h-6 flex items-center justify-center text-xs font-mono text-white/40 bg-white/5 rounded">
                          {index + 1}
                        </div>
                        <input
                          type="text"
                          placeholder="Lat"
                          value={vertex.lat}
                          onChange={(e) => updateVertexInput(index, 'lat', e.target.value)}
                          className="flex-1 px-2 py-1.5 text-xs font-mono bg-white/5 border border-white/10 rounded text-white placeholder-white/30 focus:outline-none focus:border-primary"
                        />
                        <input
                          type="text"
                          placeholder="Lng"
                          value={vertex.lng}
                          onChange={(e) => updateVertexInput(index, 'lng', e.target.value)}
                          className="flex-1 px-2 py-1.5 text-xs font-mono bg-white/5 border border-white/10 rounded text-white placeholder-white/30 focus:outline-none focus:border-primary"
                        />
                        <button
                          onClick={() => removeVertexInput(index)}
                          disabled={vertexInputs.length <= 3}
                          className={cn(
                            'w-6 h-6 flex items-center justify-center text-xs rounded transition-all',
                            vertexInputs.length <= 3
                              ? 'text-white/20 cursor-not-allowed'
                              : 'text-white/50 hover:text-red-400 hover:bg-red-500/20'
                          )}
                          title={vertexInputs.length <= 3 ? 'Minimum 3 vertices required' : 'Remove vertex'}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                  {/* Add vertex button */}
                  <button
                    onClick={addVertexInput}
                    className="w-full py-2 text-xs font-mono uppercase tracking-widest border border-dashed border-white/20 text-white/50 rounded hover:border-white/40 hover:text-white/70 transition-all"
                  >
                    + Add Vertex
                  </button>
                  {/* Apply button */}
                  <button
                    onClick={applyPolygonFromCoords}
                    className="w-full py-2.5 text-xs font-mono uppercase tracking-widest bg-primary text-black rounded font-bold hover:bg-primary/90 transition-all"
                  >
                    Apply Polygon
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body
  )
}


