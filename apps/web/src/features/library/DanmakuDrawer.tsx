import { fmtClock } from '../../shared/format'
import { api } from '../../shared/api'
import type { DanmakuDetail } from '../../shared/types'
import { useEffect, useState } from 'react'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
export function DanmakuDrawer(props: { itemId: number; onClose: () => void }) {
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


