# JWPUB Format - Complete Reference

## 1. File Structure

```
publication.jwpub (ZIP)
├── manifest.json
└── contents (ZIP)
    ├── symbol.db (SQLite, encrypted content)
    └── media files (jpg, png, pdf...)
```

## 2. How to Create a .jwpub

### Input folder structure
```
my-publication/
├── Page1.html          → becomes document "Page1" in TOC
├── Page1/              → media subfolder (exact same name, no .html)
│   └── image.jpg
├── Page2.html          → becomes document "Page2" in TOC
└── Page2/
    └── photo.png
```

### Run the tool
```bash
printf 'folder_path\nMepsLanguageIndex\nSymbol\nYear\nTitle\n' | ./html2jwpub
```

### Parameters
- **folder_path**: absolute path to the HTML folder
- **MepsLanguageIndex**: 4 (Italian), 0 (English), 1 (Spanish), etc.
- **Symbol**: short identifier (e.g., "legcol", "GPW")
- **Year**: publication year (affects encryption key!)
- **Title**: display title in JW Library

### Important notes
- Files are processed in `.sorted()` order (alphabetical) - we added this fix
- Each .html file = one document/page in the publication TOC
- The filename (minus .html) becomes the page title in the TOC
- Media subfolder must match the HTML filename exactly (no extension)

## 3. Encryption

### Algorithm
1. Build string: `{MepsLanguageIndex}_{Symbol}_{Year}` (+ `_{IssueTagNumber}` if non-zero)
2. SHA-256 hash of that string
3. XOR with constant: `11cbb5587e32846d4c26790c633da289f66fe5842a3a585ce1bc3a294af5ada7`
4. First 16 bytes = AES-128-CBC key, Last 16 bytes = IV
5. Content is zlib-compressed then AES encrypted

### Decryption (for inspecting existing .jwpub)
Temporarily replace main.swift with decrypt code, build, run, restore. See conversation history for exact code.

## 4. HTML Format for JW Library

### Required structure
```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>/* CSS here */</style>
</head>
<body>
    <p id="p1" data-pid="1">Text here</p>
</body>
</html>
```

### Key patterns
- **Paragraph IDs**: `<p id="p1" data-pid="1">` - sequential numbering, needed for highlighting/annotations
- **Bible links**: `<a href="jwpub://b/NWTR/book:chapter:verse" class="b">Display text</a>`
- **Bible link format**: `jwpub://b/NWTR/{bookNum}:{chapter}:{verse}` or range `{bookNum}:{ch}:{v1}-{bookNum}:{ch}:{v2}`
- **Supported HTML tags**: a, br, div, em, h1, h2, h3, input, label, li, ol, p, small, span, strong, style, svg, tt, ul

### Bible book numbers (NWTR scheme)
```
1=Gen 2=Eso 3=Lev 4=Num 5=Deu 6=Gio 7=Gdc 8=Rut
9=1Sa 10=2Sa 11=1Re 12=2Re 13=1Cr 14=2Cr 15=Esd 16=Nee
17=Est 18=Giobbe 19=Sal 20=Prov 21=Ecc 22=Cant 23=Isa 24=Ger
25=Lam 26=Eze 27=Dan 28=Osea 29=Gioele 30=Amos 31=Abd 32=Giona
33=Mic 34=Naum 35=Abac 36=Sof 37=Agg 38=Zac 39=Mal
40=Matt 41=Marco 42=Luca 43=Giov 44=Atti 45=Rom 46=1Cor 47=2Cor
48=Gal 49=Efe 50=Fil 51=Col 52=1Tess 53=2Tess 54=1Tim 55=2Tim
56=Tito 57=Filem 58=Ebr 59=Giac 60=1Pie 61=2Pie 62=1Giov
63=2Giov 64=3Giov 65=Giuda 66=Riv
```

## 5. Design Utilities (du-*) CSS Classes

These are built into the JW Library renderer. They work without defining them in your `<style>`.

### Text colors (du-color--)
```
blue:    100, 200, 300, 400, 500, 600, 700
coral:   200, 300, 400, 500, 700
green:   200, 300, 400, 500, 600
gray:    50, 100, 400, 500
maroon:  600, 800
indigo:  200, 300, 400, 500
gold:    600
pink:    600, 700
Special: coolGray-700, textSubdued, white
```

### Background colors (du-bgColor--)
```
blue:      100, 200, 300, 400, 500, 600
coral:     200, 300, 400
green:     300, 400, 500
indigo:    200, 300, 400
gold:      400
turquoise: 400
```

### Border colors (du-borderColor--)
```
blue: 600
```

### Font sizes (du-fontSize--)
```
baseMinus3, baseMinus2, baseMinus1, base, basePlus1
```

### Text align (du-textAlign--)
```
center
```

### Color hex values (from CSS variables in publications)
```css
--du-color--blue-100: #c2d4f1    --du-color--blue-500: #4a6da7
--du-color--blue-200: #9fb9e3    --du-color--blue-600: #345996
--du-color--blue-300: #7f9fd3    --du-color--blue-700: #224680
--du-color--blue-400: #6385bf
--du-color--green-200: #b7e492   --du-color--green-500: #66a333
--du-color--green-300: #9cd36e   --du-color--green-600: #54941f
--du-color--green-400: #81bd4f
--du-color--coral-200: #f6b293   --du-color--coral-500: #c3673c
--du-color--coral-300: #ea9771
--du-color--coral-400: #d97e54
--du-color--maroon-600: #942926  --du-color--maroon-800: #680c0a
--du-color--gray-50:  #f1f1f1    --du-color--gray-500:  #757575
--du-color--gray-100: #d8d8d8
```

## 6. Advanced HTML Patterns (from real publications)

### Expandable sections (accordion)
```html
<div class="category du-bgColor--blue-100">
    <input type="checkbox" id="chkbx1">
    <label for="chkbx1"><p class="du-fontSize--basePlus1">Title</p></label>
    <div class="subcategory"><ul>
        <div class="item">
            <input type="checkbox" id="chkbx2">
            <label for="chkbx2"><li><p>Item title</p></li></label>
            <div class="details du-fontSize--base du-bgColor--blue-200">
                <p id="p1" data-pid="1">Content here</p>
            </div>
        </div>
    </ul></div>
</div>
```

### Tooltips
```html
<div class="bar" style="border-color: #color; background-color: #color">
    <p>Label</p>
    <div class="tooltip">
        <p>Tooltip content</p>
    </div>
</div>
```

### Collapsible groups
```html
<div class="group">
    <input type="checkbox" id="grp1">
    <label for="grp1">Group title</label>
    <div class="subgroup">Content</div>
</div>
```

### Credits page style
```html
<div style="text-align: center; padding: 20px">
    <h1 id="p1" data-pid="1" style="color: #555">Title</h1>
    <h3 id="p2" data-pid="2" style="color: #555">Author</h3>
    <div style="color: #757575">
        <p id="p3" data-pid="3">Description</p>
        <strong><tt>Disclaimer text</tt></strong>
    </div>
</div>
```

## 7. Database Schema (key tables)

- **Publication**: metadata (title, symbol, year, language)
- **Document**: one row per HTML page (DocumentId, Title, Content=encrypted BLOB, ContentLength)
- **PublicationViewItem**: TOC structure (ParentPublicationViewItemId for hierarchy)
- **Multimedia** + **DocumentMultimedia**: media files linked to documents
- **TextUnit**: maps documents for search
- Hardcoded: ParagraphCount=254, PublicationType="Manual/Guidelines", MepsBuildNumber=12345

## 8. Manifest.json Structure
```json
{
    "name": "symbol.jwpub",
    "hash": "SHA256 of contents file",
    "timestamp": "ISO8601",
    "version": 1,
    "expandedSize": 12345,
    "contentFormat": "z-a",
    "htmlValidated": false,
    "mepsPlatformVersion": 2.1,
    "mepsBuildNumber": 12345,
    "publication": {
        "fileName": "symbol.db",
        "type": 1,
        "title": "...",
        "symbol": "...",
        "year": 2026,
        "language": 4,
        "hash": "SHA1 of .db file",
        "timestamp": "ISO8601",
        "publicationType": "Manual/Guidelines",
        "categories": ["manual"]
    }
}
```

## 9. Tab Navigation (multi-section publications)

JW Library supporta la navigazione a tab (come in Emerge Alive). I tab sono definiti nella tabella `PublicationViewItem`.

### Come funzionano i tab

I tab sono item con `ParentPublicationViewItemId = -1` (root level). Ogni tab contiene sotto-item.

```
PublicationViewItem:
Id  Parent  Title              DocId
--  ------  -----------------  -----
1   -1      TAB 1 TITLE        -1      ← Tab 1 (nodo strutturale, no documento)
2   1       Pagina intro       0       ← Documento figlio del tab 1
3   1       Sotto-sezione      -1      ← Sotto-gruppo (nodo strutturale)
4   3       Pagina A           1       ← Documento figlio del sotto-gruppo
5   3       Pagina B           2
12  -1      TAB 2 TITLE        -1      ← Tab 2
13  12      Pagina intro       3
14  12      Sotto-gruppo       -1
15  14      Pagina C           4
```

### Regole
- `Parent = -1` → crea un **tab** nella barra di navigazione
- `DocId = -1` → nodo strutturale (gruppo/sotto-gruppo, non punta a un documento)
- `DocId >= 0` → punta a un documento nella tabella Document
- I sotto-gruppi sono nodi con `Parent = id_del_tab` e `DocId = -1`
- I documenti figli puntano al sotto-gruppo tramite il loro Parent

### Esempio reale: Emerge Alive (3 tab)
```
PREPARAZIONE (Parent=-1)  →  9 documenti figli
PIANO (Parent=-1)         →  2 sotto-gruppi ("Disastri naturali", "Pericoli causati dall'uomo")
                              ciascuno con documenti figli
EVACUAZIONE (Parent=-1)   →  10 documenti figli
```

### Supporto nel tool html2jwpub
Il tool attuale **non supporta** i tab. Crea una struttura piatta (un solo root, tutti i documenti come figli diretti). Per i tab serve modificare `jwpubCreator.swift` per creare multiple root PublicationViewItem con `Parent = -1`.

### Pattern HTML aggiuntivi scoperti da Emerge Alive

#### Link interni tra documenti
```html
<a class="it" href="jwpub://p/I:46500105/">ZAINO DI EMERGENZA</a>
```
Formato: `jwpub://p/{lang}:{MepsDocumentId}/`

#### Checkbox interattive
```html
<li class="gen-field">
    <input type="checkbox" id="ch1" name="ch-1">
    <p id="p3" data-pid="3" class="high">Item text</p>
    <textarea id="tx1" name="tx-1" class="txt"></textarea>
</li>
```

#### Textarea per note
```html
<textarea id="tx1" name="tx-1" class="txt"></textarea>
```

#### Classi di priorità (custom CSS)
```css
.high   { font-size: 110%; font-weight: bold; color: #3f3c6d }  /* Essenziale */
.medium { color: #504d7c }                                       /* Raccomandato */
.low    { font-size: 80%; color: #6c6997 }                       /* Facoltativo */
```

#### Nuovi colori scoperti
```
du-color--orange-500: #e96d00
du-color--coolGray-500: #597477
du-color--indigo-800: #211e49
du-color--indigo-700: #2f2c5c
du-color--indigo-600: #3f3c6d
```

## 10. Tips & Gotchas

- **Year affects encryption**: changing the year changes the encryption key, so existing highlights/notes would break if you reimport with a different year
- **Symbol must be unique**: JW Library uses symbol to identify publications; reimporting same symbol replaces the previous version
- **CSS inline is safest**: du-* classes work but are undocumented; inline styles always work
- **h2 tag not in discovered tags**: only h1 and h3 were found in real publications, but h2 works fine with inline CSS
- **No h1 in real pubs**: real publications use `<p class="du-fontSize--basePlus1">` for headers inside content, h1/h3 only for page-level titles
- **data-pid is important**: paragraphs with `data-pid` attribute can be highlighted and annotated in JW Library
