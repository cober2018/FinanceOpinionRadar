// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。
/* ---------------- 类型 ---------------- */

export type LiveMonitor = {
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
  viewer_count: number | null
  viewer_avg: number | null
  viewer_peak: number | null
  viewer_sample_count: number | null
}

export type LibraryItem = {
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
  members_only?: boolean
  progress: { phase: string; detail: string; at: string } | null
  chat_count: number
  is_asset: boolean
  expires_at: string | null
  viewer_count?: number | null
  viewer_avg?: number | null
  viewer_peak?: number | null
  viewpoint_total?: number
  viewpoint_needs_review?: number
  viewpoint_confirmed?: number
}

export type VPVideoRow = {
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
  viewpoint_summary?: string | null
  stance_summary?: { horizon: string | null; tags: { stance: string; entity: string | null; count: number }[] }[]
}

export type DanmakuStats = { total: number; sessions: number; latest_message_at: string | null }

export type DanmakuMessage = { user_name: string | null; text: string | null; published_at: string | null }

export type DanmakuDetail = {
  item_id: number
  title: string | null
  total: number
  messages: DanmakuMessage[]
}

export type TranscriptRow = { sequence_no: number; start_ms: number; end_ms: number; text: string }

export type SearchHit = {
  item_id: number
  title: string | null
  display_name: string
  platform: string
  published_at: string | null
  match_count: number
  snippet: string
}

export type SecuritySettings = {
  discover_dispatch_stagger_max_sec: number | null
  douyin_discover_max_pages: number | null
  proxy_pool: string[]
  review_confidence_threshold?: number | null
  review_min_evidence_chars?: number | null
  llm_review_enabled?: boolean | null
  effective: {
    discover_dispatch_stagger_max_sec: number
    douyin_discover_max_pages: number
    review_confidence_threshold?: number
    review_min_evidence_chars?: number
    llm_review_enabled?: boolean
  }
}

export type RuntimeSettings = {
  autostart: boolean
  watchdog: boolean
  installed: { autostart: boolean | null; watchdog: boolean | null }
}

export type LLMTemplate = {
  key: string
  label: string
  base_url: string
  models: string[]
  chat_path: string
  note: string
}

export type LLMProfile = {
  id?: string
  name: string
  template: string
  base_url: string
  api_key: string
  key_set?: boolean
  model: string
  chat_path: string
}

export type LLMSettings = {
  templates: LLMTemplate[]
  profiles: LLMProfile[]
  assignment: Record<string, string | null>
  stages: { key: string; label: string }[]
}

export type SystemStatus = {
  asr_provider: string
  asr_model: string
  douyin: { configured: boolean; reachable: boolean }
  recorder: { configured: boolean; container: string; synced_monitors: number }
}

export type Draft = {
  profile_url: string
  live_room_url: string
  poll_interval_sec: number
  poll_interval_min_sec: string
  poll_interval_max_sec: string
  monitor_interval_sec: number
}


export type VPRow = {
  id: number
  creator_name: string | null
  topic_name: string | null
  entity_name: string | null
  entity_raw?: string | null
  claim: string
  stance: string
  horizon: string | null
  confidence: number
  importance: number
  as_of_date: string | null
  verification_status: string
  source_item_id: number
  review_source?: string | null
  review_note?: string | null
}

export type TimelineEvent = {
  type: 'viewpoint' | 'summary'
  at: string | null
  item_id: number
  item_type: string
  title: string | null
  source_url: string | null
  creator_name: string
  avatar_url: string | null
  viewpoint_id?: number
  claim?: string
  stance?: string
  horizon?: string | null
  entity?: string | null
  conditional?: boolean
  summary?: string
  live_ongoing?: boolean
  confirmed_count?: number
  generated_at?: string | null
}

