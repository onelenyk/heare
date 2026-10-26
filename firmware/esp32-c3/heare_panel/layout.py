"""Rows and columns with weights, turned into boxes on the window.

A scene's layout is a widget, or ``{"rows": [...]}`` / ``{"cols": [...]}``
whose children are layouts again, each with a weight (``h`` in rows, ``w``
in columns, 1 if not given). There are no pixel coordinates: a model counts
them badly, and the window is the board's to divide. Children are one pixel
apart; the last takes what rounding leaves, so the boxes always fill the box
they divide.

Pure: no hardware, no imports, the same on the board and on a laptop.
"""

GAP = 1
MAX_DEPTH = 3


class LayoutError(ValueError):
    """A layout that cannot be laid out, in words a model can act on."""


KINDS = ("text", "picture", "number", "icon", "bar", "clock", "spinner", "state")
SHAPE = 'кожен віджет — {"widget": "icon", "name": "sun"}; ряди — {"rows": [...]}, колонки — {"cols": [...]}'


def normal(spec):
    """A widget as a model is likely to write it, in the one shape there is.

    ``{"type": "icon", ...}`` and ``{"icon": {"name": "sun"}}`` (or nested in
    ``widget``) are what models write when they guess; they mean the same."""
    if not isinstance(spec, dict):
        return spec
    inner = spec.get("widget")
    if isinstance(inner, dict):
        merged = {k: v for k, v in spec.items() if k != "widget"}
        merged.update(inner)
        spec = merged
    if "widget" not in spec and spec.get("type") in KINDS:
        spec = dict(spec)
        spec["widget"] = spec.pop("type")
    if "widget" not in spec and "rows" not in spec and "cols" not in spec:
        named = [k for k in spec if k in KINDS]
        if len(named) == 1:
            found = {k: v for k, v in spec.items() if k != named[0]}
            if isinstance(spec[named[0]], dict):
                found.update(spec[named[0]])
            found["widget"] = named[0]
            spec = found
    return spec


def tidy(spec):
    """The whole layout in the one shape, children and all: kept, then drawn."""
    spec = normal(spec)
    if isinstance(spec, dict) and "widget" not in spec:
        for key in ("rows", "cols"):
            if isinstance(spec.get(key), list):
                spec = dict(spec)
                spec[key] = [tidy(child) for child in spec[key]]
    return spec


def boxes(spec, x, y, width, height, depth=0):
    """Every widget of the layout with its box: [((x, y, w, h), widget)]."""
    spec = normal(spec)
    if not isinstance(spec, dict):
        raise LayoutError("розкладка — це віджет або rows/cols: " + SHAPE)
    if "widget" in spec:
        return [((x, y, width, height), spec)]
    if "rows" not in spec and "cols" not in spec:
        raise LayoutError("не віджет і не ряди: " + SHAPE)
    across = "cols" in spec
    children = spec.get("cols" if across else "rows")
    if not isinstance(children, list) or not children:
        raise LayoutError("rows чи cols мають бути непорожнім списком")
    if depth >= MAX_DEPTH:
        raise LayoutError("забагато вкладених рядів: до " + str(MAX_DEPTH))
    weights = []
    for child in children:
        weight = child.get("w" if across else "h", 1) if isinstance(child, dict) else 1
        if not isinstance(weight, int) or weight < 1 or weight > 12:
            raise LayoutError("вага — ціле від 1 до 12")
        weights.append(weight)
    room = (width if across else height) - GAP * (len(children) - 1)
    if room < len(children):
        raise LayoutError("не вміщається: забагато частин на " + str(width) + "x" + str(height))
    found = []
    start = x if across else y
    total = sum(weights)
    used = 0
    for index, child in enumerate(children):
        size = room - used if index == len(children) - 1 else room * weights[index] // total
        used += size
        if across:
            found += boxes(child, start, y, size, height, depth + 1)
        else:
            found += boxes(child, x, start, width, size, depth + 1)
        start += size + GAP
    return found
