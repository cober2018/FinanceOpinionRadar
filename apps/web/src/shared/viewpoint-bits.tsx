// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
import type { VPVideoRow } from "./types"
export function StanceBadge({ stance }: { stance: string }) {
  const cls = stance.includes('bullish') ? 'stance-bull' : stance.includes('bearish') ? 'stance-bear' : ''
  return <span className={`badge ${cls}`}>{STANCE_CN[stance] ?? stance}</span>
}

// 视频级立场标签：期限分组纵排，组内按立场分行（看多/看空/中性…），重复标的计 ×次数；
// 整体限宽，超出换行，避免观点多时把整行拉宽
export function StanceSummary({ summary }: { summary: VPVideoRow['stance_summary'] }) {
  if (!summary || summary.length === 0) return <span className="muted">-</span>
  const order = ['strong_bullish', 'bullish', 'neutral', 'bearish', 'strong_bearish', 'unclear']
  return (
    <div className="stance-grid">
      {summary.map((g, i) => (
        <div key={i} className="stance-grid-row">
          <span className="stance-horizon">
            {g.horizon === 'intraday' ? '日内' : (g.horizon ?? '未定期限')}
          </span>
          <div className="stance-grid-groups">
            {order.map((s) => {
              const items = g.tags.filter((t) => t.stance === s)
              if (items.length === 0) return null
              return (
                <div key={s} className="stance-grid-line">
                  <StanceBadge stance={s} />
                  <span className="stance-grid-items">
                    {items.map((t, k) => (
                      <span key={k} className="stance-grid-item">
                        {t.entity ?? '—'}
                        {t.count > 1 && <span className="muted">×{t.count}</span>}
                      </span>
                    ))}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

export const VP_STATUS_CN: Record<string, string> = {
  candidate: '候选',
  needs_review: '待复核',
  confirmed: '已确认',
  rejected: '已驳回',
}

/* 观点表操作列的小图标（内联 SVG，不引图标库） */
const iconSvgProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
} as const

export const IconQuote = (
  <svg {...iconSvgProps}>
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
)
export const IconDoc = (
  <svg {...iconSvgProps}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="16" y1="13" x2="8" y2="13" />
    <line x1="16" y1="17" x2="8" y2="17" />
  </svg>
)
export const IconRefresh = (
  <svg {...iconSvgProps}>
    <polyline points="23 4 23 10 17 10" />
    <polyline points="1 20 1 14 7 14" />
    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
  </svg>
)

export const STANCE_CN: Record<string, string> = {
  strong_bullish: '强烈看多',
  bullish: '看多',
  neutral: '中性',
  bearish: '看空',
  strong_bearish: '强烈看空',
  unclear: '不明',
}
