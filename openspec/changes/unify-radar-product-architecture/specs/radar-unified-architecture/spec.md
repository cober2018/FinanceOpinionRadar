## ADDED Requirements

### Requirement: The whole project follows one product architecture
The radar SHALL organize its backend, frontend, background tasks, capability engines and deployment under the unified responsibility layout in design.md. Existing valuable implementations SHALL be reused or adapted within that layout. Completion MUST NOT consist only of renaming or wrapping independently operated old products while keeping the previous application architecture as the runtime dependency.

#### Scenario: A capability is migrated into the product
- **WHEN** an existing capability is accepted as migrated
- **THEN** its implementation, callers, configuration and build or runtime entry belong to the unified product layout and no active business caller still relies on the replaced old application structure.

### Requirement: Product modules own business flow and state
The radar SHALL own the authoritative source, content, review, summary and delivery business records and task state. API, scheduler and worker entries SHALL invoke the same product business handlers. Engines MUST NOT introduce a second independent business scheduler or source of truth for those records.

#### Scenario: A monitored source produces a video or live session
- **WHEN** the scheduler dispatches work and a worker processes the media
- **THEN** the work follows the radar-owned business lifecycle and the resulting content, transcript, viewpoints and delivery state belong to that lifecycle.

### Requirement: Required engines are delivered without old product workspaces
Required media collection, recording, danmaku and currently supported transcription capabilities SHALL be available from product-controlled source packages or buildable engine processes. They MUST NOT require another product's source directory, virtual environment or management-page session. Reused implementations SHALL retain their recorded source, version and required notices.

#### Scenario: Recording starts without a legacy management session
- **WHEN** live monitoring is enabled through the radar and the product starts with valid platform configuration
- **THEN** the product-managed recording lifecycle detects and records the source without requiring a StreamCap management-page session to activate monitoring.

#### Scenario: A supported transcription provider is selected
- **WHEN** an operator installs the declared runtime for that provider and configures it in the radar
- **THEN** transcription uses the product's installed engine and worker without borrowing voice-pro code or Python environments.

### Requirement: Cross-module mutations preserve one transaction boundary
Modules SHALL follow the data ownership and dependency direction defined in design.md. Existing cross-module use cases SHALL be coordinated through product workflow handlers using one session and one commit or rollback owner. Participating domain operations MUST NOT commit independently. Source or review changes and necessary withdrawal events SHALL be atomic; an eligible summary and its ready event SHALL be atomic. Existing source-item then event-order locking and event cursor semantics SHALL be preserved. Slow model calls SHALL remain outside the write transaction, with eligibility and fingerprint rechecked before publication.

#### Scenario: A source or review change invalidates a published summary
- **WHEN** the workflow changes source information or review state such that the current ready summary becomes invalid
- **THEN** the change and its withdrawal event commit together; if the transaction fails, neither change is persisted on its own.

#### Scenario: A candidate summary is committed or rejected
- **WHEN** summary generation finishes and its workflow rechecks the source fingerprint and review eligibility under the existing locks
- **THEN** a valid candidate and its ready event commit together, a stale or ineligible candidate is not published, and a transaction failure leaves neither a new summary nor a new event independently persisted.

### Requirement: Continuous engines preserve lifecycle and incremental delivery
Collection SHALL own recording and danmaku start, stop, exit handling and recovery. Engines SHALL report stable session and segment identity and completion state; content SHALL own persisted ingestion progress. Migration SHALL preserve existing single-executor controls, queue separation, deduplication, transcript offsets, partial-file handling and separate audience-message semantics. Retry responsibility SHALL remain explicit between protocol reconnection and business processing without duplicate business dispatch.

#### Scenario: A segment is still being written or is delivered again
- **WHEN** a recording segment has not completed, or the same completed segment is redelivered after restart
- **THEN** an incomplete segment is not marked processed, and an already processed segment does not append duplicate transcript content or change existing evidence offsets.

#### Scenario: A recording or danmaku process stops or exits unexpectedly
- **WHEN** the process stops normally or exits with unfinished work
- **THEN** completed outputs and unfinished progress remain distinguishable, an abnormal exit is not marked normal completion, and recovery resumes from persisted progress only after confirming the old executor is gone and acquiring the existing single-executor control.

### Requirement: Existing behavior and durable identity remain compatible
The refactor SHALL preserve the implemented business capabilities, visible page behavior, identifiers, transcripts, evidence, review rules, video and data-date conventions, current summaries and versions, event continuity, subscriptions and delivery history. Existing public and console API paths, authentication and payload behavior SHALL remain compatible with the implementation baseline. Pending features from other unapproved changes MUST NOT be silently added.

#### Scenario: A downstream consumer continues after the refactor
- **WHEN** an existing consumer uses its current API Key, source or video identifiers and summary event cursor
- **THEN** the same supported pull and push contracts continue with valid identity and version continuity, without architecture-induced duplication, lost review eligibility or changed time meaning.

#### Scenario: An internal module or page is moved
- **WHEN** the implementation is relocated into the target backend modules or frontend features
- **THEN** existing behavior and data remain valid and affected imports, ORM registration, background tasks and frontend calls resolve through the unified implementation.

### Requirement: One product controls configuration and delivery
The radar SHALL provide a documented product installation, configuration, startup and release path for its own entries and engines. Required databases, queues, storage, media tools and model services SHALL be declared dependencies managed by that path. Multiple necessary processes SHALL be allowed under the same controlled product version; a single process or container is not required.

#### Scenario: A new operator installs the product
- **WHEN** the operator follows the radar installation and configuration instructions on a supported environment
- **THEN** the radar API, frontend, tasks and required engines can be built and started without following separate old-product installation or page-operation instructions.

### Requirement: Independence and compatibility are demonstrated before completion
Implementation completion SHALL include results from relevant existing checks, frontend build and an isolated end-to-end run without old project directories, environments or management sessions. The run SHALL demonstrate the supported media processing, review and summary flow and the existing pull/push contracts. Documentation SHALL distinguish proposal, implementation, local integration and production release status. New test code MUST NOT be generated without explicit user authorization.

#### Scenario: The refactor is proposed for completion
- **WHEN** implementation is reviewed for archival
- **THEN** the same requirement records identify the actual code revision, remaining work, existing-check results and independent-run evidence; merely creating folders or passing schema validation does not establish implementation completion.

### Requirement: Migration demonstrates durable work handover and rollback
Before implementation completion, an isolated rehearsal SHALL demonstrate handover using existing data or locally prepared equivalent records, including queued and active work, live segments, media references and delivery states. The rehearsal SHALL preserve task names, queues and argument compatibility for outstanding messages, keep stored media references readable, and prevent old and new executors from concurrently processing the same work. Rollback SHALL demonstrate compatibility with state written by the new version, not merely restoration of old code. Rehearsal MUST NOT consume production queues or send duplicate notifications to real subscribers. Production operations remain outside proposal authorization.

#### Scenario: The new version takes over outstanding work
- **WHEN** old dispatch is paused and work is drained or recorded for continuation before the old executors stop
- **THEN** the new version consumes compatible queued messages and resumes recorded work with valid file references, stable segment identity and continuous event cursors, without clearing outstanding queues or resetting all work to new.

#### Scenario: A delivery has already succeeded or has an unknown result
- **WHEN** delivery processing resumes after migration
- **THEN** existing sent, dead and unknown state semantics remain valid, unknown deliveries are not automatically resent because of migration, and retryable failures retain their existing retry state.

#### Scenario: The rehearsal returns to the old version
- **WHEN** the new version has written data and a rollback is rehearsed
- **THEN** dispatch and executors are stopped before switching, the old version demonstrably reads and resumes the resulting data, queue and file state, and any incompatibility prevents claiming successful rollback until a reversible mapping is validated; restoring a stale snapshot must not erase already delivered event history.
