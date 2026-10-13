from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

from meteorology.concurrency import bounded_results


def test_large_input_is_not_submitted_up_front():
    consumed = []
    submitted = Event()
    release = Event()
    results = []

    def values():
        for number in range(1000):
            consumed.append(number)
            if len(consumed) == 4:
                submitted.set()
            yield number

    def work(number):
        assert release.wait(10)
        return number

    with ThreadPoolExecutor(max_workers=2) as executor:
        stream = bounded_results(executor, work, values(), max_inflight=4)
        thread = Thread(target=lambda: results.append(next(stream)))
        thread.start()
        try:
            assert submitted.wait(10)
            assert consumed == [0, 1, 2, 3]
        finally:
            release.set()
            thread.join(10)
        assert not thread.is_alive() and len(results) == 1
        stream.close()
