import { getApiBase } from './dataClient'

export type Finding = { id: string; rule: string; severity: 'block' | 'acknowledgement' | 'information'; message: string; basis: string; evidence: Record<string, unknown> }
export type ParameterSpec = { type: string; values?: string[]; default?: unknown; required?: boolean; min?: number; max?: number }
export type ResearchProduct = {
  id: string; category: string; name: string; publisher: string; version: string; endpoint: string; kind: string; countries: string[];
  units: string; native_spacing: Record<string, unknown> | null; observation_period: Record<string, unknown> | null;
  release_date: string | null; accuracy: Record<string, unknown> | null; license: string | null; attribution: string;
  semantics: Record<string, unknown>; parameters: Record<string, ParameterSpec>; credentials: string[]; access_ready: boolean;
  assessment: { disposition: string; findings: Finding[]; documentation: string[]; verification: { status: string; verified_at: string | null; latest_live_acquisition?: Record<string,unknown>; latest_discovery?: Record<string,unknown> } }
}
export type ResearchSelection = { product_id: string; parameters: Record<string, unknown> }
export type ResearchPlan = {
  plan_id: string; plan_hash: string; project: string; as_of: string; aoi_hash: string; target_crs: string;
  findings: Finding[]; estimates: Record<string, unknown>;
  selections: { id: string; product: ResearchProduct; parameters: Record<string, unknown>; assets: { id: string; url: string; size: number | null; metadata?: { source_crs?: string; asset?: Record<string, unknown>; properties?: Record<string, unknown> } }[]; recipe: Record<string, unknown>; findings: Finding[] }[]
}
export type ResearchValidation = { report_hash: string; findings: Finding[]; results: Record<string, unknown>[] }
export type ResearchDiscovery = { id: string; project: string; status: string; stage: string; product: string | null; plan_id: string | null; plan_hash: string | null; error: string | null; request: {selections: ResearchSelection[]} }

export async function researchRequest<T>(path: string, body?: unknown, signal?: AbortSignal, options?: { method?: string; headers?: Record<string,string> }): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, { method: options?.method || (body === undefined ? 'GET' : 'POST'),
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...options?.headers }, body: body === undefined ? undefined : JSON.stringify(body), signal })
  if (response.status === 204) return undefined as T
  const text = await response.text()
  let payload
  try { payload = JSON.parse(text) }
  catch { throw new Error(response.ok ? 'The server returned an unreadable response. Refresh the acquisition history before retrying.' : `The acquisition service returned HTTP ${response.status}. Discovery may still be running; retry when the local service is available.`) }
  if (!response.ok) throw Object.assign(new Error(typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail || payload)),{status:response.status})
  return payload as T
}

export const jobArtifactUrl = (id: string, path: string) => `${getApiBase()}/dataset-jobs/${encodeURIComponent(id)}/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`
