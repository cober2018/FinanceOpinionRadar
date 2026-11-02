import { api } from '../../shared/api'
import type { LLMProfile, LLMSettings, LLMTemplate, RuntimeSettings, SecuritySettings } from '../../shared/types'
import { useCallback, useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
function LLMSettingsCard() {
  const [templates, setTemplates] = useState<LLMTemplate[]>([])
  const [stages, setStages] = useState<LLMSettings['stages']>([])
  const [profiles, setProfiles] = useState<LLMProfile[]>([])
  const [assignment, setAssignment] = useState<Record<string, string | null>>({})
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState<Record<string, string>>({})

  const reload = useCallback(() => {
    api<LLMSettings>('/settings/llm')
      .then((s) => {
        setTemplates(s.templates)
        setStages(s.stages)
        setProfiles(s.profiles)
        setAssignment(s.assignment)
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(reload, [reload])

  const updateProfile = (idx: number, patch: Partial<LLMProfile>) => {
    setProfiles((ps) => ps.map((p, i) => (i === idx ? { ...p, ...patch } : p)))
  }

  const applyTemplate = (idx: number, key: string) => {
    const t = templates.find((x) => x.key === key)
    updateProfile(idx, {
      template: key,
      base_url: t?.base_url ?? '',
      model: t?.models[0] ?? '',
      chat_path: t?.chat_path ?? '/chat/completions',
    })
  }

  const addProfile = () => {
    setProfiles((ps) => [
      ...ps,
      { name: '', template: 'custom', base_url: '', api_key: '', model: '', chat_path: '/chat/completions' },
    ])
  }

  const removeProfile = (idx: number) => {
    setProfiles((ps) => ps.filter((_, i) => i !== idx))
  }

  const persist = async (): Promise<LLMProfile[]> => {
    const saved = await api<LLMProfile[]>('/settings/llm/profiles', {
      method: 'PUT',
      body: JSON.stringify({ profiles }),
    })
    await api('/settings/llm/assignment', {
      method: 'PUT',
      body: JSON.stringify(assignment),
    })
    setProfiles(saved)
    return saved
  }

  const save = async () => {
    setError(null)
    setMsg(null)
    setSaving(true)
    try {
      await persist()
      setMsg('已保存（模型档案与阶段分配即时生效）')
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const testConn = async (idx: number) => {
    const pid = profiles[idx]?.id
    if (!pid) {
      setTestResult((m) => ({ ...m, [idx]: '❌ 请先保存，再测试' }))
      return
    }
    setTestResult((m) => ({ ...m, [idx]: '测试中…' }))
    try {
      await persist()
      const r = await api<{ ok: boolean; latency_ms?: number; model?: string; error?: string }>(
        '/settings/llm/test',
        { method: 'POST', body: JSON.stringify({ profile_id: pid }) },
      )
      setTestResult((m) => ({
        ...m,
        [idx]: r.ok ? `✅ 连通（${r.latency_ms}ms，模型 ${r.model}）` : `❌ ${r.error ?? '失败'}`,
      }))
    } catch (e) {
      setTestResult((m) => ({ ...m, [idx]: `❌ ${(e as Error).message}` }))
    }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>大模型</h3>
      </div>
      <div className="stack">
        {error && <p className="error">{error}</p>}
        {msg && <p className="ok">{msg}</p>}
        <p className="hint" style={{ margin: 0 }}>
          可配置多个模型（OpenAI 兼容协议），在下方「阶段分配」里为每个环节指定用哪个；未指定的环节默认用第一个。
        </p>
        {profiles.map((p, idx) => {
          const current = templates.find((t) => t.key === p.template)
          return (
            <div key={p.id ?? `new-${idx}`} className="stack" style={{ borderTop: '1px solid var(--line, #ddd)', paddingTop: 8 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <b style={{ fontSize: 13 }}>模型 {idx + 1}</b>
                <input
                  value={p.name}
                  onChange={(e) => updateProfile(idx, { name: e.target.value })}
                  placeholder="名称（如：MiniMax-M3 主力）"
                  style={{ flex: 1 }}
                />
                {profiles.length > 1 && (
                  <button className="ghost" onClick={() => removeProfile(idx)}>删除</button>
                )}
              </div>
              <label className="field">
                服务商模板：选中后自动填地址与模型，可再手动改
                <select value={p.template} onChange={(e) => applyTemplate(idx, e.target.value)}>
                  {templates.map((t) => (
                    <option key={t.key} value={t.key}>{t.label}</option>
                  ))}
                </select>
                {current?.note && <span className="muted">{current.note}</span>}
              </label>
              <label className="field">
                Base URL
                <input value={p.base_url} onChange={(e) => updateProfile(idx, { base_url: e.target.value })} placeholder="https://api.deepseek.com/v1" />
              </label>
              <label className="field">
                API Key {p.key_set && <span className="muted">（已设置，留空保持不变）</span>}
                <input type="password" value={p.api_key} onChange={(e) => updateProfile(idx, { api_key: e.target.value })} placeholder={p.key_set ? '••••••••' : 'sk-…'} />
              </label>
              <label className="field">
                模型（可下拉选或手输）
                <input value={p.model} onChange={(e) => updateProfile(idx, { model: e.target.value })} list={`llm-models-${idx}`} placeholder="deepseek-chat" />
                <datalist id={`llm-models-${idx}`}>
                  {(current?.models ?? []).map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>
              </label>
              <label className="field">
                Chat 路径（一般不动；MiniMax 为 /text/chatcompletion_v2）
                <input value={p.chat_path} onChange={(e) => updateProfile(idx, { chat_path: e.target.value })} />
              </label>
              <div className="form-actions" style={{ margin: 0 }}>
                <button className="ghost" onClick={() => testConn(idx)}>测试此模型</button>
                {testResult[idx] && <span className="muted">{testResult[idx]}</span>}
              </div>
            </div>
          )
        })}
        <div>
          <button className="ghost" onClick={addProfile}>+ 新增模型</button>
        </div>
        {stages.length > 0 && (
          <div className="stack" style={{ borderTop: '1px solid var(--line, #ddd)', paddingTop: 8 }}>
            <b style={{ fontSize: 13 }}>阶段分配</b>
            {stages.map((st) => (
              <label className="field" key={st.key}>
                {st.label}
                <select
                  value={assignment[st.key] ?? ''}
                  onChange={(e) => setAssignment((a) => ({ ...a, [st.key]: e.target.value || null }))}
                >
                  {profiles.length === 0 && <option value="">（先添加模型）</option>}
                  {profiles.map((p) => (
                    <option key={p.id ?? p.name} value={p.id ?? ''}>
                      {p.name || '未命名'}{p.model ? `（${p.model}）` : ''}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <label className="field">
              兜底模型（任一环节的主模型请求失败时自动切到它重跑；选「不启用」关闭）
              <select
                value={assignment.fallback ?? ''}
                onChange={(e) => setAssignment((a) => ({ ...a, fallback: e.target.value || null }))}
              >
                <option value="">不启用</option>
                {profiles.map((p) => (
                  <option key={p.id ?? p.name} value={p.id ?? ''}>
                    {p.name || '未命名'}{p.model ? `（${p.model}）` : ''}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        <div className="form-actions">
          <button className="primary" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存配置'}</button>
        </div>
        <p className="hint">配置存库即时生效：抽取/复核/总结/润色各环节按分配选用对应模型。新填的 API Key 保存后才能「测试此模型」。</p>
      </div>
    </div>
  )
}

// IP 代理池管理（青果长效代理，24h 轮换）：配置凭证 / 查询在用 / 提取新IP / 释放
function QgProxyCard({ onPoolChange }: { onPoolChange: (pool: string[]) => void }) {
  const [key, setKey] = useState('')
  const [pwd, setPwd] = useState('')
  const [pwdSaved, setPwdSaved] = useState(false)
  const [ips, setIps] = useState<{ server: string | null; in_pool: boolean }[]>([])
  const [pool, setPool] = useState<string[]>([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const cfg = await api<{ key: string; auth_pwd: string }>('/proxy-pool/qg/config')
      setKey(cfg.key)
      setPwdSaved(Boolean(cfg.auth_pwd))
      const d = await api<{ ips: { server: string | null; in_pool: boolean }[]; pool: string[] }>(
        '/proxy-pool/qg/ips',
      )
      setIps(d.ips)
      setPool(d.pool)
      onPoolChange(d.pool) // 同步回「安全」卡的代理池文本框，两处显示一致
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const run = async (name: string, fn: () => Promise<string>) => {
    setBusy(name)
    setError(null)
    setMsg(null)
    try {
      // 流程自动化：表单里填了新凭证时，任何操作前先自动保存（不再依赖手动点保存）
      if (key.trim() && pwd.trim()) {
        await api('/proxy-pool/qg/config', {
          method: 'PUT',
          body: JSON.stringify({ key, auth_pwd: pwd }),
        })
        setPwdSaved(true)
        setPwd('')
      }
      setMsg(await fn())
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  const saveConfig = () =>
    run('config', async () => {
      if (!pwd.trim() && pwdSaved) return '凭证未变化'
      await api('/proxy-pool/qg/config', {
        method: 'PUT',
        body: JSON.stringify({ key, auth_pwd: pwd }),
      })
      setPwdSaved(true)
      setPwd('')
      return '凭证已保存'
    })

  const extract = () =>
    run('extract', async () => {
      const d = await api<{ extracted: { server: string }[]; pool: string[] }>(
        '/proxy-pool/qg/extract?num=1',
        { method: 'POST' },
      )
      return `已提取 ${d.extracted.length} 个新 IP 并入池`
    })

  const releaseAll = () =>
    run('releaseAll', async () => {
      if (!window.confirm('确定释放全部在用 IP？代理池将移除对应出口（可再提取新 IP）。')) {
        throw new Error('已取消')
      }
      const d = await api<{ released: string[] }>('/proxy-pool/qg/release?all=true', {
        method: 'POST',
      })
      return `已释放 ${d.released.length} 个 IP`
    })

  const releaseOne = (server: string) =>
    run(`rel:${server}`, async () => {
      const d = await api<{ released: string[] }>(
        `/proxy-pool/qg/release?ip=${encodeURIComponent(server)}`,
        { method: 'POST' },
      )
      return `已释放 ${d.released.join('、')}`
    })

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>IP 代理池管理（青果长效代理）</h3>
      </div>
      <div className="stack">
        {error && <p className="error">{error}</p>}
        {msg && <p className="ok">{msg}</p>}
        <div className="grid-2">
          <label className="field">
            AuthKey
            <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="青果业务 KEY" />
          </label>
          <label className="field">
            AuthPwd {pwdSaved && <span className="muted">（已保存，留空保持不变）</span>}
            <input
              type="password"
              value={pwd}
              onChange={(e) => setPwd(e.target.value)}
              placeholder={pwdSaved ? '********' : '青果 AuthPwd'}
            />
          </label>
        </div>
        <div>
          <button className="primary" disabled={busy !== '' || !key || (!pwd && !pwdSaved)} onClick={saveConfig}>
            {busy === 'config' ? '保存中…' : '保存凭证'}
          </button>{' '}
          <button disabled={busy !== ''} onClick={extract} title="通道占用时需先释放">
            {busy === 'extract' ? '提取中…' : '提取新 IP'}
          </button>{' '}
          <button className="ghost" disabled={busy !== '' || ips.length === 0} onClick={releaseAll}>
            {busy === 'releaseAll' ? '释放中…' : '释放全部'}
          </button>
        </div>
        <div>
          <b style={{ fontSize: 13 }}>在用 IP（{ips.length}）</b>{' '}
          <button className="ghost" disabled={busy !== ''} onClick={load}>刷新</button>
        </div>
        {ips.map((it) => (
          <div key={it.server ?? 'unknown'} className="vp-item" style={{ padding: '8px 12px', marginBottom: 6 }}>
            <code style={{ fontSize: 12 }}>{it.server ?? '-'}</code>{' '}
            <span className={`badge ${it.in_pool ? 'ok' : 'run'}`}>{it.in_pool ? '已入池' : '未入池'}</span>{' '}
            <button className="ghost" disabled={busy !== ''} onClick={() => it.server && releaseOne(it.server)}>
              {busy === `rel:${it.server}` ? '释放中…' : '释放'}
            </button>
          </div>
        ))}
        {ips.length === 0 && <p className="muted" style={{ fontSize: 12 }}>当前无在用 IP：点「提取新 IP」获取。</p>}
        <label className="field">
          当前代理池生效列表（提取/释放自动同步；也可在上方「安全」卡手工增删其他出口）
          <textarea rows={3} value={pool.join('\n')} readOnly style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11 }} />
        </label>
        <p className="hint">
          全自动维护：系统每 10 分钟自动「查询在用 IP → 同步代理池」，通道空闲时自动提取新 IP；点「刷新」立即触发同一流程。唯一需要手动的是更换凭证后填入新 AuthKey/AuthPwd。
        </p>
      </div>
    </div>
  )
}

function SystemServiceCard() {
  const [rt, setRt] = useState<RuntimeSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    api<RuntimeSettings>('/settings/runtime')
      .then(setRt)
      .catch((e: Error) => setError(e.message))
  }, [])
  useEffect(reload, [reload])

  const toggle = async (flag: 'autostart' | 'watchdog', value: boolean) => {
    setBusy(true)
    setError(null)
    setMsg(null)
    try {
      const saved = await api<RuntimeSettings>('/settings/runtime', {
        method: 'PUT',
        body: JSON.stringify({ [flag]: value }),
      })
      setRt(saved)
      setMsg(value ? '已开启' : '已关闭')
    } catch (e) {
      setError((e as Error).message)
      reload()
    } finally {
      setBusy(false)
    }
  }

  const stateText = (intent: boolean | undefined, installed: boolean | null | undefined) => {
    if (installed === null || installed === undefined) return '状态未知'
    if (installed === intent) return installed ? '已生效' : '未启用'
    return intent ? '启动失败（见后端日志）' : '停止失败（见后端日志）'
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <h3>系统服务</h3>
      </div>
      <div className="stack">
        {error && <p className="error">{error}</p>}
        {msg && <p className="ok">{msg}</p>}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <b>开机自启</b>
            <p className="muted" style={{ fontSize: 12 }}>
              登录本机后自动拉起平台：Postgres/Redis/解析服务/API/转写 worker（幂等，已在跑的跳过）。
            </p>
          </div>
          <label className="switch">
            <input
              type="checkbox"
              checked={rt?.autostart ?? false}
              disabled={busy || !rt}
              onChange={(e) => toggle('autostart', e.target.checked)}
            />
            <span className="slider" />
          </label>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <b>看门狗</b>
            <p className="muted" style={{ fontSize: 12 }}>
              每 60 秒巡检，组件「运行中掉线」自动拉起。不做冷启动；开启后手动停止的组件会被自动拉起，维护前请先关闭。
            </p>
          </div>
          <label className="switch">
            <input
              type="checkbox"
              checked={rt?.watchdog ?? false}
              disabled={busy || !rt}
              onChange={(e) => toggle('watchdog', e.target.checked)}
            />
            <span className="slider" />
          </label>
        </div>
        {rt && (
          <p className="muted" style={{ fontSize: 12 }}>
            生效状态：开机自启 {stateText(rt.autostart, rt.installed.autostart)}；看门狗{' '}
            {stateText(rt.watchdog, rt.installed.watchdog)}。开关状态存库，重启后保持。
          </p>
        )}
      </div>
    </div>
  )
}

export function SettingsPage() {
  const [stagger, setStagger] = useState('')
  const [pages, setPages] = useState('')
  const [pool, setPool] = useState('')
  const [reviewThr, setReviewThr] = useState('')
  const [reviewChars, setReviewChars] = useState('')
  const [llmReviewOn, setLlmReviewOn] = useState(true)
  const [effective, setEffective] = useState<SecuritySettings['effective'] | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    api<SecuritySettings>('/settings/security')
      .then((s) => {
        setStagger(s.discover_dispatch_stagger_max_sec?.toString() ?? '')
        setPages(s.douyin_discover_max_pages?.toString() ?? '')
        setPool(s.proxy_pool.join('\n'))
        setReviewThr(s.review_confidence_threshold?.toString() ?? '')
        setReviewChars(s.review_min_evidence_chars?.toString() ?? '')
        setLlmReviewOn(s.effective.llm_review_enabled !== false)
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
          review_confidence_threshold: reviewThr === '' ? null : Number(reviewThr),
          review_min_evidence_chars: reviewChars === '' ? null : Number(reviewChars),
          llm_review_enabled: llmReviewOn,
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
      <QgProxyCard onPoolChange={(p) => setPool(p.join('\n'))} />
      <SystemServiceCard />
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
          <div className="grid-2">
            <label className="field">
              观点自动复核阈值（0.5-1）：大模型置信度 ≥ 此值且证据充分 → 自动通过；低于 → 进人工复核
              <input type="number" step="0.05" min="0.5" max="1" value={reviewThr} onChange={(e) => setReviewThr(e.target.value)} placeholder="留空用默认" />
              {effective && <span className="muted">当前生效：{effective.review_confidence_threshold}</span>}
            </label>
            <label className="field">
              证据最小字数（0-2000）：观点的原文证据合计低于此字数 → 进人工复核
              <input type="number" min="0" max="2000" value={reviewChars} onChange={(e) => setReviewChars(e.target.value)} placeholder="留空用默认" />
              {effective && <span className="muted">当前生效：{effective.review_min_evidence_chars}</span>}
            </label>
          </div>
          <label className="field">
            <input type="checkbox" checked={llmReviewOn} onChange={(e) => setLlmReviewOn(e.target.checked)} style={{ marginRight: 6 }} />
            LLM 二道复核：规则复核未放行的候选送大模型逐条裁决（只放行不驳回；判断口径含人工修正校准规则），关闭即回到纯规则复核
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

