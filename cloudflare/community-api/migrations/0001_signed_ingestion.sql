PRAGMA foreign_keys = ON;

CREATE TABLE installations (
  id TEXT PRIMARY KEY,
  public_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
);

CREATE TABLE nonces (
  installation_id TEXT NOT NULL,
  nonce TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (installation_id, nonce),
  FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE
);

CREATE TABLE submissions (
  receipt_id TEXT PRIMARY KEY,
  installation_id TEXT NOT NULL,
  period_start TEXT NOT NULL,
  accepted_at TEXT NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  body_sha256 TEXT NOT NULL,
  record_count INTEGER NOT NULL CHECK (record_count BETWEEN 1 AND 100),
  UNIQUE (installation_id, period_start),
  FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE
);

CREATE INDEX idx_nonces_seen_at ON nonces(seen_at);
CREATE INDEX idx_submissions_installation ON submissions(installation_id);
