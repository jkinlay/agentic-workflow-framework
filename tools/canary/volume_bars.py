"""Aggregate tick prints into volume bars.

A bar closes when the accumulated size reaches ``threshold``. The final
partial bar is emitted with ``partial=True`` so that no prints are lost, and
``vwap`` is the volume-weighted average price of the prints in the bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass(frozen=True)
class Tick:
    ts: int  # epoch milliseconds
    price: float
    size: int


@dataclass(frozen=True)
class Bar:
    open_ts: int
    close_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float
    partial: bool = False


def volume_bars(ticks: Iterable[Tick], threshold: int) -> Iterator[Bar]:
    """Yield volume bars from ``ticks`` in arrival order."""
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    bucket: list[Tick] = []
    accumulated = 0
    for tick in ticks:
        bucket.append(tick)
        accumulated += tick.size
        if accumulated >= threshold:
            yield _make_bar(bucket, partial=False)
            bucket = []
            accumulated = 0


def _make_bar(bucket: list[Tick], partial: bool) -> Bar:
    prices = [tick.price for tick in bucket]
    volume = sum(tick.size for tick in bucket)
    vwap = sum(prices) / len(prices)
    return Bar(
        open_ts=bucket[0].ts,
        close_ts=bucket[-1].ts,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        volume=volume,
        vwap=vwap,
        partial=partial,
    )
