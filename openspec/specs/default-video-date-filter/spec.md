# default-video-date-filter Specification

## Purpose
TBD - created by archiving change fix-default-video-date-filter. Update Purpose after archive.
## Requirements
### Requirement: Initial date selection matches the first video list request
On first entering the viewpoint page, video library or review queue, the front end SHALL select today's date using its existing date convention and include the same selected date as `date_from` and `date_to` in the first list request. It MUST NOT initially request the unfiltered list while presenting a selected day.

#### Scenario: First entry into video library
- **WHEN** a user first opens the video library
- **THEN** the selected date is today and the initial list request carries that date as both existing date parameters.

#### Scenario: First entry into viewpoints or review queue
- **WHEN** a user first opens viewpoints or the review queue
- **THEN** the initial list request uses the displayed selected day and retains the existing has_viewpoints or pending_review condition.

### Requirement: Existing date convention and manual selection remain intact
The fix SHALL reuse the existing date formatter, query parameters and backend filtering. It MUST NOT alter date fields, timezone interpretation, data classification, manual day selection or the existing ways to clear the filter.

#### Scenario: User selects another date
- **WHEN** a user selects a different day
- **THEN** the existing filtering request uses that selected day exactly as before the fix.

#### Scenario: User clears the date filter
- **WHEN** a user invokes an existing clear-filter action
- **THEN** the selected-date state is cleared and the list follows the existing unfiltered behavior.

### Requirement: An empty selected-day result stays empty
The front end SHALL display the existing empty state when the selected date has no results and MUST NOT silently fetch or substitute the full list.

#### Scenario: No videos on the selected day
- **WHEN** the selected-day request returns no rows
- **THEN** that page shows an empty list and keeps the selected-day filter.

