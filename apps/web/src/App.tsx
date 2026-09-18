import { useCallback, useEffect, useState } from 'react'
import './App.css'

/* ---------------- 类型 ---------------- */

type LiveMonitor = {
  account_id: number
  display_name: string
  platform?: string
  profile_url: string | null
  live_room_url: string | null
  room_id: string | null
  enabled: boolean
  discovery_mode: 'manual' | 'auto_poll'
  poll_interval_sec: number
  poll_interval_min_sec: number | null
  poll_interval_max_sec: number | null
  live_monitor_enabled: boolean
  monitor_interval_sec: number
  recorder_synced: boolean
  is_live: boolean | null
  session_status: string | null
  session_closed: boolean
  segment_count: number
  transcript_count: number
  last_segment_at: string | null
}

type LibraryItem = {
  id: number
  account_id: number
  platform: string
  display_name: string
  title: string | null
  item_type: string
  status: string
  duration_ms: number | null
  published_at: string | null
  backfill: boolean
}

type TranscriptRow = { sequence_no: number; start_ms: number; end_ms: number; text: string }

type SearchHit = {
  item_id: number
  title: string | null
  display_name: string
  platform: string
  published_at: string | null
  match_count: number
  snippet: string
}

type SecuritySettings = {
  discover_dispatch_stagger_max_sec: number | null
  douyin_discover_max_pages: number | null
  proxy_pool: string[]
  effective: { discover_dispatch_stagger_max_sec: number; douyin_discover_max_pages: number }
}

type LLMTemplate = {
  key: string
  label: string
  base_url: string
  models: string[]
  chat_path: string
  note: string
}

type LLMSettings = {
  templates: LLMTemplate[]
  template: string
  base_url: string
  api_key: string
  model: string
  chat_path: string
  effective: { base_url: string; model: string; timeout_sec: number; max_retries: number; key_set: boolean }
}

type SystemStatus = {
  asr_provider: string
  asr_model: string
  douyin: { configured: boolean; reachable: boolean }
  recorder: { configured: boolean; container: string; synced_monitors: number }
}

/* ---------------- 基础 ---------------- */

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`/api/v1${path}`, {
    headers: { 'content-type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const body = await resp.text()
    let detail = body.slice(0, 200)
    try {
      detail = JSON.parse(body).detail ?? detail
    } catch {
      /* 原样使用 */
    }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

function fmtAgo(iso: string | null): string {
  if (!iso) return '-'
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (secs < 60) return '刚刚'
  if (secs < 3600) return `${Math.floor(secs / 60)} 分钟前`
  if (secs < 86400) return `${Math.floor(secs / 3600)} 小时前`
  return `${Math.floor(secs / 86400)} 天前`
}

function fmtTs(ms: number): string {
  const s = Math.floor(ms / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

const STATUS_CN: Record<string, string> = {
  discovered: '已发现',
  resolved: '已解析',
  media_ready: '媒体就绪',
  transcribing: '转写中',
  transcribed: '已转写',
  failed: '失败',
}

function statusBadge(status: string | null) {
  if (!status) return <span className="muted">-</span>
  const cn = STATUS_CN[status] ?? status
  if (status === 'transcribed') return <span className="badge ok">{cn}</span>
  if (status === 'failed') return <span className="badge err">{cn}</span>
  if (status === 'transcribing') return <span className="badge run">{cn}</span>
  return <span className="badge">{cn}</span>
}

/* ---------------- 总览 ---------------- */

function OverviewPage({ go }: { go: (tab: string) => void }) {
  const [monitors, setMonitors] = useState<LiveMonitor[]>([])
  const [recent, setRecent] = useState<LibraryItem[]>([])
  const [status, setStatus] = useState<SystemStatus | null>(null)

  useEffect(() => {
    api<LiveMonitor[]>('/live/monitors').then(setMonitors).catch(() => {})
    api<LibraryItem[]>('/source-items?limit=10&status=transcribed')
      .then(setRecent)
      .catch(() => {})
    api<SystemStatus>('/system/status').then(setStatus).catch(() => {})
  }, [])

  const liveCount = monitors.filter((m) => m.is_live === true).length
  const watching = monitors.filter((m) => m.live_monitor_enabled).length
  const totalSegs = monitors.reduce((acc, m) => acc + m.transcript_count, 0)

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
          <div className="stat-num">{totalSegs}</div>
          <div className="stat-label">直播转录段落</div>
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

/* ---------------- 主播 ---------------- */

type Draft = {
  profile_url: string
  live_room_url: string
  poll_interval_sec: number
  poll_interval_min_sec: string
  poll_interval_max_sec: string
  monitor_interval_sec: number
}

function AccountsPage() {
  const [rows, setRows] = useState<LiveMonitor[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)

  const reload = useCallback(() => {
    api<LiveMonitor[]>('/live/monitors')
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 30_000)
    return () => clearInterval(t)
  }, [reload])

  const patch = async (id: number, body: object) => {
    setBusy(true)
    setError(null)
    try {
      await api(`/source-accounts/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const startEdit = (r: LiveMonitor) => {
    setEditing(r.account_id)
    setDraft({
      profile_url: r.profile_url ?? '',
      live_room_url: r.live_room_url ?? '',
      poll_interval_sec: r.poll_interval_sec,
      poll_interval_min_sec: r.poll_interval_min_sec?.toString() ?? '',
      poll_interval_max_sec: r.poll_interval_max_sec?.toString() ?? '',
      monitor_interval_sec: r.monitor_interval_sec,
    })
  }

  const saveEdit = async (id: number) => {
    if (!draft) return
    setBusy(true)
    try {
      await api(`/source-accounts/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          url: draft.profile_url || null,
          live_room_url: draft.live_room_url || null,
          poll_interval_sec: draft.poll_interval_sec,
          poll_interval_min_sec: draft.poll_interval_min_sec === '' ? null : Number(draft.poll_interval_min_sec),
          poll_interval_max_sec: draft.poll_interval_max_sec === '' ? null : Number(draft.poll_interval_max_sec),
          monitor_interval_sec: draft.monitor_interval_sec,
        }),
      })
      setEditing(null)
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}
      <div className="toolbar">
        <h3 className="toolbar-title">全部主播（{rows.length}）</h3>
        <button className="primary" onClick={() => setAdding(true)}>
          ＋ 添加主播
        </button>
      </div>
      <table className="board">
        <thead>
          <tr>
            <th>主播</th>
            <th>主页</th>
            <th>直播间</th>
            <th>视频监控</th>
            <th>直播值守</th>
            <th>在播</th>
            <th>录制器</th>
            <th>最近会话</th>
            <th>分片/转录</th>
            <th>最近活动</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.account_id}>
              <td>{r.display_name}</td>
              <td>{r.profile_url ? <a href={r.profile_url} target="_blank" rel="noreferrer">主页</a> : '-'}</td>
              <td>
                {r.live_room_url ? (
                  <a href={r.live_room_url} target="_blank" rel="noreferrer">{r.room_id}</a>
                ) : (
                  <span className="muted">未配置</span>
                )}
              </td>
              <td>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={r.enabled && r.discovery_mode === 'auto_poll'}
                    disabled={busy}
                    onChange={(e) =>
                      patch(r.account_id, { discovery_mode: e.target.checked ? 'auto_poll' : 'manual' })
                    }
                  />
                  <span className="slider" />
                </label>
              </td>
              <td>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={r.live_monitor_enabled}
                    disabled={busy}
                    onChange={(e) => patch(r.account_id, { live_monitor_enabled: e.target.checked })}
                  />
                  <span className="slider" />
                </label>
              </td>
              <td>
                {r.is_live === true ? (
                  <span className="live-text">● 在播</span>
                ) : r.is_live === false ? (
                  '未播'
                ) : (
                  <span className="muted">未知</span>
                )}
              </td>
              <td>{r.live_monitor_enabled ? (r.recorder_synced ? '✓' : '✗ 未同步') : '-'}</td>
              <td>
                {r.session_status
                  ? `${STATUS_CN[r.session_status] ?? r.session_status}${r.session_closed ? '（已收尾）' : ''}`
                  : '-'}
              </td>
              <td>{r.session_status ? `${r.segment_count}/${r.transcript_count}` : '-'}</td>
              <td>{fmtAgo(r.last_segment_at)}</td>
              <td>
                {editing === r.account_id && draft ? (
                  <EditForm
                    draft={draft}
                    setDraft={setDraft}
                    onSave={() => saveEdit(r.account_id)}
                    onCancel={() => setEditing(null)}
                    busy={busy}
                  />
                ) : (
                  <button className="ghost" onClick={() => startEdit(r)}>
                    编辑
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">
        视频监控 = 跟踪新发布视频（自动发现+转写）；注册时的历史视频只采标题，去「视频库」手动转写。
        直播值守 = 开播自动录制分片并转写（历史不管）。值守配置变更后值守桥在下一轮同步（默认 10 分钟）生效。
      </p>
      {adding && (
        <AddAccountModal
          onClose={() => setAdding(false)}
          onDone={() => {
            setAdding(false)
            reload()
          }}
        />
      )}
    </div>
  )
}

function EditForm(props: {
  draft: Draft
  setDraft: (d: Draft) => void
  onSave: () => void
  onCancel: () => void
  busy: boolean
}) {
  const { draft, setDraft, onSave, onCancel, busy } = props
  const set = (k: keyof Draft, v: string) => setDraft({ ...draft, [k]: v })
  return (
    <div className="edit-form">
      <label>
        主页 URL
        <input value={draft.profile_url} onChange={(e) => set('profile_url', e.target.value)} placeholder="https://www.douyin.com/user/<sec_uid>" />
      </label>
      <label>
        直播间 URL
        <input value={draft.live_room_url} onChange={(e) => set('live_room_url', e.target.value)} placeholder="https://live.douyin.com/<room_id>" />
      </label>
      <label>
        发现轮询间隔（秒）
        <input type="number" value={draft.poll_interval_sec} onChange={(e) => set('poll_interval_sec', e.target.value)} />
      </label>
      <label>
        随机区间下限（秒，可空）
        <input type="number" value={draft.poll_interval_min_sec} onChange={(e) => set('poll_interval_min_sec', e.target.value)} placeholder="如 1800" />
      </label>
      <label>
        随机区间上限（秒，可空）
        <input type="number" value={draft.poll_interval_max_sec} onChange={(e) => set('poll_interval_max_sec', e.target.value)} placeholder="如 3600" />
      </label>
      <label>
        值守分片时长（秒，300-600）
        <input type="number" value={draft.monitor_interval_sec} onChange={(e) => set('monitor_interval_sec', e.target.value)} />
      </label>
      <div className="form-actions">
        <button className="primary" onClick={onSave} disabled={busy}>保存</button>
        <button onClick={onCancel} disabled={busy}>取消</button>
      </div>
    </div>
  )
}

function AddAccountModal(props: { onClose: () => void; onDone: () => void }) {
  const [platform, setPlatform] = useState('douyin')
  const [displayName, setDisplayName] = useState('')
  const [profileUrl, setProfileUrl] = useState('')
  const [liveRoomUrl, setLiveRoomUrl] = useState('')
  const [liveMonitor, setLiveMonitor] = useState(false)
  const [intervalMin, setIntervalMin] = useState('1800')
  const [intervalMax, setIntervalMax] = useState('3600')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const created = await api<{ id: number }>('/source-accounts', {
        method: 'POST',
        body: JSON.stringify({
          platform,
          url: profileUrl,
          display_name: displayName || undefined,
          discovery_mode: 'auto_poll',
          poll_interval_sec: Number(intervalMin) || 1800,
          poll_interval_min_sec: intervalMin === '' ? null : Number(intervalMin),
          poll_interval_max_sec: intervalMax === '' ? null : Number(intervalMax),
          live_room_url: platform === 'douyin' && liveRoomUrl ? liveRoomUrl : undefined,
        }),
      })
      if (liveMonitor && platform === 'douyin') {
        await api(`/source-accounts/${created.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ live_monitor_enabled: true, monitor_interval_sec: 600 }),
        })
      }
      props.onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const placeholder =
    platform === 'douyin'
      ? 'https://www.douyin.com/user/<sec_uid>'
      : platform === 'youtube'
        ? 'https://www.youtube.com/channel/<id>/videos'
        : 'https://space.bilibili.com/<uid>'

  return (
    <div className="modal-mask" onClick={props.onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>添加主播</h3>
        {error && <p className="error">{error}</p>}
        <label className="field">
          平台
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="douyin">抖音</option>
            <option value="youtube">YouTube</option>
            <option value="bilibili">B 站</option>
          </select>
        </label>
        <label className="field">
          名称（可选）
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="主播昵称，便于辨识" />
        </label>
        <label className="field">
          主页 URL（必填）
          <input value={profileUrl} onChange={(e) => setProfileUrl(e.target.value)} placeholder={placeholder} />
        </label>
        {platform === 'douyin' && (
          <>
            <label className="field">
              直播间 URL（可选，开启值守用）
              <input value={liveRoomUrl} onChange={(e) => setLiveRoomUrl(e.target.value)} placeholder="https://live.douyin.com/<room_id>" />
            </label>
            <label className="check-row">
              <input type="checkbox" checked={liveMonitor} onChange={(e) => setLiveMonitor(e.target.checked)} />
              同时开启直播值守（开播自动录制分片并转写）
            </label>
          </>
        )}
        <div className="grid-2">
          <label className="field">
            轮询区间下限（秒）
            <input type="number" value={intervalMin} onChange={(e) => setIntervalMin(e.target.value)} />
          </label>
          <label className="field">
            轮询区间上限（秒）
            <input type="number" value={intervalMax} onChange={(e) => setIntervalMax(e.target.value)} />
          </label>
        </div>
        <p className="hint">
          添加后立即执行首次扫描：只采集历史视频标题（不自动转录）；此后按随机区间轮询，发现新视频自动转写。
        </p>
        <div className="form-actions">
          <button className="primary" onClick={submit} disabled={busy || !profileUrl}>
            {busy ? '添加中…' : '添加'}
          </button>
          <button onClick={props.onClose} disabled={busy}>取消</button>
        </div>
      </div>
    </div>
  )
}

/* ---------------- 视频库 ---------------- */

function LibraryPage() {
  const [rows, setRows] = useState<LibraryItem[]>([])
  const [monitors, setMonitors] = useState<LiveMonitor[]>([])
  const [accountFilter, setAccountFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [drawer, setDrawer] = useState<number | null>(null)
  const [drillAccount, setDrillAccount] = useState<string | null>(null)

  const reload = useCallback(() => {
    const params = new URLSearchParams()
    if (drillAccount) params.set('account_id', drillAccount)
    else {
      if (accountFilter) params.set('account_id', accountFilter)
      if (statusFilter) params.set('status', statusFilter)
    }
    params.set('limit', '200')
    api<LibraryItem[]>(`/source-items?${params}`)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [accountFilter, statusFilter, drillAccount])

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
        <button className="ghost" onClick={reload}>刷新</button>
      </div>

      <table className="board">
        <thead>
          <tr>
            <th>主播</th>
            <th>平台</th>
            <th>标题</th>
            <th>类型</th>
            <th>状态</th>
            <th>时长</th>
            <th>发布</th>
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
              <td>{r.display_name}</td>
              <td>{r.platform}</td>
              <td className="title-cell">{r.title ?? '-'}</td>
              <td>{r.item_type === 'live' ? '直播' : '视频'}</td>
              <td>
                {statusBadge(r.status)}
                {r.backfill && r.status === 'discovered' && <span className="muted">（旧）</span>}
              </td>
              <td>{r.duration_ms ? `${Math.round(r.duration_ms / 60000)} 分` : '-'}</td>
              <td>{r.published_at ? new Date(r.published_at).toLocaleDateString() : '-'}</td>
              <td onClick={(e) => e.stopPropagation()}>
                {r.status === 'discovered' || r.status === 'failed' ? (
                  <button disabled={busyId === r.id} onClick={() => transcribe(r.id)}>
                    {busyId === r.id ? '派发中…' : '转写'}
                  </button>
                ) : r.status === 'transcribed' ? (
                  <>
                    <button className="ghost" onClick={() => setDrawer(r.id)}>正文</button>
                    <button disabled={busyId === r.id} onClick={() => extractPoints(r.id)}>
                      {busyId === r.id ? '派发中…' : '抽取观点'}
                    </button>
                  </>
                ) : (
                  '-'
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">
        旧视频（注册时首扫采集标题）不自动转录，点「转写」手动解析；已转写条目点击行或「正文」查看分段文本。
      </p>

      {drawer !== null && <TranscriptDrawer itemId={drawer} onClose={() => setDrawer(null)} />}
    </div>
  )
}

function TranscriptDrawer(props: { itemId: number; onClose: () => void }) {
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

/* ---------------- 设置 ---------------- */

function LLMSettingsCard() {
  const [templates, setTemplates] = useState<LLMTemplate[]>([])
  const [template, setTemplate] = useState('custom')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [apiKeySet, setApiKeySet] = useState(false)
  const [model, setModel] = useState('')
  const [chatPath, setChatPath] = useState('/chat/completions')
  const [effective, setEffective] = useState<LLMSettings['effective'] | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    api<LLMSettings>('/settings/llm')
      .then((s) => {
        setTemplates(s.templates)
        setTemplate(s.template)
        setBaseUrl(s.base_url)
        setApiKeySet(s.effective.key_set)
        setModel(s.model)
        setChatPath(s.chat_path)
        setEffective(s.effective)
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(reload, [reload])

  const applyTemplate = (key: string) => {
    setTemplate(key)
    const t = templates.find((x) => x.key === key)
    if (t) {
      setBaseUrl(t.base_url)
      setModel(t.models[0] ?? '')
      setChatPath(t.chat_path)
    }
  }

  const save = async () => {
    setError(null)
    setMsg(null)
    try {
      await api('/settings/llm', {
        method: 'PUT',
        body: JSON.stringify({
          template,
          base_url: baseUrl,
          api_key: apiKey,
          model,
          chat_path: chatPath,
        }),
      })
      setMsg('已保存')
      setApiKey('')
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const testConn = async () => {
    setTestResult('测试中…')
    try {
      const r = await api<{ ok: boolean; latency_ms?: number; model?: string; error?: string }>(
        '/settings/llm/test',
        { method: 'POST' },
      )
      setTestResult(
        r.ok
          ? `✅ 连通（${r.latency_ms}ms，模型 ${r.model}）`
          : `❌ ${r.error ?? '失败'}`,
      )
    } catch (e) {
      setTestResult(`❌ ${(e as Error).message}`)
    }
  }

  const current = templates.find((t) => t.key === template)

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>大模型（观点抽取）</h3>
      </div>
      <div className="stack">
        {error && <p className="error">{error}</p>}
        {msg && <p className="ok">{msg}</p>}
        <label className="field">
          服务商模板：选中后自动填地址与模型，可再手动改
          <select
            value={template}
            onChange={(e) => applyTemplate(e.target.value)}
          >
            {templates.map((t) => (
              <option key={t.key} value={t.key}>{t.label}</option>
            ))}
          </select>
          {current?.note && <span className="muted">{current.note}</span>}
        </label>
        <label className="field">
          Base URL
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.deepseek.com/v1" />
          {effective && <span className="muted">当前生效：{effective.base_url || '（未配置，Mock 模式）'}</span>}
        </label>
        <label className="field">
          API Key {apiKeySet && <span className="muted">（已设置，留空保持不变）</span>}
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={apiKeySet ? '••••••••' : 'sk-…'} />
        </label>
        <label className="field">
          模型（可下拉选或手输）
          <input value={model} onChange={(e) => setModel(e.target.value)} list="llm-models" placeholder="deepseek-chat" />
          <datalist id="llm-models">
            {(current?.models ?? []).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          {effective && <span className="muted">当前生效：{effective.model}</span>}
        </label>
        <label className="field">
          Chat 路径（一般不动；MiniMax 为 /text/chatcompletion_v2）
          <input value={chatPath} onChange={(e) => setChatPath(e.target.value)} />
        </label>
        <div className="form-actions">
          <button className="primary" onClick={save}>保存配置</button>
          <button onClick={testConn}>测试连接</button>
        </div>
        {testResult && <p className="muted">{testResult}</p>}
        <p className="hint">全部走 OpenAI 兼容协议；中转/聚合网关选「自定义」手填。配置存库即时生效，下一次观点抽取即使用新配置。</p>
      </div>
    </div>
  )
}

function SettingsPage() {
  const [stagger, setStagger] = useState('')
  const [pages, setPages] = useState('')
  const [pool, setPool] = useState('')
  const [effective, setEffective] = useState<SecuritySettings['effective'] | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    api<SecuritySettings>('/settings/security')
      .then((s) => {
        setStagger(s.discover_dispatch_stagger_max_sec?.toString() ?? '')
        setPages(s.douyin_discover_max_pages?.toString() ?? '')
        setPool(s.proxy_pool.join('\n'))
        setEffective(s.effective)
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(reload, [reload])

  const save = async () => {
    setError(null)
    setMsg(null)
    try {
      const saved = await api<SecuritySettings>('/settings/security', {
        method: 'PUT',
        body: JSON.stringify({
          discover_dispatch_stagger_max_sec: stagger === '' ? null : Number(stagger),
          douyin_discover_max_pages: pages === '' ? null : Number(pages),
          proxy_pool: pool.split('\n').map((s) => s.trim()).filter(Boolean),
        }),
      })
      setEffective(saved.effective)
      setMsg('已保存，worker 下一轮生效')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="settings-grid stack">
      <LLMSettingsCard />
      <div className="panel">
        <div className="panel-head">
          <h3>安全（防风控）</h3>
        </div>
        <div className="stack">
          {error && <p className="error">{error}</p>}
          {msg && <p className="ok">{msg}</p>}
          <label className="field">
            派发错峰上限（秒，0=关闭）：同批到期账号在 0~N 秒内随机延迟，避免同一秒并发访问平台
            <input type="number" min={0} max={3600} value={stagger} onChange={(e) => setStagger(e.target.value)} placeholder="留空用 .env 默认" />
            {effective && <span className="muted">当前生效：{effective.discover_dispatch_stagger_max_sec}s</span>}
          </label>
          <label className="field">
            抖音发现翻页上限（1-20，每页 20 条）：压首扫涌量，防身份池被打爆
            <input type="number" min={1} max={20} value={pages} onChange={(e) => setPages(e.target.value)} placeholder="留空用 .env 默认" />
            {effective && <span className="muted">当前生效：{effective.douyin_discover_max_pages} 页</span>}
          </label>
          <label className="field">
            代理池（每行一条 http/socks5 URL；按「身份↔出口稳定绑定」使用，勿按请求轮换）
            <textarea rows={5} value={pool} onChange={(e) => setPool(e.target.value)} placeholder={'http://user:pass@host:port\nsocks5://host:port'} />
          </label>
          <div>
            <button className="primary" onClick={save}>保存安全设置</button>
          </div>
          <p className="hint">防风控原则：低频、错峰、身份与出口稳定。设置存库即时生效（worker 下一轮读取）。</p>
        </div>
      </div>
    </div>
  )
}

/* ---------------- 外壳 ---------------- */

const NAV = [
  { key: 'overview', label: '总览' },
  { key: 'accounts', label: '主播' },
  { key: 'library', label: '视频库' },
  { key: 'viewpoints', label: '观点' },
  { key: 'review', label: '复核队列' },
  { key: 'jobs', label: '任务' },
  { key: 'settings', label: '设置' },
] as const

type Tab = (typeof NAV)[number]['key']

function App() {
  const [tab, setTab] = useState<Tab>('overview')
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden>
            <circle cx="16" cy="16" r="11" fill="none" stroke="#2f5a94" strokeWidth="1.4" />
            <circle cx="16" cy="16" r="7" fill="none" stroke="#4f83c9" strokeWidth="1.2" />
            <circle cx="16" cy="16" r="1.4" fill="#7fb0ec" />
            <path d="M16 16 L26 8" stroke="#e0442f" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
          <span>财经观点雷达</span>
        </div>
        <nav>
          {NAV.map((n) => (
            <button key={n.key} className={tab === n.key ? 'active' : ''} onClick={() => setTab(n.key)}>
              {n.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">V1 · 抖音采集基座</div>
      </aside>
      <main className="main">
        {tab === 'overview' && <OverviewPage go={(t) => setTab(t as Tab)} />}
        {tab === 'accounts' && <AccountsPage />}
        {tab === 'library' && <LibraryPage />}
        {tab === 'settings' && <SettingsPage />}
      </main>
    </div>
  )
}

export default App
