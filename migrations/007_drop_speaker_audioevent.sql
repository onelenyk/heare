-- Drop speaker_id, speaker_confidence, audio_event_label, audio_event_score from transcripts
-- These columns are no longer used after removing speaker recognition and YAMNet features

PRAGMA foreign_keys=OFF;

CREATE TABLE transcripts_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    mode TEXT NOT NULL,
    agent_mode TEXT,
    agent_spoken INTEGER,
    turn_id INTEGER REFERENCES turns(id)
);

INSERT INTO transcripts_new (id, ts, text, mode, agent_mode, agent_spoken, turn_id)
    SELECT id, ts, text, mode, agent_mode, agent_spoken, turn_id
    FROM transcripts;

DROP TABLE transcripts;

ALTER TABLE transcripts_new RENAME TO transcripts;

CREATE INDEX IF NOT EXISTS idx_transcripts_ts ON transcripts(ts DESC);
CREATE INDEX IF NOT EXISTS idx_transcripts_turn ON transcripts(turn_id) WHERE turn_id IS NOT NULL;

PRAGMA foreign_keys=ON;

INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '7');
