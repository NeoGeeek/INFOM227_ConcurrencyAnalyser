from abstract_syntax_tree import *

def enforce_no_spawn_await_in_if_while(stmt: Stmt, inside_control: bool = False) -> None:
    """
    Vérifie récursivement qu'aucun 'spawn' ou 'await' n'apparaît
    à l'intérieur d'un 'if' ou d'un 'while'.

    :param stmt: statement à analyser
    :param inside_control: True si l'on est actuellement dans un if ou un while
    """

    # Si on rencontre un spawn ou un await alors qu'on est déjà
    # à l'intérieur d'un if ou d'un while
    if isinstance(stmt, (Spawn, Await)) and inside_control:
        raise ValueError(
            f"spawn/await not allowed inside if/while (line {stmt.line})"
        )

    # Analyse récursives pour les séquences ({ ... })
    if isinstance(stmt, Seq):
        for s in stmt.stmts:
            enforce_no_spawn_await_in_if_while(s, inside_control)

    # Analyse des deux branches pour les if
    elif isinstance(stmt, If):
        enforce_no_spawn_await_in_if_while(stmt.then_s, True)
        enforce_no_spawn_await_in_if_while(stmt.else_s, True)

    # Analyse du corps de la boucle pour les while
    elif isinstance(stmt, While):
        enforce_no_spawn_await_in_if_while(stmt.body, True)
