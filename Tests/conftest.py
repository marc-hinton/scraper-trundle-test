# -*- coding: utf-8 -*-

"""
Pytest bootstrap for the Tests package.

`GoogleScraper/__init__.py` eagerly imports every scrape-mode submodule
(selenium, http, async, ...). Those submodules require optional third
party dependencies (e.g. a specific `selenium` release) that are not
necessarily installed/importable in every environment this test suite
runs in. Since the caching tests only need `GoogleScraper.caching` (and
its own, much smaller, dependency chain: database/parsing/output_converter),
we register a lightweight placeholder for the `GoogleScraper` package in
`sys.modules` *before* it is imported anywhere. This prevents Python from
executing `GoogleScraper/__init__.py` (and therefore the heavier,
optional-dependency-laden submodules) while still allowing regular
`import GoogleScraper.<submodule>` statements to work normally.

This file only affects test collection; it does not change any
production code or behaviour.
"""

import os
import sys
import types

_GOOGLESCRAPER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'GoogleScraper')

if 'GoogleScraper' not in sys.modules and os.path.isdir(_GOOGLESCRAPER_DIR):
    try:
        import GoogleScraper  # noqa: F401 - try the real thing first
    except Exception:
        placeholder = types.ModuleType('GoogleScraper')
        placeholder.__path__ = [_GOOGLESCRAPER_DIR]
        sys.modules['GoogleScraper'] = placeholder
