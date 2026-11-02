import { WeekBar } from '../../shared/WeekBar'
import { TranscriptDrawer } from '../../features/library/LibraryPage'
import { VideoViewpointsDrawer } from './VideoViewpointsDrawer'
import { IconDoc, IconQuote, IconRefresh, StanceSummary } from '../../shared/viewpoint-bits'
import { statusBadge } from '../../shared/badges'
import { fmtDateTime } from '../../shared/format'
import { api } from '../../shared/api'
import { weekbarFmt } from '../../shared/WeekBar'
import type { VPVideoRow } from '../../shared/types'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function ViewpointsPage({ mode }: { mode: 'all' | 'review' }) {
  const [rows, setRows] = useState<VPVideoRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [vpDrawer, setVpDrawer] = useState<VPVideoRow | null>(null)
  const [textDrawer, setTextDrawer] = useState<VPVideoRow | null>(null)
  // 进入观点页默认只看当天（用户 2026-09-25）
  const [dayFilter, setDayFilter] = useState<string | null>(weekbarFmt(new Date()))

  const reload = useCallback((silent = false) => {
    const params = new URLSearchParams({ limit: '500' })
    if (mode === 'review') params.set('pending_review', 'true')
    else params.set('has_viewpoints', 'true')
    if (dayFilter) {
      params.set('date_from', dayFilter)
      params.set('date_to', dayFilter)
    }
    api<VPVideoRow[]>(`/source-items?${params}`, undefined, { silent })
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [mode, dayFilter])

  useEffect(() => {
    reload()
    const t = setInterval(() => reload(true), 15000)
    return () => clearInterval(t)
  }, [reload])

  const pendingTotal = rows.reduce((s, r) => s + r.viewpoint_needs_review, 0)
  const allTotal = rows.reduce((s, r) => s + r.viewpoint_total, 0)

  const reextractAll = async () => {
    if (!window.confirm('用 v2 整体理解引擎重抽全部旧版观点？旧观点（含已确认/已驳回）将删除并重新抽取，需人工复核的结果会重来。')) return
    setError(null)
    try {
      const d = await api<{ dispatched: number }>('/viewpoints/re-extract-all?scope=v1', { method: 'POST' })
      window.alert(`已派发 ${d.dispatched} 个视频的重抽任务，按队列依次执行（可在任务页看进度）`)
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}
      <div className="toolbar">
        <h3 className="toolbar-title">
          {mode === 'review'
            ? `复核队列（${rows.length} 个视频 · ${pendingTotal} 条待审观点）`
            : `观点（${rows.length} 个视频 · ${allTotal} 条观点）`}
        </h3>
        <button className="ghost" onClick={reextractAll}>旧版观点全部重抽（v2）</button>
      </div>
      <WeekBar value={dayFilter} onChange={setDayFilter} />
      <p className="hint">
        一个视频一条记录；点行或「观点」查看该视频抽出的全部观点（立场是每条观点自己的属性），「文案」看视频转写原文，证据在观点列表里逐条查看。
      </p>
      <div className="board-scroll">
        <table className="board vp-freeze">
          <thead>
            <tr>
              <th>发布时间</th>
              <th>观点</th>
              <th>内容状态</th>
              <th>主播</th>
              <th>视频标题</th>
              <th>立场标签</th>
              <th>总结</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => setVpDrawer(r)}>
                <td className="muted" style={{ fontSize: 11 }}>{fmtDateTime(r.published_at)}</td>
                <td>
                  <b>{r.viewpoint_total}</b> 条{' '}
                  {r.viewpoint_needs_review > 0 && (
                    <span className="badge run">{r.viewpoint_needs_review} 待审</span>
                  )}{' '}
                  {r.viewpoint_confirmed > 0 && (
                    <span className="badge ok">{r.viewpoint_confirmed} 已确认</span>
                  )}
                </td>
                <td>{statusBadge(r.status)}</td>
                <td>{r.display_name}</td>
                <td className="title-cell" title={r.title ?? ''}>{r.title ?? '-'}</td>
                <td className="stance-cell"><StanceSummary summary={r.stance_summary} /></td>
                <td
                  className="summary-cell"
                  title={r.viewpoint_summary ?? '全部复核完成后自动生成'}
                >
                  {r.viewpoint_summary ?? <span className="muted">-</span>}
                </td>
                <td onClick={(e) => e.stopPropagation()}>
                  <button
                    className="icon-btn"
                    title="查看观点"
                    aria-label="查看观点"
                    onClick={() => setVpDrawer(r)}
                  >
                    {IconQuote}
                  </button>{' '}
                  <button
                    className="icon-btn"
                    title="查看文案（转写原文）"
                    aria-label="查看文案"
                    onClick={() => setTextDrawer(r)}
                  >
                    {IconDoc}
                  </button>{' '}
                  <button
                    className="icon-btn danger"
                    title="删除旧观点并用 v2 整体理解引擎重新抽取"
                    aria-label="重新抽取"
                    onClick={() => {
                      if (window.confirm(`重抽「${r.title ?? r.id}」的观点？旧观点将删除重新抽取。`)) {
                        api(`/viewpoints/re-extract/${r.id}`, { method: 'POST' })
                          .then(() => reload())
                          .catch((e: Error) => setError(e.message))
                      }
                    }}
                  >
                    {IconRefresh}
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="muted pad">
                  {mode === 'review'
                    ? '复核队列为空（自动复核通过的不进队列）'
                    : '暂无观点：先在视频库转写内容并点「抽取观点」'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {vpDrawer !== null && (
        <VideoViewpointsDrawer
          item={vpDrawer}
          mode={mode}
          onClose={() => setVpDrawer(null)}
          onChanged={reload}
        />
      )}
      {textDrawer !== null && (
        <TranscriptDrawer itemId={textDrawer.id} onClose={() => setTextDrawer(null)} />
      )}
    </div>
  )
}

