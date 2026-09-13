// Parsing and filtering for the daemon log. Kept out of the component as
// pure functions so they can be exercised directly against real log data.

// Matches the daemon formatter: "%(asctime)s %(levelname)s %(name)s %(message)s"
// (src/main.py:48). Anything that doesn't match is a continuation line —
// tracebacks are a large share of the file and only their first line carries
// the prefix, so they must be folded into the entry above.
const LOG_LINE = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),(\d{3}) (\w+) (\S+) ([\s\S]*)$/;

// Which part of the assistant each logger belongs to. Anything unlisted
// falls to 'daemon'. The names are the spine's (src/spine, src/mouth,
// src/agent); the old map named pipecat-era loggers that no longer exist,
// so every real line fell to 'daemon' and the chips counted zero.
const LOGGER_CATEGORY = {
  // the ear and the mouth: hearing, turns, replies, speech
  'spine': 'voice',
  'spine.loop': 'voice',
  'spine.tts': 'voice',
  'heare.mouth': 'voice',
  'heare.wake': 'voice',
  'heare.voice_state': 'voice',
  // the model
  'heare.spine.llm': 'llm',
  'heare.spine.vision': 'llm',
  // the worker and its tools
  'heare.hands': 'tools',
  'heare.spine.tools': 'tools',
  'heare.direct_tools': 'tools',
  'heare.tools.system': 'tools',
  'heare.tool_gate': 'tools',
  'heare.agent_skills': 'tools',
  'heare.mcp_bridge': 'tools',
  'heare.jobs': 'tools',
  // speaking first
  'spine.engine': 'initiative',
  'heare.intents': 'initiative',
  'spine.repeats': 'initiative',
  'spine.summary': 'initiative',
  'spine.hearing': 'initiative',
  'heare.spine.environment': 'initiative',
  // roles
  'spine.role_flow': 'roles',
  'heare.spine.roles': 'roles',
  'heare.spine.role_session': 'roles',
  // what is written down
  'spine.persist': 'memory',
  'spine.search': 'memory',
  'heare.storage': 'memory',
  'heare.state': 'memory',
  // the browser bridge
  'heare.browser_bridge': 'browser',
  'websockets.server': 'browser',
  // money
  'heare.features.usage': 'usage',
  // the menubar and dashboard polling their own API
  'httpx': 'http',
  'aiohttp.access': 'http',
};

// Order here is the chip order in the UI.
export const LOG_CATEGORIES = [
  { key: 'voice', label: 'voice' },
  { key: 'llm', label: 'LLM' },
  { key: 'tools', label: 'tools' },
  { key: 'initiative', label: 'initiative' },
  { key: 'roles', label: 'roles' },
  { key: 'memory', label: 'memory' },
  { key: 'browser', label: 'browser' },
  { key: 'usage', label: 'usage' },
  { key: 'daemon', label: 'daemon' },
  { key: 'http', label: 'HTTP' },
];

// Off by default: 'http' is the menubar and the dashboard polling /state
// once a second — most of the file, and never what you came to read.
export const NOISY_CATEGORIES = ['http'];

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
