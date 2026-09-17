
import db


def test_user_id_batches_are_streamed():
    old = db._index.copy()
    try:
        db._index.clear()
        db._initialized = True
        db._index.update({str(i): {} for i in range(25)})
        batches = list(db.iter_user_id_batches(10))
        assert batches == [list(range(10)), list(range(10, 20)), list(range(20, 25))]
    finally:
        db._index.clear()
        db._index.update(old)


def test_stream_does_not_build_full_list():
    old = db._index.copy()
    try:
        db._index.clear()
        db._initialized = True
        db._index.update({str(i): {} for i in range(10000)})
        iterator = db.iter_user_ids()
        assert not isinstance(iterator, list)
        assert next(iterator) == 0
    finally:
        db._index.clear()
        db._index.update(old)


def test_broadcast_can_consume_iterator():
    from services.broadcast_queue import BroadcastJob

    async def sender(_uid):
        return None

    job = BroadcastJob(targets=db.iter_user_ids(), sender=sender)
    assert next(iter(job.targets)) is not None
