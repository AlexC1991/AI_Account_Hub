PRAGMA foreign_keys = ON;

-- One row per installation, UTC day, model, and reasoning setting. These rows
-- retain only the numeric allowlist already shown in the desktop preview.
CREATE TABLE daily_contributions (
  receipt_id TEXT NOT NULL,
  installation_id TEXT NOT NULL,
  period_start TEXT NOT NULL,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  reasoning_effort TEXT NOT NULL,
  total_tokens INTEGER NOT NULL,
  completed_tasks INTEGER NOT NULL,
  active_ms INTEGER NOT NULL,
  edits INTEGER NOT NULL,
  files_changed INTEGER NOT NULL,
  tests INTEGER NOT NULL,
  commands INTEGER NOT NULL,
  short_burn REAL NOT NULL,
  weekly_burn REAL NOT NULL,
  PRIMARY KEY (receipt_id, provider, model_id, reasoning_effort),
  FOREIGN KEY (receipt_id) REFERENCES submissions(receipt_id) ON DELETE CASCADE,
  FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE
);

CREATE INDEX idx_contributions_period_model
  ON daily_contributions(period_start, provider, model_id, reasoning_effort);
CREATE INDEX idx_contributions_installation
  ON daily_contributions(installation_id);

-- Public generation reads compact daily rollups rather than scanning raw R2
-- objects. Contributor counts are retained solely for cohort suppression.
CREATE TABLE daily_model_rollups (
  period_start TEXT NOT NULL,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  reasoning_effort TEXT NOT NULL,
  total_tokens INTEGER NOT NULL,
  completed_tasks INTEGER NOT NULL,
  active_ms INTEGER NOT NULL,
  edits INTEGER NOT NULL,
  files_changed INTEGER NOT NULL,
  tests INTEGER NOT NULL,
  commands INTEGER NOT NULL,
  short_burn REAL NOT NULL,
  weekly_burn REAL NOT NULL,
  observations INTEGER NOT NULL,
  contributor_count INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (period_start, provider, model_id, reasoning_effort)
);

CREATE INDEX idx_rollups_model_period
  ON daily_model_rollups(provider, model_id, reasoning_effort, period_start);
