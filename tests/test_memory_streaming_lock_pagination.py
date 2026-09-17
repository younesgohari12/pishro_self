import threading
import time


def test_iter_user_ids_does_not_hold_lock_while_consuming():
    import db

    original = db._index
    db._index = {str(i): i for i in range(100)}
    try:
        iterator = db.iter_user_ids()
        next(iterator)
        acquired = []

        def acquire():
            with db._lock:
                acquired.append(True)

        t = threading.Thread(target=acquire)
        t.start()
        t.join(timeout=1)
        assert acquired
    finally:
        db._index = original


def test_large_user_pagination_is_page_bounded():
    import db

    original = db._index
    db._index = {str(i): i for i in range(5000)}
    try:
        page, total = db.get_user_ids_page(50, 20)
        assert len(page) == 20
        assert total == 5000
        assert page == sorted(page, reverse=True)
    finally:
        db._index = original


def test_broadcast_can_consume_iterable():
    import db
    users = db.iter_user_ids()
    assert not isinstance(users, list)
