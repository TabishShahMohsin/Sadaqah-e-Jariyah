-- Sadaqah-e-Jariyah contribution intake (Cloudflare D1)
CREATE TABLE IF NOT EXISTS submissions (
  id           TEXT PRIMARY KEY,
  token_hash   TEXT NOT NULL,              -- SHA-256 of the upload token; the token itself is never stored
  ip_hash      TEXT NOT NULL,              -- HMAC of the IP, only for rate limits
  created      INTEGER NOT NULL,
  updated      INTEGER NOT NULL,
  status       TEXT NOT NULL,              -- uploading | received | sorting | in_review | published | needs_info | rejected | abandoned
  context      TEXT NOT NULL,
  credit       TEXT,
  course_hint  TEXT,
  links        TEXT NOT NULL DEFAULT '[]', -- JSON array of Google Drive URLs
  total_bytes  INTEGER NOT NULL DEFAULT 0,
  issue        INTEGER,
  pr           INTEGER,
  note         TEXT,
  published    TEXT NOT NULL DEFAULT '[]'  -- JSON array of archive paths
);
CREATE INDEX IF NOT EXISTS idx_sub_ip ON submissions (ip_hash, created);
CREATE INDEX IF NOT EXISTS idx_sub_status ON submissions (status, updated);

CREATE TABLE IF NOT EXISTS files (
  submission_id TEXT NOT NULL,
  idx           INTEGER NOT NULL,
  name          TEXT NOT NULL,             -- original name, display only
  size          INTEGER NOT NULL,
  ext           TEXT NOT NULL,
  r2_key        TEXT NOT NULL,             -- inbox/<id>/<idx>-<safe name>
  upload_id     TEXT NOT NULL,
  part_size     INTEGER NOT NULL,
  parts         INTEGER NOT NULL,
  done          INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (submission_id, idx)
);

CREATE TABLE IF NOT EXISTS parts (
  submission_id TEXT NOT NULL,
  idx           INTEGER NOT NULL,
  n             INTEGER NOT NULL,
  etag          TEXT NOT NULL,
  size          INTEGER NOT NULL,
  PRIMARY KEY (submission_id, idx, n)
);
