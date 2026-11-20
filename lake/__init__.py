from contextlib import contextmanager
import sqlite3


class ConflictError(ValueError):
    """An existing business identifier is bound to different input."""


class DataIntegrityError(RuntimeError):
    """Stored evidence cannot support the requested operation."""


def require_name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be nonempty text')
    return value


def encode_integer(value: int) -> str:
    return format(value, 'x')


def decode_integer(value: str) -> int:
    return int(value, 16)


@contextmanager
def transaction(db: sqlite3.Connection, *, write=False):
    db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


def initialize(db: sqlite3.Connection):
    from lake.custody.storage import VolumeStorage
    from lake.intake.batch_store import BatchStore
    from lake.publication.daybook_store import DaybookStore
    from lake.quality.rules import initialize_rules

    with transaction(db, write=True):
        BatchStore(db).initialize()
        initialize_rules(db)
        VolumeStorage(db).initialize()
        DaybookStore(db).initialize()


def open_lake(path) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        initialize(db)
    except BaseException:
        db.close()
        raise
    return db
