# -*- coding: utf-8 -*-
"""Built-in search engine plugins.

Each plugin is registered on the module-level plugin registry when
this module is imported (which happens automatically from
``GoogleScraper.pipeline.__init__``).
"""

from __future__ import annotations

from GoogleScraper.pipeline.plugin import (
    SearchEnginePlugin,
    register_plugin,
)


def _parser_for(name: str):
    """Return the parser class for ``name`` without importing at module load.

    The parsing module has heavy dependencies; loading it lazily makes
    the pipeline package cheap to import (which in turn keeps unit
    tests fast).
    """
    from GoogleScraper.parsing import get_parser_by_search_engine
    return get_parser_by_search_engine(name)


class _LazyParserPlugin(SearchEnginePlugin):
    """Base plugin that resolves its parser lazily."""

    _parser_name: str = ''

    def __init__(self, config=None):
        super().__init__(config)
        if self.parser_cls is None and self._parser_name:
            self.parser_cls = _parser_for(self._parser_name)


@register_plugin()
class GooglePlugin(_LazyParserPlugin):
    name = 'google'
    base_url = 'https://www.google.com/search'
    _parser_name = 'google'


@register_plugin()
class BingPlugin(_LazyParserPlugin):
    name = 'bing'
    base_url = 'https://www.bing.com/search'
    _parser_name = 'bing'


@register_plugin()
class YahooPlugin(_LazyParserPlugin):
    name = 'yahoo'
    base_url = 'https://search.yahoo.com/search'
    _parser_name = 'yahoo'


@register_plugin()
class YandexPlugin(_LazyParserPlugin):
    name = 'yandex'
    base_url = 'https://yandex.com/search/'
    _parser_name = 'yandex'


@register_plugin()
class DuckDuckGoPlugin(_LazyParserPlugin):
    name = 'duckduckgo'
    base_url = 'https://duckduckgo.com/html/'
    _parser_name = 'duckduckgo'


@register_plugin()
class BaiduPlugin(_LazyParserPlugin):
    name = 'baidu'
    base_url = 'https://www.baidu.com/s'
    _parser_name = 'baidu'


@register_plugin()
class AskPlugin(_LazyParserPlugin):
    name = 'ask'
    base_url = 'https://www.ask.com/web'
    _parser_name = 'ask'
