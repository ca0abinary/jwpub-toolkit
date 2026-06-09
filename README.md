# jwpub-toolkit

Create, extract and compare JWPUB files for JW Library.

## Features

- **Create** `.jwpub` publications from HTML folders
- **Extract** and decrypt existing `.jwpub` files to Markdown (with embedded extracts as collapsible regions)
- **Resolve Bible verses** from a local `nwtsty_E.jwpub` file — replaces `jwpub://b/NWTR/` links with inline verse text
- **Diff** two `.jwpub` files with a rich HTML side-by-side report

## Installation

```bash
pip install -e .
```

## Usage

### Create a JWPUB from HTML files

```bash
jwpub-toolkit create ./my-publication --symbol mypub --title "My Publication" --year 2026 --lang 4
```

Input folder structure:
```
my-publication/
├── Page1.html          → becomes document "Page1" in TOC
├── Page1/              → media subfolder (same name, no .html)
│   └── image.jpg
├── Page2.html          → becomes document "Page2" in TOC
└── Page2/
    └── photo.png
```

Parameters:
- `--symbol`: Short identifier (e.g., "legcol", "GPW")
- `--title`: Display title in JW Library
- `--year`: Publication year (affects encryption key)
- `--lang`: MepsLanguageIndex (4=Italian, 0=English, 1=Spanish)

### Extract a JWPUB

```bash
jwpub-toolkit extract publication.jwpub --output-dir ./extracted
```

Outputs Markdown by default with extracts embedded as collapsible `<details>` blocks.

Options:
- `--html`: Output raw HTML instead of Markdown
- `--bible-jwpub path/to/nwtsty_E.jwpub`: Resolve `jwpub://b/NWTR/` Bible links to inline verse text

#### Bible verse resolution

If you supply the NWT Study Bible file (`nwtsty_E.jwpub`), all Bible reference links are replaced with the full verse text:

```bash
jwpub-toolkit extract publication.jwpub --output-dir ./extracted --bible-jwpub nwtsty_E.jwpub
```

This handles single verses, multi-verse ranges, cross-chapter ranges, and cross-book ranges.

### Compare two JWPUBs

```bash
jwpub-toolkit diff old.jwpub new.jwpub --html-dir ./output
```

Options:
- `--documents-only`: Compare only Document tables
- `--text-only`: Show only text differences (ignore markup changes)
- `--no-html`: Skip HTML report generation

## Example

An example publication is included in `examples/legenda-colori/`:

```bash
jwpub-toolkit create examples/legenda-colori --symbol legcol --title "Legenda colori" --year 2026 --lang 4
```

## Format Documentation

See [JWPUB_FORMAT.md](JWPUB_FORMAT.md) for the complete JWPUB format reference.

## License

MIT
