from dataclasses import dataclass
from hashlib import sha256
import re

from src.ingestion.canonicalization import normalize_markdown_body


CHUNKING_VERSION = "chunk-v1"


_HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE_PATTERN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_LIST_ITEM_PATTERN = re.compile(
    r"^[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]+"
)

@dataclass(frozen=True)
class DeterministicChunk:
    chunk_key: str
    chunk_index: int
    chunking_version: str
    heading_path: tuple[str, ...]
    content: str
    content_hash: str
    start_offset: int
    end_offset: int
    token_count: int | None = None


def chunk_markdown_body(
    body: str,
    *,
    chunking_version: str,
    max_characters: int,
) -> list[DeterministicChunk]:
    if not chunking_version.strip():
        raise ValueError("chunking_version must not be blank")
    if max_characters < 1:
        raise ValueError("max_characters must be positive")

    normalized_body = normalize_markdown_body(body)
    chunks: list[DeterministicChunk] = []
    heading_path: tuple[str, ...] = ()
    section_start = 0
    offset = 0
    fence_marker: str | None = None

    for line in normalized_body.splitlines(keepends=True):
        line_end = offset + len(line)

        if fence_marker is not None:
            if _closes_fence(line, fence_marker):
                fence_marker = None
        else:
            opening_marker = _opening_fence_marker(line)

            if opening_marker is not None:
                fence_marker = opening_marker
            else:
                match = _HEADING_PATTERN.match(line.rstrip("\n"))

                if match is not None:
                    _append_section_chunks(
                        chunks,
                        normalized_body,
                        section_start,
                        offset,
                        heading_path,
                        chunking_version,
                        max_characters,
                    )

                    level = len(match.group(1))
                    heading = match.group(2).strip()
                    heading_path = heading_path[: level - 1] + (heading,)
                    section_start = line_end

        offset = line_end

    _append_section_chunks(
        chunks,
        normalized_body,
        section_start,
        len(normalized_body),
        heading_path,
        chunking_version,
        max_characters,
    )

    return chunks


def _append_section_chunks(
    chunks: list[DeterministicChunk],
    normalized_body: str,
    start_offset: int,
    end_offset: int,
    heading_path: tuple[str, ...],
    chunking_version: str,
    max_characters: int,
) -> None:
    block_spans = _block_spans(
        normalized_body,
        start_offset,
        end_offset,
    )
    if not block_spans:
        return

    chunk_start, chunk_end = block_spans[0]

    for block_start, block_end in block_spans[1:]:
        if block_end - chunk_start <= max_characters:
            chunk_end = block_end
            continue

        _append_chunk(
            chunks,
            normalized_body,
            chunk_start,
            chunk_end,
            heading_path,
            chunking_version,
        )
        chunk_start, chunk_end = block_start, block_end

    _append_chunk(
        chunks,
        normalized_body,
        chunk_start,
        chunk_end,
        heading_path,
        chunking_version,
    )


def _block_spans(
    normalized_body: str,
    start_offset: int,
    end_offset: int,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    list_start: int | None = None
    list_end: int | None = None
    fence_start: int | None = None
    fence_marker: str | None = None
    offset = start_offset

    for line in normalized_body[start_offset:end_offset].splitlines(
        keepends=True
    ):
        line_end = offset + len(line)

        if fence_marker is not None:
            if _closes_fence(line, fence_marker):
                _append_trimmed_span(
                    spans,
                    normalized_body,
                    fence_start,
                    line_end,
                )
                fence_start = None
                fence_marker = None
        else:
            opening_marker = _opening_fence_marker(line)

            if opening_marker is not None:
                _append_trimmed_span(
                    spans,
                    normalized_body,
                    paragraph_start,
                    paragraph_end,
                )
                _append_trimmed_span(
                    spans,
                    normalized_body,
                    list_start,
                    list_end,
                )
                paragraph_start = None
                paragraph_end = None
                list_start = None
                list_end = None
                fence_start = offset
                fence_marker = opening_marker
            elif _is_list_item(line):
                _append_trimmed_span(
                    spans,
                    normalized_body,
                    paragraph_start,
                    paragraph_end,
                )
                paragraph_start = None
                paragraph_end = None

                if list_start is None:
                    list_start = offset
                list_end = line_end
            elif list_start is not None:
                if line.strip() == "":
                    pass
                elif line.startswith((" ", "\t")):
                    list_end = line_end
                else:
                    _append_trimmed_span(
                        spans,
                        normalized_body,
                        list_start,
                        list_end,
                    )
                    list_start = None
                    list_end = None
                    paragraph_start = offset
                    paragraph_end = line_end
            elif line.strip() == "":
                _append_trimmed_span(
                    spans,
                    normalized_body,
                    paragraph_start,
                    paragraph_end,
                )
                paragraph_start = None
                paragraph_end = None
            else:
                if paragraph_start is None:
                    paragraph_start = offset
                paragraph_end = line_end

        offset = line_end

    if fence_start is not None:
        _append_trimmed_span(
            spans,
            normalized_body,
            fence_start,
            end_offset,
        )
    else:
        _append_trimmed_span(
            spans,
            normalized_body,
            list_start,
            list_end,
        )
        _append_trimmed_span(
            spans,
            normalized_body,
            paragraph_start,
            paragraph_end,
        )

    return spans


def _append_trimmed_span(
    spans: list[tuple[int, int]],
    normalized_body: str,
    start_offset: int | None,
    end_offset: int | None,
) -> None:
    if start_offset is None or end_offset is None:
        return

    start_offset, end_offset = _trim_span(
        normalized_body,
        start_offset,
        end_offset,
    )
    if start_offset < end_offset:
        spans.append((start_offset, end_offset))


def _trim_span(
    normalized_body: str,
    start_offset: int,
    end_offset: int,
) -> tuple[int, int]:
    while (
        start_offset < end_offset
        and normalized_body[start_offset].isspace()
    ):
        start_offset += 1

    while (
        end_offset > start_offset
        and normalized_body[end_offset - 1].isspace()
    ):
        end_offset -= 1

    return start_offset, end_offset


def _opening_fence_marker(line: str) -> str | None:
    match = _FENCE_PATTERN.match(line)
    if match is None:
        return None

    return match.group(1)[0]


def _closes_fence(line: str, marker: str) -> bool:
    return re.match(
        rf"^[ \t]*{re.escape(marker)}{{3,}}",
        line,
    ) is not None


def _is_list_item(line: str) -> bool:
    return _LIST_ITEM_PATTERN.match(line) is not None


def _append_chunk(
    chunks: list[DeterministicChunk],
    normalized_body: str,
    start_offset: int,
    end_offset: int,
    heading_path: tuple[str, ...],
    chunking_version: str,
) -> None:
    content = normalized_body[start_offset:end_offset]
    chunk_index = len(chunks)

    chunks.append(
        DeterministicChunk(
            chunk_key=f"chk_{chunk_index:04d}",
            chunk_index=chunk_index,
            chunking_version=chunking_version,
            heading_path=heading_path,
            content=content,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            start_offset=start_offset,
            end_offset=end_offset,
        )
    )
