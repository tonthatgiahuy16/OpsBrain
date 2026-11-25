"""Drain queued Markdown ingestion jobs until the queue is empty."""

import argparse
import asyncio

from src.database import WorkerAsyncSessionLocal
from src.ingestion.ingestion_worker import (
    DEFAULT_STALE_SECONDS,
    process_next_ingestion_job,
)


async def _drain(*, stale_after_seconds: int, max_jobs: int | None) -> int:
    processed = 0
    while max_jobs is None or processed < max_jobs:
        job_id = await process_next_ingestion_job(
            WorkerAsyncSessionLocal,
            stale_after_seconds=stale_after_seconds,
        )
        if job_id is None:
            return processed
        processed += 1
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=DEFAULT_STALE_SECONDS,
        help="Reclaim running jobs older than this many seconds.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Optional cap on jobs processed in one invocation.",
    )
    args = parser.parse_args()
    if args.stale_after_seconds < 0:
        parser.error("--stale-after-seconds must be non-negative")
    if args.max_jobs is not None and args.max_jobs < 1:
        parser.error("--max-jobs must be positive")
    processed = asyncio.run(
        _drain(
            stale_after_seconds=args.stale_after_seconds,
            max_jobs=args.max_jobs,
        )
    )
    print(f"ingested_jobs={processed}")


if __name__ == "__main__":
    main()
