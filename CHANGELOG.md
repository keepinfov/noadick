# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

### Changed

### Fixed

### Security

## [0.1.1] - 2026-08-16

### Fixed

- Weekly season cards distinguish total player-length change from Corporation
  emission instead of calling both effects inflation.

## [0.1.0] - 2026-08-16

### Added

- Telegram group game with daily `/dick` rolls, duels, no-limit Hold'em,
  profiles, leaderboards, diseases, and administrative controls.
- Chat-local closed economies with corporations, deposits, loans, PISYAGO,
  deposit insurance, debt collection, and auditable economy events.
- Private analytics with PNG charts and CSV exports, plus public weekly races.
- Weekly, chat-local, statistics-only seasons with archived snapshots, a public
  `/season` panel, charts, and optional scheduled posts. Seasons grant no
  economy rewards.
- A pure `/dick` outcome metric for season scoring without insurance, debt, or
  corporation funding effects.

### Changed

- Public game messages use a more compact presentation layer with contextual
  rough humor.
- Weekly digest settings now control season-result publication.

### Fixed

- Season catch-up finalizes every registered group while publishing only the
  newest eligible non-empty result.

### Security

- Season callbacks are bound to their initiating user and source group.
