from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from concurrency import ConcurState, ThreadInfo


@dataclass(frozen=True)
class RaceWarning:
    var: str
    kind: str
    line_a: int
    ctx_a: str
    lines_b: Tuple[int, ...]
    ctx_b: str


def mode_for(var: str, reads: Set[str], writes: Set[str]) -> Optional[str]:
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
    lines: Set[int] = set()
    if var in t.writes:
        lines |= set(t.write_sites.get(var, set()))
    if var in t.reads:
        lines |= set(t.read_sites.get(var, set()))
    if not lines:
        lines.add(t.spawn_line)
    return lines


def conflicts(mode: str, t: ThreadInfo, var: str) -> bool:
    if mode == "R":
        return var in t.writes
    if mode in ("W", "RW"):
        return (var in t.writes) or (var in t.reads)
    return False


def check_access(state: ConcurState, var: str, mode: str, line: int, ctx: str) -> List[RaceWarning]:
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
    # any overlap with at least one write:
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
