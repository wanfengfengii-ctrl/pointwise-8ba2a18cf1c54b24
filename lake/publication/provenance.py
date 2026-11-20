from collections.abc import Iterable
from dataclasses import dataclass

from lake.intake.identity import BatchIdentity


@dataclass(frozen=True)
class RowOrigin:
    batch: BatchIdentity
    position: int


def make_row_origin(batch: BatchIdentity, position: int) -> RowOrigin:
    if type(position) is not int or position < 1:
        raise ValueError('source position must be a positive integer')
    return RowOrigin(batch, position)


def matches_origin_span(origins: Iterable[RowOrigin], batch: BatchIdentity,
                        first: int, last: int) -> bool:
    items = tuple(origins)
    return (len(items) == last - first + 1
            and all(origin.batch == batch and origin.position == pos
                    for pos, origin in enumerate(items, start=first)))
