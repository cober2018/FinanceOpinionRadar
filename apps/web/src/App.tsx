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
  progress: { phase: string; detail: string; at: string } | null
  chat_count: number
  is_asset: boolean
  expires_at: string | null
  viewpoint_total?: number
  viewpoint_needs_review?: number
  viewpoint_confirmed?: number
}

type VPVideoRow = {
  id: number
  platform: string
  display_name: string
  title: string | null
  item_type: string
  status: string
  published_at: string | null
  viewpoint_total: number
  viewpoint_needs_review: number
  viewpoint_confirmed: number
}

type DanmakuStats = { total: number; sessions: number; latest_message_at: string | null }

type DanmakuMessage = { user_name: string | null; text: string | null; published_at: string | null }

type DanmakuDetail = {
  item_id: number
  title: string | null
  total: number
  messages: DanmakuMessage[]
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

function fmtDateTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

const STATUS_CN: Record<string, string> = {
  discovered: '已发现',
  resolved: '已解析',
  media_ready: '媒体就绪',
  transcribing: '转写中',
  transcribed: '已转写',
  extracting: '抽取观点中',
  reviewing: '待审核',
  ready: '已就绪',
  failed: '失败',
}

function statusBadgeWithProgress(
  status: string | null,
  progress: { phase: string; detail: string } | null,
) {
  const base = statusBadge(status)
  if (!progress || status !== 'transcribing') return base
  const phaseCn: Record<string, string> = {
    downloading: '下载视频',
    downloaded: '已下载',
    normalizing: '音频标准化',
    asr_running: '语音识别中',
    asr_done: '识别完成',
  }
  const label = phaseCn[progress.phase]
  return (
    <span>
      {base}
      {label && <div className="muted" style={{ fontSize: 11 }}>{label}</div>}
    </span>
  )
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

  const scanNow = async (r: LiveMonitor) => {
    setBusy(true)
    setError(null)
    try {
      await api(`/source-accounts/${r.account_id}/scan`, { method: 'POST' })
      setError(null)
      setTimeout(reload, 4000)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const removeAccount = async (r: LiveMonitor) => {
    const n = window.confirm(
      `确定删除主播「${r.display_name}」？\n其全部内容（视频/直播转录/观点）将一并物理删除，不可恢复。`,
    )
    if (!n) return
    setBusy(true)
    setError(null)
    try {
      await api(`/source-accounts/${r.account_id}`, { method: 'DELETE' })
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
                  <>
                    <button className="ghost" onClick={() => scanNow(r)}>扫描</button>{' '}
                    <button className="ghost" onClick={() => startEdit(r)}>编辑</button>{' '}
                    <button className="ghost danger" onClick={() => removeAccount(r)}>删除</button>
                  </>
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
          existingNames={rows.map((r) => r.display_name)}
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

function AddAccountModal(props: {
  onClose: () => void
  onDone: () => void
  existingNames: string[]
}) {
  const rowsCache = props.existingNames
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
    // 同名防呆：已有同名主播时二次确认（不同 URL 形态的同频道最易重复添加）
    const dupName = Boolean(displayName) && rowsCache.includes(displayName)
    if (dupName) {
      const go = window.confirm(`已存在同名主播「${displayName}」，确认仍要添加？`)
      if (!go) {
        setBusy(false)
        return
      }
    }
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
  const [assetFilter, setAssetFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [drawer, setDrawer] = useState<number | null>(null)
  const [chatDrawer, setChatDrawer] = useState<number | null>(null)
  const [drillAccount, setDrillAccount] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

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
    params.set('limit', '200')
    api<LibraryItem[]>(`/source-items?${params}`)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [accountFilter, statusFilter, drillAccount, assetFilter])

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
                {r.backfill && r.status === 'discovered' && <span className="muted">（旧）</span>}
                <div className="muted" style={{ fontSize: 11 }}>
                  {r.is_asset
                    ? '⭐ 精华 · 永久保留'
                    : r.expires_at
                      ? `${Math.max(0, Math.ceil((new Date(r.expires_at).getTime() - Date.now()) / 86400000))} 天后清理`
                      : ''}
                </div>
              </td>
              <td className="muted" style={{ fontSize: 11, maxWidth: 160 }}>
                {r.progress?.detail ?? '-'}
              </td>
              <td>{r.duration_ms ? `${Math.round(r.duration_ms / 60000)} 分` : '-'}</td>
              <td>{fmtDateTime(r.published_at)}</td>
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
                    {busyId === r.id ? '派发中…' : '转写'}
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
        旧视频（注册时首扫采集标题）不自动转录，点「转写」手动解析；已转写条目点击行或「正文」查看分段文本。
      </p>

      {drawer !== null && <TranscriptDrawer itemId={drawer} onClose={() => setDrawer(null)} />}
      {chatDrawer !== null && (
        <DanmakuDrawer itemId={chatDrawer} onClose={() => setChatDrawer(null)} />
      )}
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

function fmtClock(iso: string | null): string {
  if (!iso) return '--:--'
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function DanmakuDrawer(props: { itemId: number; onClose: () => void }) {
  const [data, setData] = useState<DanmakuDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<DanmakuDetail>(`/source-items/${props.itemId}/chat-messages`)
      .then(setData)
      .catch((e: Error) => setError(e.message))
  }, [props.itemId])

  return (
    <div className="drawer-mask" onClick={props.onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <div className="drawer-title">{data?.title ?? `内容 #${props.itemId}`}</div>
            <div className="muted">直播弹幕 · {data?.total ?? 0} 条</div>
          </div>
          <button className="ghost" onClick={props.onClose}>关闭</button>
        </div>
        {error && <p className="error pad">{error}</p>}
        <div className="drawer-body">
          {data?.messages.map((m, i) => (
            <div key={i} className="ts-row">
              <span className="ts-time">{fmtClock(m.published_at)}</span>
              <span className="ts-text">
                <b>{m.user_name ?? '匿名'}</b>
                {m.text ? `：${m.text}` : ''}
              </span>
            </div>
          ))}
          {data && data.messages.length === 0 && (
            <p className="muted pad">该会话暂无弹幕入库（采集窗口或未开播）</p>
          )}
          {data && data.messages.length < data.total && (
            <p className="muted pad">仅显示最早 {data.messages.length} 条，共 {data.total} 条入库</p>
          )}
        </div>
      </div>
    </div>
  )
}


/* ---------------- 观点 / 复核队列 ---------------- */

type VPRow = {
  id: number
  creator_name: string | null
  topic_name: string | null
  entity_name: string | null
  claim: string
  stance: string
  horizon: string | null
  confidence: number
  importance: number
  as_of_date: string | null
  verification_status: string
  source_item_id: number
}

const STANCE_CN: Record<string, string> = {
  strong_bullish: '强烈看多',
  bullish: '看多',
  neutral: '中性',
  bearish: '看空',
  strong_bearish: '强烈看空',
  unclear: '不明',
}

const VP_STATUS_CN: Record<string, string> = {
  candidate: '候选',
  needs_review: '待复核',
  confirmed: '已确认',
  rejected: '已驳回',
}

function ViewpointsPage({ mode }: { mode: 'all' | 'review' }) {
  const [rows, setRows] = useState<VPVideoRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [vpDrawer, setVpDrawer] = useState<VPVideoRow | null>(null)
  const [textDrawer, setTextDrawer] = useState<VPVideoRow | null>(null)

  const reload = useCallback(() => {
    const params = new URLSearchParams({ limit: '500' })
    if (mode === 'review') params.set('pending_review', 'true')
    else params.set('has_viewpoints', 'true')
    api<VPVideoRow[]>(`/source-items?${params}`)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [mode])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 15000)
    return () => clearInterval(t)
  }, [reload])

  const pendingTotal = rows.reduce((s, r) => s + r.viewpoint_needs_review, 0)
  const allTotal = rows.reduce((s, r) => s + r.viewpoint_total, 0)

  return (
    <div className="stack">
      {error && <p className="error">{error}</p>}
      <div className="toolbar">
        <h3 className="toolbar-title">
          {mode === 'review'
            ? `复核队列（${rows.length} 个视频 · ${pendingTotal} 条待审观点）`
            : `观点（${rows.length} 个视频 · ${allTotal} 条观点）`}
        </h3>
      </div>
      <p className="hint">
        一个视频一条记录；点行或「观点」查看该视频抽出的全部观点（立场是每条观点自己的属性），「文案」看视频转写原文，证据在观点列表里逐条查看。
      </p>
      <table className="board">
        <thead>
          <tr>
            <th>主播</th>
            <th>视频标题</th>
            <th>发布时间</th>
            <th>观点</th>
            <th>内容状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="clickable" onClick={() => setVpDrawer(r)}>
              <td>{r.display_name}</td>
              <td className="title-cell" title={r.title ?? ''}>{r.title ?? '-'}</td>
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
              <td onClick={(e) => e.stopPropagation()}>
                <button onClick={() => setVpDrawer(r)}>观点</button>{' '}
                <button className="ghost" onClick={() => setTextDrawer(r)}>文案</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={6} className="muted pad">
                {mode === 'review'
                  ? '复核队列为空（自动复核通过的不进队列）'
                  : '暂无观点：先在视频库转写内容并点「抽取观点」'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
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

function VideoViewpointsDrawer(props: {
  item: VPVideoRow
  mode: 'all' | 'review'
  onClose: () => void
  onChanged: () => void
}) {
  const { item, mode } = props
  const [vps, setVps] = useState<VPRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [evidence, setEvidence] = useState<{ viewpointId: number; sourceItemId: number } | null>(null)
  const [editingVp, setEditingVp] = useState<VPRow | null>(null)

  const reload = useCallback(() => {
    api<{ items: VPRow[] }>(`/viewpoints?source_item_id=${item.id}&page_size=200`)
      .then((d) => {
        const list =
          mode === 'review'
            ? d.items.filter(
                (v) => v.verification_status === 'needs_review' || v.verification_status === 'candidate',
              )
            : d.items
        setVps(list)
      })
      .catch((e: Error) => setError(e.message))
  }, [item.id, mode])

  useEffect(() => {
    reload()
  }, [reload])

  const review = async (id: number, action: 'confirm' | 'reject', reason?: string) => {
    setBusyId(id)
    setError(null)
    try {
      const q = reason ? `?reason=${encodeURIComponent(reason)}` : ''
      await api(`/viewpoints/${id}/${action}${q}`, { method: 'POST' })
      reload()
      props.onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  const doReject = (id: number) => {
    const reason = window.prompt('驳回原因（必填）：')
    if (reason && reason.trim()) review(id, 'reject', reason.trim())
  }

  return (
    <div className="drawer-mask" onClick={props.onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <b>{item.display_name}</b>
            <span className="muted"> · {item.title ?? '无标题'}</span>
            <div className="muted" style={{ fontSize: 11 }}>
              {fmtDateTime(item.published_at)} · {vps.length} 条观点
              {mode === 'review' ? '（仅列待审）' : ''}
            </div>
          </div>
          <button className="ghost" onClick={props.onClose}>关闭</button>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="drawer-body">
          {vps.map((r) => (
            <div key={r.id} className="card" style={{ marginBottom: 10 }}>
              <div>
                <span className={`badge ${r.stance === 'bullish' ? 'ok' : r.stance === 'bearish' ? 'err' : ''}`}>
                  {STANCE_CN[r.stance] ?? r.stance}
                </span>{' '}
                <span className={`badge ${r.verification_status === 'confirmed' ? 'ok' : r.verification_status === 'rejected' ? 'err' : 'run'}`}>
                  {VP_STATUS_CN[r.verification_status] ?? r.verification_status}
                </span>{' '}
                <span className="muted" style={{ fontSize: 11 }}>
                  置信 {r.confidence.toFixed(2)}
                  {r.topic_name ? ` · ${r.topic_name}` : ''}
                  {r.entity_name ? ` · ${r.entity_name}` : ''}
                </span>
              </div>
              <p style={{ margin: '8px 0' }}>{r.claim}</p>
              <div>
                {(r.verification_status === 'needs_review' || r.verification_status === 'candidate') && (
                  <>
                    <button disabled={busyId === r.id} onClick={() => review(r.id, 'confirm')}>通过</button>{' '}
                    <button className="ghost" disabled={busyId === r.id} onClick={() => doReject(r.id)}>驳回</button>{' '}
                  </>
                )}
                <button className="ghost" onClick={() => setEditingVp(r)}>修改</button>{' '}
                <button
                  className="ghost"
                  onClick={() => setEvidence({ viewpointId: r.id, sourceItemId: item.id })}
                >
                  证据
                </button>
              </div>
            </div>
          ))}
          {vps.length === 0 && <p className="muted pad">该视频暂无观点。</p>}
        </div>
      </div>
      {evidence !== null && (
        <ViewpointEvidenceDrawer
          viewpointId={evidence.viewpointId}
          sourceItemId={evidence.sourceItemId}
          onClose={() => setEvidence(null)}
          onReviewed={reload}
        />
      )}
      {editingVp !== null && (
        <EditViewpointModal
          vp={editingVp}
          onClose={() => setEditingVp(null)}
          onDone={() => {
            setEditingVp(null)
            reload()
            props.onChanged()
          }}
        />
      )}
    </div>
  )
}

function AssetLibraryPage() {
  const [rows, setRows] = useState<LibraryItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [drawer, setDrawer] = useState<number | null>(null)

  const reload = useCallback(() => {
    api<LibraryItem[]>('/source-items?asset=true&limit=500')
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [])

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


function ViewpointEvidenceDrawer(props: {
  viewpointId: number
  sourceItemId: number
  onClose: () => void
  onReviewed: () => void
}) {
  const [data, setData] = useState<{
    title: string | null
    display_name: string
    segments: { sequence_no: number; start_ms: number; end_ms: number; text: string }[]
  } | null>(null)
  const [vpRows, setVpRows] = useState<
    { viewpoint_id: number; claim: string; stance: string; verification_status: string; evidences: { segment_id: number; start_ms: number; end_ms: number; text: string }[] }[]
  >([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // 正文与证据按 source_item_id 取；复核动作按 viewpoint_id——两个 id 不能混
    api<{ title: string | null; display_name: string; segments: { sequence_no: number; start_ms: number; end_ms: number; text: string }[] }>(
      `/source-items/${props.sourceItemId}/transcript`,
    )
      .then(setData)
      .catch((e: Error) => setError(e.message))
    api<{ viewpoint_id: number; claim: string; stance: string; verification_status: string; evidences: { segment_id: number; start_ms: number; end_ms: number; text: string }[] }[]>(
      `/source-items/${props.sourceItemId}/viewpoint-evidence`,
    )
      .then((all) => setVpRows(all.filter((v) => v.viewpoint_id === props.viewpointId)))
      .catch(() => {})
  }, [props.viewpointId, props.sourceItemId])

  const review = async (action: 'confirm' | 'reject') => {
    try {
      await api(`/viewpoints/${props.viewpointId}/${action}`, { method: 'POST' })
      props.onClose()
      props.onReviewed()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="drawer-mask" onClick={props.onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <div className="drawer-title">{data?.title ?? `观点 #${props.viewpointId}`}</div>
            <div className="muted">{data?.display_name} · {data?.segments.length ?? 0} 段</div>
          </div>
          <button className="ghost" onClick={props.onClose}>关闭</button>
        </div>
        {error && <p className="error pad">{error}</p>}
        <div className="drawer-body">
          {vpRows.map((vp) => (
            <div key={vp.viewpoint_id} style={{ marginBottom: 14 }}>
              <div style={{ fontWeight: 600, color: 'var(--ink-900)' }}>
                {vp.claim} <span className="badge">{STANCE_CN[vp.stance] ?? vp.stance}</span>{' '}
                <span className="badge">{VP_STATUS_CN[vp.verification_status] ?? vp.verification_status}</span>{' '}
                <button className="ghost" onClick={() => review('confirm')}>通过</button>{' '}
                <button className="ghost" onClick={() => review('reject')}>驳回</button>
              </div>
              {vp.evidences.map((ev) => (
                <div key={ev.segment_id} className="ts-row">
                  <span className="ts-time">{fmtTs(ev.start_ms)}–{fmtTs(ev.end_ms)}</span>
                  <span className="ts-text">{ev.text}</span>
                </div>
              ))}
            </div>
          ))}
          {vpRows.length === 0 && data && (
            <>
              {data.segments.map((s) => (
                <div key={s.sequence_no} className="ts-row">
                  <span className="ts-time">{fmtTs(s.start_ms)}–{fmtTs(s.end_ms)}</span>
                  <span className="ts-text">{s.text}</span>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* ---------------- 观点修改弹窗（含 AI 润色） ---------------- */

function EditViewpointModal(props: {
  vp: VPRow
  onClose: () => void
  onDone: () => void
}) {
  const [claim, setClaim] = useState(props.vp.claim)
  const [stance, setStance] = useState(props.vp.stance)
  const [horizon, setHorizon] = useState(props.vp.horizon ?? '')
  const [importance, setImportance] = useState(String(props.vp.importance))
  const [confidence, setConfidence] = useState(String(props.vp.confidence))
  const [reason, setReason] = useState('')
  const [polishing, setPolishing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const polish = async () => {
    if (!claim.trim()) return
    setPolishing(true)
    setError(null)
    try {
      const r = await api<{ ok: boolean; polished?: string; error?: string }>('/llm/polish', {
        method: 'POST',
        body: JSON.stringify({ claim: claim.trim() }),
      })
      if (r.ok && r.polished) setClaim(r.polished)
      else setError(r.error ?? '润色失败')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPolishing(false)
    }
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await api(`/viewpoints/${props.vp.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          claim: claim.trim(),
          stance,
          horizon: horizon || null,
          importance: Number(importance),
          confidence: Number(confidence),
          reason: reason || '人工修正' + (claim.trim() !== props.vp.claim ? '（含润色）' : ''),
        }),
      })
      props.onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-mask" onClick={props.onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>修改观点 #{props.vp.id}</h3>
        {error && <p className="error">{error}</p>}
        <label className="field">
          观点内容（可手改，或点「AI 润色」自动整理表达——立场与方向保持不变）
          <textarea rows={3} value={claim} onChange={(e) => setClaim(e.target.value)} />
          <div>
            <button className="ghost" disabled={polishing} onClick={polish}>
              {polishing ? '润色中…' : '✨ AI 润色'}
            </button>
          </div>
        </label>
        <div className="grid-2">
          <label className="field">
            立场
            <select value={stance} onChange={(e) => setStance(e.target.value)}>
              {Object.entries(STANCE_CN).map(([v, cn]) => (
                <option key={v} value={v}>{cn}</option>
              ))}
            </select>
          </label>
          <label className="field">
            时间维度
            <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              <option value="">未提及</option>
              {['intraday', '1-3D', '1-4W', '1-3M', '3M+'].map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </label>
          <label className="field">
            置信度（0-1）
            <input type="number" step="0.05" min="0" max="1" value={confidence} onChange={(e) => setConfidence(e.target.value)} />
          </label>
          <label className="field">
            重要性（0-1）
            <input type="number" step="0.05" min="0" max="1" value={importance} onChange={(e) => setImportance(e.target.value)} />
          </label>
        </div>
        <label className="field">
          修改原因（写入审计日志）
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="如：AI 润色 / 人工精简表达" />
        </label>
        <div className="form-actions">
          <button className="primary" onClick={save} disabled={busy || !claim.trim()}>保存修改</button>
          <button onClick={props.onClose} disabled={busy}>取消</button>
        </div>
      </div>
    </div>
  )
}

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

function JobCenterPage() {
  const [rows, setRows] = useState<JobRow[]>([])
  const [stats, setStats] = useState<{ total: number; running: number; success: number; failed: number } | null>(null)
  const [typeF, setTypeF] = useState('')
  const [statusF, setStatusF] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)

  const reload = useCallback(() => {
    const params = new URLSearchParams()
    if (typeF) params.set('job_type', typeF)
    if (statusF) params.set('status', statusF)
    if (dateFrom) params.set('date_from', dateFrom)
    params.set('limit', '200')
    api<JobRow[]>(`/jobs?${params}`)
      .then(setRows)
      .catch((e: Error) => setError(e.message))
    api<{ total: number; running: number; success: number; failed: number }>('/jobs/stats')
      .then(setStats)
      .catch(() => {})
  }, [typeF, statusF, dateFrom])

  useEffect(() => {
    reload()
    const t = setInterval(reload, 15_000)
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
              <td className="muted" style={{ fontSize: 11 }}>{r.progress_detail ?? '-'}</td>
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
            代理池（每行一条 http/socks5 URL）——已接入媒体下载：同账号恒走同一出口（稳定绑定），失败自动冷却 10 分钟；留空则直连
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
  { key: 'assets', label: '资产库' },
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
        {tab === 'assets' && <AssetLibraryPage />}
        {tab === 'viewpoints' && <ViewpointsPage mode="all" />}
        {tab === 'review' && <ViewpointsPage mode="review" />}
        {tab === 'jobs' && <JobCenterPage />}
        {tab === 'settings' && <SettingsPage />}
      </main>
    </div>
  )
}

export default App
