## ADDED Requirements

### Requirement: Video-summary pull APIs are listed in Open Interface
The Open Interface page SHALL visibly list the implemented video-summary incremental pull API and compatible single-video query, including paths, `X-API-Key` authentication, cursor usage, eligibility, and the returned video-level summary fields.

#### Scenario: No summary events exist
- **WHEN** an operator opens Open Interface before any video-summary event has been produced
- **THEN** the pull API definitions remain visible and the page does not imply that data already exists.

### Requirement: Video-summary push event contract is listed separately
The Open Interface page SHALL visibly list the video-summary Webhook event with `ready` and `withdrawn` meanings, stable `event_id`, video-level time and `time_basis`, summary nullability, signing headers, and the need for receiver deduplication.

#### Scenario: Operator reviews event integration
- **WHEN** an operator inspects the video-summary push event
- **THEN** the page identifies the event payload and signing contract separately from the pull API and from per-viewpoint notifications.

### Requirement: Event definition and subscriber state are distinguishable
The page SHALL show video-summary subscriber state based on configured, enabled generic Webhook channels with video-summary subscription enabled, while keeping the event definition visible even when there are no subscribers.

#### Scenario: No push channel is configured
- **WHEN** there is no eligible video-summary subscriber
- **THEN** the event definition remains visible and the page clearly states that push delivery is not configured, while the pull API remains available.

#### Scenario: A channel is subscribed
- **WHEN** an eligible channel has video-summary subscription enabled
- **THEN** the page shows that subscription alongside the existing channel controls and recent delivery records.
