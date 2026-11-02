import { statusBadgeWithProgress } from '../../shared/badges'
import { STATUS_CN, fmtAgo, fmtDateTime } from '../../shared/format'
import type { LiveMonitor } from '../../shared/types'
import { api } from '../../shared/api'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
/* ---------------- 任务中心 ---------------- */

type LedgerRow = {
  item_id: number
  creator_name: string
  platform: string
  title: string
  item_type: string
  status: string
  progress_phase: string | null
  progress_detail: string | null
  published_at: string | null
  transcript_count: number
  viewpoint_total: number
  viewpoint_needs_review: number
  viewpoint_confirmed: number
  viewpoint_rejected: number
  topic_name: string | null
  error_code: string | null
  error_message: string | null
}

type JobRow = {
  id: number
  job_type: string
  status: string
  source_item_id: number | null
  attempt: number
  trace_id: string | null
  duration_ms: number | null
  error_code: string | null
  error_message: string | null
  created_at: string | null
}

const JOB_STATUS_CN: Record<string, string> = {
  success: '成功',
  failed: '失败',
  running: '运行中',
  queued: '排队中',
}

const JOB_TYPE_CN: Record<string, string> = {
  prepare_media: '转写',
  extract_viewpoints: '观点抽取',
}

export function JobCenterPage() {
  const [rows, setRows] = useState<JobRow[]>([])
  const [stats, setStats] = useState<{ total: number; running: number; success: number; failed: number } | null>(null)
  const [typeF, setTypeF] = useState('')
  const [statusF, setStatusF] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)

  const reload = useCallback((silent = false) => {
    const params = new URLSearchParams()
    if (typeF) params.set('job_type', typeF)
    if (statusF) params.set('status', statusF)
    if (dateFrom) params.set('date_from', dateFrom)
    params.set('limit', '200')
    api<JobRow[]>(`/jobs?${params}`, undefined, { silent })
      .then(setRows)
      .catch((e: Error) => setError(e.message))
    api<{ total: number; running: number; success: number; failed: number }>(
      '/jobs/stats',
      undefined,
      { silent },
    )
      .then(setStats)
      .catch(() => {})
  }, [typeF, statusF, dateFrom])

  useEffect(() => {
    reload()
    const t = setInterval(() => reload(true), 15_000)
    return () => clearInterval(t)
  }, [reload])

  const runningRows = rows.filter((r) => r.status === 'running')

  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [ledgerCreator, setLedgerCreator] = useState('')
  const [ledgerStatus, setLedgerStatus] = useState('')
  const [ledgerMonitors, setLedgerMonitors] = useState<{ account_id: number; display_name: string }[]>([])

  useEffect(() => {
    api<LiveMonitor[]>('/live/monitors')
      .then((ms) => setLedgerMonitors(ms.map((m) => ({ account_id: m.account_id, display_name: m.display_name }))))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const params = new URLSearchParams()
    if (ledgerCreator) params.set('creator_id', ledgerCreator)
    if (ledgerStatus) params.set('status', ledgerStatus)
    api<LedgerRow[]>(`/ledger?${params}`)
      .then(setLedger)
      .catch(() => {})
  }, [ledgerCreator, ledgerStatus])

  const ledgerCreators = ledgerMonitors

  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}

      {stats && (
        <div className="stat-row">
          <div className="stat-card"><div className="stat-num">{stats.total}</div><div className="stat-label">任务总数</div></div>
          <div className="stat-card"><div className="stat-num">{stats.running}</div><div className="stat-label">正在执行</div></div>
          <div className="stat-card"><div className="stat-num">{stats.success}</div><div className="stat-label">成功</div></div>
          <div className="stat-card"><div className={`stat-num ${stats.failed > 0 ? 'live-num' : ''}`}>{stats.failed}</div><div className="stat-label">失败</div></div>
        </div>
      )}

      {runningRows.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h3>⏳ 正在执行的任务（{runningRows.length}）</h3></div>
          <table className="board">
            <thead>
              <tr><th>#</th><th>任务</th><th>内容</th><th>已运行</th><th>开始时间</th></tr>
            </thead>
            <tbody>
              {runningRows.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>{JOB_TYPE_CN[r.job_type] ?? r.job_type}</td>
                  <td>{r.source_item_id ? `内容 ${r.source_item_id}` : '-'}</td>
                  <td>{fmtAgo(r.created_at)}</td>
                  <td>{r.created_at ? new Date(r.created_at).toLocaleTimeString() : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="toolbar">
        <h3 className="toolbar-title">📋 任务台账（按内容，{ledger.length} 条）</h3>
        <select value={ledgerCreator} onChange={(e) => setLedgerCreator(e.target.value)}>
          <option value="">全部主播</option>
          {ledgerCreators.map((c) => (
            <option key={c.account_id} value={c.account_id}>{c.display_name}</option>
          ))}
        </select>
        <select value={ledgerStatus} onChange={(e) => setLedgerStatus(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_CN).map(([v, cn]) => (
            <option key={v} value={v}>{cn}</option>
          ))}
        </select>
      </div>

      <table className="board">
        <thead>
          <tr>
            <th>主播</th>
            <th>标题</th>
            <th>类型</th>
            <th>内容状态</th>
            <th>进度</th>
            <th>发布</th>
            <th>转录段</th>
            <th>观点（待审/确认/驳回）</th>
            <th>主题</th>
            <th>错误</th>
          </tr>
        </thead>
        <tbody>
          {ledger.map((r) => (
            <tr key={r.item_id}>
              <td>{r.creator_name}</td>
              <td className="title-cell" title={r.title}>{r.title}</td>
              <td>{r.item_type === 'live' ? '直播' : '视频'}</td>
              <td>
                {statusBadgeWithProgress(r.status, r.progress_phase ? { phase: r.progress_phase, detail: r.progress_detail ?? '' } : null)}
              </td>
              <td
                className="muted"
                style={{ fontSize: 11, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                title={r.progress_detail ?? ''}
              >
                {r.progress_detail ?? '-'}
              </td>
              <td>{fmtDateTime(r.published_at)}</td>
              <td>{r.transcript_count}</td>
              <td>
                {r.viewpoint_total > 0 ? (
                  <span>
                    {r.viewpoint_needs_review > 0 && <span className="badge run">{r.viewpoint_needs_review} 待审</span>}{' '}
                    <span className="badge ok">{r.viewpoint_confirmed} 认</span>{' '}
                    {r.viewpoint_rejected > 0 && <span className="badge err">{r.viewpoint_rejected} 驳</span>}
                  </span>
                ) : (
                  <span className="muted">-</span>
                )}
              </td>
              <td>{r.topic_name ?? <span className="muted">-</span>}</td>
              <td>
                {r.error_code ? (
                  <span className="badge err" title={r.error_message ?? ''}>{r.error_code}</span>
                ) : (
                  '-'
                )}
              </td>
            </tr>
          ))}
          {ledger.length === 0 && (
            <tr><td colSpan={10} className="muted pad">暂无内容</td></tr>
          )}
        </tbody>
      </table>

      <div className="toolbar">
        <h3 className="toolbar-title">任务历史（按执行，{rows.length} 条）</h3>
        <select value={typeF} onChange={(e) => setTypeF(e.target.value)}>
          <option value="">全部类型</option>
          <option value="prepare_media">转写</option>
          <option value="extract_viewpoints">观点抽取</option>
        </select>
        <select value={statusF} onChange={(e) => setStatusF(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(JOB_STATUS_CN).map(([v, cn]) => (
            <option key={v} value={v}>{cn}</option>
          ))}
        </select>
        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        {(typeF || statusF || dateFrom) && (
          <button className="ghost" onClick={() => { setTypeF(''); setStatusF(''); setDateFrom('') }}>清除</button>
        )}
      </div>

      <table className="board">
        <thead>
          <tr>
            <th>#</th>
            <th>任务</th>
            <th>状态</th>
            <th>内容</th>
            <th>耗时</th>
            <th>attempt</th>
            <th>时间</th>
            <th>错误详情</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{JOB_TYPE_CN[r.job_type] ?? r.job_type}</td>
              <td>
                <span className={`badge ${r.status === 'success' ? 'ok' : r.status === 'failed' ? 'err' : 'run'}`}>
                  {JOB_STATUS_CN[r.status] ?? r.status}
                </span>
              </td>
              <td>{r.source_item_id ? `内容 ${r.source_item_id}` : '-'}</td>
              <td>{r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)}s` : '-'}</td>
              <td>{r.attempt}</td>
              <td>{fmtAgo(r.created_at)}</td>
              <td>
                {r.error_message ? (
                  <button className="ghost" onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
                    {expanded === r.id ? '收起' : '查看错误'}
                  </button>
                ) : (
                  '-'
                )}
                {expanded === r.id && r.error_message && (
                  <div className="error" style={{ whiteSpace: 'pre-wrap', marginTop: 6, maxWidth: 520 }}>{r.error_message}</div>
                )}
              </td>
            </tr>
          ))}
          {rows.length === 0 && !error && (
            <tr><td colSpan={8} className="muted pad">暂无任务记录（筛选条件下）</td></tr>
          )}
        </tbody>
      </table>
      <p className="hint">全系统任务监控：转写 / 观点抽取任务真实状态。失败任务点「查看错误」定位原因。页面每 15 秒自动刷新。</p>
    </div>
  )
}

