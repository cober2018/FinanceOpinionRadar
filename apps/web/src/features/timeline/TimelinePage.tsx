import { WeekBar } from '../../shared/WeekBar'
import type { TimelineEvent } from '../../shared/types'
import { fmtDateTime } from '../../shared/format'
import { StanceBadge } from '../../shared/viewpoint-bits'
import { api } from '../../shared/api'
import { weekbarFmt } from '../../shared/WeekBar'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function TimelinePage() {
  const [dayFilter, setDayFilter] = useState<string | null>(weekbarFmt(new Date()))
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const reload = useCallback(() => {
    setLoading(true)
    const q = dayFilter ? `?date_from=${dayFilter}&date_to=${dayFilter}` : ''
    api<{ events: TimelineEvent[] }>(`/timeline${q}`)
      .then((d) => {
        setEvents(d.events)
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [dayFilter])

  useEffect(reload, [reload])

  return (
    <div className="page">
      <div className="page-head">
        <h2>时间轴</h2>
        <span className="muted">各主播当天说了什么、总结到哪了——从近到远；直播进行中的总结随分片滚动更新。</span>
      </div>
      <WeekBar value={dayFilter} onChange={setDayFilter} />
      {error && <p className="error">{error}</p>}
      {loading ? (
        <p className="muted pad">加载中…</p>
      ) : events.length === 0 ? (
        <p className="muted pad">当天暂无事件（再点一次选中日可清除筛选看最近 300 条）。</p>
      ) : (
        <div className="timeline">
          {events.map((e, i) => {
            const t = e.at ? new Date(e.at) : null
            const hhmm = t
              ? `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
              : '--:--'
            return (
              <div className="timeline-row" key={`${e.type}-${e.item_id}-${e.viewpoint_id ?? i}`}>
                <div className="timeline-time">{hhmm}</div>
                <div className="timeline-dotline">
                  <span className={`timeline-dot ${e.type === 'summary' ? 'summary' : ''}`} />
                </div>
                <div className="timeline-card">
                  <div className="timeline-card-head">
                    {e.avatar_url ? (
                      <img className="timeline-avatar" src={e.avatar_url} alt="" />
                    ) : (
                      <span className="timeline-avatar fallback">{(e.creator_name || '?')[0]}</span>
                    )}
                    <b>{e.creator_name}</b>
                    {e.type === 'summary' ? (
                      <>
                        <span className="badge ok">视频观点</span>
                        {e.live_ongoing && <span className="badge run">直播中</span>}
                        {typeof e.confirmed_count === 'number' && (
                          <span className="muted" style={{ fontSize: 11 }}>{e.confirmed_count} 条观点</span>
                        )}
                      </>
                    ) : (
                      <>
                        <StanceBadge stance={e.stance ?? 'unclear'} />
                        {e.entity && <span className="badge">{e.entity}</span>}
                        {e.horizon && <span className="badge">{e.horizon}</span>}
                        {e.conditional && <span className="muted" style={{ fontSize: 11 }}>条件式</span>}
                      </>
                    )}
                  </div>
                  <p className="timeline-text">{e.type === 'summary' ? e.summary : e.claim}</p>
                  <p className="muted timeline-src">
                    {e.title && <>来源：{e.title}</>}
                    {e.type === 'summary' && e.generated_at && (
                      <>（生成于 {fmtDateTime(e.generated_at)}{e.live_ongoing ? '，随直播滚动更新' : ''}）</>
                    )}
                  </p>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

