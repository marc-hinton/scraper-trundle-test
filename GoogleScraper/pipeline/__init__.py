# -*- coding: utf-8 -*-
"""Async plugin pipeline for GoogleScraper.

This package re-architects the core scraping engine into a pluggable
async-first pipeline composed of three independent stages:

* :class:`FetcherStage`  - performs HTTP requests, honouring per-host
  concurrency limits and back-pressure through bounded queues.
* :class:`ParserStage`   - turns fetched pages into structured SERP
  results using the search-engine plugin's parser.
* :class:`StorageStage`  - persists parsed results (cache, database,
  output file, in-memory list ...).

The stages are wired together by :class:`PipelineCoordinator`, which
schedules search-jobs, applies back-pressure and shuts down cleanly
when every job has flowed through the pipeline.

Search engines are added as self-contained plugins that subclass
:class:`SearchEnginePlugin` and are registered on the module-level
:data:`plugin_registry`.
"""

from GoogleScraper.pipeline.plugin import (
    SearchEnginePlugin,
    PluginRegistry,
    plugin_registry,
    register_plugin,
)
from GoogleScraper.pipeline.stages import (
    FetchResult,
    ParsedResult,
    FetcherStage,
    ParserStage,
    StorageStage,
    InMemoryStorageStage,
)
from GoogleScraper.pipeline.coordinator import PipelineCoordinator

# Ensure the built-in search-engine plugins are registered when the
# package is imported.
from GoogleScraper.pipeline import builtin  # noqa: F401

__all__ = [
    'SearchEnginePlugin',
    'PluginRegistry',
    'plugin_registry',
    'register_plugin',
    'FetchResult',
    'ParsedResult',
    'FetcherStage',
    'ParserStage',
    'StorageStage',
    'InMemoryStorageStage',
    'PipelineCoordinator',
]
