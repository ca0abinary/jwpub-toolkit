"""JWPUB file extractor and decryptor.

Adapted from jwpub_extractor.py.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple, Any

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from . import crypto

ContentKey = Tuple[str, int]  # (table, row_id)


@dataclass
class PublicationIdentity:
    meps_language_index: int
    symbol: str
    year: int
    issue_tag_number: int = 0


def _find_sqlite_member_in_zipfile(zf: zipfile.ZipFile) -> Optional[str]:
    magic = b"SQLite format 3\x00"
    for info in zf.infolist():
        if info.is_dir():
            continue
        try:
            with zf.open(info, "r") as f:
                head = f.read(len(magic))
            if head.startswith(magic):
                return info.filename
        except Exception:
            continue
    common_names = {
        "publication.sqlite", "index.sqlite", "database.sqlite",
        "pub.sqlite", "publication.db", "index.db", "database.db",
    }
    for name in zf.namelist():
        lower = name.lower()
        if any(lower.endswith("/" + n) or lower.endswith(n) for n in common_names):
            return name
    return None


def _read_manifest(jwpub_path: str) -> Dict[str, Any]:
    with zipfile.ZipFile(jwpub_path) as zf:
        with zf.open("manifest.json", "r") as mf:
            return json.load(io.TextIOWrapper(mf, encoding="utf-8"))


def extract_sqlite_from_jwpub_to_temp(jwpub_path: str) -> str:
    with zipfile.ZipFile(jwpub_path) as outer:
        member = _find_sqlite_member_in_zipfile(outer)
        if member:
            raw = outer.read(member)
            tmp = tempfile.NamedTemporaryFile(prefix="jwpub_", suffix=".sqlite", delete=False)
            tmp.write(raw)
            tmp.flush(); tmp.close()
            return tmp.name

        names = set(outer.namelist())
        if "contents" not in names:
            raise FileNotFoundError("SQLite database not found in jwpub")
        content_bytes = outer.read("contents")
        if not content_bytes.startswith(b"PK\x03\x04"):
            raise FileNotFoundError("File 'contents' is not a ZIP archive as expected")

    inner_io = io.BytesIO(content_bytes)
    with zipfile.ZipFile(inner_io) as inner:
        try:
            manifest = _read_manifest(jwpub_path)
            pub = manifest.get("publication", {}) if isinstance(manifest, dict) else {}
            db_name = pub.get("fileName")
            if db_name and db_name in inner.namelist():
                raw = inner.read(db_name)
                if raw.startswith(b"SQLite format 3\x00"):
                    tmp = tempfile.NamedTemporaryFile(prefix="jwpub_", suffix=".sqlite", delete=False)
                    tmp.write(raw)
                    tmp.flush(); tmp.close()
                    return tmp.name
        except Exception:
            pass

        member = _find_sqlite_member_in_zipfile(inner)
        if not member:
            raise FileNotFoundError("SQLite database not found in jwpub (also not in nested 'contents')")
        raw = inner.read(member)
        tmp = tempfile.NamedTemporaryFile(prefix="jwpub_", suffix=".sqlite", delete=False)
        tmp.write(raw)
        tmp.flush(); tmp.close()
        return tmp.name


def read_publication_identity(conn: sqlite3.Connection) -> PublicationIdentity:
    cur = conn.cursor()
    cur.execute(
        "SELECT MepsLanguageIndex, Symbol, Year, COALESCE(IssueTagNumber, 0) FROM Publication LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Publication table missing or empty")
    return PublicationIdentity(
        meps_language_index=int(row[0]),
        symbol=str(row[1]),
        year=int(row[2]),
        issue_tag_number=int(row[3] or 0),
    )


def compute_full_hash_bytes(identity: PublicationIdentity) -> bytes:
    pub_hash_hex = crypto.compute_publication_card_hash(
        identity.meps_language_index,
        identity.symbol,
        identity.year,
        identity.issue_tag_number,
    )
    return crypto.hex_to_bytes(pub_hash_hex)


def discover_content_tables(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    results: List[Tuple[str, str]] = []
    for name in tables:
        cur.execute(f"PRAGMA table_info({name})")
        cols = [r[1] for r in cur.fetchall()]
        if "Content" in cols:
            results.append((name, "Content"))
    priority = {"Document": 0, "BibleChapter": 1, "BibleVerse": 2}
    results.sort(key=lambda tc: priority.get(tc[0], 100))
    return results


def iter_encrypted_rows(conn: sqlite3.Connection, table: str, column: str) -> Iterable[Tuple[int, bytes]]:
    cur = conn.cursor()
    possible_id_cols = ["DocumentId", "Id", "RowId", "_rowid_", "ROWID"]
    id_col = None
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    for c in possible_id_cols:
        if c in cols:
            id_col = c
            break
    if id_col is None:
        id_col = cols[0] if cols else "rowid"

    cur.execute(f"SELECT {id_col}, {column} FROM {table}")
    for row in cur.fetchall():
        row_id = int(row[0])
        blob = row[1]
        if blob is None:
            continue
        if isinstance(blob, memoryview):
            blob = bytes(blob)
        elif isinstance(blob, str):
            blob = blob.encode("utf-8")
        if not isinstance(blob, (bytes, bytearray)):
            continue
        yield row_id, bytes(blob)


def decrypt_and_inflate(content_blob: bytes, full_hash: bytes) -> Optional[str]:
    if len(full_hash) != 32:
        raise ValueError("Invalid publication hash (expected 32 bytes)")
    key = full_hash[:16]
    iv = full_hash[16:32]
    try:
        decrypted = crypto.aes128_cbc_decrypt(key, iv, content_blob)
        inflated = crypto.zlib_inflate(decrypted)
        return inflated.decode("utf-8", errors="replace")
    except Exception:
        return None


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "item"


def _write_html_document(output_path: str, title: str, body_html: str) -> None:
    effective_title = title
    m_header = re.search(r"<header[^>]*>([\s\S]*?)</header>", body_html, flags=re.IGNORECASE)
    if m_header:
        raw = m_header.group(1)
        raw = re.sub(r"<\s*(script|style)[^>]*>[\s\S]*?<\s*/\s*\1\s*>", "", raw, flags=re.IGNORECASE)
        header_text = re.sub(r"<[^>]+>", "", raw)
        header_text = header_text.replace("&nbsp;", " ")
        header_text = re.sub(r"\s+", " ", header_text).strip()
        if header_text:
            effective_title = header_text
    else:
        m_h1 = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", body_html, flags=re.IGNORECASE)
        if m_h1:
            h1_inner = m_h1.group(1)
            h1_text = re.sub(r"<[^>]+>", "", h1_inner)
            h1_text = re.sub(r"\s+", " ", h1_text).strip()
            if h1_text:
                effective_title = h1_text

    html = (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\"/>\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n"
        f"  <title>{effective_title}</title>\n"
        "  <style>body{margin:1.25rem;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Inter,Arial,sans-serif;max-width:900px}pre,code{white-space:pre-wrap;word-break:break-word}.s5{display:block;margin-left:2em}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body_html}\n"
        "</body>\n"
        "</html>\n"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


def read_manifest_name(jwpub_path: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(jwpub_path) as zf:
            with zf.open('manifest.json') as f:
                data = json.load(f)
                return data.get('name')
    except Exception:
        return None


def _ensure_utf8_meta(html_text: str) -> str:
    lowered = html_text.lower()
    if '<html' in lowered:
        if 'charset=' not in lowered:
            head_match = re.search(r'<head[^>]*>', html_text, flags=re.IGNORECASE)
            if head_match:
                pos = head_match.end()
                return html_text[:pos] + '\n<meta charset="utf-8">' + html_text[pos:]
            else:
                return re.sub(r'<html([^>]*)>', r'<html\1>\n<head><meta charset="utf-8"></head>', html_text, count=1, flags=re.IGNORECASE)
        return html_text
    return f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"></head><body>\n{html_text}\n</body></html>'


def extract_publication_assets(jwpub_path: str, dest_assets_dir: str) -> None:
    os.makedirs(dest_assets_dir, exist_ok=True)
    try:
        with zipfile.ZipFile(jwpub_path) as outer:
            sqlite_member = _find_sqlite_member_in_zipfile(outer)
            for info in outer.infolist():
                if info.is_dir():
                    continue
                if info.filename == (sqlite_member or '') or info.filename == 'contents':
                    continue
                out_path = os.path.join(dest_assets_dir, os.path.basename(info.filename))
                try:
                    with outer.open(info) as src, open(out_path, 'wb') as dst:
                        dst.write(src.read())
                except Exception:
                    pass
            if 'contents' in outer.namelist():
                content_bytes = outer.read('contents')
                if content_bytes.startswith(b'PK\x03\x04'):
                    with zipfile.ZipFile(io.BytesIO(content_bytes)) as inner:
                        inner_sqlite = _find_sqlite_member_in_zipfile(inner)
                        for info in inner.infolist():
                            if info.is_dir():
                                continue
                            if info.filename == (inner_sqlite or ''):
                                continue
                            out_path = os.path.join(dest_assets_dir, os.path.basename(info.filename))
                            try:
                                with inner.open(info) as src, open(out_path, 'wb') as dst:
                                    dst.write(src.read())
                            except Exception:
                                pass
    except Exception:
        pass


def extract_publication_to_dir(jwpub_path: str, output_root: str, documents_only: bool = False) -> Tuple[str, Dict[ContentKey, str]]:
    """Extract publication to output_root/<pub_id>/, saving raw decrypted HTML and assets.
    Returns (pub_id, mapping ContentKey->decrypted html).
    """
    sqlite_path = extract_sqlite_from_jwpub_to_temp(jwpub_path)
    contents: Dict[ContentKey, str] = {}
    pub_id = ''
    try:
        conn = sqlite3.connect(sqlite_path)
        identity = read_publication_identity(conn)
        full_hash = compute_full_hash_bytes(identity)
        pub_id = f"{identity.meps_language_index}_{identity.symbol}_{identity.year}" + (
            f"_{identity.issue_tag_number}" if identity.issue_tag_number != 0 else ''
        )
        target_dir = os.path.join(output_root, pub_id)
        os.makedirs(target_dir, exist_ok=True)
        for table, column in discover_content_tables(conn):
            if table == 'Extract':
                continue
            if documents_only and table != 'Document':
                continue
            for row_id, blob in iter_encrypted_rows(conn, table, column):
                html_text = decrypt_and_inflate(blob, full_hash)
                if html_text is None:
                    continue
                contents[(table, row_id)] = html_text
                fname = f"{table}_{row_id}.html"
                out_path = os.path.join(target_dir, fname)
                with open(out_path, 'w', encoding='utf-8') as f:
                    f.write(_ensure_utf8_meta(html_text))
        extract_publication_assets(jwpub_path, os.path.join(target_dir, 'assets'))
    finally:
        try:
            os.remove(sqlite_path)
        except OSError:
            pass
    return pub_id, contents


def process_jwpub(jwpub_path: str, output_dir: str) -> int:
    sqlite_path = extract_sqlite_from_jwpub_to_temp(jwpub_path)
    try:
        conn = sqlite3.connect(sqlite_path)
        identity = read_publication_identity(conn)
        full_hash = compute_full_hash_bytes(identity)
        os.makedirs(output_dir, exist_ok=True)
        index_entries: List[Tuple[str, str]] = []

        for table, column in discover_content_tables(conn):
            for row_id, blob in iter_encrypted_rows(conn, table, column):
                text = decrypt_and_inflate(blob, full_hash)
                if text is not None:
                    title = f"{table}:{row_id}"
                    filename = _sanitize_filename(f"{table}_{row_id}.html")
                    out_path = os.path.join(output_dir, filename)
                    _write_html_document(out_path, title, text)
                    index_entries.append((filename, title))

        links = "\n".join(
            f'<li><a href="{fn}">{title}</a></li>' for fn, title in index_entries
        )
        index_html = (
            "<header><h1>JWPub extraction</h1></header>\n"
            "<main>\n"
            f"<p>Total documents: {len(index_entries)}</p>\n"
            "<ol>\n" + links + "\n</ol>\n"
            "</main>\n"
        )
        _write_html_document(os.path.join(output_dir, "index.html"), "Extracted index", index_html)
        return 0
    finally:
        try:
            os.remove(sqlite_path)
        except OSError:
            pass


def _decrypt_extracts(conn: sqlite3.Connection, full_hash: bytes) -> Dict[int, str]:
    """Decrypt all Extract rows and return {ExtractId: html_content}."""
    extracts: Dict[int, str] = {}
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Extract'")
    if not cur.fetchone():
        return extracts
    for row_id, blob in iter_encrypted_rows(conn, "Extract", "Content"):
        html_text = decrypt_and_inflate(blob, full_hash)
        if html_text is not None:
            extracts[row_id] = html_text
    return extracts


def _get_extract_caption(conn: sqlite3.Connection, extract_id: int) -> str:
    """Get the caption text for an extract."""
    cur = conn.cursor()
    cur.execute("SELECT Caption FROM Extract WHERE ExtractId = ?", (extract_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        return f"Extract {extract_id}"
    caption_html = row[0]
    text = re.sub(r"<[^>]+>", "", caption_html)
    return re.sub(r"\s+", " ", text).strip() or f"Extract {extract_id}"


def _get_document_extract_ids(conn: sqlite3.Connection, doc_id: int) -> List[int]:
    """Get all ExtractIds linked to a document, in order."""
    cur = conn.cursor()
    cur.execute(
        "SELECT ExtractId FROM DocumentExtract WHERE DocumentId = ? ORDER BY DocumentExtractId",
        (doc_id,)
    )
    return [row[0] for row in cur.fetchall()]


def _html_to_markdown(html_text: str) -> str:
    """Convert HTML fragment to Markdown."""
    return md(html_text, heading_style="ATX", strip=['style', 'script']).strip()


def _extract_html_to_markdown(html_text: str) -> str:
    """Convert extract HTML to markdown, stripping wrapper divs."""
    soup = BeautifulSoup(html_text, "html.parser")
    body = soup.find("body")
    if body:
        content = body.decode_contents()
    else:
        content = str(soup)
    return _html_to_markdown(content)


def _embed_extracts_in_html(doc_html: str, extract_map: Dict[int, str], conn: sqlite3.Connection) -> str:
    """Find all <a class="xt" data-xtid="N"> in document HTML and insert
    placeholder markers after the containing paragraph for later replacement."""
    soup = BeautifulSoup(doc_html, "html.parser")
    xt_links = soup.find_all("a", class_="xt", attrs={"data-xtid": True})

    # Group extracts by parent paragraph (preserve order, deduplicate)
    from collections import OrderedDict
    para_extracts: OrderedDict = OrderedDict()
    seen_xtids: set = set()
    for link in xt_links:
        xtid = int(link["data-xtid"])
        if xtid in seen_xtids:
            continue
        if xtid not in extract_map:
            continue
        seen_xtids.add(xtid)
        parent = link.find_parent(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div"])
        if parent:
            para_id = id(parent)
            if para_id not in para_extracts:
                para_extracts[para_id] = (parent, [])
            para_extracts[para_id][1].append(xtid)

    # Insert placeholder markers after each paragraph that references extracts
    for para_id, (parent_el, xtids) in reversed(list(para_extracts.items())):
        for xtid in reversed(xtids):
            marker = soup.new_tag("div")
            marker["data-extract-placeholder"] = str(xtid)
            marker.string = f"EXTRACT_PLACEHOLDER_{xtid}"
            parent_el.insert_after(marker)

    return str(soup)


def _document_html_to_markdown(doc_html: str, extract_map: Dict[int, str], conn: sqlite3.Connection) -> str:
    """Convert a document HTML with embedded extracts to Markdown."""
    # First insert extract placeholders
    enriched_html = _embed_extracts_in_html(doc_html, extract_map, conn)

    soup = BeautifulSoup(enriched_html, "html.parser")

    # Extract a title from header or h1
    title = ""
    header = soup.find("header")
    if header:
        h1 = header.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        header.decompose()
    else:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # Convert the main body
    body = soup.find("body")
    content_html = body.decode_contents() if body else str(soup)

    # Convert to markdown
    markdown = _html_to_markdown(content_html)

    # Replace placeholders with collapsible extract blocks
    # markdownify escapes underscores, so handle both escaped and unescaped forms
    def replace_placeholder(match: re.Match) -> str:
        xtid = int(match.group(1))
        if xtid not in extract_map:
            return ""
        caption = _get_extract_caption(conn, xtid)
        extract_md = _extract_html_to_markdown(extract_map[xtid])
        return (
            f"\n<details>\n<summary>{caption}</summary>\n\n"
            f"{extract_md}\n\n"
            f"</details>\n"
        )

    markdown = re.sub(r"EXTRACT\\?_PLACEHOLDER\\?_(\d+)", replace_placeholder, markdown)

    # Clean up excessive blank lines
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)

    # Add title as h1 at top
    if title:
        markdown = f"# {title}\n\n{markdown}"

    return markdown.strip() + "\n"


def _strip_unresolved_links(markdown: str) -> str:
    """Convert unresolved internal links (jwpub://, jwpub-media://) to plain text."""
    # [link text](jwpub://...) or [link text](jwpub-media://...) → link text
    return re.sub(r"\[([^\]]*)\]\(jwpub(?:-media)?://[^)]*\)", r"\1", markdown)


def process_jwpub_markdown(jwpub_path: str, output_dir: str, bible_jwpub_path: Optional[str] = None, no_extracts: bool = False) -> int:
    """Extract publication as Markdown files with extracts embedded as collapsible regions.

    Args:
        jwpub_path: Path to the publication .jwpub file.
        output_dir: Output directory for markdown files.
        bible_jwpub_path: Optional path to nwtsty_E.jwpub for resolving Bible verse links.
        no_extracts: If True, exclude extracts from output for cleaner documents.
    """
    sqlite_path = extract_sqlite_from_jwpub_to_temp(jwpub_path)
    try:
        conn = sqlite3.connect(sqlite_path)
        identity = read_publication_identity(conn)
        full_hash = compute_full_hash_bytes(identity)
        os.makedirs(output_dir, exist_ok=True)

        # Set up verse resolver if Bible jwpub provided
        resolver = None
        if bible_jwpub_path:
            from .bible import BibleVerseResolver, resolve_bible_links_in_markdown
            resolver = BibleVerseResolver(bible_jwpub_path)
            resolver.open()

        # Decrypt all extracts
        extract_map = _decrypt_extracts(conn, full_hash) if not no_extracts else {}

        # Get document titles
        cur = conn.cursor()
        cur.execute("SELECT DocumentId, Title FROM Document ORDER BY DocumentId")
        doc_titles = {row[0]: row[1] for row in cur.fetchall()}

        # Process documents
        index_entries: List[Tuple[str, str]] = []
        for table, column in discover_content_tables(conn):
            if table == "Extract":
                continue
            for row_id, blob in iter_encrypted_rows(conn, table, column):
                text = decrypt_and_inflate(blob, full_hash)
                if text is None:
                    continue

                if table == "Document":
                    # Embed extracts for this document
                    doc_extract_ids = _get_document_extract_ids(conn, row_id)
                    doc_extracts = {eid: extract_map[eid] for eid in doc_extract_ids if eid in extract_map}
                    markdown = _document_html_to_markdown(text, doc_extracts, conn)
                else:
                    markdown = _html_to_markdown(text)

                # Resolve Bible verse links if enabled
                if resolver is not None:
                    markdown = resolve_bible_links_in_markdown(markdown, resolver)

                # Strip remaining unresolved internal links to plain text
                markdown = _strip_unresolved_links(markdown)

                title = doc_titles.get(row_id, f"{table}:{row_id}") if table == "Document" else f"{table}:{row_id}"
                filename = _sanitize_filename(f"{table}_{row_id}.md")
                out_path = os.path.join(output_dir, filename)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(markdown)
                index_entries.append((filename, title))

        if resolver is not None:
            print(f"Resolved {resolver.resolved_count} Bible verse references")
            resolver.close()

        # Write index as markdown
        index_lines = [f"# Extracted Publication\n\nTotal documents: {len(index_entries)}\n"]
        for i, (fn, title) in enumerate(index_entries, 1):
            index_lines.append(f"{i}. [{title}]({fn})")
        index_md = "\n".join(index_lines) + "\n"
        with open(os.path.join(output_dir, "index.md"), "w", encoding="utf-8") as f:
            f.write(index_md)

        return 0
    finally:
        if resolver is not None:
            resolver.close()
        try:
            os.remove(sqlite_path)
        except OSError:
            pass
