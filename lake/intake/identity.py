from dataclasses import dataclass
from datetime import date

from lake import require_name


def require_day(day: str) -> str:
    require_name(day, 'day')
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('day must use YYYY-MM-DD')
    return day


@dataclass(frozen=True)
class BatchIdentity:
    station: str
    day: str
    number: str

    def __post_init__(self):
        require_name(self.station, 'station')
        require_day(self.day)
        require_name(self.number, 'batch number')

    def key(self) -> tuple[str, str, str]:
        return self.station, self.day, self.number


def make_batch_identity(station: str, day: str, number: str) -> BatchIdentity:
    return BatchIdentity(station, day, number)
