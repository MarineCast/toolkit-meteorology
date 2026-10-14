"""Bounded executor submission with cancellation at the caller's boundary."""

from concurrent.futures import FIRST_COMPLETED, wait


def bounded_results(executor, function, values, *, max_inflight):
    if type(max_inflight) is not int or max_inflight < 1:
        raise ValueError("max_inflight must be a positive integer.")
    pending_values = iter(values)
    pending = set()
    try:
        while True:
            while len(pending) < max_inflight:
                value = next(pending_values, None)
                if value is None:
                    break
                pending.add(executor.submit(function, value))
            if not pending:
                return
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
    finally:
        for future in pending:
            future.cancel()
