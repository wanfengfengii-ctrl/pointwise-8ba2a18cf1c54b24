from dataclasses import dataclass
from hashlib import sha256
import json

from lake.intake.identity import BatchIdentity


@dataclass(frozen=True)
class BatchReceipt:
    identity: BatchIdentity
    row_count: int
    code: str


def make_batch_receipt(identity: BatchIdentity, row_count: int) -> BatchReceipt:
    facts = ('batch', *identity.key(), row_count)
    payload = json.dumps(facts, separators=(',', ':')).encode('ascii')
    return BatchReceipt(identity, row_count, sha256(payload).hexdigest())
