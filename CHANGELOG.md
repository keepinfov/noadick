# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Group chats now have a Telegram `🎰` casino with saved 1–50 centimetre stakes,
  compact loss messages, Corporation-funded payouts, and an administrator
  switch.
- The top three players of each completed chat season receive 10 centimetres of
  SЕКАСКО coverage for a fixed seven days measured from the season end, not a
  later finalization. Prize coverage can wait for a future deposit and never
  becomes liquid size.
- The bank has a dedicated SЕКАСКО screen with base, policy, protected, risky,
  premium, purchase-headroom, and nearest-expiry details.

### Changed

- Casino wins and losses now produce one compact line without spin controls and
  link to a private payout explanation; combinations are also shown when a
  player saves their default stake.
- Casino wins and losses participate in the chat-local economy and seasonal
  wealth timeline; an unfundable win triggers the same immediate deposit
  bail-in and recovery mode as an unfundable withdrawal.
- Controlled positive `/dick` emission is reduced from 3 to 2 centimetres.
- The first 50 centimetres of deposit principal are protected without a policy;
  active SЕКАСКО protects up to 100 additional centimetres on top. Accrued
  interest and overdue-loan recovery remain outside both protection layers.
- An unfundable withdrawal now triggers an immediate, atomic bail-in across all
  deposits without a grace period, then pays the initiator from surviving
  principal even if the Corporation balance becomes negative. This can write
  down every depositor's accrued interest and risky principal immediately; the
  normal early-withdrawal penalty still applies to surviving principal.
- Recovery keeps preserved claims withdrawable while freezing new deposits,
  loans, and SЕКАСКО until both the operating balance and stored liabilities are
  funded again.

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
