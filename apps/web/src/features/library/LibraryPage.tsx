import { WeekBar } from '../../shared/WeekBar'
import { statusBadgeWithProgress } from '../../shared/badges'
import { DanmakuDrawer } from './DanmakuDrawer'
import { STATUS_CN, fmtDateTime, fmtTs } from '../../shared/format'
import { api } from '../../shared/api'
import { weekbarFmt } from '../../shared/WeekBar'
import type { LibraryItem, LiveMonitor, SearchHit, TranscriptRow } from '../../shared/types'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function LibraryPage() {
  const [rows, setRows] = useState<LibraryItem[]>([])
  const [monitors, setMonitors] = useState<LiveMonitor[]>([])
  const [accountFilter, setAccountFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [assetFilter, setAssetFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [drawer, setDrawer] = useState<number | null>(null)
  const [chatDrawer, setChatDrawer] = useState<number | null>(null)
  const [drillAccount, setDrillAccount] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [dayFilter, setDayFilter] = useState<string | null>(weekbarFmt(new Date()))

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const allSelected = rows.length > 0 && rows.every((r) => selectedIds.has(r.id))

  const batchDelete = async () => {
    const n = window.confirm(
      `确定删除选中的 ${selectedIds.size} 条内容？转录与观点将物理删除，且不会重新导入。`,
    )
    if (!n) return
    setError(null)
    try {
      await api('/source-items/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ ids: [...selectedIds] }),
      })
      setSelectedIds(new Set())
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const reload = useCallback(() => {
    const params = new URLSearchParams()
    if (drillAccount) params.set('account_id', drillAccount)
    else {
      if (accountFilter) params.set('account_id', accountFilter)
      if (statusFilter) params.set('status', statusFilter)
    }
    if (assetFilter !== '') params.set('asset', assetFilter)
    if (dayFilter) {
      params.set('date_from', dayFilter)
      params.set('date_to', dayFilter)
    }
    params.set('limit', '200')
    api<LibraryItem[]>(`/source-items?${params}`)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [accountFilter, statusFilter, drillAccount, assetFilter, dayFilter])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    api<LiveMonitor[]>('/live/monitors').then(setMonitors).catch(() => {})
  }, [drillAccount])

  const transcribe = async (id: number) => {
    setBusyId(id)
    setError(null)
    try {
      await api(`/source-items/${id}/prepare`, { method: 'POST' })
      setTimeout(reload, 1500)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const toggleAsset = async (id: number) => {
    setError(null)
    try {
      await api(`/source-items/${id}/asset`, { method: 'POST' })
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const removeItem = async (r: LibraryItem) => {
    const n = window.confirm(
      `确定删除「${(r.title ?? '').slice(0, 30)}」？转录与观点将物理删除，且不会重新导入。`,
    )
    if (!n) return
    setBusyId(r.id)
    setError(null)
    try {
      await api(`/source-items/${r.id}`, { method: 'DELETE' })
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const extractPoints = async (id: number) => {
    setBusyId(id)
    setError(null)
    try {
      await api(`/source-items/${id}/extract`, { method: 'POST' })
      setTimeout(reload, 2000)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const creatorGroups = (() => {
    const by = new Map<number, { accountId: number; displayName: string; platform: string; items: LibraryItem[] }>()
    for (const r of rows) {
      const g = by.get(r.account_id)
      if (g) g.items.push(r)
      else by.set(r.account_id, { accountId: r.account_id, displayName: r.display_name || `账号${r.account_id}`, platform: r.platform, items: [r] })
    }
    return [...by.values()].sort((a, b) => b.items.length - a.items.length)
  })()

  const search = async () => {
    if (!query.trim()) {
      setHits(null)
      return
    }
    try {
      setHits(await api<SearchHit[]>(`/transcripts/search?q=${encodeURIComponent(query.trim())}`))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}
      <div className="toolbar">
        <input
          className="search-box"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          placeholder="全文搜索已转写内容（如「降息」「AI」）…"
        />
        <button className="primary" onClick={search}>搜索</button>
      </div>

      {hits !== null && (
        <div className="panel">
          <div className="panel-head">
            <h3>搜索结果（{hits.length}）</h3>
            <button className="ghost" onClick={() => { setHits(null); setQuery('') }}>关闭</button>
          </div>
          <table className="board">
            <thead>
              <tr>
                <th>主播</th>
                <th>标题</th>
                <th>命中</th>
                <th>摘录</th>
              </tr>
            </thead>
            <tbody>
              {hits.map((h) => (
                <tr key={h.item_id} className="clickable" onClick={() => setDrawer(h.item_id)}>
                  <td>{h.display_name}</td>
                  <td className="title-cell">{h.title ?? '-'}</td>
                  <td>{h.match_count}</td>
                  <td className="snippet-cell">{h.snippet}</td>
                </tr>
              ))}
              {hits.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted pad">无匹配内容</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {drillAccount === null && (
        <div className="panel">
          <div className="panel-head">
            <h3>按主播浏览（{creatorGroups.length}）</h3>
          </div>
          <div className="room-grid">
            {creatorGroups.map((g) => {
              const m = monitors.find((x) => x.account_id === g.accountId)
              return (
                <div key={g.accountId} className="room-card clickable-card" onClick={() => setDrillAccount(String(g.accountId))}>
                  <div className="room-name">{g.displayName}<span className="muted"> · {g.items.length} 条</span></div>
                  <div className="room-meta">{g.platform}{m?.is_live === true ? ' · 🔴 直播中' : ''}</div>
                  <div className="room-meta">
                    {g.items.filter((i) => i.status === 'transcribed').length} 已转写 ·{' '}
                    {g.items.filter((i) => i.status === 'discovered').length} 待转写 ·{' '}
                    {g.items.filter((i) => i.status === 'failed').length} 失败
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="toolbar">
        {drillAccount !== null && (
          <button className="ghost" onClick={() => setDrillAccount(null)}>← 返回全部主播</button>
        )}
        <h3 className="toolbar-title">
          {drillAccount !== null
            ? `${creatorGroups.find((g) => String(g.accountId) === drillAccount)?.displayName ?? ''} 的内容（${rows.length}）`
            : `全部内容（${rows.length}）`}
        </h3>
        <select value={accountFilter} onChange={(e) => setAccountFilter(e.target.value)}>
          <option value="">全部账号</option>
          {monitors.map((m) => (
            <option key={m.account_id} value={m.account_id}>
              {m.display_name}
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_CN).map(([v, cn]) => (
            <option key={v} value={v}>{cn}</option>
          ))}
        </select>
        <select value={assetFilter} onChange={(e) => setAssetFilter(e.target.value)}>
          <option value="">全部内容</option>
          <option value="true">⭐ 精华资产</option>
          <option value="false">普通内容</option>
        </select>
        <button className="ghost" onClick={reload}>刷新</button>
        {selectedIds.size > 0 && (
          <button className="danger" onClick={batchDelete}>
            删除选中（{selectedIds.size}）
          </button>
        )}
      </div>
      <WeekBar value={dayFilter} onChange={setDayFilter} />

      <table className="board">
        <thead>
          <tr>
            <th>
              <input
                type="checkbox"
                checked={allSelected}
                onChange={() =>
                  setSelectedIds(allSelected ? new Set() : new Set(rows.map((r) => r.id)))
                }
              />
            </th>
            <th>主播</th>
            <th>平台</th>
            <th>标题</th>
            <th>类型</th>
            <th>状态</th>
            <th>进度</th>
            <th>时长</th>
            <th>发布</th>
            <th>人气</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              className={r.status === 'transcribed' ? 'clickable' : undefined}
              onClick={() => r.status === 'transcribed' && setDrawer(r.id)}
            >
              <td onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(r.id)}
                  onChange={() => toggleSelect(r.id)}
                />
              </td>
              <td>{r.display_name}</td>
              <td>{r.platform}</td>
              <td className="title-cell">{r.title ?? '-'}</td>
              <td>{r.item_type === 'live' ? '直播' : '视频'}</td>
              <td>
                {statusBadgeWithProgress(r.status, r.progress)}
                {r.members_only && <span className="badge">会员专属</span>}
                {r.backfill && r.status === 'discovered' && <span className="muted">（旧）</span>}
                <div className="muted" style={{ fontSize: 11 }}>
                  {r.is_asset
                    ? '⭐ 精华 · 永久保留'
                    : r.expires_at
                      ? `${Math.max(0, Math.ceil((new Date(r.expires_at).getTime() - Date.now()) / 86400000))} 天后清理`
                      : ''}
                </div>
              </td>
              <td
                className="muted"
                style={{ fontSize: 11, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                title={r.progress?.detail ?? ''}
              >
                {r.progress?.detail ?? '-'}
              </td>
              <td>{r.duration_ms ? `${Math.round(r.duration_ms / 60000)} 分` : '-'}</td>
              <td>{fmtDateTime(r.published_at)}</td>
              <td>
                {r.item_type === 'live'
                  ? r.viewer_avg != null
                    ? `均 ${r.viewer_avg}${r.viewer_peak != null ? ` / 峰 ${r.viewer_peak}` : ''}`
                    : r.viewer_count != null
                      ? `${r.viewer_count} 人在看`
                      : '-'
                  : '-'}
              </td>
              <td onClick={(e) => e.stopPropagation()}>
                <button className="ghost" title={r.is_asset ? '取消精华' : '标记精华（永久保留）'} onClick={() => toggleAsset(r.id)}>
                  {r.is_asset ? '⭐' : '☆'}
                </button>{' '}
                {r.chat_count > 0 && (
                  <button className="ghost" onClick={() => setChatDrawer(r.id)}>
                    弹幕（{r.chat_count}）
                  </button>
                )}{' '}
                {r.status === 'discovered' || r.status === 'failed' ? (
                  <button disabled={busyId === r.id} onClick={() => transcribe(r.id)}>
                    {busyId === r.id ? '派发中…' : r.status === 'failed' ? '重试' : '转写'}
                  </button>
                ) : r.status === 'transcribed' ? (
                  <>
                    <button className="ghost" onClick={() => setDrawer(r.id)}>正文</button>{' '}
                    <button disabled={busyId === r.id} onClick={() => extractPoints(r.id)}>
                      {busyId === r.id ? '派发中…' : '抽取观点'}
                    </button>
                  </>
                ) : null}{' '}
                <button
                  className="ghost danger"
                  disabled={busyId === r.id}
                  onClick={() => removeItem(r)}
                >
                  删除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">
        旧视频（注册时首扫采集标题）不自动转录，点「转写」手动解析；失败条目点「重试」立即重新执行（并重置自动重试计数）；已转写条目点击行或「正文」查看分段文本。
      </p>

      {drawer !== null && <TranscriptDrawer itemId={drawer} onClose={() => setDrawer(null)} />}
      {chatDrawer !== null && (
        <DanmakuDrawer itemId={chatDrawer} onClose={() => setChatDrawer(null)} />
      )}
    </div>
  )
}

export function TranscriptDrawer(props: { itemId: number; onClose: () => void }) {
  const [data, setData] = useState<{ title: string | null; display_name: string; segments: TranscriptRow[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<{ title: string | null; display_name: string; segments: TranscriptRow[] }>(
      `/source-items/${props.itemId}/transcript`,
    )
      .then(setData)
      .catch((e: Error) => setError(e.message))
  }, [props.itemId])

  return (
    <div className="drawer-mask" onClick={props.onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <div className="drawer-title">{data?.title ?? `内容 #${props.itemId}`}</div>
            <div className="muted">{data?.display_name} · {data?.segments.length ?? 0} 段</div>
          </div>
          <button className="ghost" onClick={props.onClose}>关闭</button>
        </div>
        {error && <p className="error pad">{error}</p>}
        <div className="drawer-body">
          {data?.segments.map((s) => (
            <div key={s.sequence_no} className="ts-row">
              <span className="ts-time">{fmtTs(s.start_ms)}–{fmtTs(s.end_ms)}</span>
              <span className="ts-text">{s.text}</span>
            </div>
          ))}
          {data && data.segments.length === 0 && <p className="muted pad">无分段（可能是空音频或纯音乐）</p>}
        </div>
      </div>
    </div>
  )
}

