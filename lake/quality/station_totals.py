from collections.abc import Iterable
from dataclasses import dataclass

from lake.quality.classify import Classification, ClassifiedRow


@dataclass(frozen=True)
class StationTotal:
    station: str
    liters: int
    missing: int
    out_of_range: int


@dataclass(frozen=True)
class DayTotals:
    stations: tuple[StationTotal, ...]
    liters: int
    missing: int
    out_of_range: int


def summarize_stations(rows: Iterable[tuple[str, ClassifiedRow]]) -> DayTotals:
    by_station = {}
    for station, classified in rows:
        counts = by_station.setdefault(station, [0, 0, 0])
        match classified.classification:
            case Classification.INCLUDED:
                counts[0] += classified.row.value
            case Classification.MISSING:
                counts[1] += 1
            case Classification.OUT_OF_RANGE:
                counts[2] += 1
    totals = tuple(StationTotal(station, *counts) for station, counts in by_station.items())
    return DayTotals(totals, sum(station.liters for station in totals),
                     sum(station.missing for station in totals),
                     sum(station.out_of_range for station in totals))
