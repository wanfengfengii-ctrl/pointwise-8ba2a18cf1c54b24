from dataclasses import dataclass

from lake import ConflictError, decode_integer, encode_integer, require_name, transaction


@dataclass(frozen=True)
class RuleRevision:
    revision: str
    lower: int
    upper: int

    def __post_init__(self):
        require_name(self.revision, 'rule revision')
        if (type(self.lower) is not int or type(self.upper) is not int
                or not 0 <= self.lower <= self.upper):
            raise ValueError('rule bounds must be ordered nonnegative integers')


def initialize_rules(db):
    db.execute('''
        CREATE TABLE IF NOT EXISTS rules (
            revision TEXT PRIMARY KEY NOT NULL,
            lower_hex TEXT NOT NULL,
            upper_hex TEXT NOT NULL
        )
    ''')


def _find_rule(db, revision):
    row = db.execute('SELECT lower_hex, upper_hex FROM rules WHERE revision = ?',
                     (revision,)).fetchone()
    if row is None:
        return None
    lower, upper = row
    return RuleRevision(revision, decode_integer(lower), decode_integer(upper))


def register_ruleset(db, revision, lower, upper) -> RuleRevision:
    requested = RuleRevision(revision, lower, upper)
    with transaction(db, write=True):
        existing = _find_rule(db, revision)
        if existing is not None:
            if existing != requested:
                raise ConflictError('rule revision already has different bounds')
            return existing
        db.execute('INSERT INTO rules (revision, lower_hex, upper_hex) VALUES (?, ?, ?)',
                   (revision, encode_integer(lower), encode_integer(upper)))
        return requested


def get_ruleset(db, revision) -> RuleRevision:
    require_name(revision, 'rule revision')
    rule = _find_rule(db, revision)
    if rule is None:
        raise KeyError(revision)
    return rule
