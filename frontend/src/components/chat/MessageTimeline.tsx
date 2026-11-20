import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { DisplayMessage } from './presentation'
import { chatIsVisible, readableRow } from './visibleRead'
import { flushPositions, positionFor, positionGeneration, rememberPosition, type ChatAnchor } from './positions'
import { offsetsFor, rowAt, timelineEntries, windowIndices } from './virtualLayout'

const emptyAnchor:ChatAnchor={key:null,offset:0,top:0,atEnd:true}
export default function MessageTimeline({ items, scope, loading, loadingOlder, hasOlder, loadOlder, followSentVersion, renderRow, onVisibleItem,
  hasLater=false,loadingNewer=false,loadNewer,jumpLatest,pinnedKey }: {
  items: DisplayMessage[]; scope: string; loading: boolean; loadingOlder: boolean; hasOlder: boolean
  loadOlder: () => void; followSentVersion: number
  renderRow: (item: DisplayMessage, grouped: boolean) => ReactNode
  onVisibleItem:(item:string,receipt?:string|null)=>void
  hasLater?:boolean;loadingNewer?:boolean;loadNewer?:()=>void;jumpLatest?:()=>void;pinnedKey?:string|null
}) {
  const scroll=useRef<HTMLDivElement>(null),list=useRef<HTMLOListElement>(null)
  const anchor=useRef<ChatAnchor>({...positionFor(scope)??emptyAnchor})
  const positionEpoch=useRef(positionGeneration())
  const remember=(value:ChatAnchor)=>rememberPosition(scope,value,positionEpoch.current)
  const sizes=useRef(new Map<string,number>())
  const [measureVersion,setMeasureVersion]=useState(0)
  const [view,setView]=useState({top:0,height:800,width:800})
  const [focusedKey,setFocusedKey]=useState<string|null>(null)
  const [newCount,setNewCount]=useState(0)
  const previous=useRef<{last:string|null;force:number;ready:boolean;later:boolean}>({last:null,force:followSentVersion,ready:false,later:hasLater})
  const priorLayout=useRef<{keys:string[];offsets:number[]}>({keys:[],offsets:[0]})
  const visibleCallback=useRef(onVisibleItem);visibleCallback.current=onVisibleItem
  const entries=useMemo(()=>timelineEntries(items),[items])
  const indicesByKey=useMemo(()=>new Map(entries.map((entry,index)=>[entry.key,index])),[entries])
  useEffect(()=>{for(const key of sizes.current.keys())if(!indicesByKey.has(key))sizes.current.delete(key)},[indicesByKey])
  const offsets=useMemo(()=>offsetsFor(entries,sizes.current,view.width),[entries,measureVersion,view.width])
  const rendered=windowIndices(offsets,view.top,view.height,[focusedKey,pinnedKey].flatMap(key=>key&&indicesByKey.has(key)?[indicesByKey.get(key)!]:[]))
  const renderedSignature=rendered.join(',')
  const programmatic=useRef(false),frame=useRef<number|null>(null)
  function syncView(){
    const root=scroll.current
    if(root)setView(current=>current.top===root.scrollTop&&current.height===root.clientHeight&&current.width===root.clientWidth?current:
      {top:root.scrollTop,height:root.clientHeight,width:root.clientWidth})
  }
  function listTop(){
    const root=scroll.current,content=list.current
    return root&&content?content.getBoundingClientRect().top-root.getBoundingClientRect().top+root.scrollTop+parseFloat(getComputedStyle(content).paddingTop||'0'):0
  }
  function capture(){
    const root=scroll.current
    if(!root||loading)return
    const top=root.getBoundingClientRect().top
    const first=Array.from(root.querySelectorAll<HTMLElement>('[data-item-key]')).find(row=>row.getBoundingClientRect().bottom>top&&row.getBoundingClientRect().top<top+root.clientHeight)
    let key=first?.dataset.itemKey??null,offset=first?first.getBoundingClientRect().top-top:0
    if(!key&&entries.length){
      let index=rowAt(offsets,Math.max(0,root.scrollTop-listTop()))
      if(entries[index]?.marker!==null)index=Math.min(entries.length-1,index+1)
      key=entries[index]?.item.key??null;offset=listTop()+offsets[index]-root.scrollTop
    }
    anchor.current={key,offset,top:root.scrollTop,atEnd:!hasLater&&root.scrollHeight-root.scrollTop-root.clientHeight<=80}
    remember(anchor.current)
  }
  function restore(value:ChatAnchor){
    const root=scroll.current
    if(!root)return
    programmatic.current=true
    if(frame.current!==null)cancelAnimationFrame(frame.current)
    if(value.atEnd&&!hasLater)root.scrollTop=root.scrollHeight
    else{
      const index=value.key?indicesByKey.get(value.key):undefined
      root.scrollTop=index===undefined?value.top:listTop()+offsets[index]-value.offset
    }
    syncView()
    frame.current=requestAnimationFrame(()=>{programmatic.current=false;frame.current=null})
  }
  function bottom(){
    if(hasLater&&jumpLatest){anchor.current={...emptyAnchor};remember(anchor.current);jumpLatest();return}
    anchor.current={...emptyAnchor};restore(anchor.current);setNewCount(0);remember(anchor.current)
  }
  useLayoutEffect(()=>{
    if(loading)return
    const old=previous.current,lastIndex=items.findIndex(item=>item.key===old.last)
    const appended=old.ready&&lastIndex>=0?items.length-lastIndex-1:0
    if(old.force!==followSentVersion){bottom()}
    else{
      if(!anchor.current.atEnd&&anchor.current.key&&!indicesByKey.has(anchor.current.key)){
        const prior=priorLayout.current,index=prior.keys.indexOf(anchor.current.key)
        const following=index>=0?prior.keys.findIndex((key,i)=>i>index&&!key.startsWith('marker:')&&indicesByKey.has(key)):-1
        if(following>=0)anchor.current={...anchor.current,key:prior.keys[following],offset:anchor.current.offset+prior.offsets[following]-prior.offsets[index]}
        else{
          const nearest=rowAt(offsets,Math.max(0,anchor.current.top-listTop()))
          anchor.current={...anchor.current,key:entries[nearest]?.item.key??null,offset:0}
        }
      }
      restore(anchor.current)
      if(anchor.current.atEnd)setNewCount(0)
      else if(appended>0&&!old.later)setNewCount(count=>count+appended)
    }
    previous.current={last:items.at(-1)?.key??null,force:followSentVersion,ready:true,later:hasLater}
    priorLayout.current={keys:entries.map(entry=>entry.key),offsets}
  },[offsets,items,loading,followSentVersion,hasOlder,hasLater,view.height])
  useLayoutEffect(()=>{
    const root=scroll.current,content=list.current
    if(!root||!content||loading)return
    const observer=new ResizeObserver(records=>{
      let changed=false
      for(const record of records){
        const element=record.target as HTMLElement,key=element.dataset.itemKey??element.dataset.layoutKey
        const height=element.getBoundingClientRect().height
        if(key&&height>0&&Math.abs((sizes.current.get(key)??-1)-height)>.5){sizes.current.set(key,height);changed=true}
        if(element===root&&root.clientWidth!==view.width){sizes.current.clear();changed=true}
      }
      if(changed)setMeasureVersion(value=>value+1)
      syncView()
    })
    observer.observe(root)
    for(const row of content.querySelectorAll<HTMLElement>(':scope > [data-item-key], :scope > [data-layout-key]'))observer.observe(row)
    return()=>observer.disconnect()
  },[renderedSignature,items,loading,view.width])
  useEffect(()=>{
    const root=scroll.current
    if(!root||loading)return
    const rows=Array.from(root.querySelectorAll<HTMLElement>('[data-item-key]'))
    const visible=new Set<HTMLElement>(),order=new Map(items.map((item,index)=>[item.key,index]))
    let active=true,timer:ReturnType<typeof setTimeout>|null=null
    const evaluate=()=>{
      timer=null
      if(!active||!chatIsVisible())return
      const rect=root.getBoundingClientRect(),top=Math.max(0,rect.top),bottom=Math.min(innerHeight,rect.bottom)
      const latest=[...visible].filter(row=>{const box=row.getBoundingClientRect();return readableRow(box.top,box.bottom,top,bottom)})
        .sort((a,b)=>(order.get(a.dataset.itemKey!)??-1)-(order.get(b.dataset.itemKey!)??-1)).at(-1)
      if(latest?.dataset.itemKey)visibleCallback.current(latest.dataset.itemKey,items[order.get(latest.dataset.itemKey)!]?.original?.read_receipt)
    }
    const schedule=()=>{if(timer)clearTimeout(timer);timer=setTimeout(evaluate,120)}
    const observer=new IntersectionObserver(records=>{for(const record of records){if(record.isIntersecting)visible.add(record.target as HTMLElement);else visible.delete(record.target as HTMLElement)}schedule()},
      {root,threshold:[0,.5,1]})
    for(const row of rows)observer.observe(row)
    const dialogs=new MutationObserver(schedule);dialogs.observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']})
    root.addEventListener('scroll',schedule);document.addEventListener('visibilitychange',schedule);window.addEventListener('focus',schedule)
    return()=>{active=false;observer.disconnect();dialogs.disconnect();if(timer)clearTimeout(timer)
      root.removeEventListener('scroll',schedule);document.removeEventListener('visibilitychange',schedule);window.removeEventListener('focus',schedule)}
  },[items,loading,renderedSignature])
  useEffect(()=>{
    const flush=()=>flushPositions()
    window.addEventListener('pagehide',flush);document.addEventListener('visibilitychange',flush)
    return()=>{window.removeEventListener('pagehide',flush);document.removeEventListener('visibilitychange',flush)}
  },[])
  useLayoutEffect(()=>()=>{if(frame.current!==null)cancelAnimationFrame(frame.current);remember(anchor.current);flushPositions()},[scope])
  let previousBottom=0
  return <div className="u3-timeline-wrap">
    <div className="u3-timeline" ref={scroll} role="region" aria-label="Lịch sử cuộc trò chuyện" tabIndex={0}
      data-loaded-count={items.length} data-rendered-count={rendered.length}
      onFocusCapture={event=>setFocusedKey((event.target as HTMLElement).closest<HTMLElement>('[data-item-key]')?.dataset.itemKey??null)}
      onBlurCapture={()=>queueMicrotask(()=>setFocusedKey(scroll.current?.contains(document.activeElement)?(document.activeElement as HTMLElement).closest<HTMLElement>('[data-item-key]')?.dataset.itemKey??null:null))}
      onScroll={()=>{if(!programmatic.current)capture();syncView();if(anchor.current.atEnd)setNewCount(0)}}>
      {hasOlder&&<button type="button" className="u3-load-older" disabled={loadingOlder} onClick={loadOlder}>{loadingOlder?'Đang tải…':'Xem tin cũ hơn'}</button>}
      {loading?<div className="room-skeleton" role="status" aria-label="Đang tải tin nhắn"><span/><span/><span/></div>:<>
        <ol ref={list} className="chat-message-list u3-message-list">
          {rendered.map(index=>{
            const entry=entries[index],gap=offsets[index]-previousBottom;previousBottom=offsets[index+1]
            return <Fragment key={entry.key}>
              {gap>0&&<li className="u3-spacer" aria-hidden="true" style={{height:gap}}/>}
              {entry.marker!==null?<li className="u3-marker" data-layout-key={entry.key}><span>{entry.marker}</span></li>:renderRow(entry.item,entry.grouped)}
            </Fragment>
          })}
          {offsets.at(-1)!>previousBottom&&<li className="u3-spacer" aria-hidden="true" style={{height:offsets.at(-1)!-previousBottom}}/>}
        </ol>
        {!items.length&&<p className="u3-empty">Chưa có tin nhắn trong kênh này.</p>}
        {hasLater&&<button className="u3-load-older u3-load-newer" type="button" disabled={loadingNewer} onClick={loadNewer}>{loadingNewer?'Đang tải…':'Xem tin tiếp theo'}</button>}
      </>}
    </div>
    {(newCount>0||hasLater)&&<button type="button" className="u3-new-messages" onClick={bottom}>{newCount>0?`${newCount} tin mới · Đến cuối ↓`:'Đến tin mới nhất ↓'}</button>}
  </div>
}
