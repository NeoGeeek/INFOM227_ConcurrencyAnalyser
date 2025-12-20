from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Set

from src.effects import Effect


@dataclass
class ThreadInfo:
    """
    Informations sur un thread actif pour la détection de data races.

    :param thread_id: identifiant unique du thread
    :param desc: description du thread (ex: fonction spawnée)
    :param spawn_line: ligne où le thread a été créé
    :param reads: ensemble des variables lues par le thread
    :param writes: ensemble des variables écrites par le thread
    :param read_sites: mapping variable -> lignes lues
    :param write_sites: mapping variable -> lignes écrites
    """
    thread_id: str
    desc: str
    spawn_line: int
    reads: Set[str]
    writes: Set[str]
    read_sites: Dict[str, Set[int]]
    write_sites: Dict[str, Set[int]]


def threadinfo_from_effect(eff: Effect, tid: str, desc: str, spawn_line: int) -> ThreadInfo:
    """
    Crée un ThreadInfo à partir d'un effet calculé.

    :param eff: effet à transformer en thread
    :param tid: identifiant du thread
    :param desc: description du thread
    :param spawn_line: ligne de spawn du thread
    :return: objet ThreadInfo correspondant
    """
    return ThreadInfo(
        thread_id=tid,
        desc=desc,
        spawn_line=spawn_line,
        reads=set(eff.reads),
        writes=set(eff.writes),
        read_sites={k: set(v) for k, v in eff.read_sites.items()},
        write_sites={k: set(v) for k, v in eff.write_sites.items()},
    )


@dataclass
class ConcurState:
    """
    État concurrent du programme pour la détection de races.

    :param active: dictionnaire tid -> ThreadInfo des threads actifs
    :param handle_env: mapping handle -> ensemble de tids associés
    """
    active: Dict[str, ThreadInfo] = field(default_factory=dict)
    handle_env: Dict[str, Set[str]] = field(default_factory=dict)

    def copy(self) -> "ConcurState":
        """
        Retourne une copie indépendante de l'état concurrent.

        :return: nouvelle instance de ConcurState identique
        """
        return ConcurState(
            active=dict(self.active),
            handle_env={k: set(v) for k, v in self.handle_env.items()},
        )


def join_states(a: ConcurState, b: ConcurState) -> ConcurState:
    """
    Fusionne deux états concurrents, en combinant les informations des threads et des handles.

    :param a: premier état concurrent
    :param b: deuxième état concurrent
    :return: nouvel état combiné
    """
    out = ConcurState()
    
    # Copier les threads du premier état
    out.active = dict(a.active)

    # Fusionner les threads du deuxième état
    for tid, t in b.active.items():
        if tid in out.active:
            # Thread déjà présent : fusionner lectures, écritures et sites
            o = out.active[tid]
            merged = ThreadInfo(
                thread_id=tid,
                desc=o.desc,
                spawn_line=o.spawn_line,
                reads=set(o.reads) | set(t.reads),
                writes=set(o.writes) | set(t.writes),
                read_sites={k: set(v) for k, v in o.read_sites.items()},
                write_sites={k: set(v) for k, v in o.write_sites.items()},
            )
            # Fusionner les lignes de lecture
            for k, vs in t.read_sites.items():
                merged.read_sites.setdefault(k, set()).update(vs)
            # Fusionner les lignes d'écriture
            for k, vs in t.write_sites.items():
                merged.write_sites.setdefault(k, set()).update(vs)
            out.active[tid] = merged
        else:
            # Thread nouveau : on l'ajoute tel quel
            out.active[tid] = t

    # Fusionner handle_env
    out.handle_env = {k: set(v) for k, v in a.handle_env.items()}
    for k, vs in b.handle_env.items():
        out.handle_env.setdefault(k, set()).update(vs)

    return out
