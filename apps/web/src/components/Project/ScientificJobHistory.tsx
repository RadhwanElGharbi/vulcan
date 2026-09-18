'use client'

import { useEffect, useState } from 'react'
import { researchRequest } from '@/lib/api/researchClient'

type JobSummary = { id: string; status: string; updated_at: string; products: string[] }

export function ScientificJobHistory({ project, onOpen }: { project: string; onOpen: (id: string) => void }) {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [more, setMore] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function refresh() {
      try {
        const result = await researchRequest<{ jobs: JobSummary[]; has_more: boolean }>(`/projects/${encodeURIComponent(project)}/dataset-jobs?offset=${offset}`, undefined, controller.signal)
        if (!controller.signal.aborted) { setJobs(result.jobs); setMore(result.has_more); setError(null) }
      } catch (e) { if (!controller.signal.aborted) setError((e as Error).message) }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 5000)
    }
    refresh()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [project, offset])
  return <section className="space-y-3 border border-white/10 p-4 text-xs" aria-label="Acquisition history">
    <h3 className="font-mono uppercase tracking-wider text-white/70">Acquisition history and reports</h3>
    {error && <p role="alert" className="text-red-300">{error}</p>}
    {!error && jobs.length === 0 && <p className="text-white/50">No scientific acquisition jobs recorded for this project.</p>}
    {jobs.map(job => <button key={job.id} onClick={() => onOpen(job.id)} className="flex w-full flex-wrap items-center justify-between gap-2 border-t border-white/10 py-3 text-left hover:text-red-300">
      <span>{job.products.join(', ') || job.id}<span className="ml-3 text-white/40">{new Date(job.updated_at).toLocaleString()}</span></span>
      <span className={job.status === 'awaiting_approval' ? 'text-amber-200' : ''}>{job.status.replaceAll('_', ' ')} · View report</span>
    </button>)}
    {(offset > 0 || more) && <div className="flex gap-4"><button disabled={offset === 0} className="disabled:opacity-30" onClick={() => setOffset(value => Math.max(0, value - 20))}>Newer jobs</button><button disabled={!more} className="disabled:opacity-30" onClick={() => setOffset(value => value + 20)}>Older jobs</button></div>}
  </section>
}
