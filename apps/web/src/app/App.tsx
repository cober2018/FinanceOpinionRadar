import { useState } from 'react'
import { NAV } from '../shared/status'
import type { Tab } from '../shared/status'
// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
import "./../App.css"
import { OverviewPage } from "../features/overview/OverviewPage"
import { AccountsPage } from "../features/accounts/AccountsPage"
import { LibraryPage } from "../features/library/LibraryPage"
import { TimelinePage } from "../features/timeline/TimelinePage"
import { ViewpointsPage } from "../features/viewpoints/ViewpointsPage"
import { AssetLibraryPage } from "../features/viewpoints/AssetLibraryPage"
import { OpenApiPage } from "../features/openapi/OpenApiPage"
import { JobCenterPage } from "../features/jobs/JobCenterPage"
import { SettingsPage } from "../features/settings/SettingsPage"
import { ApiBusyIndicator } from "../shared/status"
import { BackendHealthBanner } from "../shared/status"
export default function App() {
  const [tab, setTab] = useState<Tab>('overview')
  return (
    <div className="shell">
      <ApiBusyIndicator />
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
        <BackendHealthBanner />
        {tab === 'overview' && <OverviewPage go={(t) => setTab(t as Tab)} />}
        {tab === 'timeline' && <TimelinePage />}
        {tab === 'accounts' && <AccountsPage />}
        {tab === 'library' && <LibraryPage />}
        {tab === 'assets' && <AssetLibraryPage />}
        {tab === 'viewpoints' && <ViewpointsPage mode="all" />}
        {tab === 'review' && <ViewpointsPage mode="review" />}
        {tab === 'jobs' && <JobCenterPage />}
        {tab === 'open-api' && <OpenApiPage />}
        {tab === 'settings' && <SettingsPage />}
      </main>
    </div>
  )
}

