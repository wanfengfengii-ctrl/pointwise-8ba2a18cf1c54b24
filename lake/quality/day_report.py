from dataclasses import dataclass

from lake import transaction
from lake.intake.identity import BatchIdentity
from lake.quality.classify import ClassifiedRow, classify_row
from lake.quality.rules import RuleRevision, get_ruleset
from lake.quality.selection import select_day_batches
from lake.quality.station_totals import DayTotals, summarize_stations


@dataclass(frozen=True)
class ReportBatch:
    identity: BatchIdentity
    rows: tuple[ClassifiedRow, ...]


@dataclass(frozen=True)
class DayReport:
    day: str
    rule: RuleRevision
    batches: tuple[ReportBatch, ...]
    totals: DayTotals


def build_day_report(db, day, batches, revision) -> DayReport:
    """Calculate from the caller's transaction snapshot."""
    rule = get_ruleset(db, revision)
    selection = select_day_batches(db, day, batches)
    classified = tuple(
        ReportBatch(batch.identity, tuple(classify_row(row, rule) for row in batch.rows))
        for batch in selection.batches
    )
    totals = summarize_stations((batch.identity.station, row)
                                for batch in classified for row in batch.rows)
    return DayReport(selection.day, rule, classified, totals)


def preview_day(db, day, batches, revision) -> DayReport:
    with transaction(db):
        return build_day_report(db, day, batches, revision)
