"""Drain local chunk work, then embedding work, until both queues are empty."""

import asyncio

from src.database import AsyncSessionLocal
from src.ingestion.reindex_worker import (
    process_next_chunk_reindex_item,
    process_next_embedding_reindex_item,
)


async def _drain(process) -> int:
    completed = 0
    while True:
        async with AsyncSessionLocal() as session:
            item_id = await process(session)
        if item_id is None:
            return completed
        completed += 1


async def main() -> None:
    chunks = await _drain(process_next_chunk_reindex_item)
    embeddings = await _drain(process_next_embedding_reindex_item)
    print(f"chunks={chunks} embeddings={embeddings}")


if __name__ == "__main__":
    asyncio.run(main())
