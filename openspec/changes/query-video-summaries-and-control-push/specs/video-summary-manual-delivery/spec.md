## ADDED Requirements

### Requirement: Viewpoint summary offers explicit push modes
The viewpoint summary panel SHALL offer a push action that distinguishes automatic subscription for future completed video-summary events from manual delivery of this video's current approved summary. Selecting a mode alone MUST NOT change any channel setting or enqueue a delivery.

#### Scenario: Operator selects automatic push
- **WHEN** an operator confirms automatic push for a generic Webhook channel
- **THEN** the channel's video-summary subscription is enabled for future events from the current subscription boundary; the current summary is not silently backfilled.

#### Scenario: Operator selects manual push
- **WHEN** an operator selects a channel and confirms manual push for the current video's eligible summary
- **THEN** exactly that current `ready` event is queued for that channel, with visible delivery status.

### Requirement: Manual delivery reuses secured event transport
The system SHALL accept manual delivery only for an enabled generic HTTPS Webhook channel and a currently eligible `ready` summary. It SHALL deliver the same event body, identity, signature headers, URL safety checks, retries, and delivery record semantics as automatic video-summary push. It MUST NOT send to the external channel synchronously in the operator request.

#### Scenario: Summary has lost eligibility
- **WHEN** an operator requests manual push for an unapproved, missing, or withdrawn summary
- **THEN** the request is rejected and no ready delivery is queued.

#### Scenario: Automatic and manual delivery target the same event
- **WHEN** the same channel and event are eligible through both modes
- **THEN** the channel receives no duplicate successful delivery; the operator sees the existing delivery state.

### Requirement: Manual recipients receive later withdrawal
When a manually delivered `ready` summary is later withdrawn, the service SHALL enqueue its `withdrawn` event to that channel even if automatic subscription is disabled, and SHALL preserve unknown-outcome deliveries for operator reconciliation.

#### Scenario: Manual-only channel received ready
- **WHEN** the summary is withdrawn after a manual-only channel received its ready event
- **THEN** that channel receives a signed withdrawal event and the delivery result is recorded.

#### Scenario: Summary becomes stale before queued send
- **WHEN** an enqueued manual ready event is no longer the current eligible version before the worker sends it
- **THEN** the worker does not send the stale ready body and records the skipped outcome for the operator.
