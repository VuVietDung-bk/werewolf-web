export function factionClass(label: string): string {
  const key = label.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/đ/g, 'd').trim()
  if (['soi', 'ma soi', 'phe soi', 'wolf', 'wolves'].includes(key)) return 'faction-wolf'
  if (['lang', 'dan', 'dan lang', 'phe dan', 'phe lang', 'village'].includes(key)) return 'faction-village'
  if (['trung lap', 'phe trung lap', 'neutral'].includes(key)) return 'faction-neutral'
  return 'faction-custom'
}
