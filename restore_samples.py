"""Supply fixed cold daybooks and a cold-source driver."""

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from lake import open_lake
from lake.custody.volumes import matches_volume
from lake.publication.body import decode_volume_body
from lake.reading.daybook import read_day
from tests.cold_fixture import ColdFeed, WorkSlots
from tests.support import make_cold_daybooks

TARGET = "夜班冷柜"


def prepare(db, capacity):
    alpha, beta, gamma = make_cold_daybooks(db)
    books = dict(zip(("甲", "乙", "丙"), (alpha, beta, gamma), strict=True))
    x, y = (entry.volume for entry in alpha.manifest.volumes)
    z = gamma.manifest.volumes[0].volume
    return WorkSlots(db, capacity), ColdFeed(), books, {"X": x, "Y": y, "Z": z}


def dispatch(db, feed, orders):
    for order in orders:
        feed.start(db, order.volume_id, order.target, order.session_id,
                   order.slot, fragment_size=order.fragment_size)


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "restore-fixture.sqlite3"
        with closing(open_lake(path)) as db:
            slots, feed, books, refs = prepare(db, 1)
            x, y, z = (refs[name] for name in ("X", "Y", "Z"))
            if books["乙"].manifest.volumes[0].volume != x:
                raise AssertionError("甲乙 should reference the same X")
            if read_day(db, "甲").missing_volume_ids != (x.volume_id, y.volume_id):
                raise AssertionError("甲 should need X and Y")
            feed.start(db, x.volume_id, TARGET, "fixture-x", 0, fragment_size=8)
            old = feed.events("fixture-x")
            slots.append(old[0].slot, old[0].data)

        with closing(open_lake(path)) as db:
            slots = WorkSlots(db, 1)
            if slots.read(0) != old[0].data or feed.events("fixture-x") != old:
                raise AssertionError("expected durable slot bytes and retained source events")
            slots.clear(0)
            feed.start(db, z.volume_id, TARGET, "fixture-z", 0, fragment_size=8)
            for piece in feed.events("fixture-z")[:-1]:
                slots.append(piece.slot, piece.data)
            clean = slots.read(0)
            if not matches_volume(z, clean) or [r.value for r in decode_volume_body(clean)] != [23]:
                raise AssertionError("fresh Z should contain 23 L")
            slots.append(old[1].slot, old[1].data)
            mixed = slots.read(0)
            if mixed != clean + old[1].data or matches_volume(z, mixed):
                raise AssertionError("raw slot should append bytes without session checks")
            print(f"V1 fixture: shared X, Z={len(clean)} bytes, "
                  f"raw late fragment makes {len(mixed)} bytes")


if __name__ == "__main__":
    main()
