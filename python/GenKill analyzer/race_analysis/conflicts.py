from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from concurrency import ConcurState, ThreadInfo


@dataclass(frozen=True)
class RaceWarning:
    """
    Représente un avertissement de data race détecté.
    
    :param var: variable concernée
    :param kind: type de conflit (ex: "R vs T", "W vs T", "T vs T")
    :param line_a: ligne de l'accès détecté
    :param ctx_a: contexte du premier accès (ex: thread/fonction)
    :param lines_b: lignes des autres accès concurrents
    :param ctx_b: contexte des autres accès concurrents
    """
    var: str
    kind: str
    line_a: int
    ctx_a: str
    lines_b: Tuple[int, ...]
    ctx_b: str


def mode_for(var: str, reads: Set[str], writes: Set[str]) -> Optional[str]:
    """
    Détermine le mode d'accès d'une variable dans un thread.
    
    :param var: variable à vérifier
    :param reads: ensemble des variables lues
    :param writes: ensemble des variables écrites
    :return: "R", "W", "RW" ou None si non utilisée
    """
    r = var in reads
    w = var in writes
    if r and w:
        return "RW"
    if r:
        return "R"
    if w:
        return "W"
    return None


def collect_other_lines(t: ThreadInfo, var: str) -> Set[int]:
    """
    Récupère toutes les lignes où la variable a été accédée dans un thread donné.
    
    :param t: informations sur le thread
    :param var: variable à analyser
    :return: ensemble des lignes concernées
    """
    lines: Set[int] = set()
    if var in t.writes:
        lines |= set(t.write_sites.get(var, set()))
    if var in t.reads:
        lines |= set(t.read_sites.get(var, set()))
    # si aucune ligne spécifique, on ajoute la ligne de spawn du thread
    if not lines:
        lines.add(t.spawn_line)
    return lines


def conflicts(mode: str, t: ThreadInfo, var: str) -> bool:
    """
    Vérifie si un accès à une variable entre en conflit avec un thread existant.
    
    :param mode: mode d'accès ("R", "W", "RW")
    :param t: thread concurrent à vérifier
    :param var: variable concernée
    :return: True si conflit détecté, False sinon
    """
    if mode == "R":
        return var in t.writes
    if mode in ("W", "RW"):
        return (var in t.writes) or (var in t.reads)
    return False


def check_access(state: ConcurState, var: str, mode: str, line: int, ctx: str) -> List[RaceWarning]:
    """
    Vérifie les accès concurrents entre le thread courant et tous les threads actifs.
    
    :param state: état courant des threads
    :param var: variable accédée
    :param mode: mode d'accès du thread courant
    :param line: ligne de l'accès
    :param ctx: contexte du thread courant
    :return: liste de RaceWarning détectées
    """
    out: List[RaceWarning] = []
    for t in state.active.values():
        if conflicts(mode, t, var):
            out.append(RaceWarning(
                var=var,
                kind=f"{mode} vs T",
                line_a=line,
                ctx_a=ctx,
                lines_b=tuple(sorted(collect_other_lines(t, var))),
                ctx_b=f"{t.desc} (spawn line {t.spawn_line})",
            ))
    return out


def check_thread_thread(newt: ThreadInfo, oldt: ThreadInfo, discover_line: int) -> List[RaceWarning]:
    """
    Vérifie les conflits entre deux threads concurrents.
    
    :param newt: thread nouvellement créé
    :param oldt: thread déjà actif
    :param discover_line: ligne où le nouveau thread est découvert
    :return: liste de RaceWarning détectées entre les deux threads
    """
    # Calcul de l'ensemble des variables accédées en conflit (au moins une écriture)
    overlap = (newt.writes & (oldt.reads | oldt.writes)) | (newt.reads & oldt.writes)
    
    out: List[RaceWarning] = []
    for var in sorted(overlap):
        out.append(RaceWarning(
            var=var,
            kind="T vs T",
            line_a=discover_line,
            ctx_a=f"concurrent threads overlap starting at spawn line {discover_line}",
            lines_b=tuple(sorted(collect_other_lines(oldt, var) | collect_other_lines(newt, var))),
            ctx_b=f"{oldt.desc} (spawn {oldt.spawn_line}) || {newt.desc} (spawn {newt.spawn_line})",
        ))
    return out
