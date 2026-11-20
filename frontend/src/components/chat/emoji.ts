export type Emoji = { value: string; name: string; aliases: string[] }
// Native Unicode only. Users can also paste any Unicode emoji into the composer.
export const EMOJI: Emoji[] = [
  ['🙂','Mỉm cười','smile smiling mim cuoi'], ['😀','Vui vẻ','grin happy vui'],
  ['😂','Cười ra nước mắt','joy laugh cuoi'], ['🤣','Cười lớn','rofl'],
  ['😊','Vui ngại ngùng','blush'], ['😎','Ngầu','cool sunglasses ngau'],
  ['🥰','Yêu mến','love smiling_hearts yeu'], ['😍','Yêu thích','heart_eyes'],
  ['😢','Buồn','cry sad buon'], ['😭','Khóc','sob khoc'],
  ['😱','Bất ngờ','scream bat ngo'], ['😴','Buồn ngủ','sleep ngu'],
  ['🤔','Suy nghĩ','thinking suy nghi'], ['🧐','Xem xét','monocle xem xet'],
  ['😅','Cười nhẹ','sweat_smile'], ['😐','Bình thản','neutral binh than'],
  ['🙃','Cười ngược','upside_down'], ['🤫','Giữ bí mật','shushing bi mat'],
  ['🤐','Im lặng','zipper_mouth im lang'], ['👀','Đang xem','eyes nhin xem'],
  ['👍','Đồng ý','thumbsup +1 dong y'], ['👎','Không đồng ý','thumbsdown -1 khong'],
  ['👏','Vỗ tay','clap vo tay'], ['🙏','Cảm ơn','pray thanks cam on'],
  ['🤝','Bắt tay','handshake bat tay'], ['👋','Xin chào','wave hello chao'],
  ['💪','Cố lên','muscle co len'], ['✌️','Chiến thắng','victory chien thang'],
  ['❤️','Trái tim đỏ','heart trai tim'], ['💜','Trái tim tím','purple_heart'],
  ['💔','Tan vỡ','broken_heart'], ['🔥','Lửa','fire lua'],
  ['✨','Lấp lánh','sparkles lap lanh'], ['🎉','Ăn mừng','tada party an mung'],
  ['✅','Đúng','check dung'], ['❌','Sai','cross sai'],
  ['❓','Câu hỏi','question hoi'], ['⚠️','Chú ý','warning chu y'],
  ['🐺','Sói','wolf soi'], ['🌙','Mặt trăng','moon trang dem'],
  ['☀️','Mặt trời','sun troi ngay'], ['🕯️','Ngọn nến','candle nen'],
  ['🔮','Tiên tri','crystal_ball tien tri soi'], ['🧪','Thuốc','potion thuoc phu thuy'],
  ['🛡️','Bảo vệ','shield bao ve'], ['🏹','Thợ săn','bow hunter tho san'],
  ['👻','Hồn ma','ghost ma'], ['💀','Đầu lâu','skull chet'],
  ['🎲','Xúc xắc','dice random xuc xac'], ['👨‍👩‍👧‍👦','Gia đình','family gia dinh'],
].map(([value, name, aliases]) => ({ value, name, aliases: aliases.split(' ') }))
const normalize = (text: string) => text.toLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '').replace(/đ/g,'d').replace(/:/g,'').trim()
export function findEmoji(query: string): Emoji[] {
  const q = normalize(query)
  return EMOJI.filter((item) => normalize(`${item.value} ${item.name} ${item.aliases.join(' ')}`).includes(q))
}
export function expandEmojiAliases(text: string): string {
  return text.replace(/:([a-zA-Z0-9_+-]+):/g, (full, alias: string) => EMOJI.find((e) => e.aliases.includes(alias.toLowerCase()))?.value ?? full)
}
export const RECENT_EMOJI_KEY = 'ma-soi-emoji-recents-v1'
export function recentEmoji(): Emoji[] {
  try {
    const values: unknown = JSON.parse(localStorage.getItem(RECENT_EMOJI_KEY) || '[]')
    if (!Array.isArray(values)) return []
    return values.slice(0, 24).map((value) => EMOJI.find((e) => e.value === value)).filter((e): e is Emoji => Boolean(e))
  } catch { return [] }
}
export function rememberEmoji(value: string) {
  try { localStorage.setItem(RECENT_EMOJI_KEY, JSON.stringify([value, ...recentEmoji().map((e) => e.value).filter((v) => v !== value)].slice(0,24))) } catch { /* optional device preference */ }
}
