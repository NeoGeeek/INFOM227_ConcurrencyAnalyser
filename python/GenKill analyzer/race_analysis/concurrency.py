from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Set

from .effects import Effect


@dataclass
class ThreadInfo:
    thread_id: str
    desc: str
    spawn_line: int
    reads: Set[str]
    writes: Set[str]
    read_sites: Dict[str, Set[int]]
    write_sites: Dict[str, Set[int]]


def threadinfo_from_effect(eff: Effect, tid: str, desc: str, spawn_line: int) -> ThreadInfo:
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
    active: Dict[str, ThreadInfo] = field(default_factory=dict)     # tid -> thread
    handle_env: Dict[str, Set[str]] = field(default_factory=dict)   # handle var -> {tid}

    def copy(self) -> "ConcurState":
        return ConcurState(
            active=dict(self.active),
            handle_env={k: set(v) for k, v in self.handle_env.items()},
        )


def join_states(a: ConcurState, b: ConcurState) -> ConcurState:
    out = ConcurState()
    out.active = dict(a.active)
    for tid, t in b.active.items():
        if tid in out.active:
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
            for k, vs in t.read_sites.items():
                merged.read_sites.setdefault(k, set()).update(vs)
            for k, vs in t.write_sites.items():
                merged.write_sites.setdefault(k, set()).update(vs)
            out.active[tid] = merged
        else:
            out.active[tid] = t

    out.handle_env = {k: set(v) for k, v in a.handle_env.items()}
    for k, vs in b.handle_env.items():
        out.handle_env.setdefault(k, set()).update(vs)

    return out
