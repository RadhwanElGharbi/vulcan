'use client'

import { useState } from 'react'
import { getApiBase } from '@/lib/api/dataClient'
import { jobArtifactUrl, ResearchValidation, researchRequest } from '@/lib/api/researchClient'
import { Findings } from './ScientificFetchDialog'

export function ScientificJobReview({ jobId, status, report, outputs }: { jobId: string; status: string; report?: ResearchValidation; outputs?: { file: string; gap_mask?: string; extent_gap?: string; name: string }[] }) {
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const required = report?.findings.filter(f => f.severity === 'acknowledgement') || []
  const blocked = report?.findings.some(f => f.severity === 'block')
  async function accept() {
    if (!report) return
    setBusy(true); setError(null)
    try { await researchRequest(`/dataset-jobs/${jobId}/accept`, { report_hash: report.report_hash, acknowledged_findings: [...checked].sort() }) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="space-y-4 border border-white/10 p-4 text-sm">
    <div className="flex flex-wrap gap-4 text-xs"><a href={`${getApiBase()}/dataset-jobs/${jobId}/report`} target="_blank" rel="noreferrer" className="underline">Audit report and source evidence</a><a href={`${getApiBase()}/dataset-jobs/${jobId}/bundle`} className="underline">Download provenance and replay inputs</a></div>
    {error && <p role="alert" className="text-red-300">{error}</p>}
    {status === 'succeeded' && <div className="space-y-2">{outputs?.map(output => <div key={output.file}><a className="underline" href={jobArtifactUrl(jobId, output.file)}>Export {output.name}</a></div>)}</div>}
    {status === 'awaiting_approval' && <><p className="text-amber-200">Validation found limitations requiring review. These results have not been published.</p>
      <Findings findings={report?.findings || []} checked={checked} onChange={(id,value) => setChecked(previous => { const next = new Set(previous); value ? next.add(id) : next.delete(id); return next })} />
      <div className="space-y-2">{outputs?.map(output => <div key={output.file} className="text-xs"><a className="underline" href={jobArtifactUrl(jobId, output.file)}>{output.name}: inspect staged data</a>{output.gap_mask && <> · <a className="text-amber-200 underline" href={jobArtifactUrl(jobId, output.gap_mask)}>Download gap mask</a></>}{output.extent_gap && <> · <a className="text-amber-200 underline" href={jobArtifactUrl(jobId, output.extent_gap)}>AOI outside raster extent</a></>}</div>)}</div>
      <button disabled={busy || blocked || required.some(f => !checked.has(f.id))} onClick={accept} className="bg-red-600 px-4 py-2 disabled:opacity-40">{busy ? 'Recording acceptance…' : 'Accept these measured limitations and publish'}</button>
    </>}
    {blocked && <p className="text-red-300">Mandatory checks failed. Approval cannot override these failures.</p>}
  </section>
}
