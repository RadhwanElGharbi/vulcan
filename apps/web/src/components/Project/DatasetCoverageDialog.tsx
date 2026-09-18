'use client'

import {
AlertTriangle,
CheckCircle2,
Database,
ExternalLink,
Globe,
Loader2,
PlayCircle,
RefreshCw,
Search,
Server,
X
} from 'lucide-react'
import { useEffect,useMemo,useRef,useState } from 'react'
import { createPortal } from 'react-dom'

import { trackEvent } from '@/lib/analytics'
import {
DatasetCategory,
DatasetCategoryStatus,
DatasetCoverageEntry,
DatasetCoverageResponse,
DatasetFetchJob,
DatasetInfo,
DatasetStatusResponse,
fetchDatasetCoverage,
fetchProjectDatasets,
fetchProjectDatasetStatus,
ProjectDatasets,
startDatasetFetch
} from '@/lib/api/dataClient'
import { useOnboarding } from '@/lib/context/OnboardingContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import { DatasetFetchProgressDialog } from './DatasetFetchProgressDialog'
import { ScientificFetchDialog } from './ScientificFetchDialog'
import { ScientificJobHistory } from './ScientificJobHistory'

type DatasetCoverageDialogProps = {
  open: boolean
  onClose: () => void
  onRunInBackground?: (jobId: string) => void
}

type FetchState = 'idle' | 'loading' | 'ready' | 'error'
type JobBanner = { kind: 'success' | 'error'; message: string } | null

const MAIN_DATASET_ORDER: DatasetCategory[] = [
  'dem',
  'landcover',
  'soil',
  'imagery',
  'climate',
  'population',
  'geohazard',
  'roads',
  'railways',
  'powerlines',
  'waterways',
  'pipelines'
]

// Optional / non-core dataset categories (still fetchable; just grouped separately in the UI).
const AUXILIARY_DATASET_ORDER: DatasetCategory[] = ['protected_areas', 'indigenous_lands']

// Keep a stable display order in the UI.
const DATASET_ORDER: DatasetCategory[] = [...MAIN_DATASET_ORDER, ...AUXILIARY_DATASET_ORDER]

const CATEGORY_LABELS: Record<DatasetCategory, string> = {
  dem: 'Digital Elevation Model (DEM)',
  landcover: 'Landcover (10m)',
  soil: 'Soils / Geotechnical',
  imagery: 'Satellite Imagery',
  climate: 'Climate',
  population: 'Population',
  geohazard: 'Geohazards / Seismic',
  roads: 'Road Network',
  railways: 'Rail Network',
  powerlines: 'Power Transmission',
  waterways: 'Waterways / Hydrology',
  pipelines: 'Existing Pipelines',
  protected_areas: 'Protected Areas',
  indigenous_lands: 'Indigenous Lands'
}

const CATEGORY_TYPES: Record<DatasetCategory, 'raster' | 'vector'> = {
  dem: 'raster',
  landcover: 'raster',
  soil: 'raster',
  imagery: 'raster',
  climate: 'raster',
  population: 'raster',
  geohazard: 'raster',
  roads: 'vector',
  railways: 'vector',
  powerlines: 'vector',
  waterways: 'vector',
  pipelines: 'vector',
  protected_areas: 'vector',
  indigenous_lands: 'vector'
}

const CATEGORY_KEYWORDS: Record<DatasetCategory, string[]> = {
  dem: ['dem', 'elevation', 'terrain', 'tinitaly', 'copernicus', 'srtm'],
  landcover: ['landcover', 'land cover', 'worldcover', 'esa', 'corine'],
  soil: ['soil', 'soilgrids', 'geotech', 'isric', 'fao'],
  imagery: ['imagery', 'sentinel-2', 'landsat', 'orthophoto'],
  climate: ['climate', 'weather', 'era5', 'worldclim'],
  population: ['population', 'census', 'worldpop'],
  geohazard: ['seismic', 'hazard', 'geohazard', 'earthquake', 'pga', 'landslide'],
  roads: ['road', 'highway', 'osm road'],
  railways: ['rail', 'railway', 'train'],
  powerlines: ['power', 'transmission', 'grid', 'powerline'],
  waterways: ['waterway', 'river', 'hydro', 'hydrosheds'],
  pipelines: ['pipeline', 'gas pipeline', 'scigrid'],
  protected_areas: ['protected', 'conserved', 'park', 'reserve', 'cpcad', 'wdpa'],
  indigenous_lands: [
    'indigenous',
    'aboriginal',
    'first nations',
    'inuit',
    'metis',
    'treaty',
    'reserve',
    'clss',
    'land claim'
  ]
}

const FALLBACK_PROTOCOL = '/opt/agrs/docs/datasets/DATASET_FETCHING_PROTOCOLS.md'

function isAuxiliaryCategory(category: DatasetCategory): boolean {
  return !MAIN_DATASET_ORDER.includes(category)
}

function getDefaultSourceLabel(category: DatasetCategory, iso3?: string | null): string | null {
  switch (category) {
    case 'dem':
      return 'Copernicus DEM GLO-30'
    case 'landcover':
      return 'ESA WorldCover 2021 (10m)'
    case 'soil':
      return 'ISRIC SoilGrids v2.0 (0-5cm SOC)'
    case 'geohazard':
      return 'GEM 2023 PGA (475-year return period)'
    case 'roads':
      return 'OSM Road Network'
    case 'railways':
      return 'OSM Rail Network'
    case 'powerlines':
      return 'OSM Power Transmission'
    case 'waterways':
      return iso3 === 'CAN' ? 'National Hydro Network (NHN)' : 'OSM Waterways'
    case 'pipelines':
      return iso3 === 'CAN' ? 'Canada Energy Regulator Pipelines' : 'OSM Pipelines'
    case 'protected_areas':
      return iso3 === 'CAN' ? 'Canadian Protected and Conserved Areas Database (CPCAD)' : null
    case 'indigenous_lands':
      return iso3 === 'CAN' ? 'NRCan CLSS Aboriginal Lands of Canada Legislative Boundaries' : null
    default:
      return null
  }
}

function normalizeCoverageText(entry: DatasetCoverageEntry): string {
  return [entry.dataset, entry.data_type, entry.source, entry.coverage]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

function extractResolutionMeters(value?: string | null): number | null {
  if (!value) return null
  const lowered = value.toLowerCase()
  const meterMatch = lowered.match(/(\d+(?:\.\d+)?)\s*(m|meter|metre)/)
  if (meterMatch) {
    return parseFloat(meterMatch[1])
  }
  const kmMatch = lowered.match(/(\d+(?:\.\d+)?)\s*(km|kilometer|kilometre)/)
  if (kmMatch) {
    return parseFloat(kmMatch[1]) * 1000
  }
  const arcMatch = lowered.match(/(\d+(?:\.\d+)?)\s*(arc-?second)/)
  if (arcMatch) {
    const arc = parseFloat(arcMatch[1])
    return arc * 30
  }
  return null
}

function extractYear(value?: string | null): number | null {
  if (!value) return null
  const match = value.match(/(20\d{2})/)
  if (!match) return null
  const year = parseInt(match[1], 10)
  if (year < 1980 || year > new Date().getFullYear() + 1) return null
  return year
}

function scoreCoverageEntry(category: DatasetCategory, entry: DatasetCoverageEntry): number {
  const haystack = `${entry.dataset} ${entry.data_type || ''}`.toLowerCase()
  const keywords = CATEGORY_KEYWORDS[category]
  let score = keywords.reduce((acc, keyword) => (haystack.includes(keyword) ? acc + 10 : acc), 0)
  if (!entry.applies_globally) {
    score += 8
  }
  const resolutionHints = [entry.coverage, entry.dataset, entry.access]
  for (const hint of resolutionHints) {
    const res = extractResolutionMeters(hint)
    if (res) {
      score += Math.max(0, 200 - res)
      break
    }
  }
  const yearHints = [entry.coverage, entry.dataset]
  for (const hint of yearHints) {
    const year = extractYear(hint)
    if (year) {
      score += Math.max(0, year - 2000)
      break
    }
  }
  if (haystack.includes('tinitaly')) {
    score += 50
  }
  return score
}

function inferCategoryFromEntry(entry: DatasetCoverageEntry): DatasetCategory | null {
  const haystack = `${entry.dataset} ${entry.data_type || ''}`.toLowerCase()
  let bestCategory: DatasetCategory | null = null
  let bestScore = 0
  for (const category of DATASET_ORDER) {
    const keywords = CATEGORY_KEYWORDS[category]
    const score = keywords.reduce((acc, keyword) => (new RegExp(`\\b${keyword}\\b`, 'i').test(haystack) ? acc + 1 : acc), 0)
    if (score > bestScore) {
      bestScore = score
      bestCategory = category
    }
  }
  return bestScore > 0 ? bestCategory : null
}

function pickCoverageCandidate(category: DatasetCategory, entries: DatasetCoverageEntry[]): DatasetCoverageEntry | undefined {
  const localEntries = entries.filter((entry) => !entry.applies_globally)
  const pool = localEntries.length > 0 ? localEntries : entries

  let best: DatasetCoverageEntry | undefined
  let bestScore = -Infinity

  for (const entry of pool) {
    const score = scoreCoverageEntry(category, entry)
    if (score > bestScore) {
      bestScore = score
      best = entry
    }
  }

  return best
}

function buildFallbackStatus(
  project: string,
  coverage: DatasetCoverageResponse,
  projectDatasets?: ProjectDatasets | null
): DatasetStatusResponse {
  const categories: DatasetCategoryStatus[] = DATASET_ORDER.map((category) => {
    const candidate = pickCoverageCandidate(category, coverage.entries)
    const datasetType: 'raster' | 'vector' =
      candidate?.data_type?.toLowerCase().includes('vector') ? 'vector' : CATEGORY_TYPES[category]

    return {
      category,
      label: candidate?.dataset || CATEGORY_LABELS[category],
      dataset_type: datasetType,
      required: true,
      present: false,
      description: candidate?.coverage || candidate?.access || candidate?.source || undefined,
      raw_path: undefined,
      processed_path: undefined,
      metadata_path: undefined,
      last_modified: undefined
    }
  })

  const status: DatasetStatusResponse = {
    project,
    target_epsg: 0,
    minimum_requirements_met: false,
    categories,
    protocol_reference: coverage.protocol_reference || FALLBACK_PROTOCOL
  }

  return mergeStatusWithProjectDatasets(status, projectDatasets)
}

function mergeStatusWithProjectDatasets(
  status: DatasetStatusResponse,
  projectDatasets?: ProjectDatasets | null
): DatasetStatusResponse {
  if (!projectDatasets) return status
  let changed = false
  const categories = status.categories.map((entry) => {
    if (entry.present) {
      return entry
    }
    const category = entry.category as DatasetCategory
    const match = findProjectDatasetMatch(category, projectDatasets, entry.dataset_type)
    if (!match) {
      return entry
    }
    changed = true
    return {
      ...entry,
      present: true,
      processed_path: match.path,
      description: entry.description || match.metadata?.description || match.metadata?.dataset_name || entry.description
    }
  })

  if (!changed) {
    return status
  }

  return {
    ...status,
    categories,
    minimum_requirements_met: categories.every((entry) => !entry.required || entry.present)
  }
}

function findProjectDatasetMatch(
  category: DatasetCategory,
  projectDatasets: ProjectDatasets,
  expectedType?: 'raster' | 'vector'
): DatasetInfo | undefined {
  const pools: DatasetInfo[] = []
  if (!expectedType || expectedType === 'raster') {
    pools.push(...projectDatasets.rasters)
  }
  if (!expectedType || expectedType === 'vector') {
    pools.push(...projectDatasets.vectors)
  }

  const keywords = CATEGORY_KEYWORDS[category]
  let best: { info: DatasetInfo; score: number } | undefined
  for (const dataset of pools) {
    if (expectedType && dataset.type !== expectedType) continue
    const score = scoreDatasetMatch(dataset, keywords)
    if (score > (best?.score ?? 0)) {
      best = { info: dataset, score }
    }
  }
  return best && best.score > 0 ? best.info : undefined
}

function scoreDatasetMatch(dataset: DatasetInfo, keywords?: string[] | null): number {
  const haystack = `${dataset.name} ${dataset.path}`.toLowerCase()
  let score = 0
  const list = Array.isArray(keywords) ? keywords : []
  for (const keyword of list) {
    if (haystack.includes(keyword)) {
      score += keyword === 'tinitaly' ? 20 : 10
    }
  }
  if (score > 0 && haystack.includes('processed')) {
    score += 2
  }
  return score
}

type CategorySourcePickerDialogProps = {
  open: boolean
  category: DatasetCategory | null
  value: string | null
  defaultSource: string | null
  entries: DatasetCoverageEntry[]
  disabled?: boolean
  onClose: () => void
  onSelect: (datasetName: string) => void
  onClear: () => void
}

function formatTemporalSpan(entry: DatasetCoverageEntry): string {
  const start = (entry.temporal_start || '').trim()
  const end = (entry.temporal_end || '').trim()
  if (start && end) {
    return start === end ? start : `${start} → ${end}`
  }
  return start || end || '-'
}

function CategorySourcePickerDialog({
  open,
  category,
  value,
  defaultSource,
  entries,
  disabled,
  onClose,
  onSelect,
  onClear
}: CategorySourcePickerDialogProps) {
  const [isClosing, setIsClosing] = useState(false)
  const [query, setQuery] = useState('')
  const [detailEntry, setDetailEntry] = useState<DatasetCoverageEntry | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (!open) return
    setIsClosing(false)
    setQuery('')
    setDetailEntry(null)
  }, [open])

  const handleClose = () => {
    setIsClosing(true)
    setTimeout(() => {
      setIsClosing(false)
      onClose()
    }, 150)
  }

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') handleClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(t)
  }, [open])

  const filteredEntries = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return entries
    return entries.filter((entry) => {
      const text = [
        entry.dataset,
        entry.source,
        entry.data_type,
        entry.access,
        entry.coverage,
        entry.temporal_start,
        entry.temporal_end,
        entry.frequency
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return text.includes(q)
    })
  }, [entries, query])

  const selectedKey = (value || '').trim().toLowerCase()
  const recommendedKey = (defaultSource || '').trim().toLowerCase()

  const sectionedEntries = useMemo(() => {
    const normalizeKey = (entry: DatasetCoverageEntry) => (entry.dataset || '').trim().toLowerCase()
    const rank = (entry: DatasetCoverageEntry) => {
      const key = normalizeKey(entry)
      if (selectedKey && key === selectedKey) return 0
      if (recommendedKey && key === recommendedKey) return 1
      return 2
    }
    const byName = (a: DatasetCoverageEntry, b: DatasetCoverageEntry) =>
      (a.dataset || '').toLowerCase().localeCompare((b.dataset || '').toLowerCase())
    const sort = (list: DatasetCoverageEntry[]) => {
      const out = [...list]
      out.sort((a, b) => {
        const ra = rank(a)
        const rb = rank(b)
        if (ra !== rb) return ra - rb
        return byName(a, b)
      })
      return out
    }

    const local = filteredEntries.filter((entry) => !entry.applies_globally)
    const global = filteredEntries.filter((entry) => entry.applies_globally)
    return {
      local: sort(local),
      global: sort(global)
    }
  }, [filteredEntries, recommendedKey, selectedKey])

  if (!open || !category) return null

  const title = CATEGORY_LABELS[category] ?? category
  const selectionSummary = value
    ? `Pinned: ${value}`
    : defaultSource
      ? `AUTO (DEFAULT): ${defaultSource}`
      : 'AUTO (DEFAULT)'

  const renderSection = (
    sectionTitle: string,
    subtitle: string,
    sectionEntries: DatasetCoverageEntry[],
    scope: 'AOI' | 'GLOBAL'
  ) => {
    if (sectionEntries.length === 0) return null
    return (
      <section className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-[10px] font-bold text-white uppercase tracking-wider">{sectionTitle}</div>
            <div className="text-[10px] font-mono text-white/40">{subtitle}</div>
            <p className="mt-1 text-[10px] text-amber-200/70">Historical catalogue descriptions are unverified. Review registered product metadata before fetching.</p>
          </div>
          <div className="text-[9px] font-mono text-white/40">{sectionEntries.length} options</div>
        </div>

        <div className="space-y-3">
          {sectionEntries.map((entry, i) => {
            const entryKey = (entry.dataset || '').trim().toLowerCase()
            const isSelected = entryKey === selectedKey && !!selectedKey
            const isRecommended = entryKey === recommendedKey && !!recommendedKey

            return (
              <div
                key={`${entry.dataset}-${i}`}
                className={cn(
                  "p-4 border rounded-sm transition-all",
                  "bg-black/20 border-white/10 hover:bg-white/[0.02] hover:border-white/20",
                  isSelected && "border-primary/50 bg-primary/[0.05] shadow-[0_0_0_1px_rgba(var(--primary),0.18)]",
                  !isSelected && isRecommended && "border-primary/25 bg-primary/[0.02]"
                )}
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <button
                      type="button"
                      onClick={() => setDetailEntry(entry)}
                      className="text-left group min-w-0"
                      title="View full metadata"
                    >
                      <div className="flex items-center flex-wrap gap-2">
                        <span className="text-sm font-bold text-white group-hover:text-primary transition-colors truncate">
                          {entry.dataset}
                        </span>
                        {isRecommended && (
                          <span className="text-[9px] font-mono px-2 py-0.5 bg-primary/10 border border-primary/30 text-primary rounded-sm uppercase tracking-wider">
                            Recommended
                          </span>
                        )}
                        <span
                          className={cn(
                            "text-[9px] font-mono px-2 py-0.5 border rounded-sm uppercase tracking-wider",
                            scope === 'AOI'
                              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                              : "bg-white/5 border-white/10 text-white/50"
                          )}
                        >
                          {scope}
                        </span>
                        {isSelected && (
                          <span className="inline-flex items-center gap-1 text-[9px] font-mono px-2 py-0.5 bg-primary/15 border border-primary/40 text-primary rounded-sm uppercase tracking-wider">
                            <CheckCircle2 className="w-3 h-3" />
                            Selected
                          </span>
                        )}
                      </div>
                      {entry.source && (
                        <div className="text-[11px] text-white/50 font-mono mt-1 truncate">{entry.source}</div>
                      )}
                    </button>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {entry.url ? (
                      <a
                        href={entry.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 px-3 py-2 border border-white/20 rounded-sm hover:bg-white/10 hover:border-white/40 transition-all text-white/60 hover:text-white text-[10px] font-mono uppercase tracking-wider"
                        title={`Open ${entry.dataset} documentation`}
                      >
                        <ExternalLink className="w-3 h-3" />
                        Link
                      </a>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1 px-3 py-2 border border-white/10 rounded-sm text-white/20 cursor-not-allowed text-[10px] font-mono uppercase tracking-wider"
                        title="Documentation link not available"
                      >
                        <ExternalLink className="w-3 h-3" />
                        Link
                      </span>
                    )}

                    <button
                      type="button"
                      disabled={disabled || isSelected}
                      onClick={() => {
                        onSelect(entry.dataset)
                        handleClose()
                      }}
                      className={cn(
                        "px-4 py-2 border rounded-sm transition-all text-[10px] font-mono uppercase tracking-wider",
                        disabled
                          ? "border-white/10 text-white/20 bg-black/30 cursor-not-allowed"
                          : isSelected
                            ? "border-primary/40 text-primary/70 bg-primary/10 cursor-default"
                            : "border-primary/40 bg-primary text-black hover:bg-primary/90"
                      )}
                      title={isSelected ? 'Already selected' : 'Pin this dataset for the category'}
                    >
                      {isSelected ? 'SELECTED' : 'PIN'}
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-2 gap-3">
                  <div className="p-3 bg-white/[0.02] border border-white/5 rounded-sm">
                    <div className="text-[9px] font-mono text-white/40 uppercase tracking-wider mb-1">Type</div>
                    <div className="text-[11px] font-mono text-white/80">{entry.data_type || '-'}</div>
                  </div>
                  <div className="p-3 bg-white/[0.02] border border-white/5 rounded-sm">
                    <div className="text-[9px] font-mono text-white/40 uppercase tracking-wider mb-1">Access</div>
                    <div className="text-[11px] font-mono text-white/80">{entry.access || '-'}</div>
                  </div>
                  <div className="p-3 bg-white/[0.02] border border-white/5 rounded-sm col-span-2">
                    <div className="text-[9px] font-mono text-white/40 uppercase tracking-wider mb-1">Coverage / Resolution</div>
                    <div className="text-[11px] font-mono text-white/80">{entry.coverage || '-'}</div>
                  </div>
                  <div className="p-3 bg-white/[0.02] border border-white/5 rounded-sm">
                    <div className="text-[9px] font-mono text-white/40 uppercase tracking-wider mb-1">Temporal Span</div>
                    <div className="text-[11px] font-mono text-white/80">{formatTemporalSpan(entry)}</div>
                  </div>
                  <div className="p-3 bg-white/[0.02] border border-white/5 rounded-sm">
                    <div className="text-[9px] font-mono text-white/40 uppercase tracking-wider mb-1">Update Frequency</div>
                    <div className="text-[11px] font-mono text-white/80">{entry.frequency || '-'}</div>
                  </div>
                </div>

                <div className="mt-3 pt-3 border-t border-white/10 flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => setDetailEntry(entry)}
                    className="text-[10px] font-mono uppercase tracking-wider text-white/50 hover:text-primary transition-colors"
                  >
                    View full metadata
                  </button>
                  <div className="text-[9px] font-mono text-white/30 uppercase tracking-wider">Pin closes dialog</div>
                </div>
              </div>
            )
          })}
        </div>
      </section>
    )
  }

  return createPortal(
    <>
      <div
        className={cn(
          "fixed inset-0 bg-black/70 backdrop-blur-sm z-[150]",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}
        onClick={handleClose}
      />
      <div className="fixed inset-0 z-[151] flex items-center justify-center p-4 pointer-events-none">
        <div
          className={cn(
            "relative w-[1050px] max-w-[95vw] max-h-[85vh] bg-[#0a0a0a]/95 border border-white/10 rounded-sm shadow-[0_0_50px_-10px_rgba(0,0,0,0.8)] flex flex-col pointer-events-auto overflow-hidden",
            isClosing ? "animate-fade-out" : "animate-fade-in"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <header className="px-5 py-4 border-b border-white/10 flex items-start justify-between bg-black/20 shrink-0">
            <div className="min-w-0 pr-4">
              <div className="flex items-center gap-2 text-[9px] text-white/40 uppercase tracking-[0.2em] font-mono mb-1">
                <Database className="w-3 h-3" />
                <span>Dataset Source Picker</span>
              </div>
              <h3 className="text-base font-bold text-white uppercase tracking-wide font-mono truncate">{title}</h3>
              <div className="text-[11px] text-white/50 font-mono mt-1 truncate">{selectionSummary}</div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                disabled={disabled || !value}
                onClick={() => {
                  onClear()
                  handleClose()
                }}
                className={cn(
                  "px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider border rounded-sm transition-all",
                  disabled || !value
                    ? "bg-black/40 border-white/10 text-white/20 cursor-not-allowed"
                    : "bg-black border-white/20 text-white/60 hover:text-white hover:border-white/40 hover:bg-white/[0.03]"
                )}
                title="Clear override (use AUTO/default selection)"
              >
                Use Auto
              </button>
              <button
                type="button"
                onClick={handleClose}
                className="p-1.5 hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm text-white/50 hover:text-white transition-all"
                title="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </header>

          {/* Search */}
          <div className="px-5 py-3 border-b border-white/10 bg-black/10 shrink-0">
            <div className="flex items-center gap-3">
              <div className="relative flex-1 min-w-[240px]">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/30" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search datasets (name, source, type, access, temporal span...)"
                  className="w-full bg-black/50 border border-white/10 rounded-sm pl-8 pr-8 py-2 text-[10px] font-mono text-white placeholder:text-white/30 focus:border-primary/50 focus:ring-0 outline-none transition-colors"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setQuery('')}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-white/30 hover:text-white/60"
                    title="Clear"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              <span className="text-[9px] font-mono text-white/40 shrink-0">
                {filteredEntries.length === entries.length ? `${entries.length} options` : `${filteredEntries.length} / ${entries.length}`}
              </span>
            </div>
          </div>

          {/* Entries */}
          <div className="flex-1 overflow-y-auto">
            {entries.length === 0 ? (
              <div className="p-6 text-[10px] font-mono text-white/30 text-center uppercase tracking-widest">
                No catalogue entries were found for this category.
              </div>
            ) : filteredEntries.length === 0 ? (
              <div className="p-6 text-[10px] font-mono text-white/30 text-center uppercase tracking-widest">
                No datasets match &quot;{query}&quot;
              </div>
            ) : (
              <div className="p-5 space-y-8">
                {renderSection(
                  'AOI-Aligned Datasets',
                  'Higher relevance for the current AOI/country catalogue.',
                  sectionedEntries.local,
                  'AOI'
                )}
                {renderSection(
                  'Baseline Global Datasets',
                  'Global catalogue fallbacks applicable to this AOI.',
                  sectionedEntries.global,
                  'GLOBAL'
                )}
              </div>
            )}
          </div>

          {/* Footer */}
          <footer className="px-5 py-3 border-t border-white/10 flex items-center justify-between bg-black/20 shrink-0">
            <div className="text-[10px] font-mono text-white/40">
              Picks are sent as an override string to ZEUS and logged in dataset metadata.
            </div>
            <div className="flex items-center gap-2">
              {defaultSource && (
                <span className="text-[9px] font-mono px-2 py-1 bg-white/5 border border-white/10 text-white/50 rounded-sm uppercase">
                  Default: {defaultSource}
                </span>
              )}
            </div>
          </footer>
        </div>
      </div>

      <DatasetDetailDialog
        entry={detailEntry}
        open={detailEntry !== null}
        onClose={() => setDetailEntry(null)}
        onUseDataset={(entry) => onSelect(entry.dataset)}
      />
    </>,
    document.body
  )
}

export function DatasetCoverageDialog({ open, onClose, onRunInBackground }: DatasetCoverageDialogProps) {
  const { currentProject, refreshProjectData } = useProject()
  const { reportAction } = useOnboarding()
  const coverageCache = useRef<Record<string, DatasetCoverageResponse>>({})
  const prevOpenRef = useRef(open)

  const [coverageState, setCoverageState] = useState<FetchState>('idle')
  const [coverageError, setCoverageError] = useState<string | null>(null)
  const [coverageData, setCoverageData] = useState<DatasetCoverageResponse | null>(null)

  const [readinessState, setReadinessState] = useState<FetchState>('idle')
  const [readinessError, setReadinessError] = useState<string | null>(null)
  const [datasetStatus, setDatasetStatus] = useState<DatasetStatusResponse | null>(null)
  const [projectDatasets, setProjectDatasets] = useState<ProjectDatasets | null>(null)

  const [selectedCategories, setSelectedCategories] = useState<Set<DatasetCategory>>(new Set<DatasetCategory>())
  const [forceRefetch, setForceRefetch] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progressDialogOpen, setProgressDialogOpen] = useState(false)
  const [scientificReviewOpen, setScientificReviewOpen] = useState(false)
  const [researchReference, setResearchReference] = useState<string | null>(null)
  const prevProgressDialogRef = useRef(false)
  const [jobBanner, setJobBanner] = useState<JobBanner>(null)
  const [categoryOverrides, setCategoryOverrides] = useState<Partial<Record<DatasetCategory, string | null>>>({})
  const [sourcePickerCategory, setSourcePickerCategory] = useState<DatasetCategory | null>(null)
  
  const [mounted, setMounted] = useState(false)
  const [isClosing, setIsClosing] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (prevOpenRef.current !== open) {
      trackEvent('dialog', 'DatasetCoverageDialog', open ? 'open_dataset_coverage_dialog' : 'close_dataset_coverage_dialog', {
        project: currentProject
      })
      prevOpenRef.current = open
    }
    if (open) {
      setIsClosing(false)
    }
  }, [currentProject, open])

  useEffect(() => {
    if (prevProgressDialogRef.current === progressDialogOpen) return
    trackEvent('dialog', 'DatasetCoverageDialog', progressDialogOpen ? 'open_dataset_fetch_progress_dialog' : 'close_dataset_fetch_progress_dialog', {
      project: currentProject,
      job_id: jobId
    })
    prevProgressDialogRef.current = progressDialogOpen
  }, [currentProject, jobId, progressDialogOpen])

  const handleClose = () => {
    setSourcePickerCategory(null)
    setIsClosing(true)
    setTimeout(() => {
      onClose()
    }, 150)
  }

  useEffect(() => {
    if (!open) return

    if (!currentProject) {
      setCoverageError('Select or load a project to inspect dataset coverage.')
      setCoverageState('error')
      setCoverageData(null)
      return
    }

    const cached = coverageCache.current[currentProject]
    if (cached) {
      setCoverageData(cached)
      setCoverageError(null)
      setCoverageState('ready')
      return
    }

    let cancelled = false
    setCoverageState('loading')
    setCoverageError(null)

    fetchDatasetCoverage(currentProject)
      .then((resp) => {
        if (cancelled) return
        coverageCache.current[currentProject] = resp
        setCoverageData(resp)
        setCoverageState('ready')
      })
      .catch((err) => {
        if (cancelled) return
        setCoverageError(err?.message || 'Failed to load coverage catalog.')
        setCoverageState('error')
        setCoverageData(null)
      })

    return () => {
      cancelled = true
    }
  }, [currentProject, open])

  useEffect(() => {
    if (!open) return

    if (!currentProject) {
      setProjectDatasets(null)
      return
    }

    let cancelled = false
    fetchProjectDatasets(currentProject)
      .then((resp) => {
        if (cancelled) return
        setProjectDatasets(resp)
      })
      .catch(() => {
        if (cancelled) return
        setProjectDatasets(null)
      })

    return () => {
      cancelled = true
    }
  }, [currentProject, open, refreshKey, setProjectDatasets])

  useEffect(() => {
    if (!datasetStatus || !projectDatasets) return
    setDatasetStatus((prev) => {
      if (!prev) return prev
      const merged = mergeStatusWithProjectDatasets(prev, projectDatasets)
      return merged === prev ? prev : merged
    })
  }, [projectDatasets, datasetStatus])

  useEffect(() => {
    if (!open) return

    if (!currentProject) {
      setReadinessError('Select a project to manage dataset fetching.')
      setReadinessState('error')
      setDatasetStatus(null)
      return
    }

    let cancelled = false
    setReadinessState('loading')
    setReadinessError(null)

    fetchProjectDatasetStatus(currentProject)
      .then((resp) => {
        if (cancelled) return
        setDatasetStatus(resp)
        setReadinessState('ready')
      })
      .catch((err) => {
        if (cancelled) return
        setReadinessError(err?.message || 'Failed to inspect dataset readiness.')
        setReadinessState('error')
        setDatasetStatus(null)
      })

    return () => {
      cancelled = true
    }
  }, [currentProject, open, refreshKey])

  useEffect(() => {
    if (readinessState !== 'error' || !coverageData || !currentProject) {
      return
    }
    const fallback = buildFallbackStatus(currentProject, coverageData, projectDatasets)
    setDatasetStatus(fallback)
    setReadinessState('ready')
    setReadinessError(null)
  }, [readinessState, coverageData, currentProject, projectDatasets])

  useEffect(() => {
    if (!datasetStatus) return
    setSelectedCategories((prev) => {
      if (prev.size > 0) return prev
      const missing = datasetStatus.categories
        // Auto-select only required missing categories.
        // Optional categories remain manual to avoid forcing predictable partial runs.
        .filter((entry) => entry.required && !entry.present)
        .map((entry) => entry.category as DatasetCategory)
      return new Set<DatasetCategory>(missing)
    })
  }, [datasetStatus])



  const datasetList = useMemo(() => {
    if (!datasetStatus) return []
    const orderMap = DATASET_ORDER.reduce<Record<string, number>>((acc, key, idx) => {
      acc[key] = idx
      return acc
    }, {})
    return [...datasetStatus.categories].sort((a, b) => {
      const aIdx = orderMap[a.category] ?? 99
      const bIdx = orderMap[b.category] ?? 99
      return aIdx - bIdx
    })
  }, [datasetStatus])

  const datasetGroups = useMemo(() => {
    const main: typeof datasetList = []
    const auxiliary: typeof datasetList = []
    for (const entry of datasetList) {
      const category = entry.category as DatasetCategory
      if (category && isAuxiliaryCategory(category)) {
        auxiliary.push(entry)
      } else {
        main.push(entry)
      }
    }
    return { main, auxiliary }
  }, [datasetList])

  const missingMainCategories = useMemo(() => {
    return datasetGroups.main.filter((entry) => !entry.present).map((entry) => entry.category as DatasetCategory)
  }, [datasetGroups])

  const categoryCandidates = useMemo(() => {
    const map: Record<DatasetCategory, DatasetCoverageEntry[]> = DATASET_ORDER.reduce((acc, category) => {
      acc[category] = []
      return acc
    }, {} as Record<DatasetCategory, DatasetCoverageEntry[]>)

    if (coverageData) {
      coverageData.entries.forEach((entry) => {
        const category = inferCategoryFromEntry(entry)
        if (category) {
          map[category].push(entry)
        }
      })

      DATASET_ORDER.forEach((category) => {
        map[category].sort((a, b) => scoreCoverageEntry(category, b) - scoreCoverageEntry(category, a))
      })
    }

    return map
  }, [coverageData])

  const recommendedSources = useMemo(() => {
    const map: Partial<Record<DatasetCategory, string>> = {}
    DATASET_ORDER.forEach((category) => {
      const label = getDefaultSourceLabel(category, coverageData?.iso3)
      if (label) {
        map[category] = label
      }
    })
    return map
  }, [coverageData?.iso3])


  const localEntries = useMemo(() => {
    return (coverageData?.entries || []).filter((entry) => !entry.applies_globally)
  }, [coverageData])

  const globalEntries = useMemo(() => {
    return (coverageData?.entries || []).filter((entry) => entry.applies_globally)
  }, [coverageData])

  if (!open || !mounted) return null

  const pickerCategory = sourcePickerCategory
  const pickerEntries = pickerCategory ? (categoryCandidates[pickerCategory] || []) : []
  const pickerDefaultSource = pickerCategory
    ? recommendedSources[pickerCategory] || getDefaultSourceLabel(pickerCategory, coverageData?.iso3) || null
    : null
  const pickerValue = pickerCategory ? (categoryOverrides[pickerCategory] || null) : null

  const handleToggleCategory = (category: DatasetCategory) => {
    setSelectedCategories((prev) => {
      const next = new Set(prev)
      if (next.has(category)) {
        next.delete(category)
      } else {
        next.add(category)
      }
      return next
    })
  }

  const handleSelectMissing = () => {
    setSelectedCategories(new Set(missingMainCategories))
  }

  const handleOverrideChange = (category: DatasetCategory, datasetName: string) => {
    setCategoryOverrides((prev) => {
      if (!datasetName) {
        const next = { ...prev }
        delete next[category]
        return next
      }
      return {
        ...prev,
        [category]: datasetName
      }
    })
  }

  const handleCatalogDatasetSelect = (entry: DatasetCoverageEntry) => {
    const category = inferCategoryFromEntry(entry)
    if (!category) {
      setJobBanner({ kind: 'error', message: 'Unable to map dataset to dataset category automatically.' })
      return
    }
    const datasetName = entry.dataset || entry.source
    if (!datasetName) {
      setJobBanner({ kind: 'error', message: 'Selected dataset is missing a name to send to ZEUS tools.' })
      return
    }
    setResearchReference(datasetName)
    setScientificReviewOpen(true)
    setSelectedCategories((prev) => {
      const next = new Set(prev)
      next.add(category)
      return next
    })
    setJobBanner({
      kind: 'success',
      message: `Review registered products for ${datasetName}; acquisition has not started.`
    })
  }

  const handleStartFetch = () => {
    if (!currentProject) return
    setJobBanner(null)
    setResearchReference(null)
    setScientificReviewOpen(true)
  }

  const handleJobFinished = (result?: DatasetFetchJob) => {
    setSelectedCategories(new Set())
    setRefreshKey((key) => key + 1)
    void refreshProjectData()
    if (!result) return
    if (result.status === 'succeeded') {
      setJobBanner({ kind: 'success', message: 'Dataset fetch completed successfully.' })
    } else if (result.status === 'partial') {
      setJobBanner({
        kind: 'error',
        message: result.error || 'Dataset fetch completed with partial success. Review stage logs for failed categories.'
      })
    } else if (result.status === 'failed') {
      setJobBanner({ kind: 'error', message: result.error || 'Dataset fetch failed. Check logs for details.' })
    }
  }

  const handleProgressDialogClose = () => {
    setProgressDialogOpen(false)
    setJobId(null)
  }

  const handleRunInBackground = () => {
    if (jobId && onRunInBackground) {
      onRunInBackground(jobId)
      setProgressDialogOpen(false)
      // Close the coverage dialog too
      handleClose()
    }
  }

  const minimumMet = datasetStatus?.minimum_requirements_met
  const readinessLoading = readinessState === 'loading'

  const actionsFooter = readinessState === 'ready' && datasetList.length > 0 && (
    <div className="bg-[#0a0a0a] border-t border-white/10 p-4 flex items-center justify-between z-20 shadow-[0_-10px_30px_rgba(0,0,0,0.8)] shrink-0">
      <div className="flex items-center gap-4">
          <button
              onClick={handleSelectMissing}
              disabled={missingMainCategories.length === 0 || !!jobId}
              className="text-[10px] font-mono uppercase tracking-wider text-white/50 hover:text-primary transition-colors disabled:opacity-30"
          >
              [Select Missing Core Categories]
          </button>
          <button onClick={() => setSelectedCategories(new Set())} disabled={!!jobId} className="text-[10px] font-mono uppercase tracking-wider text-white/50 hover:text-primary">Clear Selection</button>
          <span className="text-[10px] font-mono text-white/40">Existing generations are preserved</span>
      </div>

      <div className="flex items-center gap-4">
          <span className="text-xs font-mono text-white/60">
              {selectedCategories.size} SELECTED
          </span>
          <button
              onClick={handleStartFetch}
              disabled={readinessLoading || readinessState !== 'ready' || !!jobId || !currentProject}
              data-tour="fetch-datasets-btn"
              className={cn(
                  "px-6 py-2 bg-primary text-black text-xs font-bold uppercase tracking-wider rounded-sm transition-all hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2",
                  !jobId && selectedCategories.size > 0 && "shadow-[0_0_15px_rgba(var(--primary),0.4)]"
              )}
          >
              <PlayCircle className="w-4 h-4" />
              Choose Sources & Review
          </button>
      </div>
    </div>
  )

  return createPortal(
    <>
      {!progressDialogOpen && (
        <>
          <div 
            className={cn(
              "fixed inset-0 bg-black/80 backdrop-blur-md z-[100]",
              isClosing ? "animate-fade-out" : "animate-fade-in"
            )} 
            onClick={handleClose}
          >
            <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none" />
          </div>
          
          <div className="fixed inset-0 z-[101] flex items-center justify-center p-4 pointer-events-none">
            <div
              data-tour="dataset-dialog"
              className={cn(
              "relative z-10 w-[1000px] max-w-[95vw] max-h-[90vh] bg-[#0a0a0a]/95 border border-white/10 rounded-sm shadow-[0_0_50px_-10px_rgba(0,0,0,0.8)] flex flex-col pointer-events-auto overflow-hidden",
              isClosing ? "animate-fade-out" : "animate-fade-in"
            )}>
              
              {/* Header */}
              <header className="px-6 py-5 border-b border-white/10 flex items-center justify-between bg-black/20 shrink-0">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-[10px] text-white/40 uppercase tracking-[0.2em] font-mono">
                <Database className="w-3 h-3" />
                <span>Acquisition Protocol</span>
              </div>
                <div className="flex items-center gap-3">
                <h2 className="text-xl font-bold text-white uppercase tracking-wide font-mono">
                  Dataset Manager
                </h2>
                {currentProject && (
                  <div className="px-2 py-0.5 bg-white/5 border border-white/10 rounded-sm text-[10px] font-mono text-white/70">
                    {currentProject}
                  </div>
                )}
              </div>
              {coverageData?.country && (
                <div className="flex items-center gap-1.5 mt-1 text-xs text-white/50 font-mono">
                  <Globe className="w-3 h-3 text-primary/50" />
                  <span>AOI LOC:</span>
                  <span className="text-primary">{coverageData.country.toUpperCase()}</span>
                  <span className="text-white/30">({coverageData.iso3})</span>
                </div>
              )}
            </div>
            <button 
              onClick={handleClose}
              className="p-2 hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm text-white/50 hover:text-white transition-all"
            >
              <X className="w-5 h-5" />
            </button>
          </header>

          <div className="flex-1 overflow-y-auto px-6 py-6 space-y-8 bg-[linear-gradient(to_right,#ffffff02_1px,transparent_1px),linear-gradient(to_bottom,#ffffff02_1px,transparent_1px)] bg-[size:20px_20px]">
            {currentProject && <ScientificJobHistory key={currentProject} project={currentProject} onOpen={(id) => { setJobId(id); setProgressDialogOpen(true) }} />}
            {/* Status & Action Section */}
            <section className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-white/[0.02] border border-white/10 rounded-sm">
                <div className="flex items-center gap-4">
                  <div className="p-2 bg-white/5 rounded-sm border border-white/5">
                    <Server className="w-5 h-5 text-white/70" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-white uppercase tracking-wider">Dataset Availability</h3>
                    <div className="flex items-center gap-2 mt-1">
                        <div className={cn("w-2 h-2 rounded-full animate-pulse", minimumMet ? "bg-emerald-500" : "bg-amber-500")} />
                        <span className={cn("text-xs font-mono uppercase", minimumMet ? "text-emerald-500" : "text-amber-500")}>
                            {minimumMet ? 'Validated elevation available' : 'Select products for your analysis'}
                        </span>
                  </div>
                  </div>
                </div>
                
              <div className="flex items-center gap-3">
                  <button
                  onClick={() => setRefreshKey((key) => key + 1)}
                  disabled={readinessLoading || !!jobId}
                    className="flex items-center gap-2 px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider text-white/60 hover:text-white hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm transition-all disabled:opacity-50"
                  >
                    <RefreshCw className={cn("w-3 h-3", readinessLoading && "animate-spin")} />
                    Refresh Status
                  </button>
                </div>
            </div>

            {jobBanner && (
                <div className={cn(
                    "p-3 border rounded-sm text-xs font-mono flex items-center gap-3",
                  jobBanner.kind === 'success'
                        ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400" 
                        : "bg-red-500/10 border-red-500/30 text-red-400"
                )}>
                    {jobBanner.kind === 'success' ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
                {jobBanner.message}
              </div>
            )}

            {readinessState === 'loading' && (
                <div className="flex items-center justify-center gap-3 py-12 text-white/40 font-mono text-xs uppercase tracking-widest">
                  <Loader2 className="w-5 h-5 animate-spin text-primary" />
                  <span>Scanning Project Architecture...</span>
              </div>
            )}

            {readinessState === 'error' && readinessError && (
                <div className="border border-red-500/30 bg-red-500/10 text-red-400 rounded-sm p-4 text-xs font-mono">
                  ERROR: {readinessError}
              </div>
            )}

            {readinessState === 'ready' && datasetList.length > 0 && (
              <>
                <div className="space-y-3">
                  {(() => {
                    const out: JSX.Element[] = []

                    for (const entry of datasetList) {
                      const category = entry.category as DatasetCategory
                      const isSelected = selectedCategories.has(category)
                      const overrideValue = categoryOverrides[category] || null
                      const defaultSource =
                        recommendedSources[category] || getDefaultSourceLabel(category, coverageData?.iso3) || null
                      const catalogEntries = categoryCandidates[category] || []

                      const title = CATEGORY_LABELS[category] || entry.label || String(entry.category)

                      out.push(
                        <div
                          key={entry.category}
                          className={cn(
                            "group relative flex gap-4 p-4 border rounded-sm transition-all duration-200 hover:bg-white/[0.02]",
                            entry.present ? "border-emerald-500/20 bg-emerald-500/[0.02]" : "border-white/10 bg-black/20",
                            isSelected && "border-primary/50 bg-primary/[0.02]"
                          )}
                        >
                          {/* Selection Toggle */}
                          <div className="pt-1">
                            <button
                              onClick={() => handleToggleCategory(category)}
                              aria-label={`Select ${entry.label}`}
                              aria-pressed={isSelected}
                              disabled={!!jobId || readinessLoading}
                              className={cn(
                                "w-5 h-5 border rounded-sm flex items-center justify-center transition-all",
                                isSelected ? "bg-primary border-primary text-black" : "border-white/20 hover:border-white/40 bg-black/40"
                              )}
                            >
                              {isSelected && <CheckCircle2 className="w-3.5 h-3.5" />}
                            </button>
                          </div>

                          <div className="flex-1 space-y-2">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <div className="flex items-center gap-3">
                                <span className="font-bold text-sm text-white uppercase tracking-wide">{title}</span>
                                <span className="text-[9px] font-mono text-white/30 border border-white/10 px-1.5 py-0.5 rounded-sm">
                                  {entry.dataset_type.toUpperCase()}
                                </span>
                              </div>
                              <div
                                className={cn(
                                  "text-[9px] font-bold px-2 py-0.5 rounded-sm uppercase tracking-wider border",
                                  entry.present
                                    ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-500"
                                    : "bg-amber-500/10 border-amber-500/30 text-amber-500"
                                )}
                              >
                                {entry.provenance_status === 'legacy_unverified' ? 'LEGACY / UNVERIFIED' : entry.present ? 'VALIDATED' : 'MISSING'}
                              </div>
                            </div>

                            <div className="flex flex-wrap items-center gap-4 text-xs">
                              <div className="flex-1 min-w-[200px]">
                                {defaultSource ? (
                                  <div className="flex flex-col">
                                    <span className="text-[9px] text-white/40 font-mono uppercase">Catalogue reference</span>
                                    <span className="text-white/80">{defaultSource}</span>
                                  </div>
                                ) : (
                                  <span className="text-white/40 italic">No recommendation</span>
                                )}
                              </div>

                              {/* Source Override Selector */}
                              {(Boolean(defaultSource) || catalogEntries.length > 0) && (
                                <div className="flex items-center gap-2">
                                  <span className="text-[9px] text-white/40 font-mono uppercase">Registered source</span>
                                  <button
                                    type="button"
                                    disabled={!!jobId}
                                    onClick={() => { setResearchReference(null); setSelectedCategories(new Set([category])); setScientificReviewOpen(true) }}
                                    className={cn(
                                      "min-w-[150px] px-2 py-1 text-[10px] font-mono text-white border rounded-sm flex items-center justify-between gap-2",
                                      !!jobId
                                        ? "bg-black/40 border-white/10 text-white/30 cursor-not-allowed"
                                        : "bg-black border-white/20 hover:border-white/40 focus:border-primary focus:outline-none"
                                    )}
                                    title={overrideValue || (defaultSource ? `AUTO (DEFAULT): ${defaultSource}` : 'Select Source')}
                                  >
                                    <span className="truncate">
                                      SELECT PRODUCT
                                    </span>
                                    <Search className="w-3 h-3 shrink-0 text-white/40" />
                                  </button>
                                </div>
                              )}
                            </div>

                            {/* Path / Info */}
                            <div className="pt-2 border-t border-white/5 flex items-center justify-between">
                              <div className="font-mono text-[10px] text-white/40 truncate max-w-[400px]">
                                {entry.processed_path || 'No processed artifact'}
                              </div>
                              {entry.last_modified && (
                                <div className="font-mono text-[10px] text-white/30">
                                  {new Date(entry.last_modified).toLocaleDateString()}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      )
                    }

                    return out
                  })()}
                </div>
              </>
            )}
          </section>

            {/* Reference Catalog */}
            <section className="space-y-4 pt-8 border-t border-white/10">
              <div className="flex items-center gap-2 text-white/50">
                 <Database className="w-4 h-4" />
                 <h3 className="text-sm font-bold uppercase tracking-wide">Global Reference Catalog</h3>
              </div>
              
              {coverageState === 'loading' && (
                <div className="text-center py-8 text-white/30 font-mono text-xs uppercase tracking-widest">
                    Accessing Global Index...
                  </div>
                )}

              {coverageState === 'ready' && (
                <div className="grid grid-cols-1 gap-8">
                <CoverageSection
                      title="AOI-Aligned Sources"
                      subtitle="High-precision datasets overlapping target coordinates."
                  entries={localEntries}
                      onSelectDataset={handleCatalogDatasetSelect}
                />
                <CoverageSection
                      title="Baseline Global Sources"
                      subtitle="Standard fallback datasets."
                  entries={globalEntries}
                      onSelectDataset={handleCatalogDatasetSelect}
                />
                </div>
            )}
          </section>
          </div>

          {/* Fixed Footer */}
          {actionsFooter}

        </div>
      </div>
      </>
    )}
    <CategorySourcePickerDialog
      open={!progressDialogOpen && pickerCategory !== null}
      category={pickerCategory}
      value={pickerValue}
      defaultSource={pickerDefaultSource}
      entries={pickerEntries}
      disabled={!!jobId}
      onClose={() => setSourcePickerCategory(null)}
      onClear={() => {
        if (!pickerCategory) return
        handleOverrideChange(pickerCategory, '')
      }}
      onSelect={(datasetName) => {
        if (!pickerCategory) return
        handleOverrideChange(pickerCategory, datasetName)
      }}
    />
    <DatasetFetchProgressDialog
        jobId={jobId}
        open={progressDialogOpen && Boolean(jobId)}
        onClose={handleProgressDialogClose}
        onJobFinished={handleJobFinished}
        onRunInBackground={onRunInBackground ? handleRunInBackground : undefined}
      />
    {currentProject && <ScientificFetchDialog open={scientificReviewOpen} project={currentProject} initialDataset={researchReference} initialCategories={Array.from(selectedCategories)} onClose={() => setScientificReviewOpen(false)} onStarted={(id) => {
      setScientificReviewOpen(false)
      setJobId(id)
      setProgressDialogOpen(true)
      reportAction('click-fetch-datasets')
    }} />}
    </>,
    document.body
  )
}

function DatasetDetailDialog({
  entry,
  open,
  onClose,
  onUseDataset
}: {
  entry: DatasetCoverageEntry | null
  open: boolean
  onClose: () => void
  onUseDataset?: (entry: DatasetCoverageEntry) => void
}) {
  const [isClosing, setIsClosing] = useState(false)

  const handleClose = () => {
    setIsClosing(true)
    setTimeout(() => {
      setIsClosing(false)
      onClose()
    }, 150)
  }

  if (!open || !entry) return null

  const inferredCategory = inferCategoryFromEntry(entry)

  const fields = [
    { label: 'Dataset Name', value: entry.dataset },
    { label: 'Source / Provider', value: entry.source },
    { label: 'Data Type', value: entry.data_type },
    { label: 'Access', value: entry.access },
    { label: 'Coverage / Resolution', value: entry.coverage },
    { label: 'Temporal Start', value: entry.temporal_start },
    { label: 'Temporal End', value: entry.temporal_end },
    { label: 'Update Frequency', value: entry.frequency },
    { label: 'Global Dataset', value: entry.applies_globally ? 'Yes' : 'No' },
    { label: 'Category Mapping', value: inferredCategory ? CATEGORY_LABELS[inferredCategory] : 'Unmapped' },
  ]

  return createPortal(
    <>
      <div
        className={cn(
          "fixed inset-0 bg-black/70 backdrop-blur-sm z-[200]",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}
        onClick={handleClose}
      />
      <div className="fixed inset-0 z-[201] flex items-center justify-center p-4 pointer-events-none">
        <div
          className={cn(
            "relative w-[600px] max-w-[90vw] max-h-[80vh] bg-[#0a0a0a]/95 border border-white/10 rounded-sm shadow-[0_0_50px_-10px_rgba(0,0,0,0.8)] flex flex-col pointer-events-auto overflow-hidden",
            isClosing ? "animate-fade-out" : "animate-fade-in"
          )}
        >
          {/* Header */}
          <header className="px-5 py-4 border-b border-white/10 flex items-start justify-between bg-black/20 shrink-0">
            <div className="flex-1 min-w-0 pr-4">
              <div className="flex items-center gap-2 text-[9px] text-white/40 uppercase tracking-[0.2em] font-mono mb-1">
                <Database className="w-3 h-3" />
                <span>Dataset Information</span>
              </div>
              <h3 className="text-base font-bold text-white uppercase tracking-wide font-mono truncate">
                {entry.dataset}
              </h3>
              {entry.source && (
                <div className="text-[11px] text-white/50 font-mono mt-1 truncate">
                  {entry.source}
                </div>
              )}
            </div>
            <button
              onClick={handleClose}
              className="p-1.5 hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm text-white/50 hover:text-white transition-all shrink-0"
            >
              <X className="w-4 h-4" />
            </button>
          </header>

          {/* Content */}
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            {/* Info Grid */}
            <div className="grid grid-cols-2 gap-3">
              {fields.map(({ label, value }) => (
                <div
                  key={label}
                  className={cn(
                    "p-3 bg-white/[0.02] border border-white/5 rounded-sm",
                    label === 'Coverage / Resolution' && "col-span-2"
                  )}
                >
                  <div className="text-[9px] font-mono text-white/40 uppercase tracking-wider mb-1">
                    {label}
                  </div>
                  <div className={cn(
                    "text-[11px] font-mono",
                    value ? "text-white/80" : "text-white/30 italic"
                  )}>
                    {value || 'Not specified'}
                  </div>
                </div>
              ))}
            </div>

            {/* URL Section */}
            {entry.url && (
              <div className="p-3 bg-white/[0.02] border border-white/5 rounded-sm">
                <div className="text-[9px] font-mono text-white/40 uppercase tracking-wider mb-2">
                  Documentation / Source URL
                </div>
                <a
                  href={entry.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 text-[11px] font-mono text-primary hover:text-primary/80 transition-colors break-all"
                >
                  <ExternalLink className="w-3 h-3 shrink-0" />
                  {entry.url}
                </a>
              </div>
            )}
          </div>

          {/* Footer */}
          <footer className="px-5 py-3 border-t border-white/10 flex items-center justify-between bg-black/20 shrink-0">
            <div className="flex items-center gap-2">
              {inferredCategory && (
                <span className="text-[9px] font-mono px-2 py-1 bg-primary/10 border border-primary/30 text-primary rounded-sm uppercase">
                  {CATEGORY_LABELS[inferredCategory].split('(')[0].trim()}
                </span>
              )}
              {entry.applies_globally && (
                <span className="text-[9px] font-mono px-2 py-1 bg-white/5 border border-white/10 text-white/50 rounded-sm uppercase">
                  Global
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {entry.url && (
                <a
                  href={entry.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 text-[10px] font-mono uppercase tracking-wider text-white/60 hover:text-white border border-white/20 hover:border-white/40 rounded-sm transition-all flex items-center gap-1.5"
                >
                  <ExternalLink className="w-3 h-3" />
                  Open Link
                </a>
              )}
              {inferredCategory && onUseDataset && (
                <button
                  onClick={() => {
                    onUseDataset(entry)
                    handleClose()
                  }}
                  className="px-4 py-1.5 bg-primary text-black text-[10px] font-bold uppercase tracking-wider rounded-sm hover:bg-primary/90 transition-all flex items-center gap-1.5"
                >
                  <CheckCircle2 className="w-3 h-3" />
                  Use Dataset
                </button>
              )}
            </div>
          </footer>
        </div>
      </div>
    </>,
    document.body
  )
}

function CoverageSection({
  title,
  subtitle,
  entries,
  onSelectDataset
}: {
  title: string
  subtitle: string
  entries: DatasetCoverageEntry[]
  onSelectDataset?: (entry: DatasetCoverageEntry) => void
}) {
  const [searchQuery, setSearchQuery] = useState('')
  const [detailEntry, setDetailEntry] = useState<DatasetCoverageEntry | null>(null)

  const filteredEntries = useMemo(() => {
    if (!searchQuery.trim()) return entries
    const query = searchQuery.toLowerCase().trim()
    return entries.filter((entry) => {
      const searchableText = [
        entry.dataset,
        entry.source,
        entry.data_type,
        entry.access,
        entry.coverage
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return searchableText.includes(query)
    })
  }, [entries, searchQuery])

  return (
    <section className="border border-white/5 bg-white/[0.01] rounded-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-white/5 bg-white/[0.02]">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="text-xs font-bold text-white uppercase tracking-wider">{title}</div>
            <div className="text-[10px] font-mono text-white/40">{subtitle}</div>
            <p className="mt-1 text-[10px] text-amber-200/70">Historical catalogue descriptions are unverified. Review registered product metadata before fetching.</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-white/30" />
              <input
                type="text"
                placeholder="Search datasets..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="bg-black/50 border border-white/10 rounded-sm pl-7 pr-3 py-1.5 text-[10px] font-mono text-white placeholder:text-white/30 focus:border-primary/50 focus:ring-0 outline-none w-[200px] transition-colors"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-white/30 hover:text-white/60"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
            <span className="text-[9px] font-mono text-white/40">
              {filteredEntries.length === entries.length
                ? `${entries.length} datasets`
                : `${filteredEntries.length} / ${entries.length}`}
            </span>
          </div>
        </div>
      </div>
      {entries.length === 0 ? (
        <div className="p-4 text-[10px] font-mono text-white/30 text-center uppercase">
          No matching sources found
        </div>
      ) : filteredEntries.length === 0 ? (
        <div className="p-4 text-[10px] font-mono text-white/30 text-center uppercase">
          No datasets match &quot;{searchQuery}&quot;
        </div>
      ) : (
        <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
          <table className="w-full text-left text-[10px] font-mono">
            <thead className="bg-white/[0.03] text-white/40 uppercase tracking-wider sticky top-0">
              <tr>
                <th className="px-4 py-2 font-normal bg-[#0a0a0a]">Source</th>
                <th className="px-4 py-2 font-normal bg-[#0a0a0a]">Type</th>
                <th className="px-4 py-2 font-normal bg-[#0a0a0a]">Access</th>
                <th className="px-4 py-2 font-normal bg-[#0a0a0a]">Catalogue coverage</th>
                <th className="px-4 py-2 font-normal bg-[#0a0a0a]">Category Mapping</th>
                <th className="px-4 py-2 font-normal text-center bg-[#0a0a0a]">Link</th>
                <th className="px-4 py-2 font-normal text-right bg-[#0a0a0a]">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-white/70">
              {filteredEntries.map((entry, i) => {
                const inferredCategory = inferCategoryFromEntry(entry)
                return (
                  <tr key={i} className="hover:bg-white/[0.02] transition-colors">
                    <td className="px-4 py-2">
                      <button
                        onClick={() => setDetailEntry(entry)}
                        className="text-left group"
                      >
                        <div className="font-bold text-white group-hover:text-primary transition-colors">{entry.dataset}</div>
                        {entry.source && <div className="text-white/40 group-hover:text-white/50 transition-colors">{entry.source}</div>}
                      </button>
                    </td>
                    <td className="px-4 py-2">{entry.data_type || '-'}</td>
                    <td className="px-4 py-2">{entry.access || '-'}</td>
                    <td className="px-4 py-2">{!entry.coverage || ['available','yes'].includes(entry.coverage.toLowerCase()) ? 'AOI coverage unverified' : entry.coverage}</td>
                    <td className="px-4 py-2">
                        {inferredCategory ? (
                            <span className="text-primary">{CATEGORY_LABELS[inferredCategory].split('(')[0]}</span>
                        ) : (
                            <span className="text-white/20">Unmapped</span>
                    )}
                  </td>
                    <td className="px-4 py-2 text-center">
                        {entry.url ? (
                            <a
                                href={entry.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 px-2 py-1 border border-white/20 rounded-sm hover:bg-white/10 hover:border-white/40 transition-all text-white/60 hover:text-white"
                                title={`Open ${entry.dataset} documentation`}
                            >
                                <ExternalLink className="w-3 h-3" />
                            </a>
                        ) : (
                            <span
                                className="inline-flex items-center gap-1 px-2 py-1 border border-white/10 rounded-sm text-white/20 cursor-not-allowed"
                                title="Documentation link not available"
                            >
                                <ExternalLink className="w-3 h-3" />
                            </span>
                        )}
                    </td>
                    <td className="px-4 py-2 text-right">
                        {inferredCategory && onSelectDataset && (
                            <button
                                onClick={() => onSelectDataset(entry)}
                                className="px-2 py-1 border border-white/20 rounded-sm hover:bg-primary/10 hover:border-primary/50 hover:text-primary transition-all"
                            >
                                REVIEW OPTIONS
                            </button>
                    )}
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <DatasetDetailDialog
        entry={detailEntry}
        open={detailEntry !== null}
        onClose={() => setDetailEntry(null)}
        onUseDataset={onSelectDataset}
      />
    </section>
  )
}
