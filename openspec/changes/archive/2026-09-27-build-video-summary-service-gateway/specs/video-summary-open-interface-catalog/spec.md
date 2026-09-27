## ADDED Requirements

### Requirement: One video-summary service has pull and push modes
The Open Interface console SHALL provide one top-level “视频审核后总结” service entry with selectable “拉取” and “推送” modes in its detail pane. Switching modes SHALL change only the visible protocol, fields, and operational controls; it MUST NOT send an enable/disable request or change credential, channel, or subscription state. Each mode SHALL contain its own explicit access controls.

#### Scenario: Operator selects pull
- **WHEN** an operator selects “视频审核后总结” and its “拉取” mode
- **THEN** the detail pane shows `GET /open/v1/video-summaries`, `cursor`, `page_size`, `X-API-Key`, event response fields, the compatible single-video query as a related path, and API Key creation, pause/resume, and revocation controls.

#### Scenario: Operator selects push
- **WHEN** an operator selects the same service's “推送” mode
- **THEN** the detail pane shows the Webhook event contract, signing headers, subscription configuration, and recent delivery state without presenting push as a GET API.

#### Scenario: Operator switches modes
- **WHEN** an operator switches between “拉取” and “推送”
- **THEN** only the selected mode's detail and operations are shown, with no enable/disable request sent and existing API Keys, channel settings, and subscriptions unchanged.

#### Scenario: Operator manages access inside a mode
- **WHEN** an operator uses an explicit control within the pull or push detail
- **THEN** pull access is enabled or disabled for a selected API Key, while push delivery follows Webhook channel and video-summary subscription enable/disable semantics; permanent Key revocation is a separate operation.

### Requirement: Pull credentials support reversible pause
The console SHALL allow an operator to change an API Key between `active` and `disabled` through an audited management action. Only `active` Keys SHALL authenticate open pull requests. A `revoked` Key MUST NOT be reactivated, and the console MUST distinguish permanent revocation from reversible pause.

#### Scenario: Paused Key cannot pull
- **WHEN** an operator disables an active Key
- **THEN** requests using that Key are rejected until the operator re-enables it in the pull detail.

#### Scenario: Revoked Key cannot resume
- **WHEN** an operator attempts to re-enable a revoked Key
- **THEN** the management action rejects the request and the Key remains revoked.

### Requirement: Pull service supports a real read-only request
The pull service SHALL allow an operator to submit `cursor`, `page_size`, and an API Key for an explicit same-origin read-only request to the existing open endpoint, and SHALL show the actual response or error. The entered Key MUST remain transient in browser memory and MUST NOT be put in URLs, persistent storage, code snippets, or delivery logs.

#### Scenario: API returns an empty page
- **WHEN** a valid trial request returns no events
- **THEN** the console shows the real empty response and does not claim the API is unavailable.

#### Scenario: API rejects a Key
- **WHEN** a trial request returns an authentication error
- **THEN** the console shows the failure without creating a new Key or displaying the entered Key.

### Requirement: Push service uses real subscription and delivery state
The push service SHALL use the existing generic HTTPS Webhook channels and video-summary subscription flag to manage subscribers and show recent event delivery records, including unknown and failed outcomes. It SHALL distinguish channel connectivity testing from a real video-summary event delivery.

#### Scenario: No subscriber exists
- **WHEN** the channel list loads successfully and no enabled video-summary subscriber exists
- **THEN** the push service says there is no active subscriber while still showing the event contract and configuration action.

#### Scenario: Channel state cannot be loaded
- **WHEN** the channel request fails
- **THEN** the push service shows subscription state as unknown rather than zero.

#### Scenario: Channel test succeeds
- **WHEN** a generic Webhook channel connectivity test succeeds
- **THEN** the console identifies the result as a test message, not proof that a video-summary event was delivered or consumed.
