from collections.abc import Iterable
from dataclasses import dataclass
import json

from lake import DataIntegrityError, decode_integer, encode_integer
from lake.intake.identity import make_batch_identity
from lake.publication.provenance import RowOrigin, make_row_origin
from lake.quality.classify import Classification


@dataclass(frozen=True)
class FrozenRow:
    value: int | None
    classification: Classification
    origin: RowOrigin


def encode_volume_body(rows: Iterable[FrozenRow]) -> bytes:
    records = []
    for row in rows:
        value = None if row.value is None else encode_integer(row.value)
        records.append([*row.origin.batch.key(), row.origin.position,
                        value, row.classification.value])
    return json.dumps(records, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _decode_row(record) -> FrozenRow:
    if not isinstance(record, list):
        raise ValueError('row must be an array')
    station, day, number, position, encoded, label = record
    if encoded is not None and not isinstance(encoded, str):
        raise ValueError('value must be hexadecimal text or null')
    value = None if encoded is None else decode_integer(encoded)
    if value is not None and (value < 0 or encode_integer(value) != encoded):
        raise ValueError('invalid canonical liters')
    classification = Classification(label)
    if (value is None) != (classification is Classification.MISSING):
        raise ValueError('missing classification and raw value disagree')
    origin = make_row_origin(make_batch_identity(station, day, number), position)
    return FrozenRow(value, classification, origin)


def decode_volume_body(body: bytes) -> tuple[FrozenRow, ...]:
    try:
        records = json.loads(body.decode('ascii'))
        if not isinstance(records, list):
            raise ValueError('body must be an array')
        return tuple(_decode_row(record) for record in records)
    except (ValueError, TypeError) as error:
        raise DataIntegrityError('invalid frozen volume body') from error
