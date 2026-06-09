"""Bible verse resolver for jwpub://b/NWTR/ links.

Resolves verse text from a local nwtsty_E.jwpub Bible file by decrypting
the BibleVerse table. Handles references spanning multiple chapters and books.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .extractor import (
    extract_sqlite_from_jwpub_to_temp,
    read_publication_identity,
    compute_full_hash_bytes,
    decrypt_and_inflate,
)

BIBLE_BOOKS: Dict[int, str] = {
    1: "Genesis", 2: "Exodus", 3: "Leviticus", 4: "Numbers", 5: "Deuteronomy",
    6: "Joshua", 7: "Judges", 8: "Ruth", 9: "1 Samuel", 10: "2 Samuel",
    11: "1 Kings", 12: "2 Kings", 13: "1 Chronicles", 14: "2 Chronicles",
    15: "Ezra", 16: "Nehemiah", 17: "Esther", 18: "Job", 19: "Psalms",
    20: "Proverbs", 21: "Ecclesiastes", 22: "Song of Solomon", 23: "Isaiah",
    24: "Jeremiah", 25: "Lamentations", 26: "Ezekiel", 27: "Daniel",
    28: "Hosea", 29: "Joel", 30: "Amos", 31: "Obadiah", 32: "Jonah",
    33: "Micah", 34: "Nahum", 35: "Habakkuk", 36: "Zephaniah", 37: "Haggai",
    38: "Zechariah", 39: "Malachi",
    40: "Matthew", 41: "Mark", 42: "Luke", 43: "John", 44: "Acts",
    45: "Romans", 46: "1 Corinthians", 47: "2 Corinthians", 48: "Galatians",
    49: "Ephesians", 50: "Philippians", 51: "Colossians", 52: "1 Thessalonians",
    53: "2 Thessalonians", 54: "1 Timothy", 55: "2 Timothy", 56: "Titus",
    57: "Philemon", 58: "Hebrews", 59: "James", 60: "1 Peter", 61: "2 Peter",
    62: "1 John", 63: "2 John", 64: "3 John", 65: "Jude", 66: "Revelation",
}

# jwpub://b/NWTR/{book_start}:{chapter_start}:{verse_start}-{book_end}:{chapter_end}:{verse_end}
_JWPUB_BIBLE_RE = re.compile(
    r"jwpub://b/NWTR/(\d+):(\d+):(\d+)-(\d+):(\d+):(\d+)"
)


@dataclass
class BibleRef:
    """A Bible reference that may span chapters or even books."""
    book_start: int
    chapter_start: int
    verse_start: int
    book_end: int
    chapter_end: int
    verse_end: int

    @property
    def book_name_start(self) -> str:
        return BIBLE_BOOKS.get(self.book_start, f"Book {self.book_start}")

    @property
    def book_name_end(self) -> str:
        return BIBLE_BOOKS.get(self.book_end, f"Book {self.book_end}")

    @property
    def is_single_verse(self) -> bool:
        return (self.book_start == self.book_end
                and self.chapter_start == self.chapter_end
                and self.verse_start == self.verse_end)

    @property
    def is_same_chapter(self) -> bool:
        return (self.book_start == self.book_end
                and self.chapter_start == self.chapter_end)

    @property
    def display(self) -> str:
        if self.is_single_verse:
            return f"{self.book_name_start} {self.chapter_start}:{self.verse_start}"
        if self.is_same_chapter:
            return f"{self.book_name_start} {self.chapter_start}:{self.verse_start}-{self.verse_end}"
        if self.book_start == self.book_end:
            return f"{self.book_name_start} {self.chapter_start}:{self.verse_start}\u2013{self.chapter_end}:{self.verse_end}"
        return (f"{self.book_name_start} {self.chapter_start}:{self.verse_start}\u2013"
                f"{self.book_name_end} {self.chapter_end}:{self.verse_end}")


def parse_jwpub_bible_link(url: str) -> Optional[BibleRef]:
    """Parse a jwpub://b/NWTR/ URL into a BibleRef."""
    m = _JWPUB_BIBLE_RE.search(url)
    if not m:
        return None
    return BibleRef(
        book_start=int(m.group(1)),
        chapter_start=int(m.group(2)),
        verse_start=int(m.group(3)),
        book_end=int(m.group(4)),
        chapter_end=int(m.group(5)),
        verse_end=int(m.group(6)),
    )


class BibleVerseResolver:
    """Resolves Bible verse text from a local nwtsty_E.jwpub file."""

    def __init__(self, jwpub_path: str):
        self._jwpub_path = jwpub_path
        self._sqlite_path: Optional[str] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._full_hash: Optional[bytes] = None
        # Cache: (book, chapter) -> {verse_num: plain_text}
        self._chapter_cache: Dict[Tuple[int, int], Dict[int, str]] = {}
        self._resolved_count = 0

    def open(self) -> None:
        """Extract and open the Bible database."""
        import os
        self._sqlite_path = extract_sqlite_from_jwpub_to_temp(self._jwpub_path)
        self._conn = sqlite3.connect(self._sqlite_path)
        identity = read_publication_identity(self._conn)
        self._full_hash = compute_full_hash_bytes(identity)

    def close(self) -> None:
        """Clean up temporary database."""
        import os
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._sqlite_path:
            try:
                os.remove(self._sqlite_path)
            except OSError:
                pass
            self._sqlite_path = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    @property
    def resolved_count(self) -> int:
        return self._resolved_count

    def get_verse_text(self, ref: BibleRef) -> Optional[str]:
        """Get the combined verse text for a reference (may span chapters/books)."""
        texts: List[str] = []

        if ref.is_same_chapter:
            # Simple case: all verses in one chapter
            chapter_verses = self._load_chapter(ref.book_start, ref.chapter_start)
            if chapter_verses is None:
                return None
            for v in range(ref.verse_start, ref.verse_end + 1):
                if v in chapter_verses:
                    texts.append(chapter_verses[v])
        else:
            # Complex case: spanning multiple chapters or books
            texts = self._get_spanning_verses(ref)

        if texts:
            self._resolved_count += 1
            return " ".join(texts)
        return None

    def _get_spanning_verses(self, ref: BibleRef) -> List[str]:
        """Get verses that span across chapters or books."""
        texts: List[str] = []
        cur = self._conn.cursor()

        # Build list of (book, chapter) pairs to iterate
        chapters_to_process: List[Tuple[int, int]] = []

        for book in range(ref.book_start, ref.book_end + 1):
            # Determine chapter range for this book
            cur.execute(
                "SELECT MIN(ChapterNumber), MAX(ChapterNumber) FROM BibleChapter WHERE BookNumber = ?",
                (book,)
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                continue
            book_first_ch, book_last_ch = row

            if book == ref.book_start and book == ref.book_end:
                ch_start = ref.chapter_start
                ch_end = ref.chapter_end
            elif book == ref.book_start:
                ch_start = ref.chapter_start
                ch_end = book_last_ch
            elif book == ref.book_end:
                ch_start = book_first_ch
                ch_end = ref.chapter_end
            else:
                ch_start = book_first_ch
                ch_end = book_last_ch

            for ch in range(ch_start, ch_end + 1):
                chapters_to_process.append((book, ch))

        # Extract verses from each chapter
        for book, ch in chapters_to_process:
            chapter_verses = self._load_chapter(book, ch)
            if chapter_verses is None:
                continue

            # Determine verse range for this chapter
            if book == ref.book_start and ch == ref.chapter_start:
                v_start = ref.verse_start
            else:
                v_start = min(chapter_verses.keys()) if chapter_verses else 1

            if book == ref.book_end and ch == ref.chapter_end:
                v_end = ref.verse_end
            else:
                v_end = max(chapter_verses.keys()) if chapter_verses else 999

            for v in sorted(chapter_verses.keys()):
                if v_start <= v <= v_end:
                    texts.append(chapter_verses[v])

        return texts

    def _load_chapter(self, book: int, chapter: int) -> Optional[Dict[int, str]]:
        """Load and cache all verse texts for a chapter."""
        key = (book, chapter)
        if key in self._chapter_cache:
            return self._chapter_cache[key]

        if not self._conn or not self._full_hash:
            return None

        cur = self._conn.cursor()
        cur.execute(
            "SELECT FirstVerseId, LastVerseId FROM BibleChapter WHERE BookNumber = ? AND ChapterNumber = ?",
            (book, chapter)
        )
        row = cur.fetchone()
        if not row:
            return None

        first_id, last_id = row[0], row[1]

        # Fetch all verses in this chapter range
        cur.execute(
            "SELECT BibleVerseId, Label, Content FROM BibleVerse WHERE BibleVerseId BETWEEN ? AND ?",
            (first_id, last_id)
        )

        verses: Dict[int, str] = {}
        for verse_id, label, content_blob in cur.fetchall():
            if content_blob is None:
                continue

            # Parse verse number from label
            verse_num = self._parse_verse_number(label)
            if verse_num is None:
                continue

            # Decrypt the verse content
            blob = bytes(content_blob) if isinstance(content_blob, memoryview) else content_blob
            if not isinstance(blob, bytes):
                continue

            html = decrypt_and_inflate(blob, self._full_hash)
            if html is None:
                continue

            # Strip HTML to get plain text
            plain = re.sub(r"<[^>]+>", "", html)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                verses[verse_num] = plain

        self._chapter_cache[key] = verses
        return verses

    @staticmethod
    def _parse_verse_number(label: Optional[str]) -> Optional[int]:
        """Extract verse number from a BibleVerse Label field.

        Labels use:
        - <span class="vl">N</span> for regular verses
        - <span class="cl">N</span> for chapter labels (superscription = verse 1 in Psalms)
        - Empty/None for non-verse entries
        """
        if not label:
            return None
        # Check for verse label (most common)
        m = re.search(r'class="vl">(\d+)<', label)
        if m:
            return int(m.group(1))
        # Chapter labels count as verse 1 (superscriptions in Psalms)
        m = re.search(r'class="cl">(\d+)<', label)
        if m:
            return 1
        return None


def resolve_bible_links_in_markdown(markdown: str, resolver: BibleVerseResolver) -> str:
    """Replace [text](jwpub://b/NWTR/...) links with resolved verse text.

    Transforms: [Jer 31:3;](jwpub://b/NWTR/24:31:3-24:31:3)
    Into:       **Jer 31:3** — "From far away Jehovah appeared..."
    """
    pattern = re.compile(
        r"\[([^\]]*?)\]\(jwpub://b/NWTR/(\d+):(\d+):(\d+)-(\d+):(\d+):(\d+)\)"
    )

    def replace_link(match: re.Match) -> str:
        link_text = match.group(1)
        ref = BibleRef(
            book_start=int(match.group(2)),
            chapter_start=int(match.group(3)),
            verse_start=int(match.group(4)),
            book_end=int(match.group(5)),
            chapter_end=int(match.group(6)),
            verse_end=int(match.group(7)),
        )
        verse_text = resolver.get_verse_text(ref)
        if verse_text is None:
            # Couldn't resolve; keep as plain text
            return link_text
        # Clean up the link text (remove trailing semicolons/commas, markdown formatting)
        clean_text = re.sub(r"[;,]\s*$", "", link_text).strip()
        clean_text = clean_text.replace("*", "")
        return f'**{clean_text}** \u2014 \u201c{verse_text}\u201d'

    return pattern.sub(replace_link, markdown)
