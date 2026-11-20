from lake import ConflictError, transaction
from lake.intake.batch_store import AcceptedBatch, BatchStore
from lake.intake.identity import make_batch_identity
from lake.intake.receipts import BatchReceipt, make_batch_receipt
from lake.intake.rows import prepare_rows


def receive_batch(db, station, day, number, values) -> BatchReceipt:
    identity = make_batch_identity(station, day, number)
    rows = prepare_rows(values)
    store = BatchStore(db)
    with transaction(db, write=True):
        accepted = store.get(identity)
        if accepted is not None:
            if accepted.rows != rows:
                raise ConflictError('batch identity already has different ordered rows')
            return accepted.receipt
        receipt = make_batch_receipt(identity, len(rows))
        store.insert(AcceptedBatch(identity, rows, receipt))
        return receipt
