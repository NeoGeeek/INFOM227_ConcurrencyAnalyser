from __future__ import annotations

from conflicts import RaceWarning


def format_warning(w: RaceWarning) -> str:
    """
    Formate un avertissement de data race pour affichage humain.

    :param w: RaceWarning à formater
    :return: chaîne de caractères lisible décrivant la race
    """

    b_lines = ", ".join(str(x) for x in w.lines_b) if w.lines_b else "?"

    # Construction du message multi-lignes
    return (
        f"[RACE] var='{w.var}' @ line {w.line_a} ({w.kind})\n"
        f"  A: {w.ctx_a}\n"
        f"  B: lines {{{b_lines}}} in {w.ctx_b}\n"
    )

