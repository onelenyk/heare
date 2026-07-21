// Parsing and filtering for the daemon log. Kept out of the component as
// pure functions so they can be exercised directly against real log data.

// Matches the daemon formatter: "%(asctime)s %(levelname)s %(name)s %(message)s"
// (src/main.py:48). Anything that doesn't match is a continuation line —
// tracebacks are a large share of the file and only their first line carries
// the prefix, so they must be folded into the entry above.
const LOG_LINE = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3}) (\w+) (\S+) ([\s\S]*)$/;

// Which subsystem each logger belongs to. Anything unlisted falls to 'daemon'.
const LOGGER_CATEGORY = {
  'heare.transcription_gate': 'stt',
  'heare.text_injector': 'stt',
  'heare.tts': 'tts',
  'heare.switchable_llm': 'llm',
  'heare.llm_context_injector': 'llm',
  'heare.assistant_response': 'llm',
  'heare.events': 'llm',
  'heare.direct_tools': 'tools',
  'heare.agent_skills': 'tools',
  'heare.subagent_manager': 'tools',
  'heare.mute_gate': 'audio',
  'heare.echo_gate': 'audio',
  'heare.interrupt_toggle_gate': 'audio',
  'heare.cancel_flag_gate': 'audio',
  'heare.browser_bridge': 'browser',
  'websockets.server': 'browser',
  'httpx': 'http',
  'heare.usage_recorder': 'usage',
};

// Order here is the chip order in the UI.
export const LOG_CATEGORIES = [
  { key: 'tools', label: 'tool calls' },
  { key: 'llm', label: 'LLM' },
  { key: 'stt', label: 'STT' },
  { key: 'tts', label: 'TTS' },
  { key: 'audio', label: 'audio' },
  { key: 'browser', label: 'browser' },
  { key: 'usage', label: 'usage' },
  { key: 'daemon', label: 'daemon' },
  { key: 'http', label: 'HTTP' },
];

// Off by default: 'audio' is the mic-muted frame-drop heartbeat and 'http' is
// the menubar polling its own /state — together ~94% of lines, which buries
// everything worth reading. Both are one chip click away.
export const NOISY_CATEGORIES = ['audio', 'http'];

export function defaultCategories() {
  return new Set(
    LOG_CATEGORIES.map(c => c.key).filter(k => !NOISY_CATEGORIES.includes(k))
  );
}

export function categorize(loggerFull) {
  return LOGGER_CATEGORY[loggerFull] || 'daemon';
}

export function parseLogs(lines) {
  const entries = [];
  for (const line of lines) {
    const m = LOG_LINE.exec(line);
    if (m) {
      const loggerFull = m[5];
      entries.push({
        date: m[1],
        time: m[2],
        level: m[4].toUpperCase(),
        loggerFull,
        // Every daemon logger is "heare.x"; the prefix is pure noise here.
        logger: loggerFull.replace(/^heare\./, ''),
        category: categorize(loggerFull),
        message: m[6],
        detail: [],
      });
    } else if (entries.length > 0) {
      entries[entries.length - 1].detail.push(line);
    } else {
      // The tail landed mid-traceback — keep it rather than dropping it.
      entries.push({
        level: '', logger: '', loggerFull: '', category: 'daemon',
        time: '', message: '', detail: [line],
      });
    }
  }
  return entries;
}

const LEVEL_RANK = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };

/**
 * @param entries  output of parseLogs
 * @param cats     Set of enabled category keys (empty Set = show none)
 * @param minLevel 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
 * @param query    free-text, matched against message, logger and traceback
 */
export function filterLogs(entries, { cats, minLevel = 'DEBUG', query = '' } = {}) {
  const floor = LEVEL_RANK[minLevel] ?? 0;
  const q = query.trim().toLowerCase();
  return entries.filter(e => {
    if (cats && !cats.has(e.category)) return false;
    // Orphan continuations have no level; never hide them behind a level floor.
    if (e.level && (LEVEL_RANK[e.level] ?? 1) < floor) return false;
    if (q) {
      const hay = (e.message + ' ' + e.logger + ' ' + e.detail.join(' ')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function countByCategory(entries) {
  const counts = {};
  for (const e of entries) counts[e.category] = (counts[e.category] || 0) + 1;
  return counts;
}
