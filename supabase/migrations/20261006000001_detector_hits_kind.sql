-- P2 (docs/states-and-near-a-level-as-signals-2026-10-06.md §8.3): persist the
-- signal taxonomy on each raw detector hit so the evaluator and dashboards can
-- group by kind / concept without re-deriving them from labels.
--
-- Additive only: three nullable columns, no default, no backfill, no NOT NULL.
-- Pre-migration rows stay NULL; readers derive kind with scoring.kinds.kind_of().
-- Safe to roll back with `alter table detector_hits drop column ...` since
-- nothing depends on the columns yet.
alter table detector_hits
  add column if not exists kind    text,
  add column if not exists concept text,
  add column if not exists context jsonb;
