'use client'

import { fetchVectorData,getAoiFileUrl,getTerrainTileUrl,getTileUrl } from '@/lib/api/dataClient'
import { readSession,useMapView,writeSession } from '@/lib/context/MapViewContext'
import { useProject } from '@/lib/context/ProjectContext'
import { LayerStyleOptions,ManagedLayer,VectorDetail,colorForLayer,featureBounds,getDasharrayForWidth,getGeoJSONBounds,getRasterBounds,inferGeometryType } from '@/lib/map-utils'
import { TerrainSampler } from '@/lib/terrainSampler'
import { cn } from '@/lib/utils'
import { CesiumMap } from '@/lib/map/CesiumMap'
import { loadCesium } from '@/lib/map/loadCesium'
import { ChevronLeft,ChevronRight,Layers,Loader2,MapPin,Maximize2,Mountain,RefreshCw } from 'lucide-react'
import { useEffect,useMemo,useRef,useState } from 'react'
import { AttributeTable } from './AttributeTable'
import { Compass } from './Compass'
import { DatasetDigitalTwinDialog } from './DatasetDigitalTwinDialog'
import { MeasureToolPanel } from './GeoprocessingToolsPanel'
import { GoToCoordinatesBar } from './GoToCoordinatesBar'
import { LayerManager } from './LayerManager'
import { ProjectDatasetsDialog } from './ProjectDatasetsDialog'
import { StyleEditor } from './StyleEditor'

type Viewport = {center:[number,number]; zoom:number; bearing:number; pitch:number}
const DEFAULT_VIEW:Viewport = {center:[-80.5449,43.4723],zoom:2,bearing:0,pitch:0}

export function MapViewer() {
  const {currentProject, projectMetadata, datasets, isProjectLoading, completeProjectLoad, refreshProjectData, hasNewDatasets} = useProject()
  const {registerGisActions, gis} = useMapView()
  const container=useRef<HTMLDivElement>(null), dockContainer=useRef<HTMLDivElement>(null)
  const mapRef=useRef<CesiumMap|null>(null)
  const [ready,setReady]=useState(false), [mapError,setMapError]=useState<string|null>(null)
  const [viewport,setViewport]=useState(DEFAULT_VIEW)
  const [layers,setLayers]=useState<ManagedLayer[]>([])
  const [details,setDetails]=useState<Record<string,VectorDetail>>({})
  const [selected,setSelected]=useState<string|null>(null)
  const [loading,setLoading]=useState<string|null>(null)
  const [collapsed,setCollapsed]=useState(false)
  const [indexOpen,setIndexOpen]=useState(false), [twinOpen,setTwinOpen]=useState(false)
  const [indexFocus,setIndexFocus]=useState<string|null>(null)
  const [docked,setDocked]=useState(false), [dockHeight,setDockHeight]=useState(300)
  const [tableId,setTableId]=useState<string|null>(null)
  const [sort,setSort]=useState<{column:string|null;direction:'asc'|'desc'}>({column:null,direction:'asc'})
  const [styleId,setStyleId]=useState<string|null>(null)
  const [styleDraft,setStyleDraft]=useState<LayerStyleOptions>({})
  const [styles,setStyles]=useState<Record<string,LayerStyleOptions>>({})
  const [coordinatesOpen,setCoordinatesOpen]=useState(false)
  const [measure,setMeasure]=useState<'distance'|'area'|'elevation'|null>(null)
  const [terrain,setTerrain]=useState(true)
  const [terrainWarning,setTerrainWarning]=useState(false)
  const generation=useRef(0), loadedProject=useRef<string|null>(null)
  const dem=datasets?.rasters.find(d=>d.metadata?.category === 'dem' || (!d.metadata?.category && /dem|elevation/i.test(d.name)))
  const demName=dem?.name
  const demRevision=dem?.metadata?.scientific_hash||dem?.metadata?.sha256||dem?.path
  const sampler=useMemo(()=>currentProject&&demName?new TerrainSampler(getTerrainTileUrl(currentProject,demName,demRevision)):null,[currentProject,demName,demRevision])
  useEffect(()=>()=>sampler?.dispose(),[sampler])

  useEffect(()=>{
    let disposed=false
    let resize:ResizeObserver|undefined
    loadCesium().then(Cesium=>{
      if(disposed||!container.current)return
      try {
        const view=readSession<Viewport>('cesium_viewport',DEFAULT_VIEW)
        const map=new CesiumMap(Cesium,container.current,view,message=>{if(!disposed)setMapError(message)},()=>{if(!disposed)setTerrainWarning(true)})
        mapRef.current=map
        setReady(true);setMapError(null);setViewport(view)
        map.on('moveend',()=>{const v={center:map.getCenter().toArray(),zoom:map.getZoom(),bearing:map.getBearing(),pitch:map.getPitch()};setViewport(v);writeSession('cesium_viewport',v)})
        resize=new ResizeObserver(()=>map.resize());resize.observe(container.current)
      } catch(error){setMapError(error instanceof Error?error.message:'Map could not be initialized')}
    }).catch(error=>setMapError(String(error)))
    return ()=>{disposed=true;generation.current++;resize?.disconnect();mapRef.current?.remove();mapRef.current=null;setReady(false)}
  },[])

  useEffect(()=>{
    registerGisActions({openDatasetIndex:()=>{setIndexFocus(null);setIndexOpen(true)},openDatasetDigitalTwin:()=>setTwinOpen(true),openMeasureTool:setMeasure})
  },[registerGisActions])

  useEffect(()=>{
    if(mapError&&currentProject&&datasets)completeProjectLoad(currentProject,datasets,mapError)
  },[mapError,currentProject,datasets,completeProjectLoad])

  useEffect(()=>{
    const map=mapRef.current
    if(!ready||!map)return
    const run=++generation.current
    setTableId(null);setStyleId(null);setLayers([]);setDetails({});setSelected(null)
    setTerrainWarning(false)
    // Stable dataset-name order defines overlap precedence; every DEM retains its own alpha mask.
    map.setTerrainOverlays(currentProject ? [...(datasets?.rasters||[])]
      .filter(d=>d.metadata?.category==='dem'||(!d.metadata?.category&&/dem|elevation/i.test(d.name)))
      .sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:0)
      .map(d=>{const bounds=getRasterBounds(d.metadata);return {id:d.name,template:getTerrainTileUrl(currentProject,d.name,d.metadata?.scientific_hash||d.metadata?.sha256||d.path),bounds:bounds?[...bounds[0],...bounds[1]] as [number,number,number,number]:undefined}}) : [])
    for(const layer of [...(map.getStyle().layers||[])].reverse())if(layer.id.startsWith('dataset-'))map.removeLayer(layer.id)
    for(const id of Object.keys(map.getStyle().sources))if(id.startsWith('dataset-'))map.removeSource(id)
    if(!currentProject||!datasets){loadedProject.current=null;setLoading(null);return}
    const project=currentProject
    const saved=readSession<Record<string,{visible:boolean;opacity:number}>>('layer_state_'+project,{})
    let stopped=false
    const controller=new AbortController()
    const clickHandlers:Array<{id:string;handler:()=>void}>=[]
    const items=[...datasets.rasters,...datasets.vectors]
    setLoading('Loading project datasets...')
    const load=async()=>{
      const next:ManagedLayer[]=[], vectors:Record<string,VectorDetail>={}
      for(let i=0;i<items.length;i++) {
        const item=items[i], id='dataset-'+i, isAoi=/^aoi(?:_|\b)/i.test(item.name)
        const entry:ManagedLayer={id,name:item.name,type:item.type,sourceId:id,layerIds:[],visible:saved[item.name]?.visible??true,opacity:saved[item.name]?.opacity??1,order:i,path:item.path,metadata:item.metadata,status:'loading',isAoi}
        try {
          if(item.type==='raster') {
            if(stopped||run!==generation.current)return
            const bounds=getRasterBounds(item.metadata)
            map.addSource(id,{type:'raster',tiles:[getTileUrl(project,item.name)],tileSize:256,...(bounds?{bounds:[...bounds[0],...bounds[1]] as [number,number,number,number]}:{})})
            map.addLayer({id,type:'raster',source:id,paint:{'raster-opacity':entry.opacity},layout:{visibility:entry.visible?'visible':'none'}})
            entry.layerIds=[id];entry.bounds=bounds||undefined
          } else {
            const data=await fetchVectorData(project,item.name)
            if(stopped||run!==generation.current)return
            const features=(data as GeoJSON.FeatureCollection).features||[]
            map.addSource(id,{type:'geojson',data:data as GeoJSON.FeatureCollection})
            const color=isAoi?'#fbbf24':colorForLayer(item.name)
            const visibility=entry.visible?'visible':'none'
            map.addLayer({id:id+'-fill',type:'fill',source:id,filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':color,'fill-opacity':isAoi?0.04:0.25},layout:{visibility}})
            map.addLayer({id:id+'-line',type:'line',source:id,filter:['!=',['geometry-type'],'Point'],paint:{'line-color':color,'line-width':isAoi?2:1.5,'line-opacity':entry.opacity},layout:{visibility}})
            map.addLayer({id:id+'-point',type:'circle',source:id,filter:['==',['geometry-type'],'Point'],paint:{'circle-color':color,'circle-radius':4,'circle-opacity':entry.opacity},layout:{visibility}})
            entry.layerIds=[id+'-fill',id+'-line',id+'-point'];entry.bounds=getGeoJSONBounds(data)||undefined
            entry.geometryType=inferGeometryType(data);entry.featureCount=features.length
            const rows=features.map(f=>f.properties||{}), properties=[...new Set(rows.flatMap(r=>Object.keys(r)))]
            vectors[id]={properties,rows,features,sample:rows.slice(0,25)}
            for(const layerId of entry.layerIds){const handler=()=>setSelected(id);map.on('click',layerId,handler);clickHandlers.push({id:layerId,handler})}
          }
          entry.status='ready'
        }catch(error){entry.status='error';entry.message=error instanceof Error?error.message:String(error)}
        next.push(entry)
      }
      if(stopped||run!==generation.current)return
      setLayers(next);setDetails(vectors);setLoading(null)
      if(loadedProject.current!==project){
        loadedProject.current=project
        let bounds=next.find(l=>l.isAoi)?.bounds
        if(!bounds){try{const r=await fetch(getAoiFileUrl(project,'aoi.geojson'));if(r.ok)bounds=getGeoJSONBounds(await r.json())||undefined}catch{}}
        if(bounds&&!stopped)map.fitBounds(bounds,{padding:80,maxZoom:16})
      }
      if(stopped)return
      const failed=next.filter(layer=>layer.status==='error')
      if(failed.length)throw new Error(failed.map(layer=>`${layer.name}: ${layer.message}`).join('; '))
      await map.waitForReady(controller.signal)
      if(!stopped)completeProjectLoad(project,datasets)
    }
    void load().catch(error=>{
      if(!stopped){setLoading(null);completeProjectLoad(project,datasets,error instanceof Error?error.message:String(error))}
    })
    return ()=>{stopped=true;controller.abort();for(const {id,handler} of clickHandlers)map.off('click',id,handler)}
  },[ready,currentProject,datasets,completeProjectLoad])

  useEffect(()=>{
    const map=mapRef.current
    if(!ready||!map)return
    for(const layer of [...layers].sort((a,b)=>a.order-b.order))for(const id of layer.layerIds){
      const spec=map.getLayer(id);if(!spec)continue
      map.setLayoutProperty(id,'visibility',layer.visible?'visible':'none')
      const style=styles[layer.name]||{}, color=colorForLayer(layer.name)
      const opacity=style.opacity??layer.opacity
      if(spec.type==='raster')map.setPaintProperty(id,'raster-opacity',opacity)
      if(spec.type==='fill'){map.setPaintProperty(id,'fill-color',style.fillColor||color);map.setPaintProperty(id,'fill-opacity',(layer.isAoi?0.04:0.25)*opacity)}
      if(spec.type==='line'){
        map.setPaintProperty(id,'line-color',style.lineColor||(layer.isAoi?'#fbbf24':color));map.setPaintProperty(id,'line-width',style.lineWidth??2);map.setPaintProperty(id,'line-opacity',opacity)
        if(style.lineStyle)map.setPaintProperty(id,'line-dasharray',getDasharrayForWidth(style.lineStyle,style.lineWidth??2))
      }
      if(spec.type==='circle'){map.setPaintProperty(id,'circle-color',style.pointColor||color);map.setPaintProperty(id,'circle-radius',style.pointSize??4);map.setPaintProperty(id,'circle-opacity',opacity)}
      map.moveLayer(id)
    }
    if(currentProject&&layers.length)writeSession('layer_state_'+currentProject,Object.fromEntries(layers.map(l=>[l.name,{visible:l.visible,opacity:l.opacity}])))
  },[layers,styles,currentProject,ready])

  const selectLayer=(id:string)=>{setSelected(id);const bounds=layers.find(l=>l.id===id)?.bounds;if(bounds)mapRef.current?.fitBounds(bounds,{padding:80,maxZoom:17})}
  const reorder=(draggedId:string,targetId:string,position:'above'|'below')=>setLayers(prev=>{
    const ordered=[...prev].sort((a,b)=>b.order-a.order), moving=ordered.find(l=>l.id===draggedId)
    if(!moving)return prev
    const rest=ordered.filter(l=>l.id!==draggedId), index=rest.findIndex(l=>l.id===targetId)
    rest.splice(Math.max(0,index+(position==='below'?1:0)),0,moving)
    return rest.map((l,i)=>({...l,order:rest.length-i}))
  })
  const resizeDock=(event:React.MouseEvent)=>{
    event.preventDefault();const start=event.clientY, original=dockHeight
    const move=(e:MouseEvent)=>setDockHeight(Math.max(180,Math.min(window.innerHeight-160,original+start-e.clientY)))
    const up=()=>{document.removeEventListener('mousemove',move);document.removeEventListener('mouseup',up)}
    document.addEventListener('mousemove',move);document.addEventListener('mouseup',up)
  }
  const tableLayer=layers.find(l=>l.id===tableId), table=tableId?details[tableId]:null
  const sortedRows=useMemo(()=>{
    if(!table)return []
    const pairs=table.rows.map((row,i)=>({row,feature:table.features[i]}))
    if(sort.column){const key=sort.column;pairs.sort((a,b)=>String(a.row[key]??'').localeCompare(String(b.row[key]??''),undefined,{numeric:true})*(sort.direction==='asc'?1:-1))}
    return pairs
  },[table,sort])
  const styleLayer=layers.find(l=>l.id===styleId)
  const fitProject=()=>{const bounds=layers.find(l=>l.isAoi)?.bounds||layers.find(l=>l.bounds)?.bounds;if(bounds)mapRef.current?.fitBounds(bounds,{padding:80,maxZoom:16})}
  const toggleTerrain=()=>{
    const map=mapRef.current;if(!map)return
    setTerrainWarning(false);map.setTerrainEnabled(!terrain);map.easeTo({pitch:terrain?0:55});setTerrain(!terrain)
  }
  return <div className="flex w-full h-full">
    <div ref={dockContainer} className="relative flex-1 h-full min-w-0 bg-[#154360]">
      <div ref={container} className="absolute inset-0"/>
      {((!ready&&!isProjectLoading)||mapError)&&<div className="absolute inset-0 z-40 flex items-center justify-center bg-black/40 backdrop-blur-md"><div className="text-center text-white"><Loader2 className="w-10 h-10 mx-auto mb-3 animate-spin text-primary"/>{mapError||'Loading map...'}</div></div>}
      <div className="absolute top-4 left-4 z-10 space-y-3">
        <div className="bg-black/60 backdrop-blur-md border border-white/10 p-4 rounded-sm shadow-[0_0_20px_-5px_rgba(0,0,0,0.5)] w-[200px] xl:w-[240px] relative overflow-hidden">
          <div className="absolute top-0 left-0 w-2 h-2 border-t border-l border-white/30"/><div className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-white/30"/>
          <div className="flex items-center gap-2 border-b border-white/10 pb-2"><Layers className="w-3 h-3 text-primary"/><span className="text-xs font-bold text-white uppercase tracking-wider">Hybrid Satellite</span></div>
          <div className="grid grid-cols-2 gap-3 mt-3 text-[10px] font-mono uppercase"><div className="text-white/40">Zoom<div className="text-white mt-1">{viewport.zoom.toFixed(2)}</div></div><div className="text-white/40">CRS<div className="text-white mt-1">EPSG:{projectMetadata?.crs?.epsg||4326}</div></div><div className="text-white/40">Latitude<div className="text-white mt-1">{viewport.center[1].toFixed(5)}</div></div><div className="text-white/40">Longitude<div className="text-white mt-1">{viewport.center[0].toFixed(5)}</div></div></div>
          <div className="flex gap-2 mt-3"><button title="Fit project" onClick={fitProject} className="p-2 border border-white/10 hover:text-primary"><Maximize2 className="w-4 h-4"/></button><button title="Go to coordinates" onClick={()=>setCoordinatesOpen(true)} className="p-2 border border-white/10 hover:text-primary"><MapPin className="w-4 h-4"/></button><button title="Toggle terrain" aria-pressed={terrain} onClick={toggleTerrain} className={cn('p-2 border border-white/10 disabled:opacity-30',terrain&&'text-primary')}><Mountain className="w-4 h-4"/></button></div>
        </div>
        {hasNewDatasets&&<button onClick={()=>void refreshProjectData()} className="bg-primary/90 text-black p-3 text-xs">New datasets available — load layers</button>}
      </div>
      {terrainWarning&&terrain&&<div role="status" className="absolute bottom-14 left-14 z-20 max-w-sm bg-black/70 backdrop-blur-md border border-white/10 px-3 py-2 text-xs text-amber-200">Some terrain tiles are unavailable. Background terrain or a flat surface is shown in those areas. Toggle terrain to retry.</div>}
      <Compass map={mapRef.current} className="bottom-16 left-4"/>
      <button title="Refresh datasets" onClick={()=>void refreshProjectData()} className="absolute bottom-3 left-3 z-20 h-9 w-9 bg-black/60 backdrop-blur-sm border border-white/10 rounded-sm flex items-center justify-center hover:text-primary"><RefreshCw className="w-4 h-4"/></button>
      {loading&&<div className="absolute bottom-3 left-14 z-20 bg-black/70 p-2 text-xs text-white/70 flex gap-2"><Loader2 className="w-4 h-4 animate-spin"/>{loading}</div>}
      <ProjectDatasetsDialog open={indexOpen} onClose={()=>setIndexOpen(false)} onToggleDock={()=>setDocked(!docked)} isDocked={docked} dockHeight={dockHeight} onResizeStart={resizeDock} dockContainerRef={dockContainer} projectName={currentProject} datasets={datasets} loadedLayers={layers} focusDatasetKey={indexFocus}/>
      <DatasetDigitalTwinDialog open={twinOpen} onClose={()=>setTwinOpen(false)} projectName={currentProject}/>
      {tableLayer&&table&&<AttributeTable layer={tableLayer} details={table} sortedRows={sortedRows} sortConfig={sort} isDocked={docked} dockHeight={dockHeight} onClose={()=>setTableId(null)} onToggleDock={()=>setDocked(!docked)} onSort={column=>setSort(p=>({column,direction:p.column===column&&p.direction==='asc'?'desc':'asc'}))} onRowDoubleClick={feature=>{const b=featureBounds(feature);if(b)mapRef.current?.fitBounds(b,{padding:80,maxZoom:18})}} onResizeStart={resizeDock} dockContainerRef={dockContainer}/ >}
      {styleLayer&&<StyleEditor layer={styleLayer} styleDraft={styleDraft} onChange={setStyleDraft} onApply={()=>{setStyles(p=>({...p,[styleLayer.name]:styleDraft}));setStyleId(null)}} onReset={()=>{setStyles(p=>({...p,[styleLayer.name]:{}}));setStyleId(null)}} onCancel={()=>setStyleId(null)}/>}
      <GoToCoordinatesBar open={coordinatesOpen} seed={{lng:viewport.center[0],lat:viewport.center[1]}} onClose={()=>setCoordinatesOpen(false)} onGoTo={(lng,lat)=>mapRef.current?.flyTo({center:[lng,lat],zoom:15})}/>
      {measure&&<MeasureToolPanel tool={measure} map={mapRef.current} terrainSampler={sampler} demAvailable={Boolean(dem)} active onActivate={()=>{}} onClose={()=>setMeasure(null)} initialPosition={{x:260,y:100}}/>}
    </div>
    <div className={cn('relative h-full bg-[#07070a] border-l border-white/[0.06] flex flex-col overflow-visible shrink-0 transition-all duration-300',collapsed?'w-11':'w-[310px]')}>
      <button title={collapsed?'Expand sidebar':'Collapse sidebar'} onClick={()=>setCollapsed(!collapsed)} className="absolute -left-4 top-1/2 -translate-y-1/2 w-4 h-8 p-0 bg-[#0a0a0a] border border-white/10 border-r-0 text-white/40 hover:text-primary z-50 [clip-path:polygon(0_50%,100%_0,100%_100%)]">{collapsed?<ChevronLeft className="w-3 h-3"/>:<ChevronRight className="w-3 h-3"/>}</button>
      {!collapsed&&<div className="border-b border-white/10 flex items-center justify-end shrink-0 px-4 py-3"><button onClick={()=>gis.openDatasetIndex()} className="h-6 px-2 border border-blue-500/30 bg-blue-500/10 text-[9px] text-blue-300 font-mono">INDEX</button></div>}
      {!collapsed&&<div className="flex-1 overflow-hidden"><LayerManager layers={layers} selectedLayerId={selected} loadingMessage={loading} currentProject={currentProject} vectorDetails={details} onSelectLayer={selectLayer} onToggleVisibility={id=>setLayers(p=>p.map(l=>l.id===id?{...l,visible:!l.visible}:l))} onOpacityChange={(id,opacity)=>setLayers(p=>p.map(l=>l.id===id?{...l,opacity}:l))} onMoveLayer={(id,direction)=>{const ordered=[...layers].sort((a,b)=>b.order-a.order),i=ordered.findIndex(l=>l.id===id),target=ordered[i+(direction==='up'?-1:1)];if(target)reorder(id,target.id,direction==='up'?'above':'below')}} onReorderLayers={reorder} onOpenTable={setTableId} onOpenStyle={id=>{const l=layers.find(l=>l.id===id);setStyleDraft(l?styles[l.name]||{}:{});setStyleId(id)}} onOpenDatasetIndexForLayer={id=>{const l=layers.find(l=>l.id===id);setIndexFocus(l?`${l.type}:${l.name}`:null);setIndexOpen(true)}}/></div>}
    </div>
  </div>
}
