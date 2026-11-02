import { STATUS_CN, fmtAgo } from '../../shared/format'
import { api } from '../../shared/api'
import type { Draft, LiveMonitor } from '../../shared/types'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function AccountsPage() {
  const [rows, setRows] = useState<LiveMonitor[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)

  const reload = useCallback((silent = false) => {
    api<LiveMonitor[]>('/live/monitors', undefined, { silent })
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(() => {
    reload()
    const t = setInterval(() => reload(true), 30_000)
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
            <th>人气</th>
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
              <td>
                {r.is_live === true && r.viewer_count != null ? (
                  <span className="live-text">● {r.viewer_count} 人在看</span>
                ) : r.viewer_avg != null ? (
                  `均 ${r.viewer_avg}${r.viewer_peak != null ? ` / 峰 ${r.viewer_peak}` : ''}（${r.viewer_sample_count ?? '-'}片）`
                ) : (
                  '-'
                )}
              </td>
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
    // 表单弹窗不允许点遮罩关闭（误触会丢已填内容），只能走「取消/保存」
    <div className="modal-mask" onClick={(e) => e.stopPropagation()}>
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

