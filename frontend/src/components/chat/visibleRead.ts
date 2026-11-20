/** A short row must be at least half visible; a long row must reveal its bottom. */
export function readableRow(top:number,bottom:number,viewTop:number,viewBottom:number):boolean {
  const height=bottom-top,viewHeight=viewBottom-viewTop
  if(height<=0||viewHeight<=0)return false
  const overlap=Math.min(bottom,viewBottom)-Math.max(top,viewTop)
  if(overlap<=0)return false
  return height>viewHeight?bottom<=viewBottom:overlap>=height/2
}
export function chatIsVisible():boolean {
  return document.visibilityState==='visible'&&!document.querySelector('dialog[open]')
}
