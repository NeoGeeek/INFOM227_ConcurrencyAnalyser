"""
engine

Core orchestration of the SMALL concurrency race analyzer.

Responsibilities of this module:
- Determine which spawned threads can escape a function (not awaited locally).
- Traverse statements and maintain a concurrent state (active threads and handle environment).
- Check read/write accesses against active threads to report potential data races.
- Provide a simple entry point that runs all necessary phases on a parsed Program.

Key concepts:
- Effect: Interprocedural read/write footprint for functions or blocks (see effects.py).
- ThreadInfo: Snapshot of a thread's footprint used to check conflicts (see concurrency.py).
- ConcurState: Set of currently active threads + a mapping from handle variables to thread IDs.

This file only contains analysis logic and light glue; parsing/formatting and other concerns are
kept in dedicated modules.
"""

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
<<<<<<< Updated upstream
    Identifie les threads "échappés" d'une fonction, c'est-à-dire ceux qui
    sont spawnés mais jamais awaités dans la fonction. On les traite comme
    des threads qui continuent à s'exécuter après le retour de la fonction.

    :param prog: programme analysé
    :param effects: effets calculés pour chaque fonction
    :return: dictionnaire fonction -> liste de ThreadInfo représentant les threads échappés
=======
    Compute, for each function, the set of threads that may outlive the call site.

    Rationale
    ---------
    Under the project constraint (no spawn/await inside if/while), whether a spawn
    is awaited within the same function can be determined syntactically. A spawn
    whose handle is never awaited is considered to "escape" the function: when the
    function returns, that thread can still be running concurrently with the caller.

    Parameters
    ----------
    prog: Program
        Parsed program containing function definitions.
    effects: Dict[str, Effect]
        Interprocedural effects per function, used to approximate the footprint of
        a spawned call.

    Returns
    -------
    Dict[str, List[ThreadInfo]]
        For each function name, a list of ThreadInfo describing escaped threads
        that should be added at call sites of that function.
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
    Analyse un statement et met à jour l'état concurrent et la liste des warnings.

    :param stmt: statement à analyser
    :param prog: programme complet
    :param effects: effets calculés pour chaque fonction
    :param escapes: threads échappés
    :param current_func: fonction contenant le statement
    :param state: état courant des threads
    :param warnings: ensemble des avertissements détectés
    :return: nouvel état concurrent mis à jour
=======
    Analyze a single statement, update the concurrent state, and emit warnings.

    The function is a structural traversal over the AST statements. At each step it:
      1) Approximates the read/write footprint of the current operation (including
         interprocedural substitution for calls/spawns),
      2) Compares those accesses against the currently active threads in `state`
         to detect potential data races (see conflicts.py),
      3) Updates the `state` (e.g., recording new active threads for spawn, binding
         handles, removing threads on await),
      4) Accumulates warnings into the provided `warnings` set.

    Parameters
    ----------
    stmt: Stmt
        Statement to analyze.
    prog: Program
        Program being analyzed; used to resolve function definitions.
    effects: Dict[str, Effect]
        Precomputed interprocedural effects for each function name.
    escapes: Dict[str, List[ThreadInfo]]
        Escaping threads per function to be added at call sites.
    current_func: FunctionDef
        Definition of the function currently being analyzed; used for context strings
        and for computing block effects when needed.
    state: ConcurState
        Current concurrent state (active threads + handle environment) before `stmt`.
    warnings: Set[RaceWarning]
        Global set used to collect unique warnings during analysis.

    Returns
    -------
    ConcurState
        The updated state after analyzing `stmt`.
>>>>>>> Stashed changes
    """

    def add_all(ws: List[RaceWarning]) -> None:
        """Ajoute tous les warnings donnés à l'ensemble global."""
        for w in ws:
            warnings.add(w)

    # Assignements simples
    if isinstance(stmt, Assign):
        # Overwriting a handle variable invalidates any previously bound thread IDs
        # to prevent accidental awaits on stale handles.
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
        # x = f(...): evaluate args (reads), account for callee effects, then write x,
        # and add any escaped threads from f.
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
        # Handle creation (if any) is a write to the handle variable.
        if stmt.handle is not None:
            # Reset any previous binding for this handle to avoid stale awaits.
            if stmt.handle in state.handle_env:
                state.handle_env[stmt.handle] = set()
            add_all(check_access(state, stmt.handle, "W", stmt.line, f"{current_func.name}:W(handle) at spawn line {stmt.line}"))

<<<<<<< Updated upstream
        # évaluation des arguments du spawn
=======
        # Parent (spawner) evaluates arguments before the new thread starts.
>>>>>>> Stashed changes
        if isinstance(stmt.target, SpawnCall):
            arg_reads: Set[str] = set()
            for a in stmt.target.args:
                arg_reads |= vars_in_expr(a)

            # Any read for argument evaluation can race with existing threads.
            for var in sorted(arg_reads):
                add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at spawn line {stmt.line}"))

            # New thread's footprint is that of the callee with actual args substituted.
            callee_def = prog.functions[stmt.target.func]
            thr = substitute_effect(effects[stmt.target.func], callee_def, stmt.target.args)
            desc = f"spawn {stmt.target.func}(...) in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else stmt.target.func

        else:
            # Block spawn: compute footprint of the block as the new thread's effect.
            thr = compute_effect_seq(stmt.target.body, prog, effects, current_func)
            desc = f"spawn {{block}} in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else "_anon"

        # Create a fresh thread identifier and check pairwise overlaps with existing threads.
        tid = f"{current_func.name}:{tid_base}@{stmt.line}"
        newt = threadinfo_from_effect(thr, tid, desc, stmt.line)

        for old in list(state.active.values()):
            add_all(check_thread_thread(newt, old, stmt.line))

        # Activate the new thread.
        state.active[tid] = newt

        # Bind the handle to this thread id so that a later await can join it.
        if stmt.handle is not None:
            state.handle_env.setdefault(stmt.handle, set()).add(tid)
        else:
<<<<<<< Updated upstream
=======
            # allow await <functionName> for "spawn f(...);" form (syntactic sugar)
>>>>>>> Stashed changes
            if isinstance(stmt.target, SpawnCall):
                state.handle_env.setdefault(stmt.target.func, set()).add(tid)

        return state

    # Await d'un handle
    if isinstance(stmt, Await):
        # Await joins all threads currently bound to the given handle.
        tids = state.handle_env.get(stmt.handle, set())
        for tid in list(tids):
            state.active.pop(tid, None)
        # Clear bindings for the handle after the await.
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
<<<<<<< Updated upstream
    Analyse un programme entier pour détecter les data races statiques.

    :param prog: programme déjà parsé
    :return: liste triée de RaceWarning
    """
    # imposer la contrainte : pas de spawn/await dans if/while
=======
    Run the complete race analysis pipeline over a parsed program.

    Steps
    -----
    1) Enforce the project constraint that forbids spawn/await inside if/while,
       which keeps the control-flow reasoning simple and conservative.
    2) Compute interprocedural effects for each function (reads/writes and sites).
    3) Compute escaped threads for each function using those effects.
    4) For each function body, traverse statements and accumulate warnings.

    Parameters
    ----------
    prog: Program
        Parsed program as produced by the parser.

    Returns
    -------
    List[RaceWarning]
        A sorted list of race warnings for stable output.
    """
    # enforce project constraint
>>>>>>> Stashed changes
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
