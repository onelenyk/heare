-- Conversation Memory System - Migration 01
-- Add conversations and turns tables for conversation memory and turn aggregation

-- Conversation sessions (coherent discussion threads)
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL,
    end_ts REAL,  -- NULL if active
    mode TEXT NOT NULL,  -- 'silent', 'focus', or 'ambient'
    summary TEXT,  -- Updated summary of topics discussed
    entity_map TEXT  -- JSON: entities mentioned (people, places, things)
);

-- Conversation turns (aggregated utterances)
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    aggregated_text TEXT NOT NULL,  -- Combined utterances in turn
    utterance_count INTEGER NOT NULL,  -- How many fragments were combined
    topic_tags TEXT,  -- JSON: extracted topics
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- Add turn_id column to transcripts table
ALTER TABLE transcripts ADD COLUMN turn_id INTEGER REFERENCES turns(id);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_conversations_mode ON conversations(mode);
CREATE INDEX IF NOT EXISTS idx_conversations_active ON conversations(end_ts) WHERE end_ts IS NULL;
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_turn ON transcripts(turn_id) WHERE turn_id IS NOT NULL;
