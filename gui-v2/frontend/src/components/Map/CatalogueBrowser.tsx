'use client'
import { useEffect,useState } from 'react'
import { researchRequest,ResearchProduct } from '@/lib/api/researchClient'
type Row={row:number;country:string;category:string|null;dataset:string;disposition:string;product_ids:string[];reasons:string[];documentation:string[];original:{Source?:string;Access?:string;Type?:string}}
export function CatalogueBrowser({category,countries,products,selected,onSelect}:{category?:string|null;countries:string[];products:ResearchProduct[];selected:string[];onSelect:(id:string)=>void}) {
 const [rows,setRows]=useState<Row[]>([]),[error,setError]=useState<string|null>(null),[query,setQuery]=useState('')
 const [filter,setFilter]=useState(category||'')
 useEffect(()=>{setFilter(category||'')},[category])
 useEffect(()=>{const c=new AbortController();researchRequest<{entries:Row[]}>('/dataset-sources/audit',undefined,c.signal).then(d=>setRows(d.entries)).catch(e=>{if(!c.signal.aborted)setError(e.message)});return()=>c.abort()},[])
 const shown=rows.filter(row=>(!filter || row.category===filter || filter==='unclassified' && !row.category) && (row.country==='WLD' || countries.includes(row.country)) && `${row.dataset} ${row.original.Source||''}`.toLowerCase().includes(query.toLowerCase()))
 return <section aria-label="Source catalogue" className="space-y-3 text-xs">
  <div><h3 className="text-white/75">Full source catalogue</h3><p className="mt-1 text-[10px] text-white/40">{shown.length} matching entries / {rows.length} catalogue references. Registered products above have acquisition adapters; other entries retain their recorded limitations.</p></div>
  {error && <p role="alert" className="text-amber-300">{error}</p>}
  <input aria-label="Search full catalogue" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search catalogue sources" className="w-full border border-white/10 bg-white/5 p-2"/>
  {!category && <select aria-label="Catalogue category" value={filter} onChange={e=>setFilter(e.target.value)} className="w-full border border-white/10 bg-[#171717] p-2"><option value="">All categories</option>{[...new Set(rows.map(r=>r.category||'unclassified'))].sort().map(c=><option key={c} value={c}>{c.replaceAll('_',' ')}</option>)}</select>}
  <p className="text-white/50">{countries.length ? `AOI countries: ${countries.join(', ')} + worldwide sources` : 'No country detected for this AOI; showing worldwide sources.'}</p>
  <div className="max-h-80 overflow-y-auto space-y-3">{shown.map(row=><article key={row.row} className="border-t border-white/10 pt-3">
    <h4 className="text-white/65">{row.dataset}</h4><p className="mt-1 text-[10px] text-white/35">{row.original.Source} / {row.country} / {row.original.Type}</p>
    <p className="mt-1 text-[10px] text-amber-200/70">{row.disposition.replaceAll('_',' ')}{!row.product_ids.length?' - no registered acquisition adapter':''}</p>
    {row.reasons.length>0 && <p className="mt-1 text-[10px] text-white/40">{row.reasons.join('; ')}</p>}
    {row.product_ids.map(id=>{const product=products.find(p=>p.id===id);return product && !['excluded','unavailable'].includes(product.assessment.disposition)?<button key={id} onClick={()=>onSelect(id)} className="mr-2 mt-2 border border-white/15 px-2 py-1 text-white/60 hover:text-white">{selected.includes(id)?'Remove':'Add'} {product.name}</button>:null})}
    {row.documentation.filter(url=>url.startsWith('https://')).slice(0,1).map(url=><a key={url} href={url} target="_blank" rel="noreferrer" className="mt-2 block text-[10px] text-white/40 underline">Provider documentation</a>)}
  </article>)}</div>
 </section>
}
