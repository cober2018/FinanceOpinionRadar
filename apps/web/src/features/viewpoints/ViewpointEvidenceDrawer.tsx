import { fmtTs } from '../../shared/format'
import { StanceBadge, VP_STATUS_CN } from '../../shared/viewpoint-bits'
import { api } from '../../shared/api'
import { useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function ViewpointEvidenceDrawer(props: {
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
                {vp.claim} <StanceBadge stance={vp.stance} />{' '}
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

