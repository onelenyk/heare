# Findings

What was measured, what turned out to be true, and what is still wrong.
Written 2026-08-09/10. Every number here came from running the code on
this machine, not from reading it.

| | |
|---|---|
| [echo-cancellation.md](echo-cancellation.md) | Echo cancellation never worked. Four stacked bugs, chiefly an int16/float overflow that destroyed every sample. Fixed; 40–50 dB measured. |
| [two-agents.md](two-agents.md) | Voice and hands: one model, split by deadline. The A/B, and the assumption it killed. |
| [measuring.md](measuring.md) | The three instruments, and how to read them. |
| [known-broken.md](known-broken.md) | Verified defects still unfixed, with line numbers. |
| [size.md](size.md) | Why simple features cost 30 000 lines, and the measured cut. |

## The one sentence

heare was never as unintelligent as it felt. Every problem found so far
has been plumbing: a pipeline default killing turns in silence, memory
search ranked worst-first, a network call inside the interrupt path, and
an audio filter that deleted the signal it was meant to clean.

## The one lesson

All of it stayed hidden because the only instrument was a pair of ears.
A bug that makes the microphone deaf during playback is indistinguishable
by ear from echo cancellation working perfectly — and it survived for
months on exactly that ambiguity.

The instruments in [measuring.md](measuring.md) are worth more than the
code they were built to debug.
