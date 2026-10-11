"""heare, written again beside the app in ``src/``.

One rule holds the shape: ``heare.core`` imports no other part of this
package, every other part imports only ``heare.core``, and nothing here
imports ``src``. ``tests/heare/test_dependencies.py`` enforces it.
"""
