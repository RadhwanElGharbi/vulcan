'use client'
import React,{ createContext,useCallback,useContext,useRef,useState } from 'react'
export function readSession<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback
  try { return JSON.parse(localStorage.getItem('zeus_session_' + key) || 'null') ?? fallback } catch { return fallback }
}
export function writeSession<T>(key: string, value: T) {
  if (typeof window !== 'undefined') localStorage.setItem('zeus_session_' + key, JSON.stringify(value))
}
type GisActions = {
  openFetchDatasets: () => void
  openDatasetIndex: () => void
  openDatasetDigitalTwin: () => void
  openMeasureTool: (tool: 'distance' | 'area' | 'elevation') => void
}
type Value = {
  mapUiIdle: boolean; setMapUiIdle: (b: boolean) => void
  registerGisActions: (a: Partial<GisActions>) => void; gis: GisActions
}
const Context = createContext<Value | null>(null)
export function MapViewProvider({children}: {children: React.ReactNode}) {
  const [mapUiIdle, setMapUiIdle] = useState(false)
  const actions = useRef<GisActions>({openFetchDatasets:()=>{}, openDatasetIndex:()=>{}, openDatasetDigitalTwin:()=>{}, openMeasureTool:()=>{}})
  const registerGisActions = useCallback((a: Partial<GisActions>) => { Object.assign(actions.current, a) }, [])
  return <Context.Provider value={{mapUiIdle, setMapUiIdle, registerGisActions, gis:actions.current}}>{children}</Context.Provider>
}
export function useMapView() { const v = useContext(Context); if (!v) throw new Error('MapViewProvider is required'); return v }
