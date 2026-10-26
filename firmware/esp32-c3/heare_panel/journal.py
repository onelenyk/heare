"""What the panel says about itself: kept in memory, printed only when asked.

The USB port is not always read. When nobody reads it, a print waits for
room that never comes, and the panel stopped answering and fell to its
watchdog (26 October: every time the voice changed phase while the port
was closed). So the panel keeps its last lines here, readable through the
brain (the ``log`` skill), and prints them too only when the flash holds a
``debug`` file — which ``make panel-deploy DEBUG=1`` leaves.
"""

KEEP = 40
DEBUG_PATH = "/debug"
_lines = []
_loud = [None]


def say(line):
    """Keep a line; print it as well when the panel is being debugged."""
    _lines.append(str(line))
    if len(_lines) > KEEP:
        del _lines[0]
    if loud():
        print(line)


def lines():
    return list(_lines)


def loud():
    if _loud[0] is None:
        try:
            import os

            os.stat(DEBUG_PATH)
            _loud[0] = True
        except OSError:
            _loud[0] = False
    return _loud[0]
