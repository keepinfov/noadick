# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

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
