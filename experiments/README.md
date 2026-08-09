# experiments/

Scratch space for reworking the spine. Nothing in here is wired into the
daemon until it has been driven live and heard.

Rules for this folder:

- Anything here may be deleted. Nothing outside `experiments/` may import
  from it.
- Each experiment is one subfolder with its own README stating what it is
  meant to prove and how you would know it failed.
- Prove it by running it, not by testing it. The suite already passes on
  code that goes silent in production.

---

## Why this exists

heare is more capable than it feels. Every finding so far has been
plumbing, not the model: a 10s pipecat default killing turns in silence,
transcripts that dropped the speaker, an LLM call with no deadline, memory
search ranked worst-first, four of five modes with empty deny lists.

Two things underlie all of it.

**Policy written as prose.** Constraints live in the system prompt — the
tool-call cap, the confirmation rule, the differences between modes — where
nothing enforces them and nothing reports that they were ignored. If a
sentence describes a limit, it belongs in code.

**No instruments.** Usage metrics arrive from pipecat and are persisted; no
one reads them. The actions table is empty. Testing by ear is not a
preference here, it is the only available instrument, so any regression too
small to hear is invisible forever.

---

## The contract

Write this down first, because the architecture below is just the cheapest
way to satisfy it.

    For every Heard event there is exactly one Spoke event,
    beginning no later than T_max after the Heard timestamp.
    Always — whatever the state of tools, network, or model.

Enforced by a watchdog, not hoped for: if no audio has started by T_max,
the watchdog speaks a fallback and writes `Failed` to the journal.

This is a property of the system, and it is testable. Whether the answer is
*correct* is a property of the model, and it is not. Design for the first;
add the second on top.

---

## Shape

Not a processor graph — an append-only event log and one state machine.
Today the state is spread across fifteen stateful processors, which is why
"why is it silent" has no answer. Here, printing the state and the last
twenty events answers it.

    events   Heard · Decided · Spoke · WorkStarted · WorkDone · Failed
    states   IDLE → LISTENING → THINKING → SPEAKING → WORKING

    audio.py     devices, ring buffers, AEC/NS/VAD          ~300
                 out: SpeechSegment(pcm) · play(pcm) · stop()
    ears.py      segment → text, one POST                    ~80
    mouth.py     text → audio, streamed per sentence         ~150
    brain.py     bounded context → Say | Say + Delegate      ~200
    hands.py     the tools, no latency budget                ~300
    journal.py   sqlite, events + a turns view               ~150
    loop.py      state machine + watchdog. The only place    ~200
                 anything is decided.

`audio.py` is the only module with threads and callbacks. The rest is
asyncio and queues.

### Two speeds, not two agents

Same model, same key, same code. The split is temporal: what must fit in a
second, and what must not block anything.

`brain` sees three verbs — `delegate`, `remember`, `recall`. Everything
else lives in `hands`, which has no deadline and can afford a large prompt.
The conversational model stops choosing among 63 schemas under a 300ms
budget and goes back to holding a conversation.

Slow work stops being a blocked turn and becomes two utterances: "гляну"
now, the answer when it lands. Silence is not merely fixed — it becomes
impossible, because nothing in the speaking path waits.

Routing errors are asymmetric: delegating needlessly costs one extra
sentence, answering from nothing costs a fabrication. So: when unsure,
delegate.

### Fast path

A small table of exact matches — time, volume, stop, repeat, mute — answered
without the network or the model. Tens of milliseconds, fully predictable,
and a real share of daily use.

### Modes

    MODES = {"ambient": {...}, "work": ALL_TOOLS, "quiet": {...}}

A mode is the set of tools `hands` may use. Ten lines instead of a
subsystem — and code, so it cannot be disregarded.

---

## Order of work

Instrument, then change, then remove. Doing it in any other order means
changing things blind and listening to hear whether it got better.

1. **Journal, state machine, watchdog — alongside the existing pipeline.**
   Pure addition, no risk. From here on, changes are measurable.
2. **Split brain/hands.** `register_all_tools` registers three functions
   instead of 63; the rest goes behind `delegate`, and results return
   through `_handle_inject`, which already exists and works. Roughly 200
   lines, and it carries most of the felt improvement.
3. **The rest falls away.** Once the speaking path no longer depends on the
   framework, VAD, resampling and the SDKs come off one at a time.

Do not start by deleting pipecat. It is the riskiest step and it breaks the
part that currently works.

---

## Dependencies, measured

944 MB across 104 packages, for something that listens to a microphone and
talks.

`pipecat-ai` is 21 MB and directly requires `transformers`, `onnxruntime`,
`numba`, `nltk`, `pillow`:

| via | size | for |
|---|---|---|
| llvmlite + numba | 130 MB | audio resampling |
| onnxruntime + sympy | 94 MB | voice activity detection |
| transformers + tokenizers + nltk + hf_xet | 71 MB | sentence splitting, smart-turn |
| scipy (via pyloudnorm) | 88 MB | loudness normalisation |
| pillow | 15 MB | unused |

Roughly 450 MB is one framework's dependency closure. Separately,
`basedpyright` (70 MB) and `nodejs_wheel` (213 MB) sit in the venv without
appearing in `uv.lock` at all.

Carried twice: two PortAudio bindings (`sounddevice`, `pyaudio`), two LLM
SDKs (`openai`, `anthropic` — and `make_identity_bootstrap` already talks to
both APIs in raw httpx, without either), three HTTP stacks.

What the replacements actually are: WebRTC's own VAD, already present via
`pywebrtc-audio`; a 16 kHz device or thirty lines of numpy; a regex; RMS.
Leaving `numpy`, `sounddevice`, `pywebrtc-audio`, `httpx`, `aiosqlite` —
about 60 MB.

**pipecat is priced for a problem heare does not have**: many transports,
many providers, many users. heare is one microphone, one speaker, one user,
one Mac, and already fights the framework's model with a custom echo
classifier, echo gate, AEC3, mute gate and interruption fade.

Honest cost of leaving: pipecat's hard-won handling of barge-in and frame
ordering, and one-line provider swaps. The first is half-rewritten here
already; the second happens twice a year.

---

## Keep

The audio layer is the good part and the hard part, and it works: echo
classifier, AEC3, mute gate, sidetone, per-language voice swap. The
provider registry is clean enough that a new provider is one dataclass.
Killing the process group on bash cancellation was thought through.

The problem was never the quality of what was written. It is that the tests
cover what was written and the failures live in what was assembled — no test
walks the path from a person speaking to the assistant answering.
