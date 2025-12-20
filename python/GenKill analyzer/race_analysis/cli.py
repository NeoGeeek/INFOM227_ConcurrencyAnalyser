from __future__ import annotations
import sys
import argparse

from lexer import lex, LexerError
from parser import Parser, ParserError

from .engine import analyze_program
from .formatting import format_warning


def analyze_source(src: str):
    prog = Parser(lex(src)).parse_program()
    return analyze_program(prog)


def main() -> int:
    ap = argparse.ArgumentParser(description="Static race detector for SMALL + spawn/await.")
    ap.add_argument("file", help="Path to a .small source file")
    args = ap.parse_args()

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            src = f.read()
        warnings = analyze_source(src)

        if not warnings:
            print("No race candidates found.")
            return 0

        print(f"{len(warnings)} race candidate(s) found:\n")
        for w in warnings:
            print(format_warning(w))

        return 2

    except (LexerError, ParserError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
