'use client'

import {
fetchDatasetCoverage,
getApiBase,
type DatasetCoverageEntry,
type DatasetCoverageResponse,
type DatasetInfo,
type ProjectDatasets
} from '@/lib/api/dataClient'
import type { ManagedLayer } from '@/lib/map-utils'
import { formatMetadata } from '@/lib/map-utils'
import { cn } from '@/lib/utils'
import { AlertTriangle,ChevronDown,ChevronRight,Eye,EyeOff,Layers,Loader2,Search,X } from 'lucide-react'
import type { MutableRefObject,MouseEvent as ReactMouseEvent,RefObject } from 'react'
import { useCallback,useEffect,useMemo,useRef,useState } from 'react'

const DATASET_FILTERS = { all: 'All', loaded: 'Loaded', raster: 'Rasters', vector: 'Vectors', table: 'Tables', climate: 'Multidimensional' } as const
type DatasetFilter = keyof typeof DATASET_FILTERS

type DockContainerRef = RefObject<HTMLDivElement | null> | MutableRefObject<HTMLDivElement | null>

interface ProjectDatasetsDialogProps {
  open: boolean
  onClose: () => void
  onToggleDock: () => void
  isDocked: boolean
  dockHeight: number
  onResizeStart: (event: ReactMouseEvent) => void
  dockContainerRef: DockContainerRef
  projectName: string | null
  datasets: ProjectDatasets | null | undefined
  loadedLayers: ManagedLayer[]
  focusDatasetKey?: string | null
}

const CATEGORY_ORDER = [
  'aoi',
  'dem',
  'landcover',
  'soil',
  'geotechnical',
  'seismic',
  'landslides',
  'geology',
  'boreholes',
  'roads',
  'railways',
  'powerlines',
  'waterways',
  'hydrology',
  'pipelines'
] as const

const CATEGORY_LABELS: Record<string, string> = {
  aoi: 'AOI',
  dem: 'DEM',
  landcover: 'Landcover',
  soil: 'Soil',
  geotechnical: 'Geotechnical',
  seismic: 'Seismic',
  landslides: 'Landslides',
  geology: 'Geology',
  boreholes: 'Boreholes',
  roads: 'Roads',
  railways: 'Railways',
  powerlines: 'Powerlines',
  waterways: 'Waterways',
  hydrology: 'Hydrology',
  pipelines: 'Pipelines'
}

function normalize(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase()
}

function isUrl(value: string): boolean {
  return /^https?:\/\//i.test(value.trim())
}

function datasetKey(dataset: DatasetInfo): string {
  return `${dataset.type}:${dataset.name}`
}

function categoryFromDataset(dataset: DatasetInfo): string {
  const raw = (dataset as any)?.metadata?.category
  if (typeof raw === 'string' && raw.trim()) return raw.trim().toLowerCase()
  const prefix = dataset.name.split('_')[0]
  if (prefix && prefix.trim()) return prefix.trim().toLowerCase()
  return 'uncategorized'
}

function labelForCategory(category: string): string {
  return CATEGORY_LABELS[category] ?? category.replace(/_/g, ' ').toUpperCase()
}

function sourceHint(metadata: any | undefined): string | null {
  if (!metadata || typeof metadata !== 'object') return null
  const candidates = [
    metadata.selected_override,
    metadata.source,
    metadata.provider,
    metadata.provider_url,
    metadata.documentation_url,
    metadata.attribution,
    metadata.citation,
    metadata.url,
    metadata.download_url,
    metadata.driver_long_name,
    metadata.driver
  ]
  for (const value of candidates) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function CatalogField({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <div className="grid grid-cols-2 gap-2 text-[10px]">
      <div className="text-white/50">{label}</div>
      <div className="text-white/80 font-mono break-all">
        {isUrl(value) ? (
          <a
            href={value}
            target="_blank"
            rel="noreferrer"
            className="text-primary hover:text-primary/80 underline underline-offset-2"
          >
            {value}
          </a>
        ) : (
          value
        )}
      </div>
    </div>
  )
}

export function ProjectDatasetsDialog({
  open,
  onClose,
  onToggleDock,
  isDocked,
  dockHeight,
  onResizeStart,
  dockContainerRef,
  projectName,
  datasets,
  loadedLayers,
  focusDatasetKey = null
}: ProjectDatasetsDialogProps) {
  const [isClosing, setIsClosing] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<DatasetFilter>('all')
  const [expandedDatasets, setExpandedDatasets] = useState<Record<string, boolean>>({})
  const [collapsedCategories, setCollapsedCategories] = useState<Record<string, boolean>>({})
  const datasetRowRefs = useRef<Record<string, HTMLDivElement | null>>({})

  const [coverage, setCoverage] = useState<DatasetCoverageResponse | null>(null)
  const [coverageLoading, setCoverageLoading] = useState(false)
  const [coverageError, setCoverageError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setIsClosing(false)
    } else {
      const timer = setTimeout(() => {
        setQuery('')
        setFilter('all')
        setExpandedDatasets({})
        setCollapsedCategories({})
        setCoverage(null)
        setCoverageLoading(false)
        setCoverageError(null)
      }, 200)
      return () => clearTimeout(timer)
    }
  }, [open])

  useEffect(() => {
    if (!open || !projectName) return
    setCoverageLoading(true)
    setCoverageError(null)
    fetchDatasetCoverage(projectName)
      .then((resp) => setCoverage(resp))
      .catch((err) => setCoverageError(err instanceof Error ? err.message : 'Failed to load dataset catalogue.'))
      .finally(() => setCoverageLoading(false))
  }, [open, projectName])

  const handleClose = useCallback(() => {
    setIsClosing(true)
    setTimeout(() => {
      onClose()
    }, 150)
  }, [onClose])

  useEffect(() => {
    if (!open) return

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, handleClose])

  const allDatasets = useMemo<DatasetInfo[]>(() => {
    const rasters = datasets?.rasters ?? []
    const vectors = datasets?.vectors ?? []
    return [...rasters, ...vectors, ...(datasets?.multidimensional ?? []), ...(datasets?.tables ?? [])]
  }, [datasets])

  const loadedByName = useMemo(() => {
    const map = new Map<string, ManagedLayer>()
    for (const layer of loadedLayers) {
      map.set(layer.name, layer)
    }
    return map
  }, [loadedLayers])

  const datasetCategoryByKey = useMemo(() => {
    const map = new Map<string, string>()
    for (const dataset of allDatasets) {
      map.set(datasetKey(dataset), categoryFromDataset(dataset))
    }
    return map
  }, [allDatasets])

  const coverageIndex = useMemo(() => {
    const byDataset = new Map<string, DatasetCoverageEntry>()
    const bySource = new Map<string, DatasetCoverageEntry>()

    for (const entry of coverage?.entries ?? []) {
      const datasetKey = normalize(entry.dataset)
      if (datasetKey && !byDataset.has(datasetKey)) {
        byDataset.set(datasetKey, entry)
      }
      const sourceKey = normalize(entry.source)
      if (sourceKey && !bySource.has(sourceKey)) {
        bySource.set(sourceKey, entry)
      }
    }

    return { byDataset, bySource }
  }, [coverage])

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase()
    const matchesQuery = (dataset: DatasetInfo) => {
      if (!q) return true
      const hint = sourceHint(dataset.metadata) || ''
      return (
        dataset.name.toLowerCase().includes(q) ||
        dataset.path.toLowerCase().includes(q) ||
        hint.toLowerCase().includes(q)
      )
    }

    return allDatasets
      .map((dataset) => ({
        dataset,
        key: datasetKey(dataset),
        category: categoryFromDataset(dataset),
        loadedLayer: loadedByName.get(dataset.name) || null
      }))
      .filter((row) => {
        if (filter === 'loaded' && !row.loadedLayer) return false
        if (filter !== 'all' && filter !== 'loaded' && row.dataset.type !== filter) return false
        return matchesQuery(row.dataset)
      })
  }, [allDatasets, loadedByName, query, filter])

  const counts = useMemo(() => {
    const rasterCount = datasets?.rasters?.length ?? 0
    const vectorCount = datasets?.vectors?.length ?? 0
    const tableCount = datasets?.tables?.length ?? 0
    const multidimensionalCount = datasets?.multidimensional?.length ?? 0
    const loadedCount = filteredRows.filter((d) => Boolean(d.loadedLayer)).length
    return {
      rasterCount,
      vectorCount,
      tableCount,
      multidimensionalCount,
      total: allDatasets.length,
      loadedCount
    }
  }, [datasets, allDatasets, filteredRows])

  const categoryGroups = useMemo(() => {
    const groups = new Map<string, typeof filteredRows>()
    for (const row of filteredRows) {
      const cat = row.category || 'uncategorized'
      const list = groups.get(cat) ?? []
      list.push(row)
      groups.set(cat, list)
    }

    const keys = Array.from(groups.keys())
    const ordered = CATEGORY_ORDER.map((c) => c as string)
    keys.sort((a, b) => {
      const ia = ordered.indexOf(a)
      const ib = ordered.indexOf(b)
      if (ia !== -1 || ib !== -1) {
        if (ia === -1) return 1
        if (ib === -1) return -1
        return ia - ib
      }
      return a.localeCompare(b)
    })

    return keys.map((category) => {
      const rows = groups.get(category) ?? []
      const loaded = rows.filter((r) => Boolean(r.loadedLayer)).length
      return {
        category,
        label: labelForCategory(category),
        rows,
        total: rows.length,
        loaded
      }
    })
  }, [filteredRows])

  const toggleDatasetExpanded = useCallback((key: string) => {
    setExpandedDatasets((prev) => ({ ...prev, [key]: !prev[key] }))
  }, [])

  const toggleCategoryCollapsed = useCallback((category: string) => {
    setCollapsedCategories((prev) => ({ ...prev, [category]: !prev[category] }))
  }, [])

  useEffect(() => {
    if (!open || !focusDatasetKey) return
    const targetCategory = datasetCategoryByKey.get(focusDatasetKey)
    if (!targetCategory) return

    setQuery('')
    setFilter('all')
    setCollapsedCategories((prev) => ({ ...prev, [targetCategory]: false }))
    setExpandedDatasets((prev) => ({ ...prev, [focusDatasetKey]: true }))
  }, [open, focusDatasetKey, datasetCategoryByKey])

  const focusDatasetExpanded = focusDatasetKey ? Boolean(expandedDatasets[focusDatasetKey]) : false
  const focusDatasetVisible = useMemo(() => {
    if (!focusDatasetKey) return false
    return filteredRows.some((row) => row.key === focusDatasetKey)
  }, [focusDatasetKey, filteredRows])

  useEffect(() => {
    if (!open || !focusDatasetKey || !focusDatasetExpanded || !focusDatasetVisible) return
    const timer = window.setTimeout(() => {
      const row = datasetRowRefs.current[focusDatasetKey]
      row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [open, focusDatasetExpanded, focusDatasetKey, focusDatasetVisible])

  if (!open) return null

  return (
    <div
      className={cn(
        'fixed',
        isDocked
          ? 'absolute z-40 bottom-0 left-0 right-0'
          : 'inset-0 z-50 flex items-center justify-center p-4',
        isClosing ? 'animate-fade-out' : 'animate-fade-in'
      )}
      style={!isDocked ? { position: 'fixed' } : { position: 'absolute' }}
    >
      {/* Backdrop (modal only) */}
      {!isDocked && (
        <div
          className={cn('absolute inset-0 bg-black/85 backdrop-blur-md')}
          onClick={handleClose}
        >
          <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none" />
        </div>
      )}

      <div
        className={cn(
          'relative bg-[#0a0a0a]/95 border border-white/10 shadow-[0_0_50px_-10px_rgba(0,0,0,0.8)] flex flex-col pointer-events-auto overflow-hidden font-mono',
          isDocked ? 'w-full rounded-none border-x-0 border-b-0' : 'w-[900px] max-w-[95vw] max-h-[90vh] rounded-sm'
        )}
        style={
          isDocked
            ? {
                margin: 0,
                borderRadius: 0,
                height: `${dockHeight}vh`,
                maxHeight: `${dockHeight}vh`
              }
            : undefined
        }
        ref={isDocked ? (dockContainerRef as RefObject<HTMLDivElement>) : undefined}
        onClick={(e) => e.stopPropagation()}
      >
        {isDocked && (
          <div
            className="absolute top-0 left-0 right-0 h-2 cursor-ns-resize hover:bg-primary/20 transition-colors z-50"
            style={{ transform: 'translateY(-2px)' }}
            onMouseDown={onResizeStart}
            title="Drag to resize height"
          />
        )}

        {/* Decorative Top Line */}
        <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-primary/50 to-transparent" />

        {/* Header */}
        <header className="px-8 py-6 border-b border-white/10 flex items-center justify-between bg-black/20 shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 border border-primary/20 rounded-sm">
              <Layers className="w-5 h-5 text-primary" />
            </div>
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-[10px] text-white/40 uppercase tracking-[0.2em]">
                <span>Datasets</span>
                <span className="text-white/20">|</span>
                <span className="text-white/50">{projectName ?? 'NO PROJECT'}</span>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-xl font-bold text-white uppercase tracking-wide">Project Dataset Index</h2>
                <div className="flex flex-wrap items-center gap-2 px-2 py-0.5 bg-white/5 border border-white/10 rounded-sm">
                  <span className="text-[9px] text-white/50 uppercase tracking-wider">
                    Total: <span className="text-white">{counts.total}</span>
                  </span>
                  <span className="text-white/20">|</span>
                  <span className="text-[9px] text-white/50 uppercase tracking-wider">
                    Rasters: <span className="text-white">{counts.rasterCount}</span>
                  </span>
                  <span className="text-white/20">|</span>
                  <span className="text-[9px] text-white/50 uppercase tracking-wider">
                    Vectors: <span className="text-white">{counts.vectorCount}</span>
                  </span>
                  {counts.tableCount > 0 && <span className="text-[9px] text-white/50 uppercase tracking-wider">Tables: <span className="text-white">{counts.tableCount}</span></span>}
                  {counts.multidimensionalCount > 0 && <span className="text-[9px] text-white/50 uppercase tracking-wider">Multidimensional: <span className="text-white">{counts.multidimensionalCount}</span></span>}
                </div>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={onToggleDock}
              className="px-4 py-2 border border-primary/30 text-primary/80 hover:bg-primary/10 hover:text-primary rounded-sm text-[10px] uppercase font-bold tracking-wider transition-all"
              title={isDocked ? 'Undock (open as a modal)' : 'Dock to bottom'}
            >
              {isDocked ? 'Undock' : 'Dock to bottom'}
            </button>
            <button
              onClick={handleClose}
              className="p-2 hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm text-white/50 hover:text-white transition-all"
              title="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </header>

        {/* Controls */}
        <div className="px-6 py-4 border-b border-white/10 bg-white/[0.02] shrink-0">
          <div className="flex flex-col md:flex-row md:items-center gap-3">
            <div className="flex-1 flex items-center gap-2 px-3 py-2 bg-black/40 border border-white/10 rounded-sm">
              <Search className="w-4 h-4 text-white/30" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search name, path, provider..."
                className="w-full bg-transparent outline-none text-xs text-white/80 placeholder:text-white/30"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {(Object.keys(DATASET_FILTERS) as DatasetFilter[]).map((id) => (
                <button
                  key={id}
                  onClick={() => setFilter(id)}
                  aria-pressed={filter === id}
                  className={cn(
                    'px-3 py-2 border rounded-sm text-[10px] uppercase font-bold tracking-wider transition-all',
                    filter === id
                      ? 'bg-primary/10 border-primary/30 text-primary'
                      : 'bg-white/5 border-white/10 text-white/50 hover:text-white hover:border-white/20'
                  )}
                  title={
                    id === 'loaded'
                      ? `Datasets currently loaded into the map buffer (${counts.loadedCount})`
                      : undefined
                  }
                >
                  {DATASET_FILTERS[id]}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Body */}
        <div
          className={cn(
            'flex-1 overflow-y-auto',
            isDocked
              ? 'p-4 bg-[linear-gradient(to_right,#ffffff02_1px,transparent_1px),linear-gradient(to_bottom,#ffffff02_1px,transparent_1px)] bg-[size:20px_20px]'
              : 'p-8 bg-[linear-gradient(to_right,#ffffff02_1px,transparent_1px),linear-gradient(to_bottom,#ffffff02_1px,transparent_1px)] bg-[size:20px_20px]'
          )}
        >
          {!projectName && (
            <div className="p-6 text-center text-white/30 text-xs border border-white/10 rounded-sm bg-black/40">
              Select a project to view its datasets.
            </div>
          )}

          {projectName && !datasets && (
            <div className="p-6 text-center text-white/30 text-xs border border-white/10 rounded-sm bg-black/40">
              No dataset index available yet (loading or empty project).
            </div>
          )}

          {projectName && datasets && filteredRows.length === 0 && (
            <div className="p-6 text-center text-white/30 text-xs border border-white/10 rounded-sm bg-black/40">
              No datasets match your filter.
            </div>
          )}

          {projectName && datasets && filteredRows.length > 0 && (
            <div className="space-y-4">
              {/* Catalogue status */}
              <div className="flex items-center justify-between gap-3 px-4 py-3 border border-white/10 rounded-sm bg-black/40">
                <div className="flex items-center gap-2 text-[10px] text-white/40 uppercase tracking-[0.2em]">
                  <span>Catalogue (CSV)</span>
                </div>
                <div className="text-[10px] text-white/50 flex items-center gap-2">
                  {coverageLoading ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                      <span>Loading coverage catalogue…</span>
                    </>
                  ) : coverageError ? (
                    <>
                      <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                      <span className="text-amber-500">{coverageError}</span>
                    </>
                  ) : coverage ? (
                    <span>
                      Loaded <span className="text-white">{coverage.entries.length}</span> entries for{' '}
                      <span className="text-white">{coverage.iso3}</span>
                    </span>
                  ) : (
                    <span>Not loaded</span>
                  )}
                </div>
              </div>

              {/* Category groups */}
              <div className="space-y-3">
                {categoryGroups.map((group) => {
                  const isCollapsed = collapsedCategories[group.category] ?? false
                  return (
                    <div key={group.category} className="border border-white/10 rounded-sm bg-[#0a0a0a]/60 overflow-hidden">
                      <button
                        onClick={() => toggleCategoryCollapsed(group.category)}
                        className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/[0.03] transition-colors"
                        title="Toggle category"
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          <div className="text-white/40">
                            {isCollapsed ? (
                              <ChevronRight className="w-4 h-4" />
                            ) : (
                              <ChevronDown className="w-4 h-4" />
                            )}
                          </div>
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-xs font-bold text-white uppercase tracking-wider">
                              {group.label}
                            </span>
                            <span className="text-[9px] uppercase font-bold px-1.5 py-0.5 border border-white/10 rounded-sm text-white/50">
                              {group.total}
                            </span>
                            {group.loaded > 0 && (
                              <span className="text-[9px] uppercase font-bold px-1.5 py-0.5 border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 rounded-sm">
                                In Map: {group.loaded}
                              </span>
                            )}
                          </div>
                        </div>
                        <span className="text-[10px] text-white/30 font-mono uppercase tracking-widest">
                          {group.category}
                        </span>
                      </button>

                      {!isCollapsed && (
                        <div className="p-3 space-y-2 border-t border-white/10 bg-black/20">
                          {group.rows.map(({ dataset, key, loadedLayer }) => {
                            const isExpanded = Boolean(expandedDatasets[key])
                            const isFocusTarget = focusDatasetKey === key
                            const hint = sourceHint(dataset.metadata)
                            const metaRows = formatMetadata(dataset.metadata)

                            const selectedOverride = (dataset as any)?.metadata?.selected_override as string | undefined
                            const resolvedSource = (dataset as any)?.metadata?.source as string | undefined

                            const normalizedOverride = normalize(selectedOverride)
                            const normalizedSource = normalize(resolvedSource)

                            const catalogEntry =
                              (normalizedOverride
                                ? coverageIndex.byDataset.get(normalizedOverride) ||
                                  coverageIndex.bySource.get(normalizedOverride)
                                : null) ||
                              (normalizedSource
                                ? coverageIndex.byDataset.get(normalizedSource) ||
                                  coverageIndex.bySource.get(normalizedSource)
                                : null) ||
                              null

                            const displayTitle =
                              typeof (dataset as any)?.metadata?.dataset_name === 'string'
                                ? ((dataset as any).metadata.dataset_name as string)
                                : dataset.name

                            return (
                              <div
                                key={key}
                                ref={(node) => {
                                  datasetRowRefs.current[key] = node
                                }}
                                className={cn(
                                  'border rounded-sm bg-[#0a0a0a]/70 overflow-hidden',
                                  isFocusTarget
                                    ? 'border-primary/35 shadow-[0_0_0_1px_rgba(var(--primary),0.25)]'
                                    : 'border-white/10'
                                )}
                              >
                                <button
                                  onClick={() => toggleDatasetExpanded(key)}
                                  className="w-full text-left px-4 py-3 hover:bg-white/[0.03] transition-colors flex items-start gap-3"
                                  title="Expand dataset details"
                                >
                                  <div className="mt-0.5 text-white/40">
                                    {isExpanded ? (
                                      <ChevronDown className="w-4 h-4" />
                                    ) : (
                                      <ChevronRight className="w-4 h-4" />
                                    )}
                                  </div>

                                  <div className="flex-1 min-w-0">
                                    <div className="flex items-start justify-between gap-3">
                                      <div className="flex items-center gap-2 min-w-0">
                                        <span className="text-xs font-bold text-white truncate">{displayTitle}</span>
                                        <span className="text-[9px] uppercase font-bold px-1.5 py-0.5 border border-white/10 rounded-sm text-white/50">
                                          {dataset.type}
                                        </span>
                                        {loadedLayer && (
                                          <span
                                            className={cn(
                                              'text-[9px] uppercase font-bold px-1.5 py-0.5 border rounded-sm flex items-center gap-1',
                                              loadedLayer.visible
                                                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                                                : 'border-amber-500/30 bg-amber-500/10 text-amber-400'
                                            )}
                                          >
                                            {loadedLayer.visible ? (
                                              <Eye className="w-3 h-3" />
                                            ) : (
                                              <EyeOff className="w-3 h-3" />
                                            )}
                                            {loadedLayer.visible ? 'In Map' : 'Hidden'}
                                          </span>
                                        )}
                                      </div>

                                      {hint && (
                                        <span className="hidden md:block text-[10px] text-white/40 max-w-[360px] truncate">
                                          {hint}
                                        </span>
                                      )}
                                    </div>

                                    <div className="mt-1 text-[10px] text-white/40 font-mono break-all">
                                      <span className="text-white/30">Layer ID:</span> {dataset.name}
                                    </div>
                                    <div className="mt-1 text-[10px] text-white/40 font-mono break-all">
                                      <span className="text-white/30">Path:</span> {dataset.path}
                                    </div>
                                  </div>
                                </button>

                                {isExpanded && (
                                  <div className="px-4 pb-4 pt-0 border-t border-white/10 bg-black/30 space-y-4">
                                    {typeof dataset.metadata?.download_url === 'string' && <a href={dataset.metadata.download_url.startsWith('/api/') ? `${getApiBase()}${dataset.metadata.download_url.slice(4)}` : dataset.metadata.download_url} className="inline-block pt-3 text-xs text-red-300 underline">Download validated artifact</a>}
                                    {/* Map status */}
                                    {loadedLayer && (
                                      <div className="pt-3">
                                        <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-1">
                                          Map Buffer
                                        </div>
                                        <div className="grid grid-cols-2 gap-2 text-[10px] mt-2">
                                          <div className="text-white/50">Visibility</div>
                                          <div className={loadedLayer.visible ? 'text-emerald-400' : 'text-amber-400'}>
                                            {loadedLayer.visible ? 'ACTIVE' : 'HIDDEN'}
                                          </div>
                                          <div className="text-white/50">Opacity</div>
                                          <div className="text-white/80 font-mono">{Math.round(loadedLayer.opacity * 100)}%</div>
                                          <div className="text-white/50">Order</div>
                                          <div className="text-white/80 font-mono">{loadedLayer.order}</div>
                                        </div>
                                      </div>
                                    )}

                                    {/* Catalogue */}
                                    <div className="pt-3">
                                      <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-1">
                                        Catalogue (CSV)
                                      </div>

                                      {!selectedOverride && !resolvedSource && (
                                        <div className="mt-2 text-[10px] text-white/40">
                                          No override/source hint found in metadata to match against the catalogue.
                                        </div>
                                      )}

                                      {(selectedOverride || resolvedSource) && !catalogEntry && !coverageLoading && (
                                        <div className="mt-2 text-[10px] text-white/40">
                                          No catalogue match found for{' '}
                                          <span className="text-white/70 font-mono">
                                            {selectedOverride ?? resolvedSource}
                                          </span>
                                          .
                                        </div>
                                      )}

                                      {catalogEntry && (
                                        <div className="mt-2 space-y-2">
                                          <CatalogField label="Dataset" value={catalogEntry.dataset} />
                                          <CatalogField label="Source / Provider" value={catalogEntry.source ?? undefined} />
                                          <CatalogField label="Data Type" value={catalogEntry.data_type ?? undefined} />
                                          <CatalogField label="Access" value={catalogEntry.access ?? undefined} />
                                          <CatalogField label="Coverage" value={catalogEntry.coverage ?? undefined} />
                                          <CatalogField label="Temporal Start" value={catalogEntry.temporal_start ?? undefined} />
                                          <CatalogField label="Temporal End" value={catalogEntry.temporal_end ?? undefined} />
                                          <CatalogField label="Frequency" value={catalogEntry.frequency ?? undefined} />
                                          <CatalogField label="URL" value={catalogEntry.url ?? undefined} />
                                        </div>
                                      )}
                                    </div>

                                    {/* Metadata */}
                                    <div className="pt-3">
                                      <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-1">
                                        Metadata / Sourcing (Sidecar)
                                      </div>

                                      {!dataset.metadata && (
                                        <div className="mt-2 text-[10px] text-white/40">
                                          No metadata sidecar was returned for this dataset.
                                        </div>
                                      )}

                                      {dataset.metadata && metaRows.length > 0 && (
                                        <div className="mt-2 overflow-auto max-h-56 border border-white/10 rounded-sm bg-black/40">
                                          <table className="w-full text-left border-collapse text-[10px]">
                                            <tbody>
                                              {metaRows.map((row, idx) => (
                                                <tr
                                                  key={idx}
                                                  className="border-b border-white/5 last:border-0 hover:bg-white/5"
                                                >
                                                  <td className="py-1 px-2 font-medium text-white/50 border-r border-white/5 whitespace-nowrap w-32 bg-white/[0.02]">
                                                    {row.label}
                                                  </td>
                                                  <td className="py-1 px-2 text-white/80 break-all font-mono">
                                                    {isUrl(row.value) ? (
                                                      <a
                                                        href={row.value}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        className="text-primary hover:text-primary/80 underline underline-offset-2"
                                                      >
                                                        {row.value}
                                                      </a>
                                                    ) : (
                                                      row.value
                                                    )}
                                                  </td>
                                                </tr>
                                              ))}
                                            </tbody>
                                          </table>
                                        </div>
                                      )}

                                      {dataset.metadata && metaRows.length === 0 && (
                                        <pre className="mt-2 bg-black/40 p-3 rounded-sm border border-white/10 text-[9px] text-white/60 overflow-x-auto font-mono">
                                          {JSON.stringify(dataset.metadata, null, 2)}
                                        </pre>
                                      )}
                                    </div>
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
