import { STATUS_CN } from './format'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function statusBadgeWithProgress(
  status: string | null,
  progress: { phase: string; detail: string } | null,
) {
  const base = statusBadge(status)
  if (!progress || status !== 'transcribing') return base
  const phaseCn: Record<string, string> = {
    downloading: '下载视频',
    downloaded: '已下载',
    normalizing: '音频标准化',
    asr_running: '语音识别中',
    asr_done: '识别完成',
  }
  const label = phaseCn[progress.phase]
  return (
    <span>
      {base}
      {label && <div className="muted" style={{ fontSize: 11 }}>{label}</div>}
    </span>
  )
}

export function statusBadge(status: string | null) {
  if (!status) return <span className="muted">-</span>
  const cn = STATUS_CN[status] ?? status
  if (status === 'transcribed') return <span className="badge ok">{cn}</span>
  if (status === 'failed') return <span className="badge err">{cn}</span>
  if (status === 'transcribing') return <span className="badge run">{cn}</span>
  return <span className="badge">{cn}</span>
}

export { STATUS_CN }
