'use client'
import { useEffect,useState } from 'react'
import { researchRequest,ResearchProduct,ResearchSelection } from '@/lib/api/researchClient'

type Availability={years:(string|number)[];dates:string[];scenes:{id:string;date:string;datetime:string;cloud_cover:number|null}[];country?:string;basis:string;note?:string}
export function TemporalChoices({product,project,selection,onChange}:{product:ResearchProduct;project:string;selection:ResearchSelection;onChange:(patch:Record<string,unknown>)=>void}) {
  const [data,setData]=useState<Availability|null>(null)
  const [year,setYear]=useState(String(selection.parameters.start||'').slice(0,4))
  const [month,setMonth]=useState('')
  const [error,setError]=useState<string|null>(null)
  const [busy,setBusy]=useState(false)
  const [retry,setRetry]=useState(0)
  const country=String(selection.parameters.country||'')
  const variables=Array.isArray(selection.parameters.variables)?selection.parameters.variables.join(','):''
  const isPopulation=product.id==='worldpop-counts'
  const cls='w-full mt-1 border border-white/15 bg-[#171717] px-3 py-2 text-sm text-white'
  useEffect(()=>{
    const controller=new AbortController();setBusy(true);setError(null);setData(null)
    const query=new URLSearchParams({project})
    if(year)query.set('year',year)
    if(month)query.set('month',month)
    if(country)query.set('country',country)
    if(variables)query.set('variables',variables)
    researchRequest<Availability>(`/dataset-sources/${product.id}/availability?${query}`,undefined,controller.signal).then(value=>{if(!controller.signal.aborted)setData(value)}).catch(e=>{if(!controller.signal.aborted)setError(e.message)}).finally(()=>{if(!controller.signal.aborted)setBusy(false)})
    return ()=>controller.abort()
  },[project,product.id,year,month,country,variables,retry])
  const dates=data?.dates||[]
  const start=String(selection.parameters.start||'')
  const end=String(selection.parameters.end||'')
  const imagery=product.category==='imagery'
  let endDates=dates.filter(date=>date>=start)
  if(!imagery && start) {
    endDates=endDates.filter((date,index)=>Date.parse(date)-Date.parse(start)===index*86400000)
  }
  return <section className="space-y-3 border border-white/10 p-3 sm:col-span-2" aria-label={`Available dates for ${product.name}`}>
    <p className="text-xs text-white/60">{isPopulation?'Available release years':'Available acquisition dates'}</p>
    {busy && <p role="status" className="text-xs text-white/40">Reading provider catalogue...</p>}
    {error && <div role="alert" className="text-xs text-amber-300">{error} <button className="underline" onClick={()=>setRetry(v=>v+1)}>Retry</button></div>}
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-xs text-white/60">{isPopulation?'Release year':'Browse catalogue year'}<select aria-label={isPopulation?'Available release year':'Browse catalogue year'} className={cls} disabled={busy} value={isPopulation?String(selection.parameters.year||''):year} onChange={e=>{if(isPopulation)onChange({year:Number(e.target.value),...(data?.country?{country:data.country}:{})});else{setYear(e.target.value);onChange({start:undefined,end:undefined,...(imagery?{scene_ids:[]}: {})})}}}><option value="">Choose a year</option>{data?.years.map(y=><option key={y} value={y}>{y}</option>)}</select></label>
      {imagery && <label className="text-xs text-white/60">Month (optional)<select aria-label="Browse acquisition month" className={cls} value={month} onChange={e=>{setMonth(e.target.value);onChange({start:undefined,end:undefined,scene_ids:[]})}}><option value="">All months</option>{Array.from({length:12},(_,i)=>i+1).map(m=><option key={m} value={m}>{new Date(2000,m-1,1).toLocaleString('en',{month:'long'})}</option>)}</select></label>}
      {!isPopulation && ['start','end'].map(key=><label key={key} className="text-xs capitalize text-white/60">{key} date<select aria-label={`Available ${key} date`} className={cls} disabled={busy || !dates.length} value={String(selection.parameters[key]||'')} onChange={e=>onChange(key==='start'?{start:e.target.value,end:e.target.value,...(imagery?{scene_ids:[]}: {})}:{end:e.target.value,...(imagery?{scene_ids:[]}: {})})}><option value="">Choose an available date</option>{(key==='start'?dates:endDates).map(date=><option key={date}>{date}</option>)}</select></label>)}
    </div>
    {!busy && data && (isPopulation?!data.years.length:year && !dates.length) && <p className="text-xs text-amber-200">No available {isPopulation?'releases':'dates'} for this selection.</p>}
    {imagery && start && end && <div className="max-h-48 overflow-y-auto space-y-2 text-xs"><p className="text-white/50">Scenes in this date range. Leave all unchecked to include every matching scene; discovery applies your cloud-cover limit.</p>{data?.scenes.filter(s=>s.date>=start && s.date<=end).map(scene=><label key={scene.id} className="flex gap-2"><input type="checkbox" checked={(selection.parameters.scene_ids as string[]||[]).includes(scene.id)} onChange={e=>{const ids=(selection.parameters.scene_ids as string[]||[]);onChange({scene_ids:e.target.checked?[...ids,scene.id]:ids.filter(id=>id!==scene.id)})}}/><span>{scene.datetime} / clouds: {scene.cloud_cover??'unknown'}%<span className="block font-mono text-[10px] text-white/40">{scene.id}</span></span></label>)}</div>}
    {data && <p className="text-[10px] text-white/40">{data.note} <a href={data.basis.startsWith('https://')?data.basis:undefined} target="_blank" rel="noreferrer" className="underline">Availability source</a></p>}
  </section>
}
