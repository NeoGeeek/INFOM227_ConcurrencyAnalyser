from __future__ import annotations
from dataclasses import dataclass
from typing import List
import re


KEYWORDS = {
    "function", "if", "else", "while", "return",
    "spawn", "await",
    "True", "False", "and", "or",
}

_TOKEN_RE = re.compile(r"""
    (?P<WS>[ \t]+)|
    (?P<NL>\n)|
    (?P<COMMENT>//[^\n]*)|
    (?P<NUM>\d+)|
    (?P<ID>[A-Za-z_][A-Za-z0-9_]*)|
    (?P<OP>==|!=|>=|<=|[+\-*/<>])|
    (?P<SYM>[(){};,=])
""", re.VERBOSE)

@dataclass(frozen=True)
class Token:
    kind: str   # KW, ID, NUM, OP, SYM, EOF
    value: str
    line: int
    col: int

class LexerError(Exception):
    pass

def lex(src: str) -> List[Token]:
    toks: List[Token] = []
    pos = 0
    line = 1
    col = 1
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            snippet = src[pos:pos+20]
            raise LexerError(f"Unexpected character at line {line} col {col}: {snippet!r}")
        kind = m.lastgroup
        text = m.group(kind)

        if kind in ("WS", "COMMENT"):
            pos = m.end()
            col += len(text)
            continue
        if kind == "NL":
            pos = m.end()
            line += 1
            col = 1
            continue

        if kind == "ID":
            k = "KW" if text in KEYWORDS else "ID"
            toks.append(Token(k, text, line, col))
        elif kind == "NUM":
            toks.append(Token("NUM", text, line, col))
        elif kind == "OP":
            toks.append(Token("OP", text, line, col))
        elif kind == "SYM":
            toks.append(Token("SYM", text, line, col))
        else:
            raise LexerError("Internal lexer error")

        pos = m.end()
        col += len(text)

    toks.append(Token("EOF", "", line, col))
    return toks