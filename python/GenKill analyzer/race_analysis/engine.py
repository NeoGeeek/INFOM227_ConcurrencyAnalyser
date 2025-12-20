from __future__ import annotations
from typing import Dict, List, Set

from abstract_syntax_tree import *

from effects import Effect, compute_effect_seq, compute_function_effects, substitute_effect, vars_in_expr
from constraints import enforce_no_spawn_await_in_if_while, list_spawns_awaits
from concurrency import ConcurState, ThreadInfo, join_states, threadinfo_from_effect
from conflicts import RaceWarning, mode_for, check_access, check_thread_thread


# -----------------------------------------------------------------------------
# Gestion conservatrice des threads "échappés"
# -----------------------------------------------------------------------------

def compute_escaping_threads(prog: Program, effects: Dict[str, Effect]) -> Dict[str, List[ThreadInfo]]:
    """
    Identifie les threads "échappés" d'une fonction, c'est-à-dire ceux qui
    sont spawnés mais jamais awaités dans la fonction. On les traite comme
    des threads qui continuent à s'exécuter après le retour de la fonction.

    :param prog: programme analysé
    :param effects: effets calculés pour chaque fonction
    :return: dictionnaire fonction -> liste de ThreadInfo représentant les threads échappés
    """
    esc: Dict[str, List[ThreadInfo]] = {}
    for fname, fdef in prog.functions.items():
        spawns, awaits = list_spawns_awaits(fdef.body, [], [])
        awaited = {a.handle for a in awaits}

        threads: List[ThreadInfo] = []
        for s in spawns:
            if s.handle is None or s.handle in awaited:
                continue

            # calcul de l'effet du thread spawné
            if isinstance(s.target, SpawnCall):
                callee_def = prog.functions[s.target.func]
                thr = substitute_effect(effects[s.target.func], callee_def, s.target.args)
                desc = f"escaped spawn {s.target.func}(...) from {fname}"
            else:
                thr = compute_effect_seq(s.target.body, prog, effects, fdef)
                desc = f"escaped spawn {{block}} from {fname}".format(block="{block}")

            tid = f"{fname}:{s.handle}@{s.line}"
            threads.append(threadinfo_from_effect(thr, tid, desc, s.line))

        esc[fname] = threads
    return esc


# -----------------------------------------------------------------------------
# Analyseur central (statement)
# -----------------------------------------------------------------------------

def analyze_stmt(
    stmt: Stmt,
    prog: Program,
    effects: Dict[str, Effect],
    escapes: Dict[str, List[ThreadInfo]],
    current_func: FunctionDef,
    state: ConcurState,
    warnings: Set[RaceWarning],
) -> ConcurState:
    """
    Analyse un statement et met à jour l'état concurrent et la liste des warnings.

    :param stmt: statement à analyser
    :param prog: programme complet
    :param effects: effets calculés pour chaque fonction
    :param escapes: threads échappés
    :param current_func: fonction contenant le statement
    :param state: état courant des threads
    :param warnings: ensemble des avertissements détectés
    :return: nouvel état concurrent mis à jour
    """

    def add_all(ws: List[RaceWarning]) -> None:
        """Ajoute tous les warnings donnés à l'ensemble global."""
        for w in ws:
            warnings.add(w)

    # Assignements simples
    if isinstance(stmt, Assign):
        if stmt.target in state.handle_env:
            state.handle_env[stmt.target] = set()

        reads = vars_in_expr(stmt.expr)
        writes = {stmt.target}

        for var in sorted(reads | writes):
            m = mode_for(var, reads, writes)
            add_all(check_access(state, var, m, stmt.line, f"{current_func.name}:{m} at line {stmt.line}"))

        return state

    # Assignments via appel de fonction
    if isinstance(stmt, AssignCall):
        if stmt.target in state.handle_env:
            state.handle_env[stmt.target] = set()

        arg_reads: Set[str] = set()
        for a in stmt.args:
            arg_reads |= vars_in_expr(a)

        for var in sorted(arg_reads):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at call site line {stmt.line}"))

        callee_def = prog.functions[stmt.func]
        callee_eff = substitute_effect(effects[stmt.func], callee_def, stmt.args)

        for var in sorted(callee_eff.reads | callee_eff.writes):
            m = mode_for(var, callee_eff.reads, callee_eff.writes)
            lines = (callee_eff.read_sites.get(var, set()) | callee_eff.write_sites.get(var, set())) or {stmt.line}
            ln = min(lines)
            add_all(check_access(state, var, m, ln, f"{stmt.func}:{m} during call from {current_func.name} at line {stmt.line}"))

        add_all(check_access(state, stmt.target, "W", stmt.line, f"{current_func.name}:W(ret) at line {stmt.line}"))

        # propagation conservatrice des threads échappés
        for t in escapes.get(stmt.func, []):
            base = Effect(
                reads=set(t.reads),
                writes=set(t.writes),
                read_sites={k: set(v) for k, v in t.read_sites.items()},
                write_sites={k: set(v) for k, v in t.write_sites.items()},
            )
            sub = substitute_effect(base, callee_def, stmt.args)
            tid = f"escaped:{t.thread_id}@call{stmt.line}"
            state.active[tid] = threadinfo_from_effect(sub, tid, t.desc, t.spawn_line)

        return state

    # Appel de fonction sans assignation
    if isinstance(stmt, CallStmt):
        arg_reads: Set[str] = set()
        for a in stmt.args:
            arg_reads |= vars_in_expr(a)

        for var in sorted(arg_reads):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at call site line {stmt.line}"))

        callee_def = prog.functions[stmt.func]
        callee_eff = substitute_effect(effects[stmt.func], callee_def, stmt.args)

        for var in sorted(callee_eff.reads | callee_eff.writes):
            m = mode_for(var, callee_eff.reads, callee_eff.writes)
            lines = (callee_eff.read_sites.get(var, set()) | callee_eff.write_sites.get(var, set())) or {stmt.line}
            ln = min(lines)
            add_all(check_access(state, var, m, ln, f"{stmt.func}:{m} during call from {current_func.name} at line {stmt.line}"))

        for t in escapes.get(stmt.func, []):
            base = Effect(
                reads=set(t.reads),
                writes=set(t.writes),
                read_sites={k: set(v) for k, v in t.read_sites.items()},
                write_sites={k: set(v) for k, v in t.write_sites.items()},
            )
            sub = substitute_effect(base, callee_def, stmt.args)
            tid = f"escaped:{t.thread_id}@call{stmt.line}"
            state.active[tid] = threadinfo_from_effect(sub, tid, t.desc, t.spawn_line)

        return state

    # Spawn de thread
    if isinstance(stmt, Spawn):
        if stmt.handle is not None:
            if stmt.handle in state.handle_env:
                state.handle_env[stmt.handle] = set()
            add_all(check_access(state, stmt.handle, "W", stmt.line, f"{current_func.name}:W(handle) at spawn line {stmt.line}"))

        # évaluation des arguments du spawn
        if isinstance(stmt.target, SpawnCall):
            arg_reads: Set[str] = set()
            for a in stmt.target.args:
                arg_reads |= vars_in_expr(a)

            for var in sorted(arg_reads):
                add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at spawn line {stmt.line}"))

            callee_def = prog.functions[stmt.target.func]
            thr = substitute_effect(effects[stmt.target.func], callee_def, stmt.target.args)
            desc = f"spawn {stmt.target.func}(...) in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else stmt.target.func

        else:
            thr = compute_effect_seq(stmt.target.body, prog, effects, current_func)
            desc = f"spawn {{block}} in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else "_anon"

        tid = f"{current_func.name}:{tid_base}@{stmt.line}"
        newt = threadinfo_from_effect(thr, tid, desc, stmt.line)

        for old in list(state.active.values()):
            add_all(check_thread_thread(newt, old, stmt.line))

        state.active[tid] = newt

        # bind handle
        if stmt.handle is not None:
            state.handle_env.setdefault(stmt.handle, set()).add(tid)
        else:
            if isinstance(stmt.target, SpawnCall):
                state.handle_env.setdefault(stmt.target.func, set()).add(tid)

        return state

    # Await d'un handle
    if isinstance(stmt, Await):
        tids = state.handle_env.get(stmt.handle, set())
        for tid in list(tids):
            state.active.pop(tid, None)
        state.handle_env[stmt.handle] = set()
        return state

    # Return
    if isinstance(stmt, Return):
        reads = vars_in_expr(stmt.expr)
        for var in sorted(reads):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(return) at line {stmt.line}"))
        return state

    # Séquence de statements
    if isinstance(stmt, Seq):
        for s in stmt.stmts:
            state = analyze_stmt(s, prog, effects, escapes, current_func, state, warnings)
        return state

    # If / While
    if isinstance(stmt, If):
        for var in sorted(vars_in_expr(stmt.cond)):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(if-cond) at line {stmt.line}"))
        st_then = analyze_stmt(stmt.then_s, prog, effects, escapes, current_func, state.copy(), warnings)
        st_else = analyze_stmt(stmt.else_s, prog, effects, escapes, current_func, state.copy(), warnings)
        return join_states(st_then, st_else)

    if isinstance(stmt, While):
        for var in sorted(vars_in_expr(stmt.cond)):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(while-cond) at line {stmt.line}"))
        st_body = analyze_stmt(stmt.body, prog, effects, escapes, current_func, state.copy(), warnings)
        return join_states(state, st_body)

    raise TypeError(stmt)


# -----------------------------------------------------------------------------
# Analyse complète d'un programme
# -----------------------------------------------------------------------------

def analyze_program(prog: Program) -> List[RaceWarning]:
    """
    Analyse un programme entier pour détecter les data races statiques.

    :param prog: programme déjà parsé
    :return: liste triée de RaceWarning
    """
    # imposer la contrainte : pas de spawn/await dans if/while
    for f in prog.functions.values():
        enforce_no_spawn_await_in_if_while(f.body, inside_control=False)

    # calcul des effets pour chaque fonction
    effects = compute_function_effects(prog)
    # identification des threads "échappés"
    escapes = compute_escaping_threads(prog, effects)

    warnings: Set[RaceWarning] = set()
    for f in prog.functions.values():
        analyze_stmt(f.body, prog, effects, escapes, f, ConcurState(), warnings)

    return sorted(warnings, key=lambda w: (w.line_a, w.var, w.kind))
