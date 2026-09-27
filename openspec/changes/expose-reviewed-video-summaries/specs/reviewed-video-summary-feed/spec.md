## ADDED Requirements

### Requirement: Export one reviewed summary per video version
The system SHALL expose one current video-level summary derived only from confirmed viewpoints, with `item_id`, `version`, `display_name`, `video_time`, `time_basis`, `summary`, `generated_at`, and a stable `event_id`. The export SHALL NOT expose per-viewpoint speaking offsets as the video's timestamp.

#### Scenario: Eligible reviewed video
- **WHEN** a video has at least one confirmed viewpoint, no candidate or needs-review viewpoints, and a non-empty summary matching the current confirmed viewpoint set
- **THEN** its current export is `ready` and both delivery methods use the same version and payload.

#### Scenario: No eligible summary
- **WHEN** the video has no confirmed viewpoints, still has pending review, or its summary has not been generated
- **THEN** the system does not emit a ready summary or invent conclusion text.

### Requirement: Video time has explicit source semantics
The system SHALL use one video-level source time for the whole summary and SHALL return its basis. Missing source time SHALL be `null` with an `unknown` basis.

#### Scenario: Regular video time
- **WHEN** a regular video has a source `published_at` but no reliable recording-start time
- **THEN** `video_time` equals `published_at` and `time_basis` is `published_at`, without claiming it is the exact spoken time.

#### Scenario: Live recording time
- **WHEN** a live item has a recorded start time
- **THEN** `video_time` equals that start time and `time_basis` is `live_started_at`.

### Requirement: Authenticated incremental pull
The system SHALL offer an `X-API-Key` protected `/open/v1/video-summaries` paginated feed with a stable cursor and ascending change order. It SHALL preserve `GET /open/v1/items/{item_id}/summary` and its existing fields while exposing current version and eligibility metadata.

#### Scenario: Initial and incremental fetch
- **WHEN** a consumer fetches the feed and then sends the returned cursor
- **THEN** it receives only later ready or withdrawn events, without missing or duplicating a boundary event.

#### Scenario: Existing item endpoint
- **WHEN** a consumer calls the existing item summary endpoint
- **THEN** it still receives the existing fields, and an ineligible current summary is not presented as a ready export.

### Requirement: Changes and withdrawals are visible
The system SHALL version regenerated summaries and SHALL emit a withdrawn event when a previously exportable summary loses review eligibility or its confirmed-viewpoint fingerprint changes.

#### Scenario: Viewpoint edited after export
- **WHEN** a confirmed viewpoint is changed after a ready summary was exported
- **THEN** the old version becomes ineligible, a withdrawn event is available to pull and push, and a new ready version requires regeneration.
