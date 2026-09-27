## ADDED Requirements

### Requirement: Open-interface console exposes the video-summary contract
The Open Interface module SHALL describe video-summary pull and push separately from the existing individual-viewpoint endpoints, including authentication, one-video-one-summary fields, `time_basis`, cursor use, event ID, and eligibility.

#### Scenario: Operator inspects integration details
- **WHEN** an operator opens the Open Interface module
- **THEN** the module shows the pull path and Webhook event contract without implying that unreviewed or unsummarized videos are available.

### Requirement: Subscription and delivery state are visible
The console SHALL let an operator view and manage video-summary subscriptions and inspect recent sent, failed, dead, and unknown delivery states without exposing stored secrets.

#### Scenario: Inspect failed delivery
- **WHEN** a video-summary Webhook fails
- **THEN** the console shows the target channel, event ID, attempt count, last error category, and available recovery action.

#### Scenario: No configured subscriber
- **WHEN** no video-summary subscriber exists
- **THEN** the console still shows the pull API as usable and clearly marks push as not configured.
