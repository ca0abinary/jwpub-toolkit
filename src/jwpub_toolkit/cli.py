"""Unified CLI entry point for jwpub-toolkit."""
from __future__ import annotations

import argparse
import sys


def cmd_create(args: argparse.Namespace) -> int:
    from .creator import create_from_folder
    jwpub_path = create_from_folder(
        folder=args.folder,
        symbol=args.symbol,
        title=args.title,
        year=args.year,
        meps_language_index=args.lang,
    )
    print(f"Created: {jwpub_path}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    if args.html:
        from .extractor import process_jwpub
        return process_jwpub(args.jwpub, args.output_dir)
    else:
        from .extractor import process_jwpub_markdown
        bible_path = getattr(args, 'bible_jwpub', None)
        no_extracts = getattr(args, 'no_extracts', False)
        return process_jwpub_markdown(
            args.jwpub, args.output_dir,
            bible_jwpub_path=bible_path,
            no_extracts=no_extracts,
        )


def cmd_diff(args: argparse.Namespace) -> int:
    from .diff import main as diff_main
    argv = [args.jwpub_a, args.jwpub_b]
    if args.html_dir:
        argv.extend(["--html-dir", args.html_dir])
    if args.documents_only:
        argv.append("--documents-only")
    if args.text_only:
        argv.append("--text-only")
    if args.no_html:
        argv.append("--no-html")
    return diff_main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jwpub-toolkit",
        description="Create, extract and compare JWPUB files",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- create ---
    p_create = subparsers.add_parser("create", help="Create a .jwpub from an HTML folder")
    p_create.add_argument("folder", help="Path to folder containing HTML files")
    p_create.add_argument("--symbol", required=True, help="Publication symbol (e.g. 'legcol')")
    p_create.add_argument("--title", required=True, help="Publication title")
    p_create.add_argument("--year", type=int, required=True, help="Publication year")
    p_create.add_argument("--lang", type=int, required=True, help="MepsLanguageIndex (4=Italian, 0=English)")
    p_create.set_defaults(func=cmd_create)

    # --- extract ---
    p_extract = subparsers.add_parser("extract", help="Extract and decrypt a .jwpub file")
    p_extract.add_argument("jwpub", help="Path to the .jwpub file")
    p_extract.add_argument("--output-dir", required=True, help="Output directory for extracted files")
    p_extract.add_argument("--html", action="store_true", help="Output raw HTML instead of Markdown")
    p_extract.add_argument("--bible-jwpub", help="Path to nwtsty_E.jwpub for resolving Bible verse links")
    p_extract.add_argument("--no-extracts", action="store_true", help="Exclude extracts for cleaner output")
    p_extract.set_defaults(func=cmd_extract)

    # --- diff ---
    p_diff = subparsers.add_parser("diff", help="Compare two .jwpub files")
    p_diff.add_argument("jwpub_a", help="Path to first .jwpub (A)")
    p_diff.add_argument("jwpub_b", help="Path to second .jwpub (B)")
    p_diff.add_argument("--html-dir", default="./output", help="Directory for HTML diff report (default: ./output)")
    p_diff.add_argument("--documents-only", action="store_true", help="Compare only Document tables")
    p_diff.add_argument("--text-only", action="store_true", help="Show only text differences (ignore markup changes)")
    p_diff.add_argument("--no-html", action="store_true", help="Do not generate HTML report")
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
