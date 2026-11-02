import { useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function ApiBusyIndicator() {
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const handler = (e: Event) => setBusy(((e as CustomEvent).detail as number) > 0)
    window.addEventListener('api-busy', handler)
    return () => window.removeEventListener('api-busy', handler)
  }, [])
  if (!busy) return null
  return (
    <div className="api-busy" role="status">
      <span className="spinner" /> 处理中…
    </div>
  )
}

// 后端健康横幅：API 不可达时置顶提示（10s 轮询，恢复自动消失）。
// 解决"进程死了但界面操作静默失败"——用户第一时间看到的是服务挂了，不是自己点错了。
export function BackendHealthBanner() {
  const [down, setDown] = useState(false)
  useEffect(() => {
    let alive = true
    const ping = () => {
      fetch('/api/v1/system/status')
        .then((r) => { if (alive) setDown(!r.ok) })
        .catch(() => { if (alive) setDown(true) })
    }
    ping()
    const t = setInterval(ping, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [])
  if (!down) return null
  return (
    <div className="backend-down">
      ⚠ 后端服务不可达（API :8010 无响应），页面上的操作不会生效。
      在项目目录运行 <b>make up</b> 一键恢复，服务恢复后此提示自动消失。
    </div>
  )
}

export const NAV = [
  { key: 'overview', label: '总览' },
  { key: 'timeline', label: '时间轴' },
  { key: 'accounts', label: '主播' },
  { key: 'library', label: '视频库' },
  { key: 'assets', label: '资产库' },
  { key: 'viewpoints', label: '观点' },
  { key: 'review', label: '复核队列' },
  { key: 'jobs', label: '任务' },
  { key: 'open-api', label: '开放接口' },
  { key: 'settings', label: '设置' },
] as const

export type Tab = (typeof NAV)[number]['key']

