# broadcaster-open-interface-catalog Specification

## Purpose
TBD - created by archiving change catalog-broadcaster-open-interface. Update Purpose after archive.
## Requirements
### Requirement: Broadcaster catalog is discoverable
The Open Interface page SHALL expose a selectable “主播清单” service alongside the existing video-summary service. Its detail SHALL show `GET /open/v1/broadcasters`, `X-API-Key` authentication, no query parameters, complete account snapshot semantics and `source_account_id`, `display_name`, `platform`, `external_id`, `enabled`. It MUST NOT show push subscription controls for the broadcaster service.

#### Scenario: Operator selects broadcaster service
- **WHEN** an operator selects “主播清单”
- **THEN** the page shows its read-only contract and does not display video-summary cursor or push controls.

### Requirement: Broadcaster service supports actual trial requests
The detail SHALL issue an explicit same-origin read-only request to the existing broadcaster route and display the actual status and body. The Key MUST remain transient and only appear in the request header. Switching services SHALL clear prior results and SHALL be blocked while a trial request is running.

#### Scenario: Successful empty result
- **WHEN** the endpoint returns an empty items array
- **THEN** the page shows the real empty response rather than fabricated accounts.

#### Scenario: Trial is rejected
- **WHEN** the server rejects the Key
- **THEN** the page shows the actual error without revealing the entered Key or creating credentials.

### Requirement: Existing video-summary behavior is preserved
Selecting the existing video-summary service SHALL restore its original pull and push details, parameters, credential controls, subscriptions and delivery records. Selecting either service MUST NOT mutate credentials or subscriptions.

#### Scenario: Operator returns to video summaries
- **WHEN** an operator selects the video-summary service after viewing broadcasters
- **THEN** its original pull/push operations remain available with unchanged access state.

