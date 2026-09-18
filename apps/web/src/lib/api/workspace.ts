import { getApiBase } from './dataClient'

export type Workspace = { directory: string; directories: string[]; mode?: 'temporary_cloud'; expires_at?: string; storage_limit_bytes?: number }
export type DirectoryListing = { directory: string; parent: string | null; shortcuts: string[]; folders: { name: string; path: string }[]; truncated: boolean }

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  if (process.env.NEXT_PUBLIC_WEB_PREVIEW === '1') throw new Error('Cloud storage is not connected yet. You can explore the globe and draw an AOI.')
  const response = await fetch(`${getApiBase()}${path}`, { cache: 'no-store', ...options })
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not access the project directory.')
  return body
}
export const fetchWorkspace = () => request<Workspace>('/workspace')
export const pickWorkspaceDirectory = () => request<{ directory: string | null; cancelled: boolean }>('/workspace/pick-directory', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
export const browseDirectories = (path?: string) => request<DirectoryListing>(`/workspace/directories${path ? `?path=${encodeURIComponent(path)}` : ''}`)
export const saveWorkspace = (directory: string) => request<Workspace>('/workspace', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ directory }) })
