"""Abstract fetcher helpers shared by vendor providers."""

from __future__ import annotations

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
    ) -> None:
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get_json(
        self,
        url: str,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        response = self.session.get(
            url,
            params=dict(params or {}),
            headers=dict(headers or {}),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

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
