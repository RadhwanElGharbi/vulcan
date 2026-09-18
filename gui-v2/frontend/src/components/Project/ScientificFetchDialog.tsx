'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Loader2, Plus, X } from 'lucide-react'
import { Finding, ResearchDiscovery, ResearchPlan, ResearchProduct, ResearchSelection, researchRequest } from '@/lib/api/researchClient'
import { getApiBase } from '@/lib/api/dataClient'
import { withAoiCountries } from '@/lib/api/aoiSelections'
import { TemporalChoices } from './TemporalChoices'

const fieldClass = 'w-full rounded-sm border border-white/15 bg-[#171717] px-3 py-2 text-sm text-white focus:border-red-500 focus:outline-none'
const show = (value: unknown): string => {
  if (value === null || value === undefined) return 'Unknown — not supplied'
  if (Array.isArray(value)) return value.map(show).join(', ')
  if (typeof value === 'object') {
    const entries = Object.entries(value)
    return entries.length ? entries.map(([key, v]) => `${key.replaceAll('_', ' ')}: ${show(v)}`).join('; ') : 'None'
  }
  return String(value)
}

const conversions = (recipe: Record<string, unknown>) => {
  const operations = [...(Array.isArray(recipe.coordinate_operations) ? recipe.coordinate_operations : []), ...(Array.isArray(recipe.auxiliary_operations) ? recipe.auxiliary_operations : [])]
  return operations.length ? operations.map(operation => ({ name: operation.name, stated_operation_accuracy_m: operation.stated_accuracy_m, accuracy_scope: operation.accuracy_scope })) : recipe.native_export ? 'Native scientific grid retained' : 'See retained provider and processing metadata'
}

const outputCrs = (selection: ResearchPlan['selections'][number]) => {
  if (!selection.recipe.native_export) return selection.recipe.target_crs
  const systems = selection.assets.map(asset => {
    const metadata = asset.metadata
    const epsg = metadata?.asset?.['proj:epsg'] || metadata?.properties?.['proj:epsg']
    return metadata?.source_crs || selection.product.semantics.source_crs || (epsg ? `EPSG:${epsg}` : null)
  })
  return { mode: 'Native source CRS retained per asset', declarations: [...new Set(systems.filter(Boolean))],
    ...(systems.some(value => !value) ? { unresolved_declarations: 'See the retained source metadata; any mandatory CRS that cannot be interpreted blocks publication' } : {}) }
}

const qualification = (product: ResearchProduct) => {
  const verification=product.assessment.verification
  return { disposition: product.assessment.disposition, full_verification: verification.status, verified_at: verification.verified_at,
    latest_live_check: verification.latest_live_acquisition ? Object.fromEntries(Object.entries(verification.latest_live_acquisition).filter(([key]) => ['status','checked_at','error','matches_current_implementation','scope'].includes(key))) : 'Not executed',
    latest_discovery: verification.latest_discovery || 'Not executed', scope: 'A successful fetch does not establish fitness for a particular analysis' }
}

export function Findings({ findings, checked, onChange }: { findings: Finding[]; checked: Set<string>; onChange: (id: string, value: boolean) => void }) {
  return <div className="space-y-3">{findings.map(f => <div key={f.id} className={`rounded-sm border p-3 text-sm ${f.severity === 'block' ? 'border-red-500/50 text-red-300' : 'border-amber-500/30 text-amber-100'}`}>
    <label className="flex items-start gap-3">
      {f.severity === 'acknowledgement' && <input type="checkbox" className="mt-1 accent-red-500" checked={checked.has(f.id)} onChange={e => onChange(f.id, e.target.checked)} />}
      <span>{f.message}<span className="mt-1 block text-xs text-white/50">{f.rule} · {f.severity === 'acknowledgement' ? 'Explicit acceptance required' : f.severity}</span></span>
    </label>
    {f.basis.startsWith('https://') && <a href={f.basis} target="_blank" rel="noreferrer" className="ml-6 mt-2 inline-block text-xs underline">Source documentation</a>}
  </div>)}</div>
}

export function ScientificFetchDialog({ open, project, initialCategories, initialDataset, initialSelections, onClose, onStarted }: {
  open: boolean; project: string; initialCategories: string[]; initialDataset?: string | null; initialSelections?: ResearchSelection[]; onClose: () => void; onStarted: (jobId: string, selections?: ResearchSelection[]) => void
}) {
  const [countries,setCountries]=useState<string[]>([])
  const [products, setProducts] = useState<ResearchProduct[]>([])
  const [selections, setSelections] = useState<ResearchSelection[]>([])
  const [plan, setPlan] = useState<ResearchPlan | null>(null)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [selectionConflict, setSelectionConflict] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [discoveryId, setDiscoveryId] = useState<string | null>(null)
  const [discoveryStage, setDiscoveryStage] = useState<string | null>(null)
  const panel = useRef<HTMLDivElement>(null)
  const request = useRef<AbortController | null>(null)
  const idempotency = useRef('')
  const initial = useRef(initialCategories)
  initial.current = initialCategories
  const explicit = useRef(initialSelections)
  explicit.current = initialSelections
  const discoveryKey = `zeus-discovery:${project}`

  const forgetDiscovery=useCallback(() => {
    localStorage.removeItem(discoveryKey)
    setDiscoveryId(null); setDiscoveryStage(null)
  },[discoveryKey])

  const showPlan=useCallback((value: ResearchPlan) => {
    setPlan(value); setChecked(new Set())
    setSelections(value.selections.map(s => ({product_id:s.product.id,parameters:s.parameters})))
    const key=`zeus-fetch-key:${project}:${value.plan_hash}`
    idempotency.current=localStorage.getItem(key) || crypto.randomUUID()
    localStorage.setItem(key,idempotency.current)
    panel.current?.scrollTo(0, 0)
  },[project])

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    const previous = document.activeElement as HTMLElement | null
    setPlan(null); setChecked(new Set()); setError(null); setBusy(true); setSelectionConflict(false); setDiscoveryId(null); setDiscoveryStage(null)
    researchRequest<{ products: ResearchProduct[]; project_countries:string[]; default_product_by_category: Record<string,string>; catalogue_references: {dataset: string; product_ids: string[]}[] }>(`/dataset-sources?project=${encodeURIComponent(project)}`, undefined, controller.signal).then(async data => {
      setProducts(data.products)
      setCountries(data.project_countries)
      const categories = initial.current.length ? initial.current : ['dem']
      if (explicit.current) {
        const requested = explicit.current
        const missing = requested.filter(s => !data.products.some(p => p.id === s.product_id))
        if (missing.length) throw new Error(`Selected products are no longer registered: ${missing.map(s => s.product_id).join(', ')}`)
        setSelections(requested.flatMap(s => withAoiCountries({product_id:s.product_id, parameters:{...defaults(data.products.find(p => p.id === s.product_id)!), ...s.parameters}},data.products.find(p=>p.id===s.product_id)!,data.project_countries)))
      } else if (initialDataset) {
        const ids = new Set(data.catalogue_references.filter(row => row.dataset === initialDataset).flatMap(row => row.product_ids))
        const matches = data.products.filter(p => ids.has(p.id))
        setSelections(matches.flatMap(p => withAoiCountries({product_id:p.id, parameters:defaults(p)},p,data.project_countries)))
        if (!matches.length) setError(`${initialDataset} has no registered acquisition product. Its source or adapter qualification remains outstanding. Choose another product explicitly to proceed.`)
      } else setSelections(categories.map(category => data.products.find(p => p.id === data.default_product_by_category[category])).filter((p): p is ResearchProduct => Boolean(p)).flatMap(p => withAoiCountries({ product_id: p.id, parameters: defaults(p) },p,data.project_countries)))
      let saved=localStorage.getItem(discoveryKey)
      if (!saved) {
        const history=await researchRequest<{jobs:ResearchDiscovery[]}>(`/projects/${encodeURIComponent(project)}/dataset-discovery-jobs`,undefined,controller.signal)
        if (controller.signal.aborted) return
        saved=history.jobs.find(job => ['pending','running','cancelling'].includes(job.status))?.id || null
        if (saved) localStorage.setItem(discoveryKey,saved)
      }
      if (saved && explicit.current) {
        const existing = await researchRequest<ResearchDiscovery>(`/dataset-discovery-jobs/${saved}`, undefined, controller.signal)
        const requestedIds = explicit.current.map(s => s.product_id).sort().join('|')
        const existingIds = existing.request.selections.map(s => s.product_id).sort().join('|')
        if (requestedIds !== existingIds) {
          if (['pending','running','cancelling'].includes(existing.status)) {
            setSelectionConflict(true)
            throw new Error('Another discovery is still running for this project. Finish or cancel it from Datasets before reviewing this selection.')
          }
          saved = null
        }
        // A fresh Twin selection starts a fresh review, never an older completed plan.
        if (!['pending','running','cancelling'].includes(existing.status)) saved = null
      }
      if (saved) { setDiscoveryId(saved); setDiscoveryStage('Reconnecting to discovery') }
      setTimeout(() => panel.current?.focus(), 0)
    }).catch(e => { if (!controller.signal.aborted) setError(e.message) }).finally(() => { if (!controller.signal.aborted) setBusy(false) })
    return () => { controller.abort(); request.current?.abort(); previous?.focus() }
  }, [open, project, initialDataset, discoveryKey])

  useEffect(() => {
    if (!open || !discoveryId) return
    const controller=new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const job=await researchRequest<ResearchDiscovery>(`/dataset-discovery-jobs/${discoveryId}`,undefined,controller.signal)
        if (controller.signal.aborted) return
        if (job.project!==project) throw new Error('Saved discovery belongs to another project')
        setSelections(job.request.selections)
        setDiscoveryStage(`${job.stage.replaceAll('_',' ')}${job.product ? ` · ${job.product}` : ''}`)
        if (job.status==='succeeded' && job.plan_id) {
          const frozen=await researchRequest<ResearchPlan>(`/dataset-plans/${job.plan_id}`,undefined,controller.signal)
          if (controller.signal.aborted) return
          showPlan(frozen);setDiscoveryId(null);setDiscoveryStage(null);setError(null)
          return
        }
        if (['failed','cancelled'].includes(job.status)) {
          forgetDiscovery();setError(job.error || (job.status==='cancelled' ? 'Discovery cancelled. No acquisition was authorized.' : 'Discovery failed.'))
          return
        }
        setError(null)
      } catch (e) {
        if (controller.signal.aborted) return
        if ((e as Error & {status?:number}).status===404) {
          forgetDiscovery();setError('The saved discovery is no longer available. Select products and discover again.');return
        }
        setError(`${(e as Error).message} Reconnecting to the saved discovery…`)
      }
      timer=setTimeout(poll,1500)
    }
    void poll()
    return () => {controller.abort();clearTimeout(timer)}
  },[open,discoveryId,project,forgetDiscovery,showPlan])

  function defaults(product: ResearchProduct) {
    return Object.fromEntries(Object.entries(product.parameters).filter(([, spec]) => spec.default !== undefined).map(([key, spec]) => [key, spec.default]))
  }
  function toggle(id: string, value: boolean) {
    setChecked(previous => { const next = new Set(previous); value ? next.add(id) : next.delete(id); return next })
  }
  async function discover() {
    if (selectionConflict) return
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    setBusy(true); setError(null)
    try {
      const queued = await researchRequest<ResearchDiscovery>(`/projects/${encodeURIComponent(project)}/dataset-fetch/plan?background=true`, { selections }, controller.signal, {headers:{'Idempotency-Key':crypto.randomUUID()}})
      localStorage.setItem(discoveryKey,queued.id);setDiscoveryId(queued.id);setDiscoveryStage('Queued for discovery')
    } catch (e) { if (!controller.signal.aborted) setError((e as Error).message) }
    finally { if (!controller.signal.aborted) setBusy(false) }
  }
  async function cancelDiscovery() {
    if (!discoveryId) return
    setBusy(true);setError(null)
    try {
      await researchRequest(`/dataset-discovery-jobs/${discoveryId}`,undefined,undefined,{method:'DELETE'})
      setDiscoveryStage('Stopping provider requests')
    } catch(e) {setError((e as Error).message)}
    finally {setBusy(false)}
  }
  async function confirm() {
    if (!plan) return
    setBusy(true); setError(null)
    try {
      const job = await researchRequest<{ job_id: string }>(`/projects/${encodeURIComponent(project)}/dataset-fetch`, { plan_id: plan.plan_id, plan_hash: plan.plan_hash, acknowledged_findings: [...checked].sort(), idempotency_key: idempotency.current })
      forgetDiscovery()
      onStarted(job.job_id, plan.selections.map(s=>({product_id:s.product.id,parameters:s.parameters})))
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  if (!open) return null
  const missingRequired=selections.some(selection=>{const product=products.find(p=>p.id===selection.product_id);return !product || Object.entries(product.parameters).some(([key,spec])=>spec.required && (selection.parameters[key]===undefined || selection.parameters[key]==='' || selection.parameters[key]===null))})
  const pending=busy || Boolean(discoveryId)
  const findings = plan ? [...plan.findings, ...plan.selections.flatMap(s => s.findings)] : []
  const canConfirm = !findings.some(f => f.severity === 'block' || (f.severity === 'acknowledgement' && !checked.has(f.id)))
  return createPortal(<div className="fixed inset-0 z-[16000] flex items-center justify-center bg-black/75 p-4 backdrop-blur-md" role="presentation">
    <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="research-title" className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-sm border border-white/15 bg-[#0b0b0b] text-white shadow-2xl" onKeyDown={event => {
      if (event.key === 'Escape' && (!busy || discoveryId)) onClose()
      if (event.key === 'Tab') {
        const nodes = panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href]')
        if (!nodes?.length) return
        const first = nodes[0], last = nodes[nodes.length-1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }}>
      <header className="flex items-center justify-between border-b border-white/10 p-5"><div><p className="text-xs uppercase tracking-widest text-red-400">Scientific acquisition</p><h2 id="research-title" className="mt-1 text-lg">{plan ? 'Review the frozen fetch plan' : 'Choose sources and parameters'}</h2></div><button aria-label="Close acquisition review" onClick={onClose} disabled={busy && !discoveryId} className="p-2 disabled:opacity-30"><X className="h-5 w-5" /></button></header>
      <div className="flex-1 space-y-5 overflow-y-auto p-5">
        {error && <p role="alert" className="border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">{error}</p>}
        {discoveryStage && <p role="status" className="border border-white/15 p-3 text-sm text-white/70">{discoveryStage}. You can close this review or refresh and reconnect to the saved discovery.</p>}
        {!plan ? <>
          <p className="text-sm text-white/60">Select exact products. Discovery resolves assets and metadata for review before acquisition. Multiple products from the same category are supported.</p>
          <fieldset disabled={pending} className="space-y-5">
          {selections.map((selection, index) => {
            const product = products.find(p => p.id === selection.product_id)
            return <section key={index} className="space-y-3 border border-white/10 p-4">
              <div className="flex items-center gap-3"><label className="flex-1 text-xs text-white/60">Dataset source<select aria-label={`Dataset source ${index+1}`} className={`${fieldClass} mt-1`} value={selection.product_id} onChange={e => { const p = products.find(p => p.id === e.target.value)!; setSelections(previous => previous.flatMap((s, i) => i === index ? withAoiCountries({ product_id: p.id, parameters: defaults(p) },p,countries) : [s])) }}>{products.filter(p=>p.id===selection.product_id || p.countries.includes('WLD') || p.countries.some(country=>countries.includes(country))).map(p => <option key={p.id} value={p.id} disabled={['excluded','unavailable'].includes(p.assessment.disposition)}>{p.category.replaceAll('_',' ')} · {p.name}{!p.access_ready ? ' · credentials required' : ''}</option>)}</select></label><button aria-label={`Remove selection ${index+1}`} onClick={() => setSelections(previous => previous.filter((_, i) => i !== index))}><X className="h-4 w-4" /></button></div>
              {product && <><p className="text-xs text-white/50">{product.publisher} · {product.version} · {product.assessment.disposition.replaceAll('_',' ')}</p><div className="grid gap-3 sm:grid-cols-2">{Object.entries(product.parameters).filter(([key])=>!(['worldpop-counts','sentinel2-l2a','era5-single-levels'].includes(product.id) && ['year','start','end','scene_ids'].includes(key))).map(([key, spec]) => <label key={key} className="text-xs capitalize text-white/70">{key.replaceAll('_',' ')}{spec.required ? ' *' : ''}
                {spec.type === 'country' ? <p className="mt-1 border border-white/10 bg-white/5 px-3 py-2 text-sm">{String(selection.parameters[key] || 'No country detected for the AOI')}<span className="ml-2 text-xs text-white/40">From project AOI</span></p> : spec.type === 'enum' ? <select className={`${fieldClass} mt-1`} value={String(selection.parameters[key] || '')} onChange={e => setSelections(previous => previous.map((s,i) => i === index ? { ...s, parameters: { ...s.parameters, [key]: e.target.value } } : s))}><option value="" disabled>Choose {key.replaceAll("_", " ")}</option>{spec.values?.map(v => <option key={v}>{v}</option>)}</select> : <input className={`${fieldClass} mt-1`} type={spec.type === 'date' ? 'date' : ['integer','number'].includes(spec.type) ? 'number' : 'text'} min={spec.min} max={spec.max} placeholder={['list','identifier_list'].includes(spec.type) ? spec.values?.join(', ') || 'Exact acquisition IDs, separated by commas' : spec.type === 'country' ? 'ISO3, e.g. CAN' : undefined} value={Array.isArray(selection.parameters[key]) ? (selection.parameters[key] as string[]).join(', ') : String(selection.parameters[key] ?? '')} onChange={e => { const value = ['list','identifier_list'].includes(spec.type) ? e.target.value.split(',').map(v => v.trim()).filter(Boolean) : ['integer','number'].includes(spec.type) ? Number(e.target.value) : e.target.value; setSelections(previous => previous.map((s,i) => i === index ? { ...s, parameters: { ...s.parameters, [key]: value, ...(['country','variables'].includes(key) && ['worldpop-counts','sentinel2-l2a','era5-single-levels'].includes(product.id) ? {year:undefined,start:undefined,end:undefined,scene_ids:undefined} : {}) } } : s)) }} />}
              </label>)}{['worldpop-counts','sentinel2-l2a','era5-single-levels'].includes(product.id) && <TemporalChoices key={product.id} product={product} project={project} selection={selection} onChange={patch=>setSelections(previous=>previous.map((s,i)=>i===index?{...s,parameters:{...s.parameters,...patch}}:s))}/>}</div>{!product.access_ready && <p className="text-sm text-amber-300">Requires locally configured {product.credentials.join(', ')} and provider terms acceptance.</p>}</>}
            </section>
          })}
          <button className="flex items-center gap-2 border border-white/20 px-4 py-2 text-sm" onClick={() => { const p = products[0]; if (p) setSelections(previous => [...previous, ...withAoiCountries({ product_id: p.id, parameters: defaults(p) },p,countries)]) }}><Plus className="h-4 w-4" /> Add dataset</button>
          </fieldset>
          <a href={`${getApiBase()}/dataset-sources/audit`} target="_blank" rel="noreferrer" className="block text-xs text-white/60 underline">Open full catalogue assessment</a>
        </> : <>
          <p className="break-all text-xs text-white/50">Plan {plan.plan_hash}<br />Snapshot reference: {plan.as_of} · Project CRS: {plan.target_crs}</p>
          {plan.selections.map(selection => <section key={selection.id} className="space-y-4 border border-white/10 p-4"><h3 className="font-medium">{selection.product.name}</h3><dl className="grid gap-3 text-xs sm:grid-cols-2">{Object.entries({ Publisher: selection.product.publisher, Version: selection.product.version, Qualification: qualification(selection.product), 'Declared country coverage': selection.product.countries, 'AOI normalization': selection.recipe.aoi_normalization ? 'Original AOI files and exact coordinate operation retained; see recipe' : 'Explicit geographic footprint supplied', 'Observation period': selection.product.observation_period, 'Release date': selection.product.release_date, 'Native spacing': selection.product.native_spacing, 'Documented accuracy': selection.product.accuracy, Units: selection.product.units, 'Selected parameters': selection.parameters, 'Value meaning': Object.fromEntries(Object.entries(selection.product.semantics).filter(([key]) => key !== 'attribute_schema')), 'Analytical output CRS': outputCrs(selection), 'Output spacing': selection.recipe.native_export ? 'Native grid retained' : { spacing: selection.recipe.spacing, units: selection.recipe.spacing_units }, 'Coordinate conversion': conversions(selection.recipe), Resampling: selection.recipe.native_export ? 'None for scientific values' : selection.recipe.resampling, Clipping: selection.recipe.clipping_order || selection.recipe.mask, Coverage: 'Discovery footprints only; actual valid coverage measured after acquisition', Licence: selection.product.license, Attribution: selection.product.attribution, 'Resolved assets': selection.assets.length }).map(([key,value]) => <div key={key}><dt className="mb-1 text-white/45">{key}</dt><dd className="break-words text-white/90">{show(value)}</dd></div>)}</dl>
            <details className="text-xs"><summary className="cursor-pointer text-white/60">Resolved source assets and processing recipe</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-white/60">{JSON.stringify({ product: selection.product, assets: selection.assets, recipe: selection.recipe }, null, 2)}</pre></details>
          </section>)}
          <section className="border border-white/10 p-4 text-xs"><p>Known download size: {(Number(plan.estimates.known_download_bytes)/1024**2).toFixed(1)} MiB{Boolean(plan.estimates.unknown_asset_sizes) && ' + assets with unknown size'}.</p><p className="mt-2">Exact replay inputs remain on disk until explicitly deleted. Provisional output estimate: {(Number(plan.estimates.output_upper_bound_bytes)/1024**2).toFixed(1)} MiB. Intermediate raster allowance: {(Number(plan.estimates.intermediate_upper_bound_bytes || 0)/1024**2).toFixed(1)} MiB; other temporary files are additional.</p></section>
          <Findings findings={findings} checked={checked} onChange={toggle} />
        </>}
      </div>
      <footer className="flex items-center justify-between gap-3 border-t border-white/10 p-5"><button onClick={() => { if (discoveryId) { void cancelDiscovery() } else if (plan) { forgetDiscovery();setPlan(null);setChecked(new Set()) } else onClose() }} disabled={busy} className="text-sm text-white/60">{discoveryId ? 'Cancel discovery' : plan ? 'Change selection' : 'Cancel'}</button><button disabled={pending || selectionConflict || missingRequired || !selections.length || (Boolean(plan) && !canConfirm)} onClick={plan ? confirm : discover} className="flex items-center gap-2 rounded-sm bg-red-600 px-5 py-2.5 text-sm font-medium disabled:opacity-40">{pending && <Loader2 className="h-4 w-4 animate-spin" />}{pending ? 'Please wait…' : plan ? 'Confirm fetch' : 'Discover and review'}</button></footer>
    </div>
  </div>, document.body)
}
