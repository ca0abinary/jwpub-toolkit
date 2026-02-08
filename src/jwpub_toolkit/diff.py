#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import os
import re
import sqlite3
import html  # NEW: per escape titoli
from typing import Dict, Tuple, List, Optional
from bs4 import BeautifulSoup

from . import extractor


ContentKey = Tuple[str, int]  # (table, row_id)

# --- NEW: estrai anno dall'id cartella ---
def extract_year_from_id(pub_id: str) -> str:
    parts = pub_id.split('_')
    # Heuristica: primo token di 4 cifre
    for p in parts:
        if p.isdigit() and len(p) == 4:
            return p
    # fallback: ultimo token che sembra anno
    for p in reversed(parts):
        if p.isdigit() and len(p) == 4:
            return p
    return '????'


def clean_html_to_text(html_content: str) -> str:
    """Estrae il testo pulito dall'HTML rimuovendo i tag"""
    soup = BeautifulSoup(html_content, 'html.parser')

    # Rimuovi script e style
    for script in soup(["script", "style"]):
        script.decompose()

    # Estrai il testo
    text = soup.get_text()

    # Pulisci il testo
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = ' '.join(chunk for chunk in chunks if chunk)

    return text


def load_contents(jwpub_path: str, documents_only: bool = False) -> Tuple[Dict[ContentKey, str], str]:
    sqlite_path = extractor.extract_sqlite_from_jwpub_to_temp(jwpub_path)
    try:
        conn = sqlite3.connect(sqlite_path)
        identity = extractor.read_publication_identity(conn)
        full_hash = extractor.compute_full_hash_bytes(identity)
        id_label = f"{identity.meps_language_index}_{identity.symbol}_{identity.year}" + (
            f"_{identity.issue_tag_number}" if identity.issue_tag_number != 0 else ""
        )

        contents: Dict[ContentKey, str] = {}
        for table, column in extractor.discover_content_tables(conn):
            # Escludi sempre la tabella Extract (approfondimenti non confrontabili)
            if table == "Extract":
                continue
            if documents_only and table != "Document":
                continue
            for row_id, blob in extractor.iter_encrypted_rows(conn, table, column):
                html_text = extractor.decrypt_and_inflate(blob, full_hash)
                if html_text is not None:
                    contents[(table, row_id)] = html_text
        return contents, id_label
    finally:
        try:
            os.remove(sqlite_path)
        except OSError:
            pass


def sanitize_html_for_display(html_content: str) -> str:
    """Pulisce e prepara l'HTML per la visualizzazione sicura"""
    soup = BeautifulSoup(html_content, 'html.parser')

    # Rimuovi elementi potenzialmente pericolosi
    for tag in soup(["script", "style", "link", "meta"]):
        tag.decompose()

    # Rimuovi attributi pericolosi
    for tag in soup.find_all():
        # Mantieni solo attributi sicuri
        safe_attrs = ['class', 'id', 'href', 'src', 'alt', 'title']
        attrs_to_remove = []
        for attr in tag.attrs:
            if attr not in safe_attrs:
                attrs_to_remove.append(attr)
        for attr in attrs_to_remove:
            del tag[attr]

    return str(soup)


def split_into_paragraphs(text: str) -> List[str]:
    """Divide il testo in paragrafi per un confronto più dettagliato"""
    if not text.strip():
        return []

    # Strategia migliorata per dividere in paragrafi
    # Prima normalizza gli spazi e i caratteri speciali
    text = re.sub(r'\s+', ' ', text.strip())

    # Divide su pattern più specifici
    # 1. Doppi a capo
    # 2. Fine frase seguita da maiuscola
    # 3. Numeri seguiti da punto e spazio (liste)
    patterns = [
        r'\n\s*\n+',  # Doppi a capo
        r'\.(?=\s+[A-Z])',  # Punto seguito da maiuscola
        r'(?<=\.)\s+(?=\d+\.)',  # Tra frasi numerate
    ]

    # Prova prima con doppi a capo
    paragraphs = re.split(r'\n\s*\n+', text)

    # Se otteniamo solo un paragrafo, prova a dividere per frasi lunghe
    if len(paragraphs) <= 1:
        # Dividi per frasi, ma mantieni frasi ragionevolmente lunghe insieme
        sentences = re.split(r'\.(?=\s+[A-Z])', text)
        paragraphs = []
        current_para = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            if not sentence.endswith('.'):
                sentence += '.'

            # Raggruppa frasi corte insieme
            if len(current_para) < 100:  # Soglia minima per paragrafo
                current_para += (" " + sentence if current_para else sentence)
            else:
                if current_para:
                    paragraphs.append(current_para)
                current_para = sentence

        if current_para:
            paragraphs.append(current_para)

    # Pulisci e filtra i paragrafi
    cleaned_paragraphs = []
    for p in paragraphs:
        p = p.strip()
        if len(p) > 20:  # Riduco la soglia minima
            # Normalizza ulteriormente il testo
            p = re.sub(r'\s+', ' ', p)
            cleaned_paragraphs.append(p)

    return cleaned_paragraphs


def create_html_diff_view(html_a: str, html_b: str) -> Tuple[str, str]:
    """Crea una visualizzazione diff per contenuto HTML"""
    # Converte l'HTML in testo per il confronto dei paragrafi
    text_a = clean_html_to_text(html_a)
    text_b = clean_html_to_text(html_b)

    paragraphs_a = split_into_paragraphs(text_a)
    paragraphs_b = split_into_paragraphs(text_b)

    # Prepara l'HTML sanitizzato per la visualizzazione
    safe_html_a = sanitize_html_for_display(html_a)
    safe_html_b = sanitize_html_for_display(html_b)

    return safe_html_a, safe_html_b


def write_html_report(
    out_dir: str,
    id_a: str,
    id_b: str,
    only_a: List[ContentKey],
    only_b: List[ContentKey],
    changed: List[ContentKey],
    map_a: Dict[ContentKey, str],
    map_b: Dict[ContentKey, str],
    side_by_side: bool = False,
    text_only: bool = False,
    pub_name: Optional[str] = None,
    year_a: Optional[str] = None,
    year_b: Optional[str] = None,
    show_tag_boxes: bool = False,
) -> None:
    """Genera SOLO il report HTML side-by-side"""
    os.makedirs(out_dir, exist_ok=True)
    write_side_by_side_html(out_dir, id_a, id_b, only_a, only_b, changed, map_a, map_b, text_only=text_only, pub_name=pub_name, year_a=year_a, year_b=year_b, show_tag_boxes=show_tag_boxes)


def extract_item_title(html_text: str) -> Optional[str]:
    """Estrae un titolo leggibile da un frammento HTML:
    1. header > h1
    2. primo h1
    3. primo h2
    4. primo p (troncato)
    Restituisce None se non trova nulla significativo.
    """
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
    except Exception:
        return None
    # 1. header > h1
    header = soup.find('header')
    if header:
        h1 = header.find('h1')
        if h1 and h1.get_text(strip=True):
            return _clean_title_text(h1.get_text(" ", strip=True))
    # 2. primo h1 globale
    h1 = soup.find('h1')
    if h1 and h1.get_text(strip=True):
        return _clean_title_text(h1.get_text(" ", strip=True))
    # 3. primo h2
    h2 = soup.find('h2')
    if h2 and h2.get_text(strip=True):
        return _clean_title_text(h2.get_text(" ", strip=True))
    # 4. primo paragrafo
    p = soup.find('p')
    if p and p.get_text(strip=True):
        return _clean_title_text(p.get_text(" ", strip=True), paragraph=True)
    return None


def _clean_title_text(text: str, paragraph: bool = False) -> str:
    # Normalizza spazi
    text = re.sub(r'\s+', ' ', text).strip()
    # Rimuove sequenze troppo lunghe di punteggiatura iniziale
    text = re.sub(r'^[\-\u2013:\u2022\s]+', '', text)
    # Tronca se eccessivo
    max_len = 120 if not paragraph else 90
    if len(text) > max_len:
        text = text[:max_len].rstrip() + '\u2026'
    return text


def _normalize_title_for_match(s: str) -> str:
    s = re.sub(r'\s+', ' ', s)
    s = s.replace('\xa0', ' ')
    s = s.lower().strip()
    # rimuovi punteggiatura tranne parentesi e lettere/numeri
    s = re.sub(r'[^a-z0-9\u00e0\u00e8\u00e9\u00ec\u00f2\u00f9\u00e7() ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def detect_index_titles(contents: Dict[ContentKey, str]) -> Dict[ContentKey, str]:
    """Rileva indice basato su groupTOC e link jwpub://p/I:xxxxx.
    Strategia:
      1. Identifica documento indice: presenza div.groupTOC + >=5 link jwpub://
      2. Estrae lista voci (anchor text pulito) mantenendo ordine
      3. Costruisce lista (k, heading estratto) per ciascun Document
      4. Matching in due passaggi:
         a) fuzzy score combinato (SequenceMatcher + jaccard token) sopra soglia
         b) riempimento sequenziale per voci rimaste
    Restituisce mappa (Document,row_id)->titolo.
    """
    from difflib import SequenceMatcher
    doc_html = {k: h for k, h in contents.items() if k[0] == 'Document'}
    if not doc_html:
        return {}

    index_candidates: List[Tuple[int, ContentKey, List[str]]] = []  # (num_links, key, titles)
    for key, html_text in doc_html.items():
        try:
            soup = BeautifulSoup(html_text, 'html.parser')
        except Exception:
            continue
        toc_div = soup.find('div', class_=lambda c: c and 'groupTOC' in c.split())
        if not toc_div:
            continue
        anchors = toc_div.find_all('a')
        jw_anchors = [a for a in anchors if (a.get('href') or '').startswith('jwpub://p/I:')]
        if len(jw_anchors) < 5:
            continue
        titles: List[str] = []
        for a in jw_anchors:
            raw = a.get_text(' ', strip=True)
            raw = raw.replace('\xa0', ' ')
            raw = re.sub(r'\s+', ' ', raw)
            raw = re.sub(r'^\d+\s+', '', raw)  # rimuove eventuale numero pagina davanti
            clean = _clean_title_text(raw)
            if clean and clean not in titles:
                titles.append(clean)
        if titles:
            index_candidates.append((len(titles), key, titles))
    if not index_candidates:
        return {}
    index_candidates.sort(reverse=True)
    _n, index_key, index_titles = index_candidates[0]

    # Costruisci headings documento
    doc_headings: List[Tuple[ContentKey, str, str]] = []  # (key, original, normalized)
    for k, html_text in doc_html.items():
        if k == index_key:
            continue
        t = extract_item_title(html_text)
        if t:
            doc_headings.append((k, t, _normalize_title_for_match(t)))
    # Ordina per row_id per fallback sequenziale
    doc_headings.sort(key=lambda x: x[0][1])

    def jaccard(a: str, b: str) -> float:
        sa = set(a.split())
        sb = set(b.split())
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        union = len(sa | sb)
        return inter / union if union else 0.0

    result: Dict[ContentKey, str] = {}
    used_docs: set[ContentKey] = set()

    # Passo 1: fuzzy best match
    for anchor in index_titles:
        norm_anchor = _normalize_title_for_match(anchor)
        best_score = 0.0
        best_k: Optional[ContentKey] = None
        for k, orig, norm in doc_headings:
            if k in used_docs:
                continue
            sm = SequenceMatcher(None, norm_anchor, norm).ratio()
            jac = jaccard(norm_anchor, norm)
            prefix = 0.0
            # lunghezza prefisso comune
            for a_ch, b_ch in zip(norm_anchor, norm):
                if a_ch == b_ch:
                    prefix += 1
                else:
                    break
            prefix_score = prefix / max(len(norm_anchor), 1)
            score = (sm * 0.5) + (jac * 0.3) + (prefix_score * 0.2)
            if score > best_score:
                best_score = score; best_k = k
        if best_k and best_score >= 0.58:  # soglia empirica
            result[best_k] = anchor
            used_docs.add(best_k)

    # Passo 2: fallback sequenziale per anchor rimaste
    remaining_docs = [k for k, _, _ in doc_headings if k not in used_docs]
    remaining_anchors = [a for a in index_titles if a not in result.values()]
    for anchor, k in zip(remaining_anchors, remaining_docs):
        result[k] = anchor
        used_docs.add(k)

    return result


def write_side_by_side_html(
    out_dir: str,
    id_a: str,
    id_b: str,
    only_a: List[ContentKey],
    only_b: List[ContentKey],
    changed: List[ContentKey],
    map_a: Dict[ContentKey, str],
    map_b: Dict[ContentKey, str],
    text_only: bool = False,
    pub_name: Optional[str] = None,
    year_a: Optional[str] = None,
    year_b: Optional[str] = None,
    show_tag_boxes: bool = False,
) -> None:
    """Genera un report HTML con visualizzazione side-by-side delle pagine web interpretate"""

    # NEW: costruiamo mappa titoli (priorità: indice -> header -> fallback id)
    title_map: Dict[ContentKey, str] = {}
    # Usa preferibilmente i contenuti della versione B (più nuova) altrimenti A
    union_contents: Dict[ContentKey, str] = {}
    union_keys = set(map_a.keys()) | set(map_b.keys())
    for k in union_keys:
        union_contents[k] = map_b.get(k) or map_a.get(k)  # preferisci B
    # 1. indice
    index_titles = detect_index_titles(union_contents)
    title_map.update(index_titles)
    # 2. header/h1/h2
    for k, html_src in union_contents.items():
        if k not in title_map:
            t = extract_item_title(html_src)
            if t:
                title_map[k] = t
    # 3. fine: se mancano rimarranno senza titolo (verrà usato id)

    display_pub = pub_name or 'Pubblicazione'
    ya = year_a or 'A'
    yb = year_b or 'B'
    # Aggiorna CSS aggiungendo stile per raw links
    tag_box_css = """
        [data-diff-tag=\"added\"] { outline: 2px solid #28a745; background: rgba(40,167,69,0.08); }
        [data-diff-tag=\"removed\"] { outline: 2px solid #dc3545; background: rgba(220,53,69,0.08); }
        [data-diff-tag=\"replaced\"] { outline: 2px solid #ffc107; background: rgba(255,193,7,0.12); }
    """ if show_tag_boxes else ""
    html_content = f"""
<!DOCTYPE html>
<html lang=\"it\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>Confronto JWPub - {display_pub}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
            line-height: 1.6;
        }}
        .header {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .summary {{
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }}
        .summary-item {{
            background: white;
            padding: 15px;
            border-radius: 8px;
            flex: 1;
            min-width: 200px;
            text-align: center;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .summary-number {{
            font-size: 2em;
            font-weight: bold;
            color: #007acc;
        }}
        .document-section {{
            background: white;
            margin-bottom: 30px;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .document-header {{
            background: linear-gradient(135deg, #007acc, #005999);
            color: white;
            padding: 15px 20px;
            font-size: 1.2em;
            font-weight: bold;
            display: flex;
            gap: 14px; /* NEW spazio */
            align-items: center;
            flex-wrap: wrap; /* NEW per evitare overflow */
        }}
        .raw-links {{ /* NEW */
            margin-left: auto;
            display: flex;
            gap: 6px;
            align-items: center;
            flex-wrap: wrap;
        }}
        .raw-links a {{ /* NEW */
            background: rgba(255,255,255,0.18);
            color: #fff;
            padding: 4px 10px;
            border-radius: 14px;
            text-decoration: none;
            font-weight: 500;
            font-size: 0.65em;
            letter-spacing: .5px;
            line-height: 1.2;
            border: 1px solid rgba(255,255,255,0.25);
            backdrop-filter: blur(4px);
            box-shadow: 0 1px 2px rgba(0,0,0,0.25) inset, 0 0 0 1px rgba(255,255,255,0.08);
            transition: background .2s, transform .15s;
        }}
        .raw-links a:hover {{ /* NEW */
            background: rgba(255,255,255,0.32);
        }}
        .raw-links a:active {{ /* NEW */
            transform: translateY(1px);
        }}
        .status-indicator {{
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: normal;
        }}
        .status-indicator.changed {{
            background: rgba(255, 193, 7, 0.2);
            color: #fff;
        }}
        .status-indicator.added {{
            background: rgba(40, 167, 69, 0.2);
            color: #fff;
        }}
        .status-indicator.removed {{
            background: rgba(220, 53, 69, 0.2);
            color: #fff;
        }}
        .side-by-side {{
            display: flex;
            min-height: 300px;
        }}
        .column {{
            flex: 1;
            padding: 0;
            border-right: 1px solid #eee;
            overflow: hidden;
        }}
        .column:last-child {{
            border-right: none;
        }}
        .column-header {{
            font-weight: bold;
            padding: 15px 20px;
            background: #f8f9fa;
            border-bottom: 2px solid #007acc;
            color: #007acc;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        .content-container {{
            padding: 20px;
            max-height: 80vh;
            overflow-y: auto;
        }}
        .rendered-html {{
            border: 1px solid #e9ecef;
            border-radius: 4px;
            padding: 15px;
            background: white;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.1);
        }}
        .rendered-html.has-changes {{
            background: #fff3cd;
            border-color: #ffc107;
        }}
        .rendered-html.only-in-a {{
            background: #f8d7da;
            border-color: #dc3545;
        }}
        .rendered-html.only-in-b {{
            background: #d4edda;
            border-color: #28a745;
        }}
        .diff-summary {{
            background: #f8f9fa;
            padding: 10px 15px;
            margin-bottom: 15px;
            border-radius: 4px;
            font-size: 0.9em;
            color: #6c757d;
        }}
        .toc {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .toc h2 {{
            margin-top: 0;
            color: #007acc;
        }}
        .toc h3 {{
            color: #495057;
            margin-top: 20px;
            margin-bottom: 10px;
        }}
        .toc a {{
            color: #007acc;
            text-decoration: none;
            display: block;
            padding: 5px 10px;
            border-radius: 4px;
            transition: background-color 0.2s;
        }}
        .toc a:hover {{
            background-color: #f8f9fa;
            text-decoration: underline;
        }}
        .navigation {{
            position: fixed;
            top: 20px;
            right: 20px;
            background: white;
            padding: 10px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            z-index: 1000;
        }}
        .navigation button {{
            background: #007acc;
            color: white;
            border: none;
            padding: 8px 12px;
            border-radius: 4px;
            cursor: pointer;
            margin: 2px;
            font-size: 0.8em;
        }}
        .navigation button:hover {{
            background: #005999;
        }}
        /* Stili per il contenuto HTML renderizzato */
        .rendered-html h1, .rendered-html h2, .rendered-html h3,
        .rendered-html h4, .rendered-html h5, .rendered-html h6 {{
            color: #333;
            margin-top: 1.5em;
            margin-bottom: 0.5em;
        }}
        .rendered-html p {{
            margin-bottom: 1em;
        }}
        .rendered-html ul, .rendered-html ol {{
            margin-bottom: 1em;
            padding-left: 2em;
        }}
        .rendered-html blockquote {{
            border-left: 4px solid #007acc;
            padding-left: 1em;
            margin: 1em 0;
            color: #666;
        }}
        .rendered-html img {{
            max-width: 100%;
            height: auto;
        }}
        .rendered-html table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1em 0;
        }}
        .rendered-html th, .rendered-html td {{
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }}
        .rendered-html th {{
            background-color: #f2f2f2;
        }}
        .diff-paragraph {{
            padding: 8px;
            margin: 4px 0;
            border-radius: 4px;
        }}
        .diff-paragraph.unchanged {{
            background: #e9ecef;
        }}
        .diff-paragraph.removed {{
            background: #f8d7da;
            border-left: 4px solid #dc3545;
        }}
        .diff-paragraph.added {{
            background: #d4edda;
            border-left: 4px solid #28a745;
        }}
        .diff-paragraph.placeholder {{
            background: #f8f9fa;
            border-left: 4px solid #6c757d;
            color: #6c757d;
            font-style: italic;
            text-align: center;
        }}
        .diff-label {{
            font-weight: bold;
            margin-right: 8px;
        }}
        .word-removed {{
            background: #f8d7da;
            color: #721c24;
            text-decoration: line-through;
            padding: 2px 4px;
            border-radius: 2px;
        }}
        .word-added {{
            background: #d4edda;
            color: #155724;
            padding: 2px 4px;
            border-radius: 2px;
        }}
        .tag-removed {{
            background: #f8d7da;
            color: #721c24;
            padding: 2px 4px;
            border-radius: 2px;
            font-weight: bold;
        }}
        .tag-added {{
            background: #d4edda;
            color: #155724;
            padding: 2px 4px;
            border-radius: 2px;
            font-weight: bold;
        }}
        {tag_box_css}
        .view-toggle {{ padding: 6px 12px; background:#007acc; color:#fff; border:none; border-radius:4px; cursor:pointer; margin:8px 0 12px 0; font-size:0.8em; }}
        .view-toggle:hover {{ background:#005999; }}
        .line-diff-container {{ display:none; max-height:80vh; overflow:auto; border-top:1px solid #e1e4e8; background:#fff; padding:10px 12px 18px 12px; }}
        .line-diff-wrapper {{ font-family: SFMono-Regular,Consolas,'Liberation Mono',Menlo,monospace; font-size:13px; }}
        .line-diff-title {{ font-weight:bold; margin:4px 0 8px; color:#444; }}
        .line-diff-table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
        .line-diff-table td, .line-diff-table th {{ padding:2px 6px; border:0; vertical-align:top; }}
        .line-diff-table td.code {{ white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere; }}
        .line-diff-table th {{ background:#f0f3f6; font-weight:600; color:#555; font-size:11px; letter-spacing:.5px; }}
        .line-num {{ width:3.2em; background:#f6f8fa; color:#6e7781; text-align:right; user-select:none; border-right:1px solid #e1e4e8; font-size:11px; }}
        .line-diff-table tr.diff-equal td.code {{ background:#ffffff; }}
        .line-diff-table tr.diff-added td.code {{ background:#e6ffed; }}
        .line-diff-table tr.diff-removed td.code {{ background:#ffeef0; }}
        .line-diff-table tr.diff-changed td.code {{ background:#fff5b1; }}
        .intraline-added {{ background:#acf2bd; }}
        .intraline-removed {{ background:#ffb8c0; text-decoration:line-through; }}
    </style>
    <script>
        function scrollToTop() {{ window.scrollTo({{ top: 0, behavior: 'smooth' }}); }}
        function scrollToElement(id) {{ document.getElementById(id).scrollIntoView({{ behavior: 'smooth' }}); }}
        // Sincronizzazione scroll colonne (fix Safari jitter)
        document.addEventListener('DOMContentLoaded', function() {{
            document.querySelectorAll('.side-by-side').forEach(function(pair) {{
                const containers = pair.querySelectorAll('.content-container');
                if (containers.length !== 2) return;
                const left = containers[0];
                const right = containers[1];
                let syncing = false;
                let active = null; // ultimo contenitore scorruto manualmente
                let rafId = null;
                function scheduleSync(source, target) {{
                    if (syncing) return;
                    syncing = true;
                    if (rafId) cancelAnimationFrame(rafId);
                    rafId = requestAnimationFrame(() => {{
                        const maxSource = source.scrollHeight - source.clientHeight;
                        const ratio = maxSource > 0 ? (source.scrollTop / maxSource) : 0;
                        const maxTarget = target.scrollHeight - target.clientHeight;
                        const desired = ratio * maxTarget;
                        // Applica solo se differenza significativa per evitare micro correzioni (Safari)
                        if (Math.abs(target.scrollTop - desired) > 1) {{
                            target.scrollTop = desired;
                        }}
                        syncing = false;
                    }});
                }}
                function onScroll(e) {{
                    const source = e.currentTarget;
                    if (active && active !== source) return; // sync solo dalla sorgente attiva
                    const target = (source === left) ? right : left;
                    scheduleSync(source, target);
                }}
                function setActive(e) {{ active = e.currentTarget; }}
                ['pointerenter','touchstart','mousedown'].forEach(evt => {{
                    left.addEventListener(evt, setActive, {{ passive: true }});
                    right.addEventListener(evt, setActive, {{ passive: true }});
                }});
                left.addEventListener('scroll', onScroll, {{ passive: true }});
                right.addEventListener('scroll', onScroll, {{ passive: true }});
            }});
        }});
        function toggleView(sectionId) {{
            const section = document.getElementById(sectionId);
            if (!section) return;
            const sb = section.querySelector('.side-by-side');
            const ld = section.querySelector('.line-diff-container');
            if (!ld) return;
            if (ld.style.display === 'none' || ld.style.display === '') {{
                if (sb) sb.style.display = 'none';
                ld.style.display = 'block';
            }} else {{
                ld.style.display = 'none';
                if (sb) sb.style.display = 'flex';
            }}
        }}
    </script>
</head>
<body>
    <div class=\"navigation\">
        <button onclick=\"scrollToTop()\">↑ Top</button>
        <button onclick=\"scrollToElement('toc')\">📋 Indice</button>
    </div>

    <div class=\"header\">
        <h1>🔍 Confronto JWPub - {display_pub}</h1>
        <p><strong>Nome pubblicazione:</strong> {display_pub}</p>
        <p><strong>Versione precedente:</strong> {ya} &nbsp; | &nbsp; <strong>Versione nuova:</strong> {yb}</p>
        <p style=\"font-size:0.8em; color:#666;\">Cartelle: {id_a} → {id_b}</p>
    </div>

    <div class=\"summary\">
        <div class=\"summary-item\">
            <div class=\"summary-number\">{len(changed)}</div>
            <div>📝 Elementi Modificati</div>
        </div>
        <div class=\"summary-item\">
            <div class=\"summary-number\">{len(only_a)}</div>
            <div>❌ Solo in {ya}</div>
        </div>
        <div class=\"summary-item\">
            <div class=\"summary-number\">{len(only_b)}</div>
            <div>✅ Solo in {yb}</div>
        </div>
    </div>

    <div class=\"toc\" id=\"toc\">
        <h2>📋 Indice degli Elementi</h2>
"""
    # Aggiungi indice per gli elementi modificati
    if changed:
        html_content += "<h3>📝 Elementi Modificati</h3>"
        for key in changed:
            tit = html.escape(title_map.get(key, f"{key[0]}:{key[1]}"))
            html_content += f'<a href=\"#item-{key[0]}-{key[1]}\">📄 {tit}</a>'

    if only_a:
        html_content += f"<h3>❌ Elementi Solo in {ya}</h3>"
        for key in only_a:
            tit = html.escape(title_map.get(key, f"{key[0]}:{key[1]}"))
            html_content += f'<a href=\"#item-{key[0]}-{key[1]}\">📄 {tit}</a>'

    if only_b:
        html_content += f"<h3>✅ Elementi Solo in {yb}</h3>"
        for key in only_b:
            tit = html.escape(title_map.get(key, f"{key[0]}:{key[1]}"))
            html_content += f'<a href=\"#item-{key[0]}-{key[1]}\">📄 {tit}</a>'

    html_content += "</div>"

    # Genera sezioni per gli elementi modificati
    if changed:
        for key in changed:
            html_a = map_a[key]
            html_b = map_b[key]
            if text_only:
                highlighted_a, highlighted_b = compare_text_only(html_a, html_b)
            else:
                highlighted_a, highlighted_b = compare_html_content(html_a, html_b)
                if not show_tag_boxes:
                    # Rimuove attributi data-diff-tag per non mostrare riquadri
                    highlighted_a = re.sub(r'\sdata-diff-tag=\"(?:added|removed|replaced)\"', '', highlighted_a)
                    highlighted_b = re.sub(r'\sdata-diff-tag=\"(?:added|removed|replaced)\"', '', highlighted_b)
            highlighted_a = _rewrite_image_sources(highlighted_a, id_a, out_dir, embed=True)
            highlighted_b = _rewrite_image_sources(highlighted_b, id_b, out_dir, embed=True)
            text_a = clean_html_to_text(html_a)
            text_b = clean_html_to_text(html_b)
            line_table = generate_line_diff_table(html_a, html_b)
            display_title = html.escape(title_map.get(key, f"{key[0]}:{key[1]}"))
            id_label = f"{key[0]}:{key[1]}"
            html_content += f"""
    <div class=\"document-section\" id=\"item-{key[0]}-{key[1]}\">
        <div class=\"document-header\">
            📄 {display_title}<span style=\"font-size:0.65em; font-weight:normal; opacity:0.85;\">({id_label})</span>
            <span class=\"status-indicator changed\">📝 Modificato</span>
            <div class=\"raw-links\">
                <a href=\"{id_a}/{key[0]}_{key[1]}.html\" target=\"_blank\">RAW {ya}</a>
                <a href=\"{id_b}/{key[0]}_{key[1]}.html\" target=\"_blank\">RAW {yb}</a>
            </div>
        </div>
        <div style=\"padding:0 20px 0 20px;\">
            <button class=\"view-toggle\" onclick=\"toggleView('item-{key[0]}-{key[1]}')\">☯️ Toggle Line/Rendered</button>
        </div>
        <div class=\"side-by-side\">
            <div class=\"column\">
                <div class=\"column-header\">📄 {ya}</div>
                <div class=\"content-container\">
                    <div class=\"diff-summary\">
                        Caratteri: {len(text_a)} | Parole: {len(text_a.split()) if text_a else 0}
                    </div>
                    <div class=\"rendered-html has-changes\">
                        {highlighted_a}
                    </div>
                </div>
            </div>
            <div class=\"column\">
                <div class=\"column-header\">📄 {yb}</div>
                <div class=\"content-container\">
                    <div class=\"diff-summary\">
                        Caratteri: {len(text_b)} | Parole: {len(text_b.split()) if text_b else 0}
                    </div>
                    <div class=\"rendered-html has-changes\">
                        {highlighted_b}
                    </div>
                </div>
            </div>
        </div>
        <div class=\"line-diff-container\">{line_table}</div>
    </div>
"""

    # Aggiungi elementi solo in A
    if only_a:
        for key in only_a:
            html_a = map_a[key]
            safe_html_a = sanitize_html_for_display(html_a)
            safe_html_a = _rewrite_image_sources(safe_html_a, id_a, out_dir, embed=True)
            text_a = clean_html_to_text(html_a)
            display_title = html.escape(title_map.get(key, f"{key[0]}:{key[1]}"))
            id_label = f"{key[0]}:{key[1]}"
            html_content += f"""\n    <div class=\"document-section\" id=\"item-{key[0]}-{key[1]}\">\n        <div class=\"document-header\">\n            📄 {display_title}<span style=\"font-size:0.65em; font-weight:normal; opacity:0.85;\">({id_label})</span>\n            <span class=\"status-indicator removed\">❌ Solo in {ya} (Rimosso)</span>\n            <div class=\"raw-links\">\n                <a href=\"{id_a}/{key[0]}_{key[1]}.html\" target=\"_blank\">RAW {ya}</a>\n            </div>\n        </div>\n        <div class=\"side-by-side\">\n            <div class=\"column\">\n                <div class=\"column-header\">📄 {ya}</div>\n                <div class=\"content-container\">\n                    <div class=\"diff-summary\">\n                        Caratteri: {len(text_a)} | Parole: {len(text_a.split()) if text_a else 0}\n                    </div>\n                    <div class=\"rendered-html only-in-a\">\n                        {safe_html_a}\n                    </div>\n                </div>\n            </div>\n            <div class=\"column\">\n                <div class=\"column-header\">📄 {yb}</div>\n                <div class=\"content-container\">\n                    <div class=\"diff-summary\">❌ Elemento non presente in {yb}</div>\n                    <div class=\"rendered-html\">\n                        <p style=\"text-align: center; color: #6c757d; font-style: italic;\">\n                            Questo elemento è stato rimosso nella versione {yb}\n                        </p>\n                    </div>\n                </div>\n            </div>\n        </div>\n    </div>\n"""

    # Aggiungi elementi solo in B
    if only_b:
        for key in only_b:
            html_b = map_b[key]
            safe_html_b = sanitize_html_for_display(html_b)
            safe_html_b = _rewrite_image_sources(safe_html_b, id_b, out_dir, embed=True)
            text_b = clean_html_to_text(html_b)
            display_title = html.escape(title_map.get(key, f"{key[0]}:{key[1]}"))
            id_label = f"{key[0]}:{key[1]}"
            html_content += f"""\n    <div class=\"document-section\" id=\"item-{key[0]}-{key[1]}\">\n        <div class=\"document-header\">\n            📄 {display_title}<span style=\"font-size:0.65em; font-weight:normal; opacity:0.85;\">({id_label})</span>\n            <span class=\"status-indicator added\">✅ Solo in {yb} (Aggiunto)</span>\n            <div class=\"raw-links\">\n                <a href=\"{id_b}/{key[0]}_{key[1]}.html\" target=\"_blank\">RAW {yb}</a>\n            </div>\n        </div>\n        <div class=\"side-by-side\">\n            <div class=\"column\">\n                <div class=\"column-header\">📄 {ya}</div>\n                <div class=\"content-container\">\n                    <div class=\"diff-summary\">❌ Elemento non presente in {ya}</div>\n                    <div class=\"rendered-html\">\n                        <p style=\"text-align: center; color: #6c757d; font-style: italic;\">\n                            Questo elemento è stato aggiunto nella versione {yb}\n                        </p>\n                    </div>\n                </div>\n            </div>\n            <div class=\"column\">\n                <div class=\"column-header\">📄 {yb}</div>\n                <div class=\"content-container\">\n                    <div class=\"diff-summary\">\n                        Caratteri: {len(text_b)} | Parole: {len(text_b.split()) if text_b else 0}\n                    </div>\n                    <div class=\"rendered-html only-in-b\">\n                        {safe_html_b}\n                    </div>\n                </div>\n            </div>\n        </div>\n    </div>\n"""

    html_content += """
</body>
</html>
"""
    with open(os.path.join(out_dir, "side_by_side.html"), "w", encoding='utf-8') as f:
        f.write(html_content)


def compare_html_content(html_a: str, html_b: str) -> Tuple[str, str]:
    """Confronta due contenuti HTML con strategia a due livelli (blocchi + token) usando patience diff.
    1. Split per blocchi (p, li, h*, blockquote, div, section, article) SOLO top-level (evita annidati duplicati)
    2. Patience diff sui blocchi per allineare meglio il contenuto riordinato
    3. Per i blocchi cambiati/appaiati applica un diff token granular HTML-aware
    """
    import html
    import hashlib
    from bs4 import BeautifulSoup, NavigableString, Tag

    BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "div", "section", "article"}

    def clean_dom(html_content: str) -> BeautifulSoup:
        soup = BeautifulSoup(html_content, 'html.parser')
        for t in soup(["script", "style"]):
            t.decompose()
        return soup

    soup_a = clean_dom(html_a)
    soup_b = clean_dom(html_b)

    # --- 1. Estrazione blocchi (solo top-level semantici) ---
    def extract_blocks(soup: BeautifulSoup) -> List[Tag]:
        root = soup.body if soup.body else soup
        blocks: List[Tag] = []
        def visit(node: Tag):
            for child in node.children:
                if isinstance(child, Tag):
                    if child.name in BLOCK_TAGS:
                        blocks.append(child)
                        # NON visitare discendenti di un blocco già raccolto per evitare duplicati annidati
                    else:
                        visit(child)
        visit(root)
        if not blocks:
            blocks = [root]
        return blocks

    blocks_a = extract_blocks(soup_a)
    blocks_b = extract_blocks(soup_b)

    def block_signature(tag: Tag) -> str:
        text = tag.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        base = f"{tag.name}|{text[:200]}"  # tronca testo lungo per hash
        digest = hashlib.sha1(base.encode('utf-8')).hexdigest()[:12]
        return f"{tag.name}:{digest}:{len(text)}"

    sigs_a = [block_signature(b) for b in blocks_a]
    sigs_b = [block_signature(b) for b in blocks_b]

    # Deduplica blocchi consecutivi con stessa firma (riduce ripetizioni)
    def dedupe(blocks: List[Tag], sigs: List[str]) -> Tuple[List[Tag], List[str]]:
        if not blocks:
            return blocks, sigs
        new_blocks: List[Tag] = []
        new_sigs: List[str] = []
        prev = None
        for blk, sig in zip(blocks, sigs):
            if sig != prev:
                new_blocks.append(blk)
                new_sigs.append(sig)
            prev = sig
        return new_blocks, new_sigs

    blocks_a, sigs_a = dedupe(blocks_a, sigs_a)
    blocks_b, sigs_b = dedupe(blocks_b, sigs_b)

    # --- 2. Patience diff su firme blocchi ---
    def patience_opcodes(a: List[str], b: List[str]) -> List[Tuple[str, int, int, int, int]]:
        def recurse(a_lo, a_hi, b_lo, b_hi, out):
            sub_a = a[a_lo:a_hi]
            sub_b = b[b_lo:b_hi]
            if not sub_a and not sub_b:
                return
            if not sub_a:
                out.append(("insert", a_lo, a_lo, b_lo, b_hi))
                return
            if not sub_b:
                out.append(("delete", a_lo, a_hi, b_lo, b_lo))
                return
            counts_a = {}
            counts_b = {}
            for x in sub_a: counts_a[x] = counts_a.get(x, 0) + 1
            for x in sub_b: counts_b[x] = counts_b.get(x, 0) + 1
            uniques = {}
            for idx, x in enumerate(sub_a):
                if counts_a[x] == 1 and counts_b.get(x) == 1:
                    uniques[x] = [idx, None]
            for idx, x in enumerate(sub_b):
                if counts_b[x] == 1 and counts_a.get(x) == 1 and x in uniques:
                    uniques[x][1] = idx
            anchors = sorted([v for v in uniques.values() if v[1] is not None], key=lambda p: p[0])
            from bisect import bisect_left
            seq = []
            pred = []
            for i, (ai, bi) in enumerate(anchors):
                pos = bisect_left([anchors[j][1] for j in seq], bi)
                if pos == len(seq): seq.append(i)
                else: seq[pos] = i
                pred.append(seq[pos-1] if pos > 0 else None)
            lis = []
            if seq:
                k = seq[-1]
                while k is not None:
                    lis.append(anchors[k]); k = pred[k]
                lis.reverse()
            if not lis:
                matcher = difflib.SequenceMatcher(None, sub_a, sub_b, autojunk=True)
                for t, i1, i2, j1, j2 in matcher.get_opcodes():
                    out.append((t, a_lo + i1, a_lo + i2, b_lo + j1, b_lo + j2))
                return
            prev_a = a_lo; prev_b = b_lo
            for ai, bi in lis:
                ai += a_lo; bi += b_lo
                recurse(prev_a, ai, prev_b, bi, out)
                out.append(("equal", ai, ai+1, bi, bi+1))
                prev_a = ai + 1; prev_b = bi + 1
            recurse(prev_a, a_hi, prev_b, b_hi, out)
        opcodes: List[Tuple[str,int,int,int,int]] = []
        recurse(0, len(a), 0, len(b), opcodes)
        return opcodes

    block_opcodes = patience_opcodes(sigs_a, sigs_b)

    # --- 3. Diff token su blocchi ---
    TOKEN_RE = re.compile(r'(</?[^>]+>)|(&[a-zA-Z#0-9]+;)|([\w\-]+)|([^\w\s])|(\s+)', re.UNICODE)

    def html_tokenize(fragment: str) -> List[str]:
        return [t for t in (m.group(0) for m in TOKEN_RE.finditer(fragment)) if t]

    def patience_tokens(a_tokens: List[str], b_tokens: List[str]) -> List[Tuple[str,int,int,int,int]]:
        if len(a_tokens) + len(b_tokens) < 200:
            matcher = difflib.SequenceMatcher(None, a_tokens, b_tokens, autojunk=False)
            return matcher.get_opcodes()
        return patience_opcodes(a_tokens, b_tokens)

    def highlight_tokens(a_tokens: List[str], b_tokens: List[str]) -> Tuple[str,str]:
        opcodes = patience_tokens(a_tokens, b_tokens)
        out_a = []
        out_b = []
        TAG_RE = re.compile(r'^</?[^>]+>$')
        def mark_tag(token: str, status: str) -> str:
            if token.startswith('</'):
                return token
            if 'data-diff-tag=' in token:
                return token
            return re.sub(r'>$' , f' data-diff-tag="{status}">', token)
        def mark_word(token: str, cls: str) -> str:
            if token.isspace(): return token
            return f'<span class="{cls}">{html.escape(token)}</span>'
        for t, i1, i2, j1, j2 in opcodes:
            if t == 'equal':
                out_a.extend(a_tokens[i1:i2]); out_b.extend(b_tokens[j1:j2])
            elif t == 'delete':
                for tok in a_tokens[i1:i2]:
                    out_a.append(mark_tag(tok,'removed') if TAG_RE.match(tok) else mark_word(tok,'word-removed'))
            elif t == 'insert':
                for tok in b_tokens[j1:j2]:
                    out_b.append(mark_tag(tok,'added') if TAG_RE.match(tok) else mark_word(tok,'word-added'))
            elif t == 'replace':
                for tok in a_tokens[i1:i2]:
                    out_a.append(mark_tag(tok,'removed') if TAG_RE.match(tok) else mark_word(tok,'word-removed'))
                for tok in b_tokens[j1:j2]:
                    out_b.append(mark_tag(tok,'added') if TAG_RE.match(tok) else mark_word(tok,'word-added'))
        return ''.join(out_a), ''.join(out_b)

    def serialize_block(block: Tag) -> str:
        return str(block)

    rendered_a_parts: List[str] = []
    rendered_b_parts: List[str] = []

    for tag, a1, a2, b1, b2 in block_opcodes:
        if tag == 'equal':
            # Blocchi identici -> append raw
            for i, j in zip(range(a1, a2), range(b1, b2)):
                raw = serialize_block(blocks_a[i])
                rendered_a_parts.append(raw)
                rendered_b_parts.append(raw)  # identico
        else:
            # Concatena i blocchi della regione e diff token
            frag_a = ''.join(serialize_block(b) for b in blocks_a[a1:a2])
            frag_b = ''.join(serialize_block(b) for b in blocks_b[b1:b2])
            tokens_a = html_tokenize(frag_a)
            tokens_b = html_tokenize(frag_b)
            ha, hb = highlight_tokens(tokens_a, tokens_b)
            rendered_a_parts.append(ha)
            rendered_b_parts.append(hb)

    res_a = ''.join(rendered_a_parts)
    res_b = ''.join(rendered_b_parts)

    # Se identici dopo elaborazione, ritorna originali sanificati
    if normalize_html_for_comparison(res_a) == normalize_html_for_comparison(res_b):
        return str(soup_a), str(soup_b)
    return res_a, res_b

# --- NEW: JetBrains-style line diff generation ---

def _html_to_logical_lines(raw: str) -> List[str]:
    """Produce una lista di linee logiche dall'HTML per un diff più leggibile.
    Inserisce a capo attorno a principali tag di blocco per simulare un pretty print stabile.
    """
    # Rimuovi script/style per la vista lineare
    cleaned = re.sub(r"<\/(?:script|style)>", "", re.sub(r"<(script|style)[^>]*>.*?<\/\1>", "", raw, flags=re.DOTALL|re.IGNORECASE))
    # Inserisci newline prima e dopo i tag block
    BLOCK_TAG_PATTERN = r'(</?(?:p|div|li|ul|ol|h[1-6]|blockquote|section|article|table|thead|tbody|tr|td|th)\b[^>]*>)'
    cleaned = re.sub(BLOCK_TAG_PATTERN, r"\n\1\n", cleaned, flags=re.IGNORECASE)
    # Comprimi whitespace e split
    lines = [l.rstrip() for l in cleaned.splitlines()]
    # Rimuovi leading/trailing vuote multiple mantenendone una sola consecutiva
    normalized = []
    blank = False
    for l in lines:
        if not l.strip():
            if not blank:
                normalized.append("")
            blank = True
        else:
            normalized.append(l)
            blank = False
    # Trim estremi vuoti
    while normalized and normalized[0] == "":
        normalized.pop(0)
    while normalized and normalized[-1] == "":
        normalized.pop()
    return normalized


def _intraline_highlight(a: str, b: str) -> Tuple[str, str]:
    import html as _html
    if a == b:
        return _html.escape(a), _html.escape(b)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out_a: List[str] = []
    out_b: List[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        sa = _html.escape(a[i1:i2])
        sb = _html.escape(b[j1:j2])
        if tag == 'equal':
            out_a.append(sa)
            out_b.append(sb)
        elif tag == 'delete':
            if sa:
                out_a.append(f'<span class="intraline-removed">{sa}</span>')
        elif tag == 'insert':
            if sb:
                out_b.append(f'<span class="intraline-added">{sb}</span>')
        elif tag == 'replace':
            if sa:
                out_a.append(f'<span class="intraline-removed">{sa}</span>')
            if sb:
                out_b.append(f'<span class="intraline-added">{sb}</span>')
    return ''.join(out_a), ''.join(out_b)


def generate_line_diff_table(html_a: str, html_b: str) -> str:
    """Genera una tabella HTML stile JetBrains (line-based) con evidenziazione intra-linea."""
    import html as _html
    lines_a = _html_to_logical_lines(html_a)
    lines_b = _html_to_logical_lines(html_b)
    sm = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=True)
    rows: List[str] = []
    la_num = 0
    lb_num = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for off in range(i2 - i1):
                la = lines_a[i1 + off]
                lb = lines_b[j1 + off]
                la_num += 1
                lb_num += 1
                esc = _html.escape(la)
                rows.append(f'<tr class="diff-equal"><td class="line-num">{la_num}</td><td class="code">{esc}</td><td class="line-num">{lb_num}</td><td class="code">{esc}</td></tr>')
        elif tag == 'delete':
            for off in range(i1, i2):
                la = lines_a[off]
                la_num += 1
                esc = _html.escape(la)
                rows.append(f'<tr class="diff-removed"><td class="line-num">{la_num}</td><td class="code">{esc}</td><td class="line-num"></td><td class="code"></td></tr>')
        elif tag == 'insert':
            for off in range(j1, j2):
                lb = lines_b[off]
                lb_num += 1
                esc = _html.escape(lb)
                rows.append(f'<tr class="diff-added"><td class="line-num"></td><td class="code"></td><td class="line-num">{lb_num}</td><td class="code">{esc}</td></tr>')
        elif tag == 'replace':
            seg_a = lines_a[i1:i2]
            seg_b = lines_b[j1:j2]
            if len(seg_a) == len(seg_b):
                for a_line, b_line in zip(seg_a, seg_b):
                    la_num += 1
                    lb_num += 1
                    ha, hb = _intraline_highlight(a_line, b_line)
                    rows.append(f'<tr class="diff-changed"><td class="line-num">{la_num}</td><td class="code">{ha}</td><td class="line-num">{lb_num}</td><td class="code">{hb}</td></tr>')
            else:
                for a_line in seg_a:
                    la_num += 1
                    esc = _html.escape(a_line)
                    rows.append(f'<tr class="diff-removed"><td class="line-num">{la_num}</td><td class="code">{esc}</td><td class="line-num"></td><td class="code"></td></tr>')
                for b_line in seg_b:
                    lb_num += 1
                    esc = _html.escape(b_line)
                    rows.append(f'<tr class="diff-added"><td class="line-num"></td><td class="code"></td><td class="line-num">{lb_num}</td><td class="code">{esc}</td></tr>')
    table = [
        '<div class="line-diff-wrapper">',
        '<div class="line-diff-title">🔀 Diff linee (stile IDE)</div>',
        '<table class="line-diff-table" spellcheck="false">',
        '<thead><tr><th class="line-num">A</th><th>Contenuto A</th><th class="line-num">B</th><th>Contenuto B</th></tr></thead>',
        '<tbody>',
        *rows,
        '</tbody></table></div>'
    ]
    return '\n'.join(table)


def create_sentence_level_diff(text_a: str, text_b: str) -> Tuple[str, str]:
    """Crea un diff a livello di frase per un confronto ancora più granulare"""
    import html
    import re

    # Divide il testo in frasi
    sentences_a = re.split(r'[.!?]+\s+', text_a.strip())
    sentences_b = re.split(r'[.!?]+\s+', text_b.strip())

    # Pulisci le frasi vuote
    sentences_a = [s.strip() for s in sentences_a if s.strip()]
    sentences_b = [s.strip() for s in sentences_b if s.strip()]

    matcher = difflib.SequenceMatcher(None, sentences_a, sentences_b)

    highlighted_a = []
    highlighted_b = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            # Frasi identiche
            for i in range(i1, i2):
                if i < len(sentences_a):
                    sentence = html.escape(sentences_a[i])
                    highlighted_a.append(f'<span class="sentence unchanged">{sentence}.</span>')
                    highlighted_b.append(f'<span class="sentence unchanged">{sentence}.</span>')

        elif tag == 'replace':
            # Frasi modificate
            for i in range(i1, i2):
                if i < len(sentences_a):
                    sentence = html.escape(sentences_a[i])
                    highlighted_a.append(f'<span class="sentence removed">{sentence}.</span>')

            for j in range(j1, j2):
                if j < len(sentences_b):
                    sentence = html.escape(sentences_b[j])
                    highlighted_b.append(f'<span class="sentence added">{sentence}.</span>')

        elif tag == 'delete':
            # Frasi rimosse
            for i in range(i1, i2):
                if i < len(sentences_a):
                    sentence = html.escape(sentences_a[i])
                    highlighted_a.append(f'<span class="sentence removed">{sentence}.</span>')

        elif tag == 'insert':
            # Frasi aggiunte
            for j in range(j1, j2):
                if j < len(sentences_b):
                    sentence = html.escape(sentences_b[j])
                    highlighted_b.append(f'<span class="sentence added">{sentence}.</span>')

    return ' '.join(highlighted_a), ' '.join(highlighted_b)


def normalize_html_for_comparison(html_content: str) -> str:
    """Normalizza l'HTML per ridurre i falsi positivi nel diff.
    - Rimuove script/style
    - Ordina gli attributi
    - Comprimi whitespace
    - Normalizza entità HTML spaziando uniformemente
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all():
        if tag.attrs:
            ordered = {}
            for k in sorted(tag.attrs.keys()):
                ordered[k] = tag.attrs[k]
            tag.attrs = ordered
    # Serializza e normalizza whitespace (ma preserva un singolo spazio dove necessario)
    text = str(soup)
    # Sostituisci sequenze di spazi / newline con uno spazio
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def compare_text_only(html_a: str, html_b: str) -> Tuple[str, str]:
    """Diff solo testuale: ignora cambi di tag, evidenzia parole aggiunte/rimosse/modificate."""
    import html as _html
    TOK = re.compile(r"\w+|[^\w\s]")
    def extract_text(h: str) -> str:
        soup = BeautifulSoup(h, 'html.parser')
        for t in soup(["script", "style"]):
            t.decompose()
        txt = soup.get_text(" ")
        return re.sub(r"\s+", " ", txt).strip()
    ta = extract_text(html_a)
    tb = extract_text(html_b)
    tokens_a = TOK.findall(ta)
    tokens_b = TOK.findall(tb)
    sm = difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
    out_a: List[str] = []
    out_b: List[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for tok in tokens_a[i1:i2]:
                out_a.append(_html.escape(tok))
                out_b.append(_html.escape(tok))
        elif tag == 'delete':
            for tok in tokens_a[i1:i2]:
                out_a.append(f'<span class="word-removed">{_html.escape(tok)}</span>')
        elif tag == 'insert':
            for tok in tokens_b[j1:j2]:
                out_b.append(f'<span class="word-added">{_html.escape(tok)}</span>')
        elif tag == 'replace':
            for tok in tokens_a[i1:i2]:
                out_a.append(f'<span class="word-removed">{_html.escape(tok)}</span>')
            for tok in tokens_b[j1:j2]:
                out_b.append(f'<span class="word-added">{_html.escape(tok)}</span>')
    def join(parts: List[str]) -> str:
        # Inserisci spazio tra due token alfanumerici consecutivi (grezzo ma efficace)
        result: List[str] = []
        prev_alnum = False
        for frag in parts:
            # Rimuove tag span per controllo alfanumerico rapido
            plain = re.sub(r'<span[^>]*>|</span>', '', frag)
            is_alnum = bool(re.match(r'^[A-Za-z0-9]+$', plain))
            if result and prev_alnum and is_alnum:
                result.append(' ')
            result.append(frag)
            prev_alnum = is_alnum
        return ''.join(result)
    return join(out_a), join(out_b)


def _rewrite_image_sources(html_fragment: str, pub_id: str, root_out_dir: str, embed: bool) -> str:
    """Riscrive gli attributi src delle immagini per puntare alla directory assets estratte.
    embed=True => prefisso '<pub_id>/assets/' (usato dentro side_by_side.html root)
    embed=False => prefisso 'assets/' (usato dentro i file individuali della pubblicazione)
    Mantiene gli altri attributi intatti.
    """
    try:
        soup = BeautifulSoup(html_fragment, 'html.parser')
    except Exception:
        return html_fragment
    assets_dir = os.path.join(root_out_dir, pub_id, 'assets')
    if not os.path.isdir(assets_dir):
        return html_fragment
    for img in soup.find_all('img'):
        src = img.get('src')
        if not src:
            continue
        # Ignora URL assoluti o data URI
        if re.match(r'^(?:https?:)?//', src) or src.startswith('data:') or src.startswith('mailto:'):
            continue
        basename = os.path.basename(src)
        if not basename:
            continue
        cand = os.path.join(assets_dir, basename)
        if os.path.exists(cand):
            new_src = f"{pub_id}/assets/{basename}" if embed else f"assets/{basename}"
            img['src'] = new_src
    return str(soup)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two .jwpub files and show differences")
    parser.add_argument("jwpub_a", help="Path to first .jwpub (A)")
    parser.add_argument("jwpub_b", help="Path to second .jwpub (B)")
    # Default ./output
    parser.add_argument("--html-dir", help="Write an HTML diff report to this directory (default: ./output)", default="./output")
    parser.add_argument("--side-by-side", action="store_true", help="Generate side-by-side HTML comparison (default on)")
    parser.add_argument("--documents-only", action="store_true", help="Compare only Document tables (not Extract)")
    parser.add_argument("--no-html", action="store_true", help="Do not generate HTML report (override default)")
    parser.add_argument("--text-only", action="store_true", help="Mostra solo differenze testuali (ignora i cambi di markup)")
    parser.add_argument("--show-tag-boxes", action="store_true", help="Mostra riquadri di evidenziazione per cambi nei tag (di default solo testo)")
    args = parser.parse_args(argv)

    # NEW: leggi nomi manifest
    manifest_name_a = extractor.read_manifest_name(args.jwpub_a)
    manifest_name_b = extractor.read_manifest_name(args.jwpub_b)

    contents_a, id_a = load_contents(args.jwpub_a, args.documents_only)
    contents_b, id_b = load_contents(args.jwpub_b, args.documents_only)

    year_a = extract_year_from_id(id_a)
    year_b = extract_year_from_id(id_b)

    keys_a = set(contents_a.keys())
    keys_b = set(contents_b.keys())
    only_a = sorted(list(keys_a - keys_b))
    only_b = sorted(list(keys_b - keys_a))
    common = keys_a & keys_b
    changed = []
    for k in common:
        if normalize_html_for_comparison(contents_a[k]) != normalize_html_for_comparison(contents_b[k]):
            changed.append(k)
    changed.sort()

    if not args.no_html:
        # Usa il nuovo estrattore per generare cartelle e ottenere contenuti
        pub_id_a, contents_a = extractor.extract_publication_to_dir(args.jwpub_a, args.html_dir, args.documents_only)
        pub_id_b, contents_b = extractor.extract_publication_to_dir(args.jwpub_b, args.html_dir, args.documents_only)
        id_a = pub_id_a
        id_b = pub_id_b
        write_html_report(
            args.html_dir,
            id_a,
            id_b,
            only_a,
            only_b,
            changed,
            contents_a,
            contents_b,
            True,
            text_only=args.text_only,
            pub_name=manifest_name_a or manifest_name_b,
            year_a=year_a,
            year_b=year_b,
            show_tag_boxes=args.show_tag_boxes,
        )
        print(f"Report HTML side-by-side scritto in {os.path.abspath(args.html_dir)}/side_by_side.html")

    print(f"Pubblicazione: {manifest_name_a or manifest_name_b}")
    print(f"Anni: {year_a} -> {year_b}")
    print(f"Only in {year_a}: {len(only_a)}")
    print(f"Only in {year_b}: {len(only_b)}")
    print(f"Changed: {len(changed)}")
    for k in changed[:20]:
        name = f"{k[0]}:{k[1]}"
        print(f"\n=== Changed: {name} ===")
        diff = difflib.unified_diff(
            contents_a[k].splitlines(), contents_b[k].splitlines(),
            fromfile=f"{year_a} {name}", tofile=f"{year_b} {name}", lineterm=""
        )
        for line in diff:
            print(line)
    if len(changed) > 20:
        print(f"\n... altri {len(changed)-20} diff omessi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
