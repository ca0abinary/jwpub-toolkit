"""JWPUB file creator.

Ported from jwpubCreator.swift + main.swift (html2jwpub project).
Creates .jwpub files from HTML folders.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sqlite3
import zipfile
from datetime import datetime, timezone

from . import crypto
from . import db_schema


class JwpubCreator:
    """Creates a .jwpub publication from HTML files and media."""

    def __init__(self, folder: str, db_name: str):
        self.folder_path = folder
        self.db_name = db_name
        self.db_path = os.path.join(folder, f"{db_name}.db")
        self.pub_title = ""
        self.document_id = -1
        self.multimedia_id = -1
        self.view_item_id = 1  # starts at 1 (root item)
        self.view_item_doc_id = 0
        self.view_item_field_id = 0
        self.symbol = ""
        self.year = 0
        self.meps_language_index = 0
        self.media_paths: list[str] = []

        # Remove existing db if present
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(db_schema.INIT_STRUCTURE)
        self.conn.execute(db_schema.INSERT_ANDROID_METADATA)
        self.conn.commit()

    def insert_publication(self, title: str, symbol: str, year: int, meps_language_index: int) -> None:
        self.pub_title = title
        self.symbol = symbol
        self.year = year
        self.meps_language_index = meps_language_index

        # Publication and RefPublication share the same params:
        # 1=title, 2=symbol(root), 3=year(root), 4-7=title, 8-12=symbol, 13=year, 14=lang, 15=buildnum
        params = (
            title, symbol, year,
            title, title, title, title,
            symbol, symbol, symbol, symbol, symbol,
            year, meps_language_index, db_schema.MEPS_BUILD_NUMBER,
        )

        for query in [db_schema.INSERT_PUBLICATION, db_schema.INSERT_REF_PUBLICATION]:
            self.conn.execute(query, params)

        self.conn.execute(db_schema.INSERT_PUBLICATION_ATTRIBUTE)
        self.conn.execute(db_schema.INSERT_PUBLICATION_CATEGORY)
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW)
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_SCHEMA)
        self.conn.execute(db_schema.INSERT_PUBLICATION_YEAR, (year,))

        # Root PublicationViewItem (id=1, parent=-1, docid=-1)
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_ITEM, (1, -1, title, 0, -1))

        # Root PublicationViewItemField
        self.view_item_field_id += 1
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_ITEM_FIELD, (1, 1, title))

        self.conn.commit()

    def insert_document(self, doc_title: str, content: str, parent_view_item_id: int = 1) -> int:
        """Insert a document and return its document_id."""
        encrypted = crypto.encrypt(content, self.meps_language_index, self.symbol, self.year)
        self.document_id += 1
        doc_id = self.document_id
        meps_doc_id = 12000000 + doc_id + 1

        # Document
        self.conn.execute(db_schema.INSERT_DOCUMENT, (
            doc_id, meps_doc_id, self.meps_language_index,
            doc_title, doc_title,
            encrypted,
            len(content),
        ))

        # TextUnit
        self.conn.execute(db_schema.INSERT_TEXT_UNIT, (doc_id + 1, doc_id))

        # PublicationViewItem for this document
        self.view_item_id += 1
        item_id = self.view_item_id
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_ITEM, (
            item_id, parent_view_item_id, doc_title, None, doc_id,
        ))

        # PublicationViewItemDocument
        self.view_item_doc_id += 1
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_ITEM_DOCUMENT, (
            self.view_item_doc_id, item_id, doc_id,
        ))

        # PublicationViewItemField
        self.view_item_field_id += 1
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_ITEM_FIELD, (
            self.view_item_field_id, item_id, doc_title,
        ))

        self.conn.commit()
        return doc_id

    def insert_tab(self, tab_title: str) -> int:
        """Insert a tab (structural node with no document). Returns the view_item_id."""
        self.view_item_id += 1
        item_id = self.view_item_id
        # ParentPublicationViewItemId = -1 means top-level tab
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_ITEM, (
            item_id, -1, tab_title, 0, -1,
        ))

        self.view_item_field_id += 1
        self.conn.execute(db_schema.INSERT_PUBLICATION_VIEW_ITEM_FIELD, (
            self.view_item_field_id, item_id, tab_title,
        ))

        self.conn.commit()
        return item_id

    def insert_media(self, media_name: str, mime_type: str, res_path: str) -> None:
        self.multimedia_id += 1
        self.media_paths.append(res_path)

        self.conn.execute(db_schema.INSERT_MULTIMEDIA, (
            self.multimedia_id, 0, 1, 1, mime_type, media_name, media_name, -1,
        ))

        self.conn.execute(db_schema.INSERT_DOCUMENT_MULTIMEDIA, (
            self.document_id, self.multimedia_id,
        ))

        self.conn.commit()

    def finalize_jwpub(self) -> str:
        """Close DB, create ZIP archives, write manifest. Returns path to .jwpub file."""
        self.conn.close()

        db_file = self.db_path
        contents_path = os.path.join(self.folder_path, "contents")
        manifest_path = os.path.join(self.folder_path, "manifest.json")
        jwpub_path = os.path.join(self.folder_path, f"{self.db_name}.jwpub")

        # Clean up existing files
        for path in [contents_path, manifest_path, jwpub_path]:
            if os.path.exists(path):
                os.remove(path)

        # Create inner ZIP (contents) with DB and media
        with zipfile.ZipFile(contents_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_file, os.path.basename(db_file))
            for res_path in self.media_paths:
                zf.write(res_path, os.path.basename(res_path))

        # Compute hashes
        with open(db_file, "rb") as f:
            db_hash = hashlib.sha1(f.read()).hexdigest()
        with open(contents_path, "rb") as f:
            contents_data = f.read()
            contents_hash = hashlib.sha256(contents_data).hexdigest()

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build manifest
        manifest = {
            "name": f"{self.db_name}.jwpub",
            "hash": contents_hash,
            "timestamp": timestamp,
            "version": 1,
            "expandedSize": len(contents_data),
            "contentFormat": "z-a",
            "htmlValidated": False,
            "mepsPlatformVersion": 2.1,
            "mepsBuildNumber": db_schema.MEPS_BUILD_NUMBER,
            "publication": {
                "fileName": f"{self.db_name}.db",
                "type": 1,
                "title": self.pub_title,
                "shortTitle": self.pub_title,
                "displayTitle": self.pub_title,
                "referenceTitle": self.pub_title,
                "undatedReferenceTitle": self.pub_title,
                "titleRich": "",
                "displayTitleRich": "",
                "referenceTitleRich": "",
                "undatedReferenceTitleRich": "",
                "symbol": self.symbol,
                "uniqueEnglishSymbol": self.symbol,
                "uniqueSymbol": self.symbol,
                "englishSymbol": self.symbol,
                "language": self.meps_language_index,
                "hash": db_hash,
                "timestamp": timestamp,
                "minPlatformVersion": 1,
                "schemaVersion": 8,
                "year": self.year,
                "issueId": 0,
                "issueNumber": 0,
                "publicationType": db_schema.PUBLICATION_TYPE,
                "rootSymbol": self.symbol,
                "rootYear": self.year,
                "rootLanguage": self.meps_language_index,
                "images": [],
                "categories": ["manual"],
                "attributes": [],
            },
        }

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        # Create outer ZIP (.jwpub)
        with zipfile.ZipFile(jwpub_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(contents_path, "contents")
            zf.write(manifest_path, "manifest.json")

        # Clean up intermediate files
        os.remove(db_file)
        os.remove(contents_path)
        os.remove(manifest_path)

        return jwpub_path


def _guess_mime_type(file_path: str) -> str:
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


def create_from_folder(
    folder: str,
    symbol: str,
    title: str,
    year: int,
    meps_language_index: int,
) -> str:
    """Create a .jwpub from a folder of HTML files.

    Each .html file becomes a document. Media subfolders (matching the HTML
    filename without extension) are included as multimedia.

    Returns the path to the generated .jwpub file.
    """
    creator = JwpubCreator(folder=folder, db_name=symbol)
    creator.insert_publication(title=title, symbol=symbol, year=year, meps_language_index=meps_language_index)

    for file_name in sorted(os.listdir(folder)):
        if not file_name.endswith(".html"):
            continue

        print(f"Processing {file_name}")
        file_path = os.path.join(folder, file_name)
        with open(file_path, "r", encoding="utf-8") as f:
            file_content = f.read()

        # Check for media subfolder
        res_folder = os.path.join(folder, file_name.removesuffix(".html"))
        if os.path.isdir(res_folder):
            for res_name in sorted(os.listdir(res_folder)):
                res_path = os.path.join(res_folder, res_name)
                if os.path.isfile(res_path):
                    print(f"  Media: {res_name}")
                    mime_type = _guess_mime_type(res_path)
                    creator.insert_media(media_name=res_name, mime_type=mime_type, res_path=res_path)

        doc_title = file_name.removesuffix(".html")
        creator.insert_document(doc_title=doc_title, content=file_content)

    jwpub_path = creator.finalize_jwpub()
    print(f"Done: {jwpub_path}")
    return jwpub_path
