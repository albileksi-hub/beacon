"""Drive the collector over HTTP from many connections at once.

    python loadtest.py --events 5000 --workers 32

Everything measured so far has been the database path called directly from one
thread. This is the endpoint, under concurrency, which is the only number that
describes what the service can actually take.

Reports throughput, latency percentiles, and -- the point of the exercise --
anything that failed.
"""

import argparse
import statistics
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
    " (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]
PAGES = ["/", "/pricing", "/docs", "/blog/how-we-built-it", "/contact"]

def _send(client: httpx.Client, base: str, site: str, index: int) -> tuple[int, float, str]:
    payload = {
        "site_id": site,
        "name": "pageview",
        "url": f"https://{site}{PAGES[index % len(PAGES)]}",
        "referrer": None if index % 3 else "https://news.ycombinator.com/",
        "screen_width": 1280 + (index % 5) * 160,
    }
    headers = {"user-agent": USER_AGENTS[index % len(USER_AGENTS)]}

    started = time.perf_counter()
    try:
        response = client.post(f"{base}/api/event", json=payload, headers=headers)
    except httpx.HTTPError as error:
        return 0, (time.perf_counter() - started) * 1000, type(error).__name__

    return response.status_code, (time.perf_counter() - started) * 1000, ""


def run(base: str, site: str, events: int, workers: int) -> int:
    statuses: Counter[str] = Counter()
    latencies: list[float] = []
    lock = threading.Lock()

    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    with httpx.Client(timeout=30.0, limits=limits) as client:
        # Warm the connection pool and the process caches so the first few
        # requests do not land in the percentiles as setup cost.
        for index in range(min(20, events)):
            _send(client, base, site, index)

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for status, milliseconds, error in pool.map(
                lambda index: _send(client, base, site, index), range(events)
            ):
                with lock:
                    statuses[error or str(status)] += 1
                    latencies.append(milliseconds)
        elapsed = time.perf_counter() - started

    ordered = sorted(latencies)

    def percentile(fraction: float) -> float:
        return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]

    print(f"  {events:,} events over {workers} connections in {elapsed:.2f}s")
    print(f"  throughput   {events / elapsed:,.0f} requests/sec")
    print(f"  latency      median {statistics.median(ordered):.1f}ms")
    print(f"               p95    {percentile(0.95):.1f}ms")
    print(f"               p99    {percentile(0.99):.1f}ms")
    print(f"               max    {ordered[-1]:.1f}ms")
    summary = ", ".join(f"{key}: {value:,}" for key, value in statuses.most_common())
    print(f"  responses    {summary}")

    failures = sum(count for key, count in statuses.items() if key != "202")
    if failures:
        print(f"\n  {failures:,} requests did not answer 202")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8100")
    parser.add_argument("--site", default="demo.example")
    parser.add_argument("--events", type=int, default=5000)
    parser.add_argument("--workers", type=int, default=32)
    args = parser.parse_args()

    print(f"collector    {args.base}/api/event\n")
    return run(args.base, args.site, args.events, args.workers)


if __name__ == "__main__":
    sys.exit(main())
