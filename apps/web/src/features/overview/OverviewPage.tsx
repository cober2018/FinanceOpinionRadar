import { fmtAgo } from '../../shared/format'
import { statusBadge } from '../../shared/badges'
import { api } from '../../shared/api'
import type { DanmakuStats, LibraryItem, LiveMonitor, SystemStatus } from '../../shared/types'
import { useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function OverviewPage({ go }: { go: (tab: string) => void }) {
  const [monitors, setMonitors] = useState<LiveMonitor[]>([])
  const [recent, setRecent] = useState<LibraryItem[]>([])
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [contents, setContents] = useState(0)
  const [chatTotal, setChatTotal] = useState(0)

  useEffect(() => {
    api<{ stats: { transcribed_contents: number; live_segments: number } }>('/dashboard')
      .then((d) => {
        setContents(d.stats.transcribed_contents)
        setTotalSegs(d.stats.live_segments)
      })
      .catch(() => {})
    api<LiveMonitor[]>('/live/monitors').then(setMonitors).catch(() => {})
    api<LibraryItem[]>('/source-items?limit=10&status=transcribed')
      .then(setRecent)
      .catch(() => {})
    api<SystemStatus>('/system/status').then(setStatus).catch(() => {})
    api<DanmakuStats>('/danmaku/stats')
      .then((s) => setChatTotal(s.total))
      .catch(() => {})
  }, [])

  const liveCount = monitors.filter((m) => m.is_live === true).length
  const watching = monitors.filter((m) => m.live_monitor_enabled).length
  const [totalSegs, setTotalSegs] = useState(0)

  return (
    <div className="stack">
      <div className="stat-row">
        <div className="stat-card">
          <div className="stat-num">{monitors.length}</div>
          <div className="stat-label">监控主播</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{watching}</div>
          <div className="stat-label">直播值守中</div>
        </div>
        <div className="stat-card">
          <div className={`stat-num ${liveCount > 0 ? 'live-num' : ''}`}>{liveCount}</div>
          <div className="stat-label">正在直播</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{contents}</div>
          <div className="stat-label">已转写内容</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{totalSegs}</div>
          <div className="stat-label">直播转录段落</div>
        </div>
        <div className="stat-card">
          <div className="stat-num">{chatTotal}</div>
          <div className="stat-label">直播弹幕</div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3>直播间状态</h3>
          <button className="ghost" onClick={() => go('accounts')}>
            主播管理 →
          </button>
        </div>
        <div className="room-grid">
          {monitors
            .filter((m) => m.live_monitor_enabled)
            .map((m) => (
              <div key={m.account_id} className={`room-card ${m.is_live === true ? 'room-live' : ''}`}>
                <div className="room-name">
                  {m.display_name}
                  {m.is_live === true && <span className="live-dot">● 直播中</span>}
                </div>
                <div className="room-meta">
                  房间 {m.room_id ?? '-'} · 值守 {m.monitor_interval_sec}s 分片
                </div>
                <div className="room-meta">
                  最近会话 {statusBadge(m.session_status)} · {m.segment_count} 片 / {m.transcript_count} 段
                </div>
                <div className="room-meta">
                  {m.is_live === true && m.viewer_count != null
                    ? `人气 ${m.viewer_count} 人在看`
                    : m.viewer_avg != null
                      ? `人气 平均 ${m.viewer_avg} 人 / 峰值 ${m.viewer_peak ?? '-'}`
                      : '人气 -'}
                </div>
              </div>
            ))}
          {watching === 0 && <p className="muted pad">还没有开启直播值守的主播。到「主播」页添加并打开值守开关。</p>}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h3>最近转写</h3>
          <button className="ghost" onClick={() => go('library')}>
            视频库 →
          </button>
        </div>
        <table className="board">
          <thead>
            <tr>
              <th>主播</th>
              <th>标题</th>
              <th>平台</th>
              <th>发布时间</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((r) => (
              <tr key={r.id}>
                <td>{r.display_name}</td>
                <td className="title-cell">{r.title ?? '-'}</td>
                <td>{r.platform}</td>
                <td>{fmtAgo(r.published_at)}</td>
              </tr>
            ))}
            {recent.length === 0 && (
              <tr>
                <td colSpan={4} className="muted pad">
                  暂无已完成转写的内容
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {status && (
        <div className="panel">
          <div className="panel-head">
            <h3>引擎状态</h3>
          </div>
          <div className="status-row">
            <span>
              转录引擎 <b>{status.asr_provider === 'mlx' ? 'mlx（Apple Metal 加速）' : status.asr_provider}</b> · {status.asr_model}
            </span>
            <span>
              抖音解析服务{' '}
              {status.douyin.reachable ? <b className="ok-text">在线</b> : <b className="err-text">不可达</b>}
            </span>
            <span>
              录制器 {status.recorder.configured ? `${status.recorder.container} · ${status.recorder.synced_monitors} 路值守` : '未配置'}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

