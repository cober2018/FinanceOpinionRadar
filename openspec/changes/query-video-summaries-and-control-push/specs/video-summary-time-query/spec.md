## ADDED Requirements

### Requirement: Time-based pull uses video time
The service SHALL provide authenticated read-only queries for current eligible video summaries using the video's `video_time`, not summary completion time. It SHALL preserve `GET /open/v1/video-summaries` as the independent `ready`/`withdrawn` event cursor feed.

#### Scenario: Video completed late
- **WHEN** a video from yesterday receives an approved summary today
- **THEN** it appears in the existing new-event feed and eligible push delivery, but not in today's video-time query.

#### Scenario: Video time unavailable
- **WHEN** the only known date is summary completion and `video_time` is unavailable
- **THEN** the item is excluded from time-based queries and no fallback timestamp is fabricated.

### Requirement: Caller can query a video-time range
The service SHALL accept timezone-qualified ISO 8601 `from` and `to` timestamps and return summaries whose video times fall in the half-open interval `[from,to)`. It SHALL reject an invalid or inverted interval and paginate with a stable descending `(video_time,item_id)` cursor.

#### Scenario: Range spans multiple days
- **WHEN** an authorized caller requests a valid range
- **THEN** only current eligible summaries in that interval are returned in stable newest-first order, with continuation information until exhausted.

### Requirement: Caller can query one creator on one day
The service SHALL accept a stable `creator_id` and an `Asia/Shanghai` calendar date, and allow pagination through every current eligible summary for that creator whose video time belongs to that date. It MUST NOT match creator display names as identities.

#### Scenario: Creator has more than one page
- **WHEN** one creator published more eligible videos on the requested day than fit in one page
- **THEN** the caller can retrieve all matching videos through continuation without duplicates or omissions.

### Requirement: Caller can query today's most recent N summaries
The service SHALL accept N from 1 to 200 and return at most N current eligible summaries across all creators from the current `Asia/Shanghai` calendar day, ordered by video time descending and then item ID descending.

#### Scenario: Fewer than N summaries exist
- **WHEN** fewer than N current eligible summaries have today's video time
- **THEN** the service returns only those available summaries and does not fill the result with older videos.

### Requirement: Time queries return only current eligible versions
Each time-query result SHALL represent at most one currently valid `ready` version per video and include the existing video-level summary, creator identity/name, video time and time basis, version, and event ID. Historical and withdrawn versions MUST NOT be returned as current summaries.

#### Scenario: Summary is withdrawn
- **WHEN** the latest version for an item is `withdrawn`
- **THEN** no version of that item appears in time queries, while the withdrawal remains available in the existing event feed.
