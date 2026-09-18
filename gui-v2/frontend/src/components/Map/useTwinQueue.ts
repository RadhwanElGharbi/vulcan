'use client'

import { useEffect, useRef, useState } from 'react'
import { researchRequest, ResearchPlan } from '@/lib/api/researchClient'

export type QueueJob = {id:string; project:string; plan_id:string; status:string; error?:string|null; categories:Record<string,{status:string; stages:Record<string,{status:string}>}>}
export type QueueRun = {job:QueueJob; plan:ResearchPlan}
export function selectionStatus(job:QueueJob, id:string):string {
  if (job.status === 'succeeded') return 'Published'
  if (job.status === 'awaiting_approval') return 'Awaiting approval'
  if (job.status === 'cancelled') return 'Cancelled'
  if (job.status === 'cancelling') return 'Cancelling'
  const entry=job.categories?.[id]
  if (entry?.status === 'failed') return 'Failed'
  if (['failed','partial'].includes(job.status)) return 'Not published · job failed'
  const running=Object.entries(entry?.stages || {}).find(([,stage])=>stage.status==='running')?.[0]
  const labels:Record<string,string>={prefetch_scan:'Checking sources',fetch:'Downloading',raw_metadata:'Preserving sources',process:'Processing',processed_metadata:'Indexing',validation:'Validating',layer_publish:'Publishing'}
  if (running) return labels[running] || 'Running'
  if (entry?.stages?.validation?.status==='succeeded') return 'Validated · awaiting publication'
  return job.status==='pending' ? 'Queued' : 'Waiting for worker'
}

export function useTwinQueue(project:string, open:boolean) {
  const key=`vulcan.digital-twin.jobs.v1.${project}`
  const [ids,setIds]=useState<string[]>([])
  const [loaded,setLoaded]=useState(false)
  const [runs,setRuns]=useState<QueueRun[]>([])
  const [error,setError]=useState<string|null>(null)
  const plans=useRef(new Map<string,ResearchPlan>())
  useEffect(()=>{
    try {
      const saved:unknown=JSON.parse(localStorage.getItem(key)||'[]')
      if (Array.isArray(saved) && saved.every(id=>typeof id==='string')) setIds(saved.slice(-20))
    } catch {setError('Saved queue could not be read. Acquisition history remains available.')}
    setLoaded(true)
  },[key])
  useEffect(()=>{
    if (!loaded) return
    try {localStorage.setItem(key,JSON.stringify(ids))} catch {setError('Queue could not be saved in this browser. Use acquisition history after refresh.')}
  },[ids,key,loaded])
  useEffect(()=>{
    if (!open) return
    const controller=new AbortController()
    researchRequest<{active_jobs:Record<string,{job_id:string}>}>('/dataset-jobs/active',undefined,controller.signal).then(data=>{
      const active=data.active_jobs[project]?.job_id
      if(active && !controller.signal.aborted) setIds(old=>[...new Set([...old,active])].slice(-20))
    }).catch(e=>{if(!controller.signal.aborted)setError(e.message)})
    return ()=>controller.abort()
  },[project,open])
  useEffect(()=>{
    if (!open || !ids.length) return
    const controller=new AbortController()
    let timer:ReturnType<typeof setTimeout>
    async function poll() {
      const results=await Promise.allSettled(ids.map(async id=>{
        const job=await researchRequest<QueueJob>(`/dataset-jobs/${encodeURIComponent(id)}`,undefined,controller.signal)
        if(job.project!==project) throw Error('Queue job belongs to another project')
        let plan=plans.current.get(job.plan_id)
        if(!plan) {
          plan=await researchRequest<ResearchPlan>(`/dataset-plans/${encodeURIComponent(job.plan_id)}`,undefined,controller.signal)
          if(plan.project!==project) throw Error('Queue plan belongs to another project')
          plans.current.set(job.plan_id,plan)
        }
        return {job,plan}
      }))
      if(controller.signal.aborted)return
      const successful=results.flatMap(result=>result.status==='fulfilled'?[result.value]:[])
      setRuns(old=>ids.flatMap(id=>{const run=successful.find(r=>r.job.id===id)||old.find(r=>r.job.id===id);return run?[run]:[]}))
      const failed=results.find(result=>result.status==='rejected')
      setError(failed?.status==='rejected' ? `Status unavailable; displayed statuses may be stale. ${failed.reason.message}` : null)
      timer=setTimeout(poll,3000)
    }
    void poll()
    return ()=>{controller.abort();clearTimeout(timer)}
  },[ids,open,project])
  function track(id:string) {
    setIds(old=>[...new Set([...old,id])].slice(-20))
  }
  return {runs,ids,error,track}
}
