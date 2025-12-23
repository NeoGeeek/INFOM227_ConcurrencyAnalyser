from __future__ import annotations

from src.abstract_syntax_tree import *


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
        raise ValueError(f"spawn/await not allowed inside if/while (line {stmt.line})")

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


def list_spawns_awaits(stmt: Stmt, spawns=None, awaits=None):
    """
    Parcourt récursivement un statement et liste tous les spawn et await qu'il contient.

    :param stmt: statement à analyser (Seq, Spawn, Await, etc.)
    :param spawns: liste accumulant les objets Spawn trouvés
    :param awaits: liste accumulant les objets Await trouvés
    :return: tuple (liste des spawns, liste des awaits)
    """

    # Initialisation des listes si elles n'ont pas été fournies
    if spawns is None:
        spawns = []
    if awaits is None:
        awaits = []

    # Si le statement est un spawn, on l'ajoute à la liste des spawns
    if isinstance(stmt, Spawn):
        spawns.append(stmt)

    # Si le statement est un await, on l'ajoute à la liste des awaits
    elif isinstance(stmt, Await):
        awaits.append(stmt)

    # Si le statement est une séquence, on parcourt récursivement chaque statement contenu
    elif isinstance(stmt, Seq):
        for s in stmt.stmts:
            list_spawns_awaits(s, spawns, awaits)

    # Retourne les deux listes accumulées
    return spawns, awaits
