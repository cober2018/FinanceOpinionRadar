// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
import { useState } from 'react'

// 周历日期筛选（仿风险日历一周条）：点选某天只看当天，再点一次清除；箭头切周。
// value 为 'YYYY-MM-DD' 或 null（看全部）。
export const WEEKBAR_WD = ['一', '二', '三', '四', '五', '六', '日']


export function weekbarFmt(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 当天主播事件流（2026-09-30）：左侧时间轴 + 右侧卡片，从近到远；
// 观点卡片 = 主播在某个钟点说的一件事（直播观点时刻=起播+证据偏移），
// 总结卡片 = 视频观点（直播进行中随分片滚动更新）


export function WeekBar({ value, onChange }: { value: string | null; onChange: (day: string | null) => void }) {
  const [weekStart, setWeekStart] = useState<Date>(() => {
    const base = value ? new Date(`${value}T00:00:00`) : new Date()
    const d = new Date(base)
    d.setDate(d.getDate() - ((d.getDay() + 6) % 7)) // 回到本周一
    return d
  })
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart)
    d.setDate(d.getDate() + i)
    return d
  })
  const today = weekbarFmt(new Date())
  const shift = (n: number) =>
    setWeekStart((s) => {
      const d = new Date(s)
      d.setDate(d.getDate() + n * 7)
      return d
    })
  return (
    <div className="weekbar">
      <button className="ghost weekbar-nav" onClick={() => shift(-1)} title="上一周">‹</button>
      {days.map((d, i) => {
        const day = weekbarFmt(d)
        return (
          <button
            key={day}
            className={`weekbar-day${value === day ? ' active' : ''}${day === today ? ' today' : ''}`}
            onClick={() => onChange(value === day ? null : day)}
          >
            <span className="wd">{WEEKBAR_WD[i]}</span>
            <span className="dd">{day.slice(5)}</span>
          </button>
        )
      })}
      <button className="ghost weekbar-nav" onClick={() => shift(1)} title="下一周">›</button>
      {value && (
        <button className="ghost" onClick={() => onChange(null)}>全部</button>
      )}
    </div>
  )
}

// 立场徽章（A股习惯）：看多族=红、看空族=绿，其余灰
