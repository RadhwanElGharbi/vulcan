'use client'

import { useEffect, useRef, useState } from 'react'
import { Folder, Loader2 } from 'lucide-react'
import { fetchWorkspace, pickWorkspaceDirectory, saveWorkspace, type Workspace } from '@/lib/api/workspace'

export function WorkspaceDirectory({ onChanged }: { onChanged: () => void }) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    fetchWorkspace().then(value => { if (mounted.current) setWorkspace(value) }).catch(error => { if (mounted.current) setError(error.message) })
    return () => { mounted.current = false }
  }, [])

  const choose = async () => {
    if (busy) return
    setBusy(true); setError(null)
    try {
      const result = window.electron?.pickLocalCacheDirectory
        ? await window.electron.pickLocalCacheDirectory()
        : await pickWorkspaceDirectory()
      if (result.cancelled || !result.directory) return
      const value = await saveWorkspace(result.directory)
      if (mounted.current) { setWorkspace(value); onChanged() }
    } catch (error) {
      if (mounted.current) setError(error instanceof Error ? error.message : String(error))
    } finally { if (mounted.current) setBusy(false) }
  }

  return <>
    <div className="flex items-center gap-3 px-8 py-3 border-b border-white/10 bg-white/[0.02]">
      <Folder className="w-4 h-4 shrink-0 text-white/40" />
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-wider text-white/40">{workspace?.mode === 'temporary_cloud' ? 'Temporary workspace' : 'Save new projects in'}</div>
        <div className="text-xs text-white/75 truncate mt-1" title={workspace?.mode === 'temporary_cloud' ? undefined : workspace?.directory}>
          {workspace?.mode === 'temporary_cloud'
            ? `Download your project before ${workspace.expires_at ? new Date(workspace.expires_at).toLocaleString() : 'the workspace expires'}.`
            : workspace?.directory || 'Loading directory...'}
        </div>
      </div>
      {workspace?.mode !== 'temporary_cloud' && <button disabled={busy} onClick={() => void choose()} className="flex items-center gap-2 shrink-0 px-3 py-2 text-xs border border-white/15 hover:border-primary/50 hover:text-primary disabled:opacity-50">
        {busy && <Loader2 className="w-3 h-3 animate-spin" />}{busy ? 'Choose a folder in the system dialog...' : 'Choose directory'}
      </button>}
    </div>
    {error && <p role="alert" className="px-8 py-2 text-xs text-red-300">{error}</p>}
  </>
}
