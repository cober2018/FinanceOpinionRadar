import { TranscriptDrawer } from '../../features/library/LibraryPage'
import { fmtDateTime } from '../../shared/format'
import { statusBadge } from '../../shared/badges'
import { api } from '../../shared/api'
import { weekbarFmt } from '../../shared/WeekBar'
import type { LibraryItem } from '../../shared/types'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function AssetLibraryPage() {
  const [rows, setRows] = useState<LibraryItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [drawer, setDrawer] = useState<number | null>(null)
  // 时间范围筛选（资产量少，按范围比找具体日期合理——用户 2026-09-24）
  const [range, setRange] = useState('')

  const reload = useCallback(() => {
    const params = new URLSearchParams({ asset: 'true', limit: '500' })
    if (range) {
      const months = { '1m': 1, '3m': 3, '6m': 6, '1y': 12 }[range] ?? 0
      const from = new Date()
      from.setMonth(from.getMonth() - months)
      params.set('date_from', weekbarFmt(from))
    }
    api<LibraryItem[]>(`/source-items?${params}`)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [range])

  useEffect(() => reload(), [reload])

  const toggleAsset = async (id: number) => {
    setError(null)
    try {
      await api(`/source-items/${id}/asset`, { method: 'POST' })
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}
      <div className="toolbar">
        <h3 className="toolbar-title">资产库（{rows.length}）</h3>
        <select value={range} onChange={(e) => setRange(e.target.value)}>
          <option value="">全部时间</option>
          <option value="1m">近一个月</option>
          <option value="3m">近三个月</option>
          <option value="6m">近半年</option>
          <option value="1y">近一年</option>
        </select>
      </div>
      <p className="hint">
        精华内容的永久档案：不参与 30 天生命周期清理，转录与观点随内容永久保留。在视频库点 ☆ 可加入。
      </p>
      <table className="board">
        <thead>
          <tr>
            <th>主播</th>
            <th>平台</th>
            <th>标题</th>
            <th>类型</th>
            <th>状态</th>
            <th>发布时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.display_name}</td>
              <td>{r.platform}</td>
              <td className="title-cell" title={r.title ?? ''}>{r.title ?? '-'}</td>
              <td>{r.item_type === 'live' ? '直播' : '视频'}</td>
              <td>{statusBadge(r.status)}</td>
              <td className="muted" style={{ fontSize: 11 }}>{fmtDateTime(r.published_at)}</td>
              <td>
                <button className="ghost" onClick={() => setDrawer(r.id)}>文案</button>{' '}
                <button
                  className="ghost"
                  disabled={busyId === r.id}
                  title="移出资产库（恢复生命周期规则）"
                  onClick={() => toggleAsset(r.id)}
                >
                  取消精华
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={7} className="muted pad">
                资产库为空：到视频库点 ☆ 把重要内容标记为精华（永久保留）。
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {drawer !== null && <TranscriptDrawer itemId={drawer} onClose={() => setDrawer(null)} />}
    </div>
  )
}


