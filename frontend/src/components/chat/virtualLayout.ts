import { canGroup, dayKey, phaseText, type DisplayMessage } from './presentation'

export type LayoutEntry={key:string;item:DisplayMessage;marker:string|null;grouped:boolean}
export function timelineEntries(items:DisplayMessage[]):LayoutEntry[]{
  const entries:LayoutEntry[]=[]
  items.forEach((item,index)=>{
    const previous=items[index-1],date=!previous||dayKey(previous.createdAt)!==dayKey(item.createdAt)
    const phase=phaseText(item),changedPhase=Boolean(phase&&(!previous||phaseText(previous)!==phase))
    if(date||changedPhase)entries.push({key:`marker:${item.key}`,item,grouped:false,
      marker:`${date?dayKey(item.createdAt):''}${date&&changedPhase?' · ':''}${changedPhase?phase:''}`})
    entries.push({key:item.key,item,marker:null,grouped:!date&&!changedPhase&&canGroup(previous,item)})
  })
  return entries
}
export function estimateHeight(entry:LayoutEntry,width:number):number{
  if(entry.marker!==null)return 42
  const chars=Math.max(12,Math.floor((width-140)/7.5))
  const lines=entry.item.text.split('\n').reduce((count,line)=>count+Math.max(1,Math.ceil(line.length/chars)),0)
  return Math.max(26,lines*22+(entry.grouped?4:40)+(entry.item.original?.reactions.length?30:0))
}
export function offsetsFor(entries:LayoutEntry[],sizes:ReadonlyMap<string,number>,width:number):number[]{
  const offsets=[0]
  for(const entry of entries)offsets.push(offsets.at(-1)!+(sizes.get(entry.key)??estimateHeight(entry,width)))
  return offsets
}
/** First row whose bottom is beyond the requested coordinate. */
export function rowAt(offsets:number[],top:number):number{
  let low=0,high=offsets.length-1
  while(low<high){const mid=(low+high)>>>1;if(offsets[mid+1]<=top)low=mid+1;else high=mid}
  return Math.min(low,Math.max(0,offsets.length-2))
}
export function windowIndices(offsets:number[],top:number,height:number,pinned:number[]=[]):number[]{
  if(offsets.length<2)return []
  // Keep short histories mounted; larger histories have roughly one viewport of overscan.
  const count=offsets.length-1
  const start=count<=100?0:rowAt(offsets,Math.max(0,top-600))
  const end=count<=100?count-1:rowAt(offsets,top+height+600)
  const indices=new Set<number>()
  for(let i=start;i<=end;i++)indices.add(i)
  for(const i of pinned)if(i>=0&&i<count)indices.add(i)
  return [...indices].sort((a,b)=>a-b)
}
