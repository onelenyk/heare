-- Heare memory store schema (v6.3, US-02).
--
-- Six base tables cover the five memory kinds plus an entity link table.
-- A single FTS5 virtual table indexes the searchable text columns from
-- conversation, episodic, and semantic. Triggers keep the FTS5 index in
-- sync with inserts, updates, and deletes on those three tables.
--
-- WAL journal mode and a 5000 ms busy timeout are set in store.py at
-- connection time, not here, because PRAGMAs are connection-scoped on
-- SQLite and we want store.py to enforce them on every reopen.

CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_session_ts
    ON conversation(session_id, ts);

CREATE TABLE IF NOT EXISTS episodic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    tags TEXT,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_episodic_ts ON episodic(ts DESC);

CREATE TABLE IF NOT EXISTS semantic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_semantic_key ON semantic(key);

CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    args_json TEXT,
    result_json TEXT,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_log_ts ON action_log(ts DESC);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    attrs_json TEXT,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS entity_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id INTEGER NOT NULL,
    rel TEXT NOT NULL,
    dst_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    FOREIGN KEY (src_id) REFERENCES entities(id),
    FOREIGN KEY (dst_id) REFERENCES entities(id)
);

CREATE INDEX IF NOT EXISTS idx_entity_links_src ON entity_links(src_id);
CREATE INDEX IF NOT EXISTS idx_entity_links_dst ON entity_links(dst_id);

-- Unified full-text index across the three searchable text tables.
-- ``kind`` discriminates the source so recall can filter by memory type;
-- ``ref_id`` points back to the row in the source table.
CREATE VIRTUAL TABLE IF NOT EXISTS fts_memory USING fts5(
    kind UNINDEXED,
    ref_id UNINDEXED,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- Conversation triggers
CREATE TRIGGER IF NOT EXISTS conversation_ai
AFTER INSERT ON conversation
BEGIN
    INSERT INTO fts_memory(kind, ref_id, text)
    VALUES ('conversation', NEW.id, NEW.content);
END;

CREATE TRIGGER IF NOT EXISTS conversation_au
AFTER UPDATE OF content ON conversation
BEGIN
    DELETE FROM fts_memory
    WHERE kind = 'conversation' AND ref_id = OLD.id;
    INSERT INTO fts_memory(kind, ref_id, text)
    VALUES ('conversation', NEW.id, NEW.content);
END;

CREATE TRIGGER IF NOT EXISTS conversation_ad
AFTER DELETE ON conversation
BEGIN
    DELETE FROM fts_memory
    WHERE kind = 'conversation' AND ref_id = OLD.id;
END;

-- Episodic triggers (FTS over summary)
CREATE TRIGGER IF NOT EXISTS episodic_ai
AFTER INSERT ON episodic
BEGIN
    INSERT INTO fts_memory(kind, ref_id, text)
    VALUES ('episodic', NEW.id, NEW.summary);
END;

CREATE TRIGGER IF NOT EXISTS episodic_au
AFTER UPDATE OF summary ON episodic
BEGIN
    DELETE FROM fts_memory
    WHERE kind = 'episodic' AND ref_id = OLD.id;
    INSERT INTO fts_memory(kind, ref_id, text)
    VALUES ('episodic', NEW.id, NEW.summary);
END;

CREATE TRIGGER IF NOT EXISTS episodic_ad
AFTER DELETE ON episodic
BEGIN
    DELETE FROM fts_memory
    WHERE kind = 'episodic' AND ref_id = OLD.id;
END;

-- Semantic triggers (FTS over value)
CREATE TRIGGER IF NOT EXISTS semantic_ai
AFTER INSERT ON semantic
BEGIN
    INSERT INTO fts_memory(kind, ref_id, text)
    VALUES ('semantic', NEW.id, NEW.value);
END;

CREATE TRIGGER IF NOT EXISTS semantic_au
AFTER UPDATE OF value ON semantic
BEGIN
    DELETE FROM fts_memory
    WHERE kind = 'semantic' AND ref_id = OLD.id;
    INSERT INTO fts_memory(kind, ref_id, text)
    VALUES ('semantic', NEW.id, NEW.value);
END;

CREATE TRIGGER IF NOT EXISTS semantic_ad
AFTER DELETE ON semantic
BEGIN
    DELETE FROM fts_memory
    WHERE kind = 'semantic' AND ref_id = OLD.id;
END;
