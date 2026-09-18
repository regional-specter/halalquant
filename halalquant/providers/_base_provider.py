"""Abstract fetcher helpers shared by vendor providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional, Sequence

import pandas as pd
import requests

from halalquant.base import BaseDataProvider, DateLike


class AbstractFetcher(BaseDataProvider, ABC):
    """HTTP-capable base for remote data vendors."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        session: Optional[requests.Session] = None,
        timeout: float = 30.0,
        min_interval: float = 0.0,
    ) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout
        self.min_interval = float(min_interval or 0.0)
        self._last_request = 0.0

    def _get_json(
        self,
        url: str,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        self._throttle()
        response = self.session.get(
            url,
            params=dict(params or {}),
            headers=dict(headers or {}),
            timeout=self.timeout,
        )
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response.json()

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        remaining = self.min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)


    @abstractmethod
    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        ...
