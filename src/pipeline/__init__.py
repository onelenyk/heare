"""Conversation state shared by the engine and the tool layer.

What is left here after the pipecat engine was removed: the two objects
that were never about any engine. ``SessionState`` carries the active
mode and its tool policy; ``LanguageState`` carries the language the
current turn is being conducted in. The agent tools, the store and the
spine all read them.

The package keeps its name for now because thirty import sites do; the
files it used to hold — the graph, the stages, the transports — are gone.
"""
