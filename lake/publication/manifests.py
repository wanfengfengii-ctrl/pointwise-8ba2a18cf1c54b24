from collections.abc import Iterable
from dataclasses import dataclass

from lake import DataIntegrityError
from lake.custody.volumes import VolumeRef
from lake.intake.identity import BatchIdentity


@dataclass(frozen=True)
class VolumeEntry:
    batch: BatchIdentity
    volume: VolumeRef
    row_count: int
    first_position: int
    last_position: int


@dataclass(frozen=True)
class DayManifest:
    day: str
    revision: str
    batches: tuple[BatchIdentity, ...]
    volumes: tuple[VolumeEntry, ...]


def build_manifest(day: str, revision: str, batches: Iterable[BatchIdentity],
                   volumes: Iterable[VolumeEntry]) -> DayManifest:
    selected = tuple(batches)
    entries = tuple(volumes)
    ordinals = {batch: pos for pos, batch in enumerate(selected)}
    if len(ordinals) != len(selected) or any(batch.day != day for batch in selected):
        raise DataIntegrityError('invalid daybook selection')
    previous = -1
    for entry in entries:
        ordinal = ordinals.get(entry.batch, -1)
        if ordinal <= previous:
            raise DataIntegrityError('volume entries do not follow selection order')
        if (entry.row_count < 1 or entry.first_position != 1
                or entry.last_position != entry.row_count):
            raise DataIntegrityError('volume has an invalid source position span')
        previous = ordinal
    return DayManifest(day, revision, selected, entries)
