import { useCallback, useEffect, useState } from 'react'
import './App.css'

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

type SecuritySettings = {
  discover_dispatch_stagger_max_sec: number | null
  douyin_discover_max_pages: number | null
  proxy_pool: string[]
  effective: { discover_dispatch_stagger_max_sec: number; douyin_discover_max_pages: number }
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

type Draft = {
  profile_url: string
  live_room_url: string
  poll_interval_sec: number
  poll_interval_min_sec: string
  poll_interval_max_sec: string
  monitor_interval_sec: number
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`/api/v1${path}`, {
    headers: { 'content-type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const body = await resp.text()
    throw new Error(`${resp.status}: ${body.slice(0, 200)}`)
  }
  return resp.json() as Promise<T>
}

function fmtAgo(iso: string | null): string {
  if (!iso) return '-'
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (secs < 3600) return `${Math.floor(secs / 60)} 分钟前`
  if (secs < 86400) return `${Math.floor(secs / 3600)} 小时前`
  return `${Math.floor(secs / 86400)} 天前`
}

const STATUS_CN: Record<string, string> = {
  discovered: '发现',
  resolved: '已解析',
  media_ready: '媒体就绪',
  transcribing: '转写中',
  transcribed: '已收尾',
  failed: '失败',
}

function MonitorPage() {
  const [rows, setRows] = useState<LiveMonitor[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)

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
    const body: object = {
      url: draft.profile_url || null,
      live_room_url: draft.live_room_url || null,
      poll_interval_sec: draft.poll_interval_sec,
      poll_interval_min_sec: draft.poll_interval_min_sec === '' ? null : Number(draft.poll_interval_min_sec),
      poll_interval_max_sec: draft.poll_interval_max_sec === '' ? null : Number(draft.poll_interval_max_sec),
      monitor_interval_sec: draft.monitor_interval_sec,
    }
    setBusy(true)
    try {
      await api(`/source-accounts/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
      setEditing(null)
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      {error && <p className="error">{error}</p>}
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
                      patch(r.account_id, {
                        discovery_mode: e.target.checked ? 'auto_poll' : 'manual',
                      })
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
                  <span className="live">🔴 在播</span>
                ) : r.is_live === false ? (
                  '未播'
                ) : (
                  <span className="muted">未知</span>
                )}
              </td>
              <td>{r.live_monitor_enabled ? (r.recorder_synced ? '✓' : '✗ 未同步') : '-'}</td>
              <td>
                {r.session_status
                  ? `${STATUS_CN[r.session_status] ?? r.session_status}${r.session_closed ? '(closed)' : ''}`
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
                  <button onClick={() => startEdit(r)}>编辑</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && !error && <p>暂无抖音账号：先用 POST /api/v1/source-accounts 注册。</p>}
      <p className="hint">
        视频监控 = 跟踪该主播"新发布"的视频（自动发现+自动转写）；注册时的历史视频只采集标题，
        不自动转录（去「视频库」手动点转写）。直播值守 = 开播自动录制分片并转写（历史不管）。
        开启值守后第一次需打开一次 StreamCap Web UI 激活监控循环。页面每 30 秒自动刷新。
      </p>
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
      <div>
        <button onClick={onSave} disabled={busy}>保存</button>
        <button onClick={onCancel} disabled={busy}>取消</button>
      </div>
    </div>
  )
}

function LibraryPage() {
  const [rows, setRows] = useState<LibraryItem[]>([])
  const [monitors, setMonitors] = useState<LiveMonitor[]>([])
  const [accountFilter, setAccountFilter] = useState<string>('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const reload = useCallback(() => {
    const params = new URLSearchParams()
    if (accountFilter) params.set('account_id', accountFilter)
    if (statusFilter) params.set('status', statusFilter)
    params.set('limit', '200')
    api<LibraryItem[]>(`/source-items?${params}`)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [accountFilter, statusFilter])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    api<LiveMonitor[]>('/live/monitors').then(setMonitors).catch(() => {})
  }, [])

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

  return (
    <div>
      {error && <p className="error">{error}</p>}
      <div className="filters">
        <select value={accountFilter} onChange={(e) => setAccountFilter(e.target.value)}>
          <option value="">全部账号</option>
          {monitors.map((m) => (
            <option key={m.account_id} value={m.account_id}>
              {m.display_name}（{m.platform}）
            </option>
          ))}
        </select>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">全部状态</option>
          {['discovered', 'transcribing', 'transcribed', 'failed'].map((s) => (
            <option key={s} value={s}>
              {STATUS_CN[s] ?? s}
            </option>
          ))}
        </select>
        <button onClick={reload}>刷新</button>
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
            <th>发布时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.display_name}</td>
              <td>{r.platform}</td>
              <td className="title-cell">{r.title ?? '-'}</td>
              <td>{r.item_type === 'live' ? '直播' : '视频'}</td>
              <td>
                {STATUS_CN[r.status] ?? r.status}
                {r.backfill && r.status === 'discovered' && (
                  <span className="muted">（旧视频）</span>
                )}
              </td>
              <td>{r.duration_ms ? `${Math.round(r.duration_ms / 60000)} 分钟` : '-'}</td>
              <td>{r.published_at ? new Date(r.published_at).toLocaleDateString() : '-'}</td>
              <td>
                {r.status === 'discovered' || r.status === 'failed' ? (
                  <button disabled={busyId === r.id} onClick={() => transcribe(r.id)}>
                    {busyId === r.id ? '转写中…' : '转写'}
                  </button>
                ) : (
                  '-'
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">
        旧视频（注册账号时首扫采集的标题）默认不自动转录；点「转写」手动解析单条。
        视频监控开着的主播，新发布视频会自动发现并转写。
      </p>
    </div>
  )
}

function SecurityPage() {
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
      const body = {
        discover_dispatch_stagger_max_sec: stagger === '' ? null : Number(stagger),
        douyin_discover_max_pages: pages === '' ? null : Number(pages),
        proxy_pool: pool
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean),
      }
      const saved = await api<SecuritySettings>('/settings/security', {
        method: 'PUT',
        body: JSON.stringify(body),
      })
      setEffective(saved.effective)
      setMsg('已保存，worker 下一轮生效')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="security">
      {error && <p className="error">{error}</p>}
      {msg && <p className="ok">{msg}</p>}
      <p className="hint">
        防风控原则：低频、错峰、身份与出口稳定。以下设置存库覆盖 .env 默认；worker 下一轮读取生效。
        代理池为出口代理预留位（下载/解析链路接线挂账 EPIC-04+），当前仅存储。
      </p>
      <label>
        派发错峰上限（秒，0=关闭）：同批到期账号在 0~N 秒内随机延迟，避免同一秒并发访问平台。
        <input type="number" min={0} max={3600} value={stagger} onChange={(e) => setStagger(e.target.value)} placeholder="留空用 .env 默认" />
        {effective && <span className="muted"> 当前生效：{effective.discover_dispatch_stagger_max_sec}s</span>}
      </label>
      <label>
        抖音发现翻页上限（1-20，每页 20 条）：压首扫涌量，防身份池被打爆。
        <input type="number" min={1} max={20} value={pages} onChange={(e) => setPages(e.target.value)} placeholder="留空用 .env 默认" />
        {effective && <span className="muted"> 当前生效：{effective.douyin_discover_max_pages} 页</span>}
      </label>
      <label>
        代理池（每行一条 http/socks5 URL；按"身份↔出口稳定绑定"原则使用，勿按请求轮换）
        <textarea rows={5} value={pool} onChange={(e) => setPool(e.target.value)} placeholder={'http://user:pass@host:port\nsocks5://host:port'} />
      </label>
      <button onClick={save}>保存安全设置</button>
    </div>
  )
}

function App() {
  const [tab, setTab] = useState<'monitor' | 'library' | 'security'>('monitor')
  return (
    <div className="app">
      <nav>
        <h1>财经观点雷达 · 监控台</h1>
        <button className={tab === 'monitor' ? 'active' : ''} onClick={() => setTab('monitor')}>
          监控
        </button>
        <button className={tab === 'library' ? 'active' : ''} onClick={() => setTab('library')}>
          视频库
        </button>
        <button className={tab === 'security' ? 'active' : ''} onClick={() => setTab('security')}>
          安全设置
        </button>
      </nav>
      {tab === 'monitor' ? <MonitorPage /> : tab === 'library' ? <LibraryPage /> : <SecurityPage />}
    </div>
  )
}

export default App
