## ADDED Requirements

### Requirement: Pull detail documents three video-time queries
The existing single video-summary service's “拉取” detail SHALL show the time-range, creator-day, and today-latest query modes, their mutually exclusive parameters, video-time semantics, date zone, current-only result shape, and real read-only trial response alongside the existing event cursor feed.

#### Scenario: Operator switches query mode
- **WHEN** an operator selects one of the three new query modes
- **THEN** the detail shows that mode's required inputs and does not mix them with the event-cursor parameters.

### Requirement: Push detail distinguishes automatic and manual delivery
The existing service's “推送” detail SHALL explain future automatic subscription and current-video manual delivery separately, and SHALL show actual channel, subscription, withdrawal and delivery status without treating mode selection as a delivery action.

#### Scenario: No channel is configured
- **WHEN** no eligible Webhook channel exists
- **THEN** the push contract remains visible and the page identifies that no delivery target is configured.
