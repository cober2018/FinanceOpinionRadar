import { fmtDateTime } from '../../shared/format'
import { api } from '../../shared/api'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
/* ---------------- 开放接口（API Key + 推送渠道） ---------------- */

type ApiKeyRow = {
  id: number
  name: string
  prefix: string
  status: string
  last_used_at: string | null
  created_at: string | null
}

type PushChannelRow = {
  id: number
  name: string
  channel_type: string
  url: string
  has_secret: boolean
  enabled: boolean
  summary_enabled: boolean
  viewpoint_enabled: boolean
  created_at: string | null
}

type VideoSummaryDeliveryRow = {
  id: number
  channel_name: string
  event_id: string
  item_id: number
  version: number
  event_state: string
  status: string
  attempt: number
  last_http_status: number | null
  error: string | null
  updated_at: string | null
}

const CHANNEL_TYPE_CN: Record<string, string> = {
  feishu: '飞书机器人',
  dingtalk: '钉钉机器人',
  generic_webhook: '通用 Webhook',
}

export function OpenApiPage() {
  const [service, setService] = useState<'summaries' | 'broadcasters' | 'viewpoints' | 'transcripts' | 'meta'>('summaries')
  const [mode, setMode] = useState<'pull' | 'push'>('pull')
  const [trialKey, setTrialKey] = useState('')
  const [trialCursor, setTrialCursor] = useState('0')
  const [trialPageSize, setTrialPageSize] = useState('50')
  const [trialLoading, setTrialLoading] = useState(false)
  const [trialResult, setTrialResult] = useState<string | null>(null)
  const [keys, setKeys] = useState<ApiKeyRow[]>([])
  const [keysStatus, setKeysStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [channels, setChannels] = useState<PushChannelRow[]>([])
  const [channelsStatus, setChannelsStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [summaryDeliveries, setSummaryDeliveries] = useState<VideoSummaryDeliveryRow[]>([])
  const [deliveriesStatus, setDeliveriesStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState<string | null>(null)
  const [newKey, setNewKey] = useState<{ name: string; api_key: string } | null>(null)
  const [keyName, setKeyName] = useState('')
  const [chForm, setChForm] = useState(false)
  const [chDraft, setChDraft] = useState({ name: '', channel_type: 'generic_webhook', url: '', secret: '', viewpoint_enabled: false })
  const [testing, setTesting] = useState<number | null>(null)
  const summarySubscribers = channels.filter((channel) =>
    channel.channel_type === 'generic_webhook' && channel.has_secret && channel.enabled && channel.summary_enabled
  )

  const reload = useCallback(() => {
    api<{ items: ApiKeyRow[] }>('/open-admin/api-keys').then((d) => {
      setKeys(d.items)
      setKeysStatus('ready')
    }).catch((e: Error) => {
      setKeys([])
      setKeysStatus('error')
      setError(e.message)
    })
    api<{ items: PushChannelRow[] }>('/open-admin/push-channels').then((d) => {
      setChannels(d.items)
      setChannelsStatus('ready')
    }).catch((e: Error) => {
      setChannels([])
      setChannelsStatus('error')
      setError(e.message)
    })
    api<{ items: VideoSummaryDeliveryRow[] }>('/open-admin/video-summary-deliveries').then((d) => {
      setSummaryDeliveries(d.items)
      setDeliveriesStatus('ready')
    }).catch((e: Error) => {
      setSummaryDeliveries([])
      setDeliveriesStatus('error')
      setError(e.message)
    })
  }, [])
  useEffect(reload, [reload])

  const createKey = async () => {
    if (!keyName.trim()) return
    setError(null)
    try {
      const r = await api<{ id: number; api_key: string }>('/open-admin/api-keys', {
        method: 'POST',
        body: JSON.stringify({ name: keyName.trim() }),
      })
      setNewKey({ name: keyName.trim(), api_key: r.api_key })
      setKeyName('')
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const toggleKey = async (r: ApiKeyRow) => {
    if (r.status === 'active' && !window.confirm(`停用「${r.name}」？此 Key 对全部开放 API 的访问将暂停，之后可重新启用。`)) return
    setError(null)
    try {
      await api(`/open-admin/api-keys/${r.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: r.status !== 'active' }),
      })
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const trialPull = async () => {
    const cursor = Number(trialCursor)
    const pageSize = Number(trialPageSize)
    if (!trialKey.trim()) {
      setTrialResult('请填写 API Key。')
      return
    }
    if (service === 'summaries' && (!Number.isSafeInteger(cursor) || cursor < 0 || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 200)) {
      setTrialResult('cursor 须为非负整数，page_size 须为 1–200。')
      return
    }
    setTrialLoading(true)
    setTrialResult(null)
    try {
      const query = new URLSearchParams({ cursor: String(cursor), page_size: String(pageSize) })
      const path = service === 'broadcasters' ? '/open/v1/broadcasters' : `/open/v1/video-summaries?${query}`
      const resp = await fetch(path, { headers: { 'X-API-Key': trialKey.trim() } })
      const body: unknown = await resp.json()
      setTrialResult(JSON.stringify({ http_status: resp.status, body }, null, 2))
    } catch (e) {
      setTrialResult(`请求失败：${(e as Error).message}`)
    } finally {
      setTrialLoading(false)
    }
  }

  const revokeKey = (r: ApiKeyRow) => {
    if (!window.confirm(`吊销「${r.name}」（${r.prefix}…）？使用该 Key 的下游系统将立即失效。`)) return
    api(`/open-admin/api-keys/${r.id}`, { method: 'DELETE' }).then(reload).catch((e: Error) => setError(e.message))
  }

  const createChannel = async () => {
    setError(null)
    try {
      await api('/open-admin/push-channels', {
        method: 'POST',
        body: JSON.stringify({
          name: chDraft.name.trim(),
          channel_type: chDraft.channel_type,
          url: chDraft.url.trim(),
          secret: chDraft.secret.trim() || null,
          viewpoint_enabled: chDraft.viewpoint_enabled,
        }),
      })
      setChForm(false)
      setChDraft({ name: '', channel_type: 'generic_webhook', url: '', secret: '', viewpoint_enabled: false })
      reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const toggleChannel = (r: PushChannelRow) => {
    api(`/open-admin/push-channels/${r.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: !r.enabled }),
    }).then(reload).catch((e: Error) => setError(e.message))
  }

  const toggleSummary = (r: PushChannelRow) => {
    api(`/open-admin/push-channels/${r.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ summary_enabled: !r.summary_enabled }),
    }).then(reload).catch((e: Error) => setError(e.message))
  }

  const toggleViewpoints = (r: PushChannelRow) => {
    api(`/open-admin/push-channels/${r.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ viewpoint_enabled: !r.viewpoint_enabled }),
    }).then(reload).catch((e: Error) => setError(e.message))
  }

  const reconcileSummary = (delivery: VideoSummaryDeliveryRow, outcome: 'sent' | 'retry') => {
    const note = window.prompt(outcome === 'sent' ? '请填写对方确认收到的依据' : '请填写核对后重试的原因')
    if (!note?.trim()) return
    api(`/open-admin/video-summary-deliveries/${delivery.id}/reconcile`, {
      method: 'POST',
      body: JSON.stringify({ outcome, note: note.trim() }),
    }).then(reload).catch((e: Error) => setError(e.message))
  }

  const testChannel = async (r: PushChannelRow) => {
    setTesting(r.id)
    setError(null)
    try {
      await api(`/open-admin/push-channels/${r.id}/test`, { method: 'POST' })
      window.alert('渠道测试消息已发出，请到接收端确认；这不代表视频总结事件已投递。')
    } catch (e) {
      window.alert((e as Error).message)
    } finally {
      setTesting(null)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h2>开放接口</h2>
        <p className="muted">
          对外成果输出（/open/v1）：X-API-Key 鉴权、只读、默认仅已确认观点；推送支持飞书/钉钉/通用 Webhook。
          本系统是产品矩阵的数据底座，深加工由下游完成。
        </p>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="gateway-layout">
        <aside className="gateway-catalog" aria-label="服务目录">
          <div className="gateway-catalog-label">服务目录</div>
          {([
            ['summaries', '视频审核后总结'],
            ['broadcasters', '主播清单'],
            ['viewpoints', '已确认观点'],
            ['transcripts', '转录内容'],
            ['meta', '主播映射与标的词典'],
          ] as const).map(([id, label]) => (
            <button key={id} type="button" className={`gateway-service${service === id ? ' gateway-service-selected' : ''}`} aria-pressed={service === id} disabled={trialLoading} onClick={() => { setService(id); setTrialResult(null) }}>
              {label}
            </button>
          ))}
        </aside>
        <section className="gateway-detail" aria-label="开放接口服务详情">
          {service === 'viewpoints' ? <>
            <div className="gateway-detail-head"><div>
              <p className="muted">开放接口 / 服务目录</p>
              <h3>已确认观点</h3>
              <p className="muted">成果主接口：默认只出已确认观点，支持增量拉取与多维过滤。</p>
            </div></div>
            <div className="gateway-path"><span className="gateway-method">GET</span><code>/open/v1/viewpoints</code></div>
            <h4>请求参数</h4>
            <table className="table"><thead><tr><th>参数</th><th>说明</th><th>范围</th></tr></thead><tbody>
              <tr><td><code>page / page_size</code></td><td>分页</td><td>page_size 1–200，默认 20</td></tr>
              <tr><td><code>status</code></td><td>复核状态过滤</td><td>confirmed（默认）/ candidate / needs_review / rejected / all</td></tr>
              <tr><td><code>since</code></td><td>增量拉取：只返回 updated_at 晚于该时刻的观点</td><td>ISO 时间</td></tr>
              <tr><td><code>creator_id / entity_id / stance</code></td><td>按主播 / 标的 / 立场过滤</td><td>可选</td></tr>
            </tbody></table>
            <h4>返回字段</h4>
            <p><code>items</code>、<code>total</code>、<code>page</code>、<code>page_size</code>。每条观点含 claim、stance、horizon、标的、主播、置信度与 <code>updated_at</code>（增量游标）。</p>
            <p>单条详情：<code>GET /open/v1/viewpoints/&#123;viewpoint_id&#125;</code>。</p>
          </> : service === 'transcripts' ? <>
            <div className="gateway-detail-head"><div>
              <p className="muted">开放接口 / 服务目录</p>
              <h3>转录内容</h3>
              <p className="muted">粗清洗成果：转录全文与关键词检索。</p>
            </div></div>
            <div className="gateway-path"><span className="gateway-method">GET</span><code>/open/v1/items/&#123;item_id&#125;/transcript</code></div>
            <p>返回该视频/直播的转录全文，与控制台同构的分段结构（start_ms / end_ms / text / speaker）。</p>
            <div className="gateway-path"><span className="gateway-method">GET</span><code>/open/v1/transcripts/search?q=</code></div>
            <p>转录关键词搜索：条目级命中，附摘要片段，用于下游全文检索场景。</p>
          </> : service === 'meta' ? <>
            <div className="gateway-detail-head"><div>
              <p className="muted">开放接口 / 服务目录</p>
              <h3>主播映射与标的词典</h3>
              <p className="muted">辅助对齐接口：账号维度对齐与标的标准化。</p>
            </div></div>
            <div className="gateway-path"><span className="gateway-method">GET</span><code>/open/v1/creators</code></div>
            <p>主播与平台账号映射（creator → 多平台账号），供下游按平台/账号维度对齐数据。</p>
            <div className="gateway-path"><span className="gateway-method">GET</span><code>/open/v1/entities</code></div>
            <p>标的词典：与控制台 taxonomy 同一数据源，供下游对齐标的口径。</p>
          </> : service === 'broadcasters' ? <>
            <div className="gateway-detail-head"><div>
              <p className="muted">开放接口 / 服务目录</p>
              <h3>主播清单</h3>
              <p className="muted">拉取全部来源账号的当前快照，供下游同步主播目录与关注关系。</p>
            </div></div>
            <div className="gateway-path"><span className="gateway-method">GET</span><code>/open/v1/broadcasters</code></div>
            <p>使用 <code>X-API-Key</code> 鉴权，无查询参数。与视频总结共用 API Key，可在“视频审核后总结 → 拉取”详情中创建、暂停、启用或吊销。</p>
            <h4>返回字段</h4>
            <p>返回 <code>items</code> 数组，每项代表一个来源账号；包括启用和停用的账号。</p>
            <table className="table"><thead><tr><th>字段</th><th>说明</th></tr></thead><tbody>
              <tr><td><code>source_account_id</code></td><td>稳定来源账号 ID，用于关联视频总结与关注关系</td></tr>
              <tr><td><code>display_name</code></td><td>主播名称，不作为唯一标识</td></tr>
              <tr><td><code>platform</code></td><td>来源平台</td></tr>
              <tr><td><code>external_id</code></td><td>平台账号标识</td></tr>
              <tr><td><code>enabled</code></td><td>账号是否启用</td></tr>
            </tbody></table>
            <p className="muted">这是完整快照，不是增量事件流。下游按账号 ID 同步增删改；清单中缺失的账号应标记停用，不按同名主播迁移关注关系。</p>
            <h4>在线试读</h4>
            <div className="gateway-trial"><div className="gateway-trial-fields">
              <label className="field">API Key<input type="password" autoComplete="off" value={trialKey} onChange={(e) => setTrialKey(e.target.value)} placeholder="仅用于本次页面试读" /></label>
              <button className="primary" onClick={trialPull} disabled={trialLoading}>{trialLoading ? '请求中…' : '运行请求'}</button>
            </div><pre className="gateway-response" aria-live="polite">{trialResult ?? '运行请求后显示实际响应。空 items 表示当前没有来源账号。'}</pre></div>
          </> : <>
          <div className="gateway-detail-head">
            <div>
              <p className="muted">开放接口 / 服务目录</p>
              <h3>视频审核后总结</h3>
              <p className="muted">同一份审核后总结，可按需拉取或订阅推送。</p>
            </div>
            <div className="gateway-mode-tabs" role="tablist" aria-label="交付方式">
              <button type="button" role="tab" aria-selected={mode === 'pull'} className={mode === 'pull' ? 'selected' : ''} onClick={() => setMode('pull')}>拉取</button>
              <button type="button" role="tab" aria-selected={mode === 'push'} className={mode === 'push' ? 'selected' : ''} onClick={() => setMode('push')}>推送</button>
            </div>
          </div>
          {mode === 'pull' ? <div role="tabpanel">
            <div className="gateway-path"><span className="gateway-method">GET</span><code>/open/v1/video-summaries</code></div>
            <p className="muted">增量拉取审核后的总结版本事件，使用 <code>X-API-Key</code> 鉴权；只有启用中的 Key 可访问。这里的 Key 管理影响全部 <code>/open/v1/*</code> 接口。</p>
            <h4>请求参数</h4>
            <table className="table"><thead><tr><th>参数</th><th>说明</th><th>范围</th></tr></thead><tbody>
              <tr><td><code>cursor</code></td><td>最后已处理的事件序号，首次填 0</td><td>非负整数，默认 0</td></tr>
              <tr><td><code>page_size</code></td><td>每页事件数</td><td>1–200，默认 50</td></tr>
            </tbody></table>
            <h4>返回字段</h4>
            <p><code>items</code>、<code>next_cursor</code>、<code>has_more</code>；每项包含 <code>event_id</code>、<code>item_id</code>、<code>version</code>、<code>state</code>、<code>display_name</code>、<code>video_time</code>、<code>time_basis</code>、<code>summary</code>、<code>updated_at</code>。<code>withdrawn</code> 事件的总结为 null，携带 <code>withdraws_version</code>。</p>
            <p>单视频查询：<code>GET /open/v1/items/&#123;item_id&#125;/summary</code>；无可用总结时 <code>summary</code> 为 null，查看 <code>export_state</code> 判断状态。</p>
            <p className="muted">视频级时间：普通视频取来源发布时间，直播取已记录的开播时间；来源缺失时为 null，<code>time_basis</code> 为 <code>unknown</code>。</p>
            <h4>在线试读</h4>
            <div className="gateway-trial">
              <div className="gateway-trial-fields">
                <label className="field">API Key<input type="password" autoComplete="off" value={trialKey} onChange={(e) => setTrialKey(e.target.value)} placeholder="仅用于本次页面试读" /></label>
                <label className="field">cursor<input inputMode="numeric" value={trialCursor} onChange={(e) => setTrialCursor(e.target.value)} /></label>
                <label className="field">page_size<input inputMode="numeric" value={trialPageSize} onChange={(e) => setTrialPageSize(e.target.value)} /></label>
                <button className="primary" onClick={trialPull} disabled={trialLoading}>{trialLoading ? '请求中…' : '运行请求'}</button>
              </div>
              <pre className="gateway-response" aria-live="polite">{trialResult ?? '运行请求后显示实际响应。空 items 表示当前没有可拉取事件。'}</pre>
            </div>
      <h3>API Key（{keysStatus === 'ready' ? keys.length : '待确认'}）</h3>
      <div className="toolbar">
        <input
          placeholder="Key 名称（如：兄弟产品 A）"
          value={keyName}
          onChange={(e) => setKeyName(e.target.value)}
        />
        <button className="primary" onClick={createKey} disabled={!keyName.trim()}>创建 Key</button>
      </div>
      {newKey && (
        <div className="callout">
          <b>「{newKey.name}」已创建。</b> 明文仅显示这一次，请立即交给下游系统保管：
          <code style={{ userSelect: 'all', display: 'block', margin: '8px 0', wordBreak: 'break-all' }}>
            {newKey.api_key}
          </code>
          <button className="ghost" onClick={() => setNewKey(null)}>我已保存，关闭</button>
        </div>
      )}
      <table className="table">
        <thead>
          <tr><th>名称</th><th>前缀</th><th>状态</th><th>最近使用</th><th>操作</th></tr>
        </thead>
        <tbody>
          {keys.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td><code>{r.prefix}…</code></td>
              <td>{r.status === 'active' ? '启用' : r.status === 'disabled' ? '停用' : '已吊销'}</td>
              <td className="muted">{r.last_used_at ? fmtDateTime(r.last_used_at) : '从未'}</td>
              <td>
                {r.status !== 'revoked' && (<>
                  <button className="ghost" onClick={() => toggleKey(r)}>{r.status === 'active' ? '停用' : '启用'}</button>{' '}
                  <button className="ghost" onClick={() => revokeKey(r)}>吊销</button>
                </>)}
              </td>
            </tr>
          ))}
          {keysStatus === 'ready' && keys.length === 0 && (
            <tr><td colSpan={5} className="muted pad">暂无 Key。下游系统凭 X-API-Key 请求头访问 /open/v1/*。</td></tr>
          )}
          {keysStatus !== 'ready' && <tr><td colSpan={5} className="muted pad">{keysStatus === 'loading' ? '正在读取 Key…' : 'Key 状态读取失败，请刷新页面重试。'}</td></tr>}
        </tbody>
      </table>

          </div> : <div role="tabpanel">
            <div className="gateway-path"><span className="gateway-method gateway-method-push">WEBHOOK</span><code>HTTPS POST → 接收方 URL</code></div>
            <p>向已启用且订阅视频总结的通用 Webhook 渠道投递与拉取相同的事件载荷。<code>ready</code> 为新版本，<code>withdrawn</code> 为旧版本撤回。接收方按 <code>event_id</code> 去重。</p>
            <h4>事件与签名</h4>
            <p>载荷含 <code>event_id</code>、<code>item_id</code>、<code>version</code>、<code>state</code>、<code>display_name</code>、<code>video_time</code>、<code>time_basis</code>、<code>summary</code>、<code>updated_at</code>。撤回事件的 <code>summary</code> 为 null，并含 <code>withdraws_version</code>。</p>
            <p>请求头：<code>X-Radar-Event-ID</code>、<code>X-Radar-Timestamp</code>、<code>X-Radar-Signature</code>（HMAC-SHA256）。</p>
            <p><b>有效订阅：{channelsStatus === 'ready' ? `${summarySubscribers.length} 个` : '待确认'}。</b> {channelsStatus === 'loading' ? '正在读取渠道配置。' : channelsStatus === 'error' ? '渠道配置读取失败，请刷新页面重试。' : summarySubscribers.length > 0 ? `接收渠道：${summarySubscribers.map((channel) => channel.name).join('、')}。` : '尚未配置启用中的视频总结接收渠道。'}</p>
            <p className="muted">渠道测试只发送测试消息，真实事件是否送达以投递记录为准；订阅前的历史事件可从拉取接口补齐。</p>
      <h3 style={{ marginTop: 28 }}>推送渠道（{channelsStatus === 'ready' ? channels.length : '待确认'}）</h3>
      <div className="toolbar">
        <button className="primary" onClick={() => setChForm(!chForm)}>新建渠道</button>
        <span className="muted">视频总结只支持带签名密钥的通用 HTTPS Webhook；渠道创建后需单独开启视频总结订阅。</span>
      </div>
      {chForm && (
        <div className="callout">
          <div className="grid-2">
            <label className="field">
              名称
              <input value={chDraft.name} onChange={(e) => setChDraft({ ...chDraft, name: e.target.value })} placeholder="如：运营群" />
            </label>
            <label className="field">
              类型
              <select value={chDraft.channel_type} onChange={(e) => setChDraft({ ...chDraft, channel_type: e.target.value })}>
                {Object.entries(CHANNEL_TYPE_CN).map(([v, cn]) => (
                  <option key={v} value={v}>{cn}</option>
                ))}
              </select>
            </label>
            <label className="field" style={{ gridColumn: '1 / -1' }}>
              Webhook URL
              <input value={chDraft.url} onChange={(e) => setChDraft({ ...chDraft, url: e.target.value })} placeholder={chDraft.channel_type === 'generic_webhook' ? 'https://example.com/webhook/video-summary' : 'https://open.feishu.cn/open-apis/bot/v2/hook/…'} />
            </label>
            <label className="field" style={{ gridColumn: '1 / -1' }}>
              签名 Secret（视频总结订阅必填；飞书/钉钉按机器人设置）
              <input value={chDraft.secret} onChange={(e) => setChDraft({ ...chDraft, secret: e.target.value })} placeholder="可选" />
            </label>
            <label className="field" style={{ gridColumn: '1 / -1' }}>
              <input type="checkbox" checked={chDraft.viewpoint_enabled} onChange={(e) => setChDraft({ ...chDraft, viewpoint_enabled: e.target.checked })} /> 同时接收逐条确认观点（仅接收视频总结时请取消）
            </label>
          </div>
          <div className="form-actions">
            <button className="primary" onClick={createChannel} disabled={!chDraft.name.trim() || !chDraft.url.trim()}>创建</button>
            <button onClick={() => setChForm(false)}>取消</button>
          </div>
        </div>
      )}
      <table className="table">
        <thead>
          <tr><th>名称</th><th>类型</th><th>URL</th><th>状态</th><th>逐条观点</th><th>视频总结</th><th>操作</th></tr>
        </thead>
        <tbody>
          {channels.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td>{CHANNEL_TYPE_CN[r.channel_type] ?? r.channel_type}{r.has_secret ? '（加签）' : ''}</td>
              <td className="muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {r.url}
              </td>
              <td>{r.enabled ? '启用' : '停用'}</td>
              <td><button className="ghost" onClick={() => toggleViewpoints(r)}>{r.viewpoint_enabled ? '开启' : '关闭'}</button></td>
              <td>{r.summary_enabled ? '已订阅' : '未订阅'}{r.channel_type === 'generic_webhook' && r.has_secret && <button className="ghost" onClick={() => toggleSummary(r)}>{r.summary_enabled ? '关闭' : '开启'}</button>}</td>
              <td>
                <button className="ghost" disabled={testing === r.id} onClick={() => testChannel(r)}>
                  {testing === r.id ? '发送中…' : '测试'}
                </button>{' '}
                <button className="ghost" onClick={() => toggleChannel(r)}>{r.enabled ? '停用' : '启用'}</button>{' '}
                <button
                  className="ghost"
                  onClick={() => {
                    if (window.confirm(`删除渠道「${r.name}」？投递日志一并删除。`))
                      api(`/open-admin/push-channels/${r.id}`, { method: 'DELETE' }).then(reload).catch((e: Error) => setError(e.message))
                  }}
                >
                  删除
                </button>
              </td>
            </tr>
          ))}
          {channelsStatus === 'ready' && channels.length === 0 && (
            <tr><td colSpan={7} className="muted pad">暂无渠道。视频总结推送需新建带签名密钥的通用 HTTPS Webhook。</td></tr>
          )}
          {channelsStatus !== 'ready' && (
            <tr><td colSpan={7} className="muted pad">{channelsStatus === 'loading' ? '正在读取渠道配置…' : '渠道配置读取失败，请刷新页面重试。'}</td></tr>
          )}
        </tbody>
      </table>

      <h3 style={{ marginTop: 28 }}>视频总结投递（最近 {deliveriesStatus === 'ready' ? `${summaryDeliveries.length} 条` : '待确认'}）</h3>
      <p className="muted">推送可能失败或结果未知；接收方可用增量拉取补漏。订阅开启前的历史事件请先拉取。</p>
      <table className="table">
        <thead><tr><th>渠道</th><th>事件</th><th>视频</th><th>状态</th><th>尝试</th><th>处理</th></tr></thead>
        <tbody>
          {summaryDeliveries.map((d) => <tr key={d.id}>
            <td>{d.channel_name}</td>
            <td><code>{d.event_id}</code> · {d.event_state} v{d.version}</td>
            <td>{d.item_id}</td>
            <td>{d.status}{d.last_http_status ? ` (HTTP ${d.last_http_status})` : ''}{d.error ? ` · ${d.error}` : ''}</td>
            <td>{d.attempt}</td>
            <td>{['unknown', 'failed', 'dead'].includes(d.status) && <><button className="ghost" onClick={() => reconcileSummary(d, 'sent')}>核对为已收到</button><button className="ghost" onClick={() => reconcileSummary(d, 'retry')}>核对后重试</button></>}</td>
          </tr>)}
          {deliveriesStatus === 'ready' && summaryDeliveries.length === 0 && <tr><td colSpan={6} className="muted pad">暂无视频总结投递记录；拉取接口仍可独立使用。</td></tr>}
          {deliveriesStatus !== 'ready' && <tr><td colSpan={6} className="muted pad">{deliveriesStatus === 'loading' ? '正在读取投递记录…' : '投递记录读取失败，状态待确认。'}</td></tr>}
        </tbody>
      </table>
          </div>}
          </>}
        </section>
      </div>
    </div>
  )
}

