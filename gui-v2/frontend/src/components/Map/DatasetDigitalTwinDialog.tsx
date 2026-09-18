'use client'

import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Circle, Layers, ListOrdered, Plus, Trash2, X, XCircle } from 'lucide-react'
import { ResearchProduct, ResearchSelection, researchRequest } from '@/lib/api/researchClient'
import { useProject } from '@/lib/context/ProjectContext'
import { ScientificFetchDialog } from '../Project/ScientificFetchDialog'
import { DatasetFetchProgressDialog } from '../Project/DatasetFetchProgressDialog'
import { ScientificJobHistory } from '../Project/ScientificJobHistory'
import { selectionStatus, useTwinQueue } from './useTwinQueue'
import { CatalogueBrowser } from './CatalogueBrowser'
import { DigitalTwinLayerStack } from './DigitalTwinLayerStack'

const groups = [
  {name:'Atmosphere', categories:[['climate','Weather / Climate']]},
  {name:'Infrastructure', categories:[['roads','Roads'],['railways','Railways'],['powerlines','Power Lines'],['pipelines','Pipelines']]},
  {name:'Built Environment', categories:[['parcels','Parcels'],['zoning','Zoning'],['population','Population']]},
  {name:'Nature & Imagery', categories:[['landcover','Land Cover'],['imagery','Satellite Imagery'],['protected_areas','Protected Areas'],['indigenous_lands','Indigenous Lands']]},
  {name:'Terrain & Water', categories:[['dem','Elevation (DEM)'],['waterways','Rivers & Lakes'],['wetlands','Wetlands'],['landslides','Landslides']]},
  {name:'Subsurface', categories:[['soil','Soil Properties'],['geohazard','Seismic Hazard']]},
]
type Sources = {products:ResearchProduct[]; project_countries:string[]}
const eligible = (p:ResearchProduct) => !['unavailable','excluded'].includes(p.assessment.disposition)

export function DatasetDigitalTwinDialog({open,onClose,projectName}:{open:boolean;onClose:()=>void;projectName:string|null}) {
  // Keyed child prevents selections and job dialogs leaking between projects.
  return projectName ? <Twin key={projectName} open={open} onClose={onClose} project={projectName}/> : null
}

function Twin({open,onClose,project}:{open:boolean;onClose:()=>void;project:string}) {
  const {refreshProjectData,datasets} = useProject()
  const [sources,setSources] = useState<Sources|null>(null)
  const [selected,setSelected] = useState<string[]>([])
  const [loaded,setLoaded] = useState(false)
  const [notice,setNotice] = useState<string|null>(null)
  const [error,setError] = useState<string|null>(null)
  const [category,setCategory] = useState<string|null>(null)
  const [pickerMode,setPickerMode] = useState(false)
  const [queueCollapsed,setQueueCollapsed] = useState(false)
  const [catalogueOpen,setCatalogueOpen]=useState(false)
  const [history,setHistory] = useState(false)
  const queue=useTwinQueue(project,open)
  const [query,setQuery] = useState('')
  const [review,setReview] = useState(false)
  const [job,setJob] = useState<string|null>(null)
  const [progress,setProgress] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  const observedJobs=useRef(new Map<string,string>())
  useEffect(()=>{
    let published=false
    for(const {job} of queue.runs) {
      const previous=observedJobs.current.get(job.id)
      if(previous && previous!==job.status && job.status==='succeeded') published=true
      observedJobs.current.set(job.id,job.status)
    }
    if(published) void refreshProjectData()
  },[queue.runs,refreshProjectData])
  const storageKey = `vulcan.digital-twin.products.v1.${project}`
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setError(null)
    researchRequest<Sources>(`/dataset-sources?project=${encodeURIComponent(project)}`,undefined,controller.signal).then(data => {
      if (controller.signal.aborted) return
      setSources(data)
      if (!loaded) {
        try {
          const raw = localStorage.getItem(storageKey)
          const saved:unknown = raw ? JSON.parse(raw) : []
          if (!Array.isArray(saved) || saved.some(id => typeof id !== 'string')) throw new Error('Invalid saved selections')
          setSelected([...new Set(saved as string[])])
          if (!raw && localStorage.getItem(`agrs.dataset_digital_twin.assignments.${project}`)) setNotice('Previous catalogue assignments used ambiguous names. Please select exact products below; your old assignments remain saved.')
        } catch { setNotice('Saved selections could not be read. Select products again; the original saved data has not been changed.') }
        setLoaded(true)
      }
    }).catch(e => {if (!controller.signal.aborted) setError(e.message)})
    return () => controller.abort()
  },[open,project,loaded,storageKey])
  useEffect(() => {
    if (!open || review || progress) return
    const previous = document.activeElement as HTMLElement|null
    panel.current?.focus()
    return () => previous?.focus()
  },[open,review,progress])
  function toggle(id:string) {
    const next = selected.includes(id) ? selected.filter(value => value !== id) : [...selected,id]
    setSelected(next)
    try {localStorage.setItem(storageKey,JSON.stringify(next))} catch {setNotice('Selections are available for this session, but browser storage could not save them.')}
  }
  const present=new Set([...(datasets?.rasters||[]),...(datasets?.vectors||[]),...(datasets?.multidimensional||[]),...(datasets?.tables||[])].map(d=>d.metadata?.category).filter(Boolean))
  const total=groups.flatMap(g=>g.categories).length
  const presentCount=groups.flatMap(g=>g.categories).filter(([id])=>present.has(id)).length
  const products = sources?.products || []
  const countries = sources?.project_countries || []
  const inCountry = (p:ResearchProduct) => p.countries.includes('WLD') || p.countries.some(country => countries.includes(country))
  const visible = products.filter(p => p.category === category && (inCountry(p) || selected.includes(p.id)) && `${p.name} ${p.publisher}`.toLowerCase().includes(query.toLowerCase()))
  const invalid = selected.filter(id => !products.some(p => p.id === id && eligible(p)))
  const initialSelections:ResearchSelection[] = selected.map(product_id => ({product_id,parameters:{}}))
  const label = groups.flatMap(g => g.categories).find(([id]) => id === category)?.[1] || category
  return <>
    {open && !review && !progress && createPortal(<div className="fixed inset-0 z-[15000] flex">
      <div className="absolute inset-0 bg-black/85 backdrop-blur-lg" onClick={onClose}/>
      <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Digital Twin" onKeyDown={e => {
        if (e.key === 'Escape') {if(category)setCategory(null);else if(catalogueOpen)setCatalogueOpen(false);else if(history)setHistory(false);else onClose()}
        if (e.key === 'Tab') {
          const controls = panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, [tabindex="0"]')
          if (!controls?.length) return
          const first=controls[0], last=controls[controls.length-1]
          if (e.shiftKey && (document.activeElement===first || document.activeElement===panel.current)) {e.preventDefault();last.focus()}
          else if (!e.shiftKey && document.activeElement===last) {e.preventDefault();first.focus()}
        }
      }} className="relative z-10 flex h-full w-full text-white outline-none">
        <div className={`flex h-full flex-col transition-all duration-300 ${category ? pickerMode ? 'w-full md:w-[55%]' : 'w-full md:w-[62%]' : 'w-full'}`}>
        <header className="shrink-0 px-6 py-5 border-b border-white/[0.06] bg-[#0a0a0a]/95">
          <div className="flex items-start justify-between gap-4">
            <div><div className="flex items-center gap-3 mb-1.5"><Layers className="w-5 h-5 text-primary"/><h2 className="text-sm font-bold uppercase tracking-[0.15em]">Dataset Digital Twin</h2></div><p className="text-[11px] text-white/50 font-mono">Project: <span className="text-white/80">{project}</span></p></div>
            <div className="flex gap-2"><button onClick={()=>{setCatalogueOpen(true);setCategory(null);setHistory(false)}} className="px-3 text-[10px] font-mono text-white/50 hover:text-white">Full catalogue</button><button aria-label="Clear selected datasets" onClick={()=>{setSelected([]);try{localStorage.setItem(storageKey,'[]')}catch{setNotice('Could not save cleared selections.')}}} className="p-2 border border-white/10 text-white/40 hover:text-white hover:border-white/25 hover:bg-white/[0.03] rounded-md"><Trash2 className="w-4 h-4"/></button><button aria-label="Close Digital Twin" onClick={onClose} className="p-2 border border-white/10 text-white/40 hover:text-white hover:border-white/25 hover:bg-white/[0.03] rounded-md"><X className="w-4 h-4"/></button></div>
          </div>
          <div className="mt-4 flex items-center gap-4 flex-wrap">
            <div className="relative w-10 h-10 shrink-0"><svg className="w-10 h-10 -rotate-90" viewBox="0 0 36 36"><circle cx="18" cy="18" r="14" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="3"/><circle cx="18" cy="18" r="14" fill="none" stroke="rgb(16,185,129)" strokeWidth="3" strokeDasharray={`${presentCount/total*88} 88`} strokeLinecap="round"/></svg><span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-white/70">{Math.round(presentCount/total*100)}%</span></div>
            <span className="text-[10px] font-mono text-white/40"><span className="text-emerald-400 font-bold">{presentCount}</span>/{total} categories in project</span><span className="text-[10px] font-mono text-amber-400/70">{selected.length} selected to fetch</span>
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar pb-80">
          {error && <p role="alert" className="m-4 text-red-300">{error}</p>}
          {notice && <p role="status" className="m-4 text-amber-200 text-sm">{notice}</p>}
          {!sources ? <p className="p-6">{error ? 'Close and reopen to retry.' : 'Loading registered products...'}</p> : <DigitalTwinLayerStack groups={groups} present={present} selectedCount={group=>products.filter(p=>group.categories.some(([id])=>id===p.category) && selected.includes(p.id)).length} onGroupChange={()=>{setCategory(null);setHistory(false)}} renderCategories={group=>group.categories.map(([id,name])=>{
              const matching=products.filter(p=>p.category===id && inCountry(p) && eligible(p))
              const chosen=products.filter(p=>p.category===id && selected.includes(p.id))
              return <div key={id} role="button" tabIndex={0} onKeyDown={e=>{if(e.target===e.currentTarget && ['Enter',' '].includes(e.key)){e.preventDefault();setCategory(id);setPickerMode(false);setHistory(false);setQuery('')}}} aria-pressed={category===id} onClick={()=>{setCategory(id);setPickerMode(false);setHistory(false);setQuery('')}} className={`group flex w-full items-center gap-3 px-4 py-2.5 text-left transition-all duration-150 border-l-2 ${category===id?'bg-primary/[0.08] border-l-primary':'border-l-transparent hover:bg-white/[0.03]'}`}>
                <span className={`w-2 h-2 shrink-0 rounded-full ${present.has(id)?'bg-emerald-400':'bg-white/10 ring-1 ring-white/10'}`}/>
                <span className="min-w-0 flex-1"><span className="block text-[11px] font-medium text-white/40">{name}</span><span className="mt-0.5 block truncate font-mono text-[9px] text-white/30">{chosen.length ? chosen.map(p=>p.name).join('; ') : matching.length ? `Choose dataset - ${matching.length} available` : products.some(p=>p.category===id) ? 'No products for this country' : 'Not supported yet'}</span></span>
                <span className="flex items-center gap-2">{chosen.length ? <><span className="text-[9px] text-emerald-400/70">{chosen.length} queued</span><Check size={12} className="text-emerald-400"/></> : matching.length ? <Circle size={12} className="text-amber-400"/> : <XCircle size={12} className="text-white/20"/>}<button aria-label={`Assign dataset to ${name}`} onClick={e=>{e.stopPropagation();setCategory(id);setPickerMode(true);setHistory(false);setQuery('')}} className="p-1 rounded-sm opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity text-white/30 hover:text-primary hover:bg-primary/10"><Plus size={12}/></button></span>
              </div>
            })}/>}

        </div>
        </div>
        {category && <aside aria-label={pickerMode ? "Dataset source picker" : "Layer detail"} className={`absolute right-0 top-0 z-20 flex h-full w-full flex-col border-l border-white/[0.06] bg-[#080808]/[0.98] transition-all duration-300 ${pickerMode ? 'md:w-[45%]' : 'md:w-[38%]'}`}>
          <header className="flex shrink-0 items-center justify-between border-b border-white/10 p-4"><div><div className="text-[9px] font-mono text-white/30 uppercase tracking-widest mb-1">{pickerMode ? 'Assign dataset' : 'Layer detail'}</div><h3 className="text-lg font-bold uppercase tracking-wide">{label}</h3></div><button aria-label="Close source picker" onClick={()=>setCategory(null)}><X size={17}/></button></header>
          <div className="overflow-y-auto px-5 pt-4 pb-80 space-y-3">
            {!pickerMode ? <><div className="p-4 border border-white/[0.06] bg-white/[0.015] rounded-md"><p className="text-[9px] font-mono text-white/30 uppercase tracking-wider mb-3">Status</p><p className="text-xs text-white/60">{present.has(category) ? 'Present in project' : 'Not present in project'}</p></div><div className="p-4 border border-white/[0.06] bg-white/[0.015] rounded-md"><p className="text-[9px] font-mono text-white/30 uppercase tracking-wider mb-3">Selected datasets</p><p className="text-xs text-white/60">{products.filter(p=>p.category===category && selected.includes(p.id)).map(p=>p.name).join('; ') || 'No dataset selected yet.'}</p></div><button onClick={()=>setPickerMode(true)} className="w-full px-4 py-3 bg-primary/[0.08] border border-primary/25 hover:bg-primary/[0.15] text-primary rounded-md text-[10px] font-bold uppercase tracking-wider">Choose dataset</button></> : <>

            <input aria-label="Search products" placeholder="Search datasets" value={query} onChange={e=>setQuery(e.target.value)} className="w-full border border-white/15 bg-white/5 p-2 text-sm"/>
            <p className="text-xs text-white/50">{countries.length ? `AOI countries: ${countries.join(', ')} + worldwide sources` : 'No country detected for this AOI; showing worldwide sources.'}</p>
            {!visible.length && <p className="text-sm text-white/40">No matching registered datasets.</p>}
            {visible.map(p=><label key={p.id} className="flex cursor-pointer gap-3 border-b border-white/5 py-3 hover:text-white">
              <input className="mt-1 accent-red-500" type="checkbox" checked={selected.includes(p.id)} disabled={!eligible(p) && !selected.includes(p.id)} onChange={()=>toggle(p.id)}/>
              <span><span className="block text-sm text-white/75">{p.name}</span><span className="mt-1 block font-mono text-xs text-white/35">{p.publisher} / {p.version}</span>{!p.access_ready && <span className="text-xs text-amber-300">Credentials needed</span>}{!inCountry(p) && <span className="block text-xs text-amber-300">Outside project country coverage</span>}</span>
            </label>)}
            <div className="mt-5 border-t border-white/10 pt-4"><CatalogueBrowser category={category} countries={countries} products={products} selected={selected} onSelect={toggle}/></div>
            </>}
          </div>
        </aside>}
        {catalogueOpen && <aside aria-label="Full catalogue browser" className="absolute inset-3 z-40 overflow-y-auto border border-white/10 bg-[#080808]/95 p-5 backdrop-blur-xl"><button aria-label="Close full catalogue" onClick={()=>setCatalogueOpen(false)} className="float-right p-2"><X size={18}/></button><CatalogueBrowser countries={countries} products={products} selected={selected} onSelect={toggle}/></aside>}
        {history && <aside className="absolute inset-x-3 top-24 z-20 max-h-[55vh] overflow-auto border border-white/15 bg-[#111314] p-4"><button aria-label="Close acquisition history" onClick={()=>setHistory(false)} className="float-right p-2"><X size={18}/></button><ScientificJobHistory project={project} onOpen={id=>{setJob(id);setProgress(true)}}/></aside>}
        <aside aria-label="Fetch queue" className="absolute bottom-3 right-3 z-30 flex max-h-[44vh] w-[min(390px,calc(100%-24px))] flex-col border border-white/[0.08] bg-[#080808]/85 backdrop-blur-lg rounded-md shadow-2xl">
          <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-4 py-3"><button aria-expanded={!queueCollapsed} onClick={()=>setQueueCollapsed(value=>!value)} className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest"><ListOrdered size={17} className="text-red-400"/>Fetch queue <span className="text-white/35">{selected.length + queue.runs.reduce((n,run)=>n+run.plan.selections.length,0)}</span><ChevronDown size={15} className={queueCollapsed?'rotate-180':''}/></button><button className="text-xs text-white/40 hover:text-white" onClick={()=>{setHistory(value=>!value);setCategory(null)}}>History</button></header>
          {!queueCollapsed && <div className="overflow-y-auto px-4" aria-live="polite">
            {!selected.length && !queue.ids.length && <p className="py-5 text-xs text-white/35">Choose datasets from the categories to add them here.</p>}
            {queue.error && <p role="alert" className="py-2 text-xs text-amber-300">{queue.error}</p>}
            {selected.map(id=>{const p=products.find(product=>product.id===id);return <div key={id} className="flex items-center gap-3 border-b border-white/5 py-3"><Circle size={13} className="shrink-0 text-amber-400"/><span className="min-w-0 flex-1"><span className="block truncate text-xs text-white/75" title={p?.name||id}>{p?.name||id}</span><span className="block text-[11px] text-white/35">{invalid.includes(id)?'Unavailable':!p?.access_ready?'Credentials needed - not submitted':'Selected - awaiting review'}</span></span><button aria-label={`Remove ${p?.name||id} from queue`} onClick={()=>toggle(id)} className="p-1 text-white/30 hover:text-white"><X size={14}/></button></div>})}
            {queue.ids.filter(id=>!queue.runs.some(run=>run.job.id===id)).map(id=><p key={id} className="py-3 text-xs text-white/40">Connecting to job {id.slice(0,8)}...</p>)}
            {queue.runs.map(run=>run.plan.selections.map(selection=><button key={`${run.job.id}:${selection.id}`} onClick={()=>{setJob(run.job.id);setProgress(true)}} className="flex w-full items-start gap-3 border-b border-white/5 py-3 text-left hover:bg-white/5"><span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${run.job.status==='succeeded'?'bg-emerald-400':run.job.status==='failed'?'bg-red-400':'bg-amber-400'}`}/><span className="min-w-0"><span className="block truncate text-xs text-white/75">{selection.product.name}</span><span className="block text-[11px] text-white/40">{Object.entries(selection.parameters).map(([key,value])=>`${key}: ${String(value)}`).join(' / ')}</span><span className="block text-[11px] text-white/50">{selectionStatus(run.job,selection.id)} - View job</span></span></button>))}
          </div>}
          <footer className="shrink-0 border-t border-white/10 p-3"><button disabled={!sources || !loaded || !selected.length || invalid.length>0} onClick={()=>setReview(true)} className="w-full bg-red-600 px-4 py-2.5 text-xs font-medium hover:bg-red-500 disabled:opacity-35">Review &amp; fetch selected{selected.length ? ` (${selected.length})` : ''}</button></footer>
        </aside>
      </div>
    </div>,document.body)}
    <ScientificFetchDialog open={open && review} project={project} initialCategories={[]} initialSelections={initialSelections} onClose={()=>setReview(false)} onStarted={(id,submitted)=>{
      observedJobs.current.set(id,'pending');queue.track(id);setReview(false);setCategory(null)
      const sent=new Set(submitted?.map(s=>s.product_id)||selected)
      const remaining=selected.filter(value=>!sent.has(value));setSelected(remaining)
      try {localStorage.setItem(storageKey,JSON.stringify(remaining))} catch {setNotice('Could not save the updated selection. The submitted job is retained in acquisition history.')}
    }}/>
    <DatasetFetchProgressDialog jobId={job} open={open && progress} onClose={()=>{setProgress(false);void refreshProjectData()}} onRunInBackground={()=>setProgress(false)} onJobFinished={()=>{void refreshProjectData()}}/>
  </>
}
