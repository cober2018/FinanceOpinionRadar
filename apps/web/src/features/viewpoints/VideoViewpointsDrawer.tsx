import type { VPRow } from '../../shared/types'
import { EditViewpointModal } from './EditViewpointModal'
import { ViewpointEvidenceDrawer } from './ViewpointEvidenceDrawer'
import { StanceBadge, VP_STATUS_CN } from '../../shared/viewpoint-bits'
import { fmtDateTime } from '../../shared/format'
import { api } from '../../shared/api'
import { useCallback, useEffect, useState } from 'react'
import type { VPVideoRow } from '../../shared/types'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function VideoViewpointsDrawer(props: {
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

  const regenerateSummary = async () => {
    setError(null)
    try {
      await api(`/source-items/${item.id}/summarize`, { method: 'POST' })
      window.alert('已派发重新总结，稍后自动刷新')
    } catch (e) {
      setError((e as Error).message)
    }
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
        <div className="summary-box">
          <div className="summary-head">
            <b>视频观点</b>
            {item.viewpoint_summary && (
              <button className="ghost" style={{ fontSize: 11 }} onClick={regenerateSummary}>
                重新生成
              </button>
            )}
          </div>
          {item.viewpoint_summary ? (
            <p style={{ margin: '4px 0 0', lineHeight: 1.7 }}>{item.viewpoint_summary}</p>
          ) : (
            <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>
              全部观点复核完成后自动生成；也可现在
              <button className="ghost" style={{ fontSize: 11, padding: '0 4px' }} onClick={regenerateSummary}>
                立即生成
              </button>
            </p>
          )}
        </div>
        {error && <p className="error">{error}</p>}
        <div className="drawer-body">
          {vps.map((r, idx) => (
            <div key={r.id} className="vp-item">
              <div className="vp-item-head">
                <span className="vp-item-no">#{idx + 1}</span>
                {(r.entity_name ?? r.entity_raw) && (
                  <span className="badge">{r.entity_name ?? r.entity_raw}</span>
                )}
                {r.horizon && <span className="badge">{r.horizon}</span>}
                <StanceBadge stance={r.stance} />
                <span className={`badge ${r.verification_status === 'confirmed' ? 'ok' : r.verification_status === 'rejected' ? 'err' : 'run'}`}>
                  {VP_STATUS_CN[r.verification_status] ?? r.verification_status}
                </span>
                <span className="muted" style={{ fontSize: 11 }}>
                  置信 {r.confidence.toFixed(2)}
                  {r.topic_name ? ` · ${r.topic_name}` : ''}
                  {r.review_source ? ` · ${r.review_source === 'llm' ? 'LLM 放行' : r.review_source === 'rule' ? '规则放行' : '人工'}` : ''}
                </span>
              </div>
              <p style={{ margin: '0 0 10px', lineHeight: 1.6 }}>{r.claim}</p>
              {r.review_note && (
                <p className="muted" style={{ margin: '-4px 0 10px', fontSize: 12, lineHeight: 1.5 }}>
                  复核意见：{r.review_note}
                </p>
              )}
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

