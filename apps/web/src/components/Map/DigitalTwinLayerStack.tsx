'use client'

import { useState, type ReactNode, type CSSProperties } from 'react'
import styles from './DigitalTwinLayerStack.module.css'

type Group = {name:string; categories:string[][]}
const colors=['163,207,221','196,204,218','215,193,159','157,203,178','153,195,219','192,164,184']
const subtitles=['Climate & weather','Transport & utilities','Parcels, zoning & population','Land cover & satellite context','Elevation, rivers & wetlands','Soils & seismic hazard']
// Decorative cartographic linework; this is navigation, not a data preview.
function Pattern({index}:{index:number}) {
  if(index===0) return <>{[0,1,2,3].map(n=><path key={n} d={`M ${220+n*23} ${48+n*15} C ${300+n*20} ${10+n*17}, ${365+n*25} ${88+n*12}, ${510+n*15} ${40+n*16}`}/>)}</>
  if(index===1) return <><path d="M145 65L300 62 425 25M260 105L370 66 600 58M350 125L480 78 565 100M370 66L330 48 430 17"/><path d="M190 65L296 66 426 30M263 110L373 71 600 63"/></>
  if(index===2) return <>{[0,1,2,3].map(n=><path key={n} d={`M${210+n*67} ${55-n*5}l65 -15 65 18 -65 20z M${240+n*55} ${79-n*3}l48 -12 48 15 -48 15z`}/>)}</>
  if(index===3) return <><path d="M160 65Q210 25 300 50T450 40 605 65M240 90Q270 55 340 83T500 73 570 90M335 111Q340 80 410 104T490 97"/><path d="M290 45l70 80M380 30l70 78M455 30l75 61" opacity=".5"/></>
  if(index===4) return <>{[0,1,2,3,4].map(n=><ellipse key={n} cx="395" cy="68" rx={45+n*31} ry={9+n*6} transform="rotate(-5 395 68)"/>)}</>
  return <><path d="M165 66l95 -18 76 21 68 -37 97 38 110 -7M230 91l81 -21 83 28 58 -22 80 18M290 112l105 -21 75 24"/><path d="M397 25l-32 28 36 23 -24 25 36 29"/></>
}

export function DigitalTwinLayerStack({groups,present,selectedCount,renderCategories,onGroupChange}:{groups:Group[];present:Set<string>;selectedCount:(group:Group)=>number;renderCategories:(group:Group)=>ReactNode;onGroupChange:()=>void}) {
  const [active,setActive]=useState<string|null>(null)
  const selected=groups.find(group=>group.name===active)
  function choose(name:string){setActive(old=>old===name?null:name);onGroupChange()}
  return <div className={styles.layout}>
    <div className={styles.scene}>
      <div className={styles.intro}><span>Explore the layers</span><p>Select a layer to choose its datasets</p></div>
      <svg className={styles.stack} viewBox="0 0 820 600" role="group" aria-label="Digital Twin category layers">
        <g className={styles.guides} aria-hidden="true"><path d="M80 80V460M410 12V392M740 80V460M410 160V540"/></g>
        {[...groups].reverse().map(group=>{
          const index=groups.indexOf(group),chosen=selectedCount(group),count=group.categories.filter(([id])=>present.has(id)).length
          return <g key={group.name} transform={`translate(0 ${index*76})`}>
            <g className={`${styles.tile} ${active===group.name?styles.active:''}`} style={{'--tile-color':colors[index]} as CSSProperties}>
              <title>{group.name} - {subtitles[index]}</title>
              <path className={styles.edge} d="M80 80L410 160 740 80V85L410 165 80 85Z"/>
              <path className={styles.surface} d="M80 80L410 12 740 80 410 160Z"/>
              <g className={styles.pattern} aria-hidden="true"><Pattern index={index}/></g>
              <path className={styles.rim} d="M80 80L410 160 740 80"/>
              <g transform="translate(407 136) rotate(-13.6)"><text className={styles.name}>{group.name.toUpperCase()}</text><text y="17" className={styles.meta}>{chosen ? `${chosen} selected` : `${count}/${group.categories.length} in project`}</text></g>
              <circle cx="111" cy="80" r="2.5" className={styles.dot}/>
              <path role="button" tabIndex={0} aria-label={`${group.name}: ${chosen} selected datasets`} aria-expanded={active===group.name} aria-controls={active===group.name?'twin-layer-categories':undefined} onClick={()=>choose(group.name)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose(group.name)}}} className={styles.hit} d="M250 122L410 158 690 95 525 82Z"/>
            </g>
          </g>
        })}
      </svg>
      <p className={styles.caption}>Six layers. One connected view.</p>
    </div>
    {selected && <section id="twin-layer-categories" aria-label={`${selected.name} categories`} tabIndex={0} className={styles.details}>
      <header><span>Explore layer</span><button aria-label="Close layer categories" onClick={()=>{setActive(null);onGroupChange()}}>×</button><h3>{selected.name}</h3><p>{subtitles[groups.indexOf(selected)]}</p></header>
      {renderCategories(selected)}
    </section>}
  </div>
}
