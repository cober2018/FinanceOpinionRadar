// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function fmtAgo(iso: string | null): string {
  if (!iso) return '-'
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (secs < 60) return '刚刚'
  if (secs < 3600) return `${Math.floor(secs / 60)} 分钟前`
  if (secs < 86400) return `${Math.floor(secs / 3600)} 小时前`
  return `${Math.floor(secs / 86400)} 天前`
}

export function fmtTs(ms: number): string {
  const s = Math.floor(ms / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function fmtDateTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export const STATUS_CN: Record<string, string> = {
  discovered: '已发现',
  resolved: '已解析',
  media_ready: '媒体就绪',
  transcribing: '转写中',
  transcribed: '已转写',
  extracting: '抽取观点中',
  reviewing: '待审核',
  ready: '已就绪',
  failed: '失败',
}

export function fmtClock(iso: string | null): string {
  if (!iso) return '--:--'
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

