from __future__ import annotations
import sys
import argparse
import time

from src.lexer import lex, LexerError
from src.parser import Parser, ParserError

from src.engine import analyze_program
from src.formatting import format_warning


def analyze_source(src: str):
    """
    Analyse le code source SMALL donné en chaîne de caractères.

    :param src: chaîne de caractères contenant le code source
    :return: liste de RaceWarning détectées
    """
    # 1. Lexer : transforme le code source en tokens
    # 2. Parser : construit l'AST (Programme)
    prog = Parser(lex(src)).parse_program()
    # 3. Analyse statique pour détecter les races
    return analyze_program(prog)


def main() -> int:
    """
    Fonction principale du programme, utilisée lorsqu'on exécute le script.
    Retourne un code de sortie :
      0 = pas de race détectée
      1 = erreur de parsing/lexing
      2 = races détectées
    """

    start = int(time.time()*1000)

    # Création du parser de ligne de commande
    ap = argparse.ArgumentParser(description="Static race detector for SMALL + spawn/await.")
    ap.add_argument("file", help="Path to a .small source file")
    args = ap.parse_args()

    try:
        # Lecture du fichier source
        with open(args.file, "r", encoding="utf-8") as f:
            src = f.read()

        # Analyse statique pour détecter les races
        warnings = analyze_source(src)
        end = int(time.time()*1000)
        print("The analysis took", (end - start), "ms.")

        # Aucun avertissement : code 0
        if not warnings:
            print("No race candidates found.")
            return 0

        # Affichage des avertissements
        print(f"{len(warnings)} race candidate(s) found:\n")
        for w in warnings:
            print(format_warning(w))

        # Code de sortie 2 : races détectées
        return 2

    except (LexerError, ParserError, ValueError) as e:
        # Gestion des erreurs de parsing ou de règles sémantiques
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

# Point d'entrée du script
if __name__ == "__main__":
    start = time.time()
    import sys
    sys.exit(main())
    end = time.time()
    length = end - start
    print("It took", length, "seconds!")
