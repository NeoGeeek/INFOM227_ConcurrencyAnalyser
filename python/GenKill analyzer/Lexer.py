from __future__ import annotations
from dataclasses import dataclass
from typing import List
import re


# Ensemble des mots-clés du langage que le lexer doit reconnaître
KEYWORDS = {
    "function", "if", "else", "while", "return",  # Mots-clés de contrôle/structure
    "spawn", "await",                            # Mots-clés liés à la concurrence/asynchronisme
    "True", "False", "and", "or",                # Booléens et opérateurs logiques
}

# Expression régulière principale pour identifier les différents types de tokens
_TOKEN_RE = re.compile(r"""
    (?P<WS>[ \t]+)|                     # Espaces ou tabulations (ignorés)
    (?P<NL>\n)|                         # Nouvelle ligne
    (?P<COMMENT>//[^\n]*)|              # Commentaires commençant par //
    (?P<NUM>\d+)|                       # Nombres (séquence de chiffres)
    (?P<ID>[A-Za-z_][A-Za-z0-9_]*)|     # Identificateurs (lettre ou _ suivie de lettres/chiffres/_)
    (?P<OP>==|!=|>=|<=|[+\-*/<>])|      # Opérateurs (comparaison ou arithmétique)
    (?P<SYM>[(){};,=])                  # Symboles ponctuation/syntaxe
""", re.VERBOSE)

@dataclass(frozen=True)
class Token:
    kind: str   # Type du token : KW, ID, NUM, OP, SYM, EOF
    value: str  # Valeur exacte du token tel qu'il apparaît dans le code source
    line: int   # Numéro de ligne du token
    col: int    # Position du token sur la ligne

class LexerError(Exception):
    pass

# Transforme une chaîne de caractères `src` en une liste de tokens.
def lex(src: str) -> List[Token]:
    toks: List[Token] = []   # Liste des tokens générés
    pos = 0                  # Position actuelle dans la chaîne source
    line = 1                 # Numéro de ligne courant
    col = 1                  # Colonne courante

    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)

        # Si aucun token n'est reconnu
        if not m:
            snippet = src[pos:pos+20]
            raise LexerError(f"Unexpected character at line {line} col {col}: {snippet!r}")

        kind = m.lastgroup
        text = m.group(kind)

        # Ignorer les espaces et commentaires
        if kind in ("WS", "COMMENT"):
            pos = m.end()
            col += len(text)
            continue

        # Gestion des nouvelles lignes
        if kind == "NL":
            pos = m.end()
            line += 1
            col = 1
            continue

        # Identifier les tokens
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

    # Ajouter un token EOF pour signaler la fin du fichier
    toks.append(Token("EOF", "", line, col))
    return toks