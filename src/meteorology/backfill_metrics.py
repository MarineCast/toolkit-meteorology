"""Client-side stage timers; no provider URLs, tokens or error bodies are recorded."""

import time


class MeasuredProvider:
    def __init__(self, provider, resources):
        self.provider, self.resources = provider, resources
        self.budget = provider.budget if getattr(provider, "handles_http_budget", False) else None
        self.handles_http_budget = getattr(provider, "handles_http_budget", False)
        self.queued = None

    def verify_terms(self, terms):
        return self.provider.verify_terms(terms)

    def submit(self, dataset, selection):
        begin = time.perf_counter()
        try:
            result = self.provider.submit(dataset, selection)
        finally:
            self.resources.metric("submission_seconds", time.perf_counter() - begin)
        self.resources.metric("submitted_jobs", 1)
        self.queued = time.perf_counter()
        return result

    def status(self, identity):
        if self.queued is None:
            self.queued = time.perf_counter()
        result = self.provider.status(identity)
        if result == "successful":
            self.resources.metric(
                "client_observed_queue_job_seconds", time.perf_counter() - self.queued
            )
            self.queued = None
        return result

    def result_opener(self, identity):
        opener = self.provider.result_opener(identity)
        resources = self.resources

        def opened():
            begin = time.perf_counter()
            response = opener()
            resources.metric("transfer_seconds", time.perf_counter() - begin)

            class TimedResponse:
                headers = response.headers

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    response.close()

                def read(self, size):
                    begin = time.perf_counter()
                    try:
                        return response.read(size)
                    finally:
                        resources.metric("transfer_seconds", time.perf_counter() - begin)

            return TimedResponse()

        return opened
