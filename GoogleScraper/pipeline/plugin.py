# -*- coding: utf-8 -*-
"""Plugin machinery for the async scraping pipeline.

Each search engine is expressed as a :class:`SearchEnginePlugin`
subclass.  A plugin fully describes how to talk to a single search
engine: the request URL, the query parameters, the request headers,
the host used for per-host concurrency accounting and the parser
class that turns a downloaded HTML page into structured results.

Plugins are discovered through the module-level
:data:`plugin_registry`.  Third parties may add their own engines
without touching the coordinator by decorating a class with
:func:`register_plugin`.
"""

from __future__ import annotations

from urllib.parse import urlencode, urlparse
from typing import Any, Dict, Iterable, Optional, Type


class SearchEnginePlugin:
    """Base class for search engine plugins.

    Subclasses must set :attr:`name`, :attr:`base_url` and
    :attr:`parser_cls`.  They may override the helper hooks
    (:meth:`build_params`, :meth:`build_headers`, :meth:`host`) to
    customise per-engine behaviour.
    """

    #: canonical short name, e.g. ``"google"``
    name: str = ''
    #: base URL used for the search request (query string is appended)
    base_url: str = ''
    #: parser class used to turn the response body into structured results
    parser_cls: Optional[Type] = None
    #: default HTTP headers sent with every request
    default_headers: Dict[str, str] = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    # ------------------------------------------------------------------ hooks
    def build_params(self, query: str, page_number: int = 1,
                     num_results_per_page: int = 10,
                     search_type: str = 'normal') -> Dict[str, Any]:
        """Return the GET parameters for a single search request.

        The default implementation defers to
        :func:`GoogleScraper.http_mode.get_GET_params_for_search_engine`
        so that the existing per-engine parameter logic keeps working.
        """
        from GoogleScraper.http_mode import get_GET_params_for_search_engine
        return get_GET_params_for_search_engine(
            query, self.name,
            page_number=page_number,
            num_results_per_page=num_results_per_page,
            search_type=search_type,
        )

    def build_headers(self) -> Dict[str, str]:
        return dict(self.default_headers)

    def build_url(self, query: str, page_number: int = 1,
                  num_results_per_page: int = 10,
                  search_type: str = 'normal') -> str:
        params = self.build_params(
            query, page_number=page_number,
            num_results_per_page=num_results_per_page,
            search_type=search_type,
        )
        sep = '&' if '?' in self.base_url else '?'
        return '{}{}{}'.format(self.base_url, sep, urlencode(params))

    def host(self) -> str:
        """Host used for the per-host concurrency semaphore."""
        parsed = urlparse(self.base_url)
        return parsed.netloc or self.name

    def parse(self, html: str) -> Any:
        """Instantiate the parser class with the fetched html."""
        if self.parser_cls is None:
            raise NotImplementedError(
                'Plugin {!r} has no parser_cls set'.format(self.name))
        return self.parser_cls(config=self.config, html=html)


class PluginRegistry:
    """A small registry keyed by search engine name.

    The registry supports both direct :meth:`register` calls and
    decorator use through the module-level :func:`register_plugin`.
    """

    def __init__(self) -> None:
        self._plugins: Dict[str, Type[SearchEnginePlugin]] = {}

    # ------------------------------------------------------------------ api
    def register(self, plugin_cls: Type[SearchEnginePlugin],
                 name: Optional[str] = None) -> Type[SearchEnginePlugin]:
        if not issubclass(plugin_cls, SearchEnginePlugin):
            raise TypeError(
                'plugin_cls must subclass SearchEnginePlugin, got {!r}'.format(
                    plugin_cls))
        key = (name or plugin_cls.name).lower()
        if not key:
            raise ValueError('Cannot register plugin without a name')
        self._plugins[key] = plugin_cls
        return plugin_cls

    def unregister(self, name: str) -> None:
        self._plugins.pop(name.lower(), None)

    def get(self, name: str) -> Type[SearchEnginePlugin]:
        try:
            return self._plugins[name.lower()]
        except KeyError:
            raise KeyError(
                'No search-engine plugin registered for {!r}'.format(name))

    def create(self, name: str,
               config: Optional[Dict[str, Any]] = None) -> SearchEnginePlugin:
        return self.get(name)(config=config)

    def names(self) -> Iterable[str]:
        return tuple(self._plugins.keys())

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)


#: Process-wide default registry.
plugin_registry = PluginRegistry()


def register_plugin(name: Optional[str] = None,
                    registry: Optional[PluginRegistry] = None):
    """Class decorator used to add a plugin to a registry.

    Usage::

        @register_plugin()
        class GooglePlugin(SearchEnginePlugin):
            name = 'google'
            ...
    """

    target = registry if registry is not None else plugin_registry

    def decorator(cls: Type[SearchEnginePlugin]) -> Type[SearchEnginePlugin]:
        target.register(cls, name=name)
        return cls

    return decorator
