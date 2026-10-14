"""Small language-aware sentence splitter for realtime transcript chunks."""

from __future__ import annotations


_END_MARKS = frozenset(".!?。！？")
_CLOSING_MARKS = frozenset("\"'”’»」』】）)]}")


def split_complete_sentences(text: str) -> tuple[list[str], str]:
    """Return complete sentences and the unfinished tail.

    Commas are intentionally not treated as sentence boundaries. The caller
    applies a word-count fallback when no sentence punctuation has been emitted
    yet.
    """

    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return [], ""

    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(clean):
        mark = clean[index]
        if mark not in _END_MARKS:
            index += 1
            continue
        if (
            mark == "."
            and index > 0
            and index + 1 < len(clean)
            and clean[index - 1].isdigit()
            and clean[index + 1].isdigit()
        ):
            index += 1
            continue

        end = index + 1
        while end < len(clean) and clean[end] in _END_MARKS | _CLOSING_MARKS:
            end += 1
        next_char = clean[end] if end < len(clean) else ""
        is_japanese_boundary = mark in "。！？"
        is_ascii_boundary = mark in ".!?" and (
            not next_char or next_char.isspace()
        )
        if is_japanese_boundary or is_ascii_boundary:
            sentence = clean[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = end
        index = end

    return sentences, clean[start:].strip()
