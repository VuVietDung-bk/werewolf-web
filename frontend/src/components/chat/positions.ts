/** Only source IDs and viewport geometry persist; never message text or access tokens. */
export type ChatAnchor = { key: string | null; offset: number; top: number; atEnd: boolean }
export const POSITION_KEY='ma-soi-chat-positions:v2'
export const chatPositions=new Map<string,ChatAnchor>()
let hydrated=false
let pending:ReturnType<typeof setTimeout>|null=null
let generation=0
export function positionGeneration(){return generation}
const sourceKey=/^(chat|private_message|role_request):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function valid(value:unknown):value is ChatAnchor {
  if(!value||typeof value!=='object')return false
  const anchor=value as ChatAnchor
  return (anchor.key===null||typeof anchor.key==='string'&&sourceKey.test(anchor.key))&&
    Number.isFinite(anchor.offset)&&Math.abs(anchor.offset)<=1e7&&Number.isFinite(anchor.top)&&anchor.top>=0&&anchor.top<=1e9&&typeof anchor.atEnd==='boolean'
}
function hydrate(){
  if(hydrated)return
  hydrated=true
  try{
    const entries:unknown=JSON.parse(sessionStorage.getItem(POSITION_KEY)??'[]')
    if(Array.isArray(entries))for(const entry of entries.slice(-128)){
      if(Array.isArray(entry)&&typeof entry[0]==='string'&&entry[0].length<=2048&&valid(entry[1]))chatPositions.set(entry[0],entry[1])
    }
  }catch{/* Position persistence is optional. */}
}
export function positionFor(scope:string):ChatAnchor|undefined{hydrate();return chatPositions.get(scope)}
export function flushPositions(){
  if(pending!==null){clearTimeout(pending);pending=null}
  try{if(chatPositions.size)sessionStorage.setItem(POSITION_KEY,JSON.stringify([...chatPositions]));else sessionStorage.removeItem(POSITION_KEY)}catch{/* Keep the in-memory position. */}
}
export function rememberPosition(scope:string,anchor:ChatAnchor,expectedGeneration=generation){
  if(expectedGeneration!==generation)return
  hydrate()
  if(!valid(anchor))return
  chatPositions.delete(scope);chatPositions.set(scope,{...anchor})
  if(chatPositions.size>128)chatPositions.delete(chatPositions.keys().next().value!)
  if(pending===null)pending=setTimeout(flushPositions,150)
}
export function forgetPosition(scope:string){hydrate();chatPositions.delete(scope);flushPositions()}
export function clearPositions(){
  generation++
  if(pending!==null){clearTimeout(pending);pending=null}
  chatPositions.clear();hydrated=true
  try{sessionStorage.removeItem(POSITION_KEY)}catch{/* Optional storage. */}
}
