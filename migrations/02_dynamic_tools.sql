-- Dynamic tools table for user-created tools
-- Tools created at runtime via create_tool are stored here and loaded on startup
CREATE TABLE IF NOT EXISTS dynamic_tools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,           -- Tool identifier (lowercase)
    sdk_name TEXT NOT NULL,              -- CamelCase version for SDK compatibility
    execution_type TEXT NOT NULL,        -- 'bash', 'fetch', 'python'
    description TEXT NOT NULL,           -- Human-readable purpose
    enabled BOOLEAN DEFAULT TRUE,        -- Can be disabled without deleting
    definition_json TEXT NOT NULL,       -- JSON: args schema + implementation
    created_ts REAL NOT NULL,            -- Creation timestamp
    modified_ts REAL NOT NULL,           -- Last modification timestamp
    last_used_ts REAL,                   -- Last time tool was executed
    usage_count INTEGER DEFAULT 0        -- Number of times tool was used
);

-- Index for querying enabled tools
CREATE INDEX IF NOT EXISTS idx_dynamic_tools_enabled ON dynamic_tools(enabled);

-- Index for analytics: most recently used tools
CREATE INDEX IF NOT EXISTS idx_dynamic_tools_last_used ON dynamic_tools(last_used_ts DESC);

-- Insert schema version record
INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', 2);
