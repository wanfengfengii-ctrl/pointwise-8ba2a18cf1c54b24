from dataclasses import dataclass
from enum import StrEnum

from lake.intake.rows import RawRow
from lake.quality.rules import RuleRevision


class Classification(StrEnum):
    INCLUDED = 'included'
    MISSING = 'missing'
    OUT_OF_RANGE = 'out_of_range'


@dataclass(frozen=True)
class ClassifiedRow:
    row: RawRow
    classification: Classification


def classify_row(row: RawRow, rule: RuleRevision) -> ClassifiedRow:
    if row.value is None:
        classification = Classification.MISSING
    elif rule.lower <= row.value <= rule.upper:
        classification = Classification.INCLUDED
    else:
        classification = Classification.OUT_OF_RANGE
    return ClassifiedRow(row, classification)
