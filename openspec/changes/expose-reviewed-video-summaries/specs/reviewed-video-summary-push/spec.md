## ADDED Requirements

### Requirement: Explicit video-summary subscription
The system SHALL allow an authorized operator to opt a generic Webhook channel into video-summary events. Existing viewpoint-push channels SHALL remain unchanged unless explicitly opted in.

#### Scenario: Subscription enabled
- **WHEN** an operator enables video-summary delivery for a valid channel
- **THEN** new ready and withdrawn video-summary events are eligible for that channel only.

#### Scenario: Existing channel untouched
- **WHEN** a channel has no video-summary subscription
- **THEN** it continues its prior viewpoint-push behavior without receiving video-summary events.

### Requirement: Push uses the pull contract
The system SHALL push the same `event_id`, item, version, state, video time, creator name, and summary semantics as the pull feed. The request SHALL be signed and bounded by target validation and timeout.

#### Scenario: Ready summary push
- **WHEN** an eligible reviewed summary event is published
- **THEN** each enabled subscriber receives a signed HTTPS Webhook carrying that event's stable ID and version.

#### Scenario: Withdrawn summary push
- **WHEN** an exported summary loses eligibility
- **THEN** each enabled subscriber receives a withdrawn event referring to the invalidated item and version, without a fabricated replacement conclusion.

### Requirement: Delivery remains traceable and retry-safe
The system SHALL persist channel × event delivery attempts, errors, final response class, and state. A consumer SHALL be able to deduplicate by event ID. A timeout or connection loss after send SHALL be marked unknown and SHALL NOT be treated as confirmed non-delivery.

#### Scenario: Explicit failure
- **WHEN** the target clearly returns a non-success response
- **THEN** the attempt is recorded and retried within a bounded policy; terminal failure is visible for manual recovery.

#### Scenario: Unknown outcome
- **WHEN** the request times out or the connection drops after transmission may have begun
- **THEN** the attempt is marked unknown, its stable event ID is retained, and reconciliation or explicit operator action precedes another attempt.

#### Scenario: Pull repairs missed push
- **WHEN** a subscriber detects a missed or unresolved push event
- **THEN** it can retrieve the same event through the incremental pull feed.
