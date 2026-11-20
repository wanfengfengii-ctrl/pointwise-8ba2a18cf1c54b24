from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class RawRow:
    position: int
    value: int | None


def prepare_rows(values: Iterable[int | None]) -> tuple[RawRow, ...]:
    rows = []
    for position, value in enumerate(values, start=1):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'row {position} must be a nonnegative integer or null')
        rows.append(RawRow(position, value))
    return tuple(rows)
