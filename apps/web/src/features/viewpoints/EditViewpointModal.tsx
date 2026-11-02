import type { VPRow } from '../../shared/types'
import { STANCE_CN } from '../../shared/viewpoint-bits'
import { api } from '../../shared/api'
import { useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function EditViewpointModal(props: {
  vp: VPRow
  onClose: () => void
  onDone: () => void
}) {
  const [claim, setClaim] = useState(props.vp.claim)
  const [stance, setStance] = useState(props.vp.stance)
  const [horizon, setHorizon] = useState(props.vp.horizon ?? '')
  const [entityName, setEntityName] = useState(props.vp.entity_name ?? props.vp.entity_raw ?? '')
  const [entities, setEntities] = useState<string[]>([])
  const [importance, setImportance] = useState(String(props.vp.importance))
  const [confidence, setConfidence] = useState(String(props.vp.confidence))
  const [reason, setReason] = useState('')
  const [polishing, setPolishing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<{ items: { canonical_name: string }[] }>('/entities')
      .then((d) => setEntities(d.items.map((e) => e.canonical_name)))
      .catch(() => setEntities([])) // 联想失败不阻塞编辑
  }, [])

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
          entity_name: entityName.trim(),
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
    // 表单弹窗不允许点遮罩关闭（误触会丢已填内容），只能走「取消/保存」；
    // 遮罩点击阻断冒泡——嵌在抽屉里时不能让外层 drawer-mask 把抽屉关掉
    <div className="modal-mask" onClick={(e) => e.stopPropagation()}>
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
            标的（留空 = 清除）
            <input
              list="vp-entity-options"
              value={entityName}
              onChange={(e) => setEntityName(e.target.value)}
              placeholder="如：英伟达、标普500"
            />
            <datalist id="vp-entity-options">
              {entities.map((n) => (
                <option key={n} value={n} />
              ))}
            </datalist>
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
            立场
            <select value={stance} onChange={(e) => setStance(e.target.value)}>
              {Object.entries(STANCE_CN).map(([v, cn]) => (
                <option key={v} value={v}>{cn}</option>
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

