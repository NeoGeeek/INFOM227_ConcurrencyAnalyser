"""
engine

Orchestration centrale de l'analyseur de data races pour SMALL.

Responsabilités de ce module :
- Déterminer quels threads spawnés peuvent "échaper" d'une fonction (non awaités localement).
- Parcourir les statements et maintenir un état concurrent (threads actifs et environnement des handles).
- Vérifier les accès en lecture/écriture par rapport aux threads actifs pour détecter les potentielles data races.
- Fournir un point d'entrée simple qui exécute toutes les phases nécessaires sur un Program déjà parsé.

Concepts clés :
- Effect : empreinte interprocédurale en lecture/écriture pour les fonctions ou blocs (voir effects.py).
- ThreadInfo : capture de l'empreinte d'un thread utilisée pour vérifier les conflits (voir concurrency.py).
- ConcurState : ensemble de threads actuellement actifs + mapping des variables handle vers les IDs de thread.

Ce fichier contient uniquement la logique d'analyse et un peu de "glue" ; parsing/formatage et autres préoccupations
sont gérés dans des modules dédiés.
"""

from __future__ import annotations
from typing import Dict, List, Set

from src.abstract_syntax_tree import *
from src.effects import Effect, compute_effect_seq, compute_function_effects, substitute_effect, vars_in_expr
from src.constraints import enforce_no_spawn_await_in_if_while, list_spawns_awaits
from src.concurrency import ConcurState, ThreadInfo, join_states, threadinfo_from_effect
from src.conflicts import RaceWarning, mode_for, check_access, check_thread_thread


# -----------------------------------------------------------------------------
# Gestion conservatrice des threads "échappés"
# -----------------------------------------------------------------------------

def compute_escaping_threads(prog: Program, effects: Dict[str, Effect]) -> Dict[str, List[ThreadInfo]]:
    """
    Calcul, pour chaque fonction, de l'ensemble des threads pouvant survivre après le point d'appel.

    Justification
    -------------
    Dans le cadre du projet (pas de spawn/await dans if/while), il est possible de déterminer
    de manière syntaxique si un spawn est awaité dans la même fonction. Un spawn dont le handle
    n'est jamais awaité est considéré comme "échappant" à la fonction : lorsque la fonction
    retourne, ce thread peut continuer à s'exécuter en parallèle avec l'appelant.

    Paramètres
    ----------
    prog : Program
        Programme parsé contenant les définitions de fonctions.
    effects : Dict[str, Effect]
        Effets interprocéduraux pour chaque fonction, utilisés pour approximer
        l'empreinte d'un appel spawné.

    Retours
    -------
    Dict[str, List[ThreadInfo]]
        Pour chaque nom de fonction, liste de ThreadInfo décrivant les threads échappés
        qui doivent être ajoutés aux sites d'appel de cette fonction.
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
    Analyse un seul statement, met à jour l'état concurrent et émet des avertissements.

    La fonction effectue un parcours structurel des statements de l'AST. À chaque étape, elle :
      1) Approxime l'empreinte lecture/écriture de l'opération courante (y compris
         la substitution interprocédurale pour les appels et les spawns),
      2) Compare ces accès avec les threads actuellement actifs dans `state`
         pour détecter d'éventuelles data races (voir conflicts.py),
      3) Met à jour `state` (par ex. en enregistrant de nouveaux threads actifs pour un spawn,
         en liant les handles, en supprimant les threads sur await),
      4) Accumule les avertissements dans l'ensemble `warnings` fourni.

    Paramètres
    ----------
    stmt : Stmt
        Statement à analyser.
    prog : Program
        Programme en cours d'analyse ; utilisé pour résoudre les définitions de fonctions.
    effects : Dict[str, Effect]
        Effets interprocéduraux pré-calculés pour chaque fonction.
    escapes : Dict[str, List[ThreadInfo]]
        Threads échappés par fonction à ajouter aux sites d'appel.
    current_func : FunctionDef
        Définition de la fonction en cours d'analyse ; utilisée pour les chaînes de contexte
        et pour calculer les effets de blocs si nécessaire.
    state : ConcurState
        État concurrent courant (threads actifs + environnement des handles) avant `stmt`.
    warnings : Set[RaceWarning]
        Ensemble global utilisé pour collecter les avertissements uniques pendant l'analyse.

    Retours
    -------
    ConcurState
        L'état mis à jour après l'analyse de `stmt`.

    """

    def add_all(ws: List[RaceWarning]) -> None:
        """Ajoute tous les warnings donnés à l'ensemble global."""
        for w in ws:
            warnings.add(w)

    # Assignements simples
    if isinstance(stmt, Assign):
        # Réinitialisation des bindings de handle pour éviter les awaits sur des handles obsolètes
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
        # x = f(...): évaluer les arguments (lectures), prendre en compte les effets de la fonction appelée,
        # puis écrire dans x, et ajouter tous les threads échappés provenant de f
        if stmt.target in state.handle_env:
            state.handle_env[stmt.target] = set()

        arg_reads: Set[str] = set()
        for a in stmt.args:
            arg_reads |= vars_in_expr(a)

        # Vérifie les lectures des arguments
        for var in sorted(arg_reads):
            add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at call site line {stmt.line}"))

        callee_def = prog.functions[stmt.func]
        callee_eff = substitute_effect(effects[stmt.func], callee_def, stmt.args)

        # Vérifie les lectures/écritures dans le corps appelé
        for var in sorted(callee_eff.reads | callee_eff.writes):
            m = mode_for(var, callee_eff.reads, callee_eff.writes)
            lines = (callee_eff.read_sites.get(var, set()) | callee_eff.write_sites.get(var, set())) or {stmt.line}
            ln = min(lines)
            add_all(check_access(state, var, m, ln, f"{stmt.func}:{m} during call from {current_func.name} at line {stmt.line}"))

        # Vérifie l'écriture du résultat
        add_all(check_access(state, stmt.target, "W", stmt.line, f"{current_func.name}:W(ret) at line {stmt.line}"))

        # Propagation des threads échappés
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
        # La création d'un handle (si elle existe) est considérée comme une écriture dans la variable handle
        if stmt.handle is not None:
            # Réinitialise toute liaison précédente pour ce handle afin d'éviter des await obsolètes
            if stmt.handle in state.handle_env:
                state.handle_env[stmt.handle] = set()
            add_all(check_access(state, stmt.handle, "W", stmt.line, f"{current_func.name}:W(handle) at spawn line {stmt.line}"))

        # Le parent (thread spawnant) évalue les arguments avant que le nouveau thread ne démarre
        if isinstance(stmt.target, SpawnCall):
            arg_reads: Set[str] = set()
            for a in stmt.target.args:
                arg_reads |= vars_in_expr(a)

            # Toute lecture pour l'évaluation des arguments peut entrer en conflit avec des threads existants
            for var in sorted(arg_reads):
                add_all(check_access(state, var, "R", stmt.line, f"{current_func.name}:R(arg) at spawn line {stmt.line}"))

            # L'empreinte du nouveau thread correspond à celle de la fonction appelée, avec les arguments réels substitués
            callee_def = prog.functions[stmt.target.func]
            thr = substitute_effect(effects[stmt.target.func], callee_def, stmt.target.args)
            desc = f"spawn {stmt.target.func}(...) in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else stmt.target.func

        else:
            # Spawn de bloc : calculer l'empreinte du bloc comme effet du nouveau thread
            thr = compute_effect_seq(stmt.target.body, prog, effects, current_func)
            desc = f"spawn {{block}} in {current_func.name}"
            tid_base = stmt.handle if stmt.handle else "_anon"

        # Créer un identifiant de thread unique et vérifier les chevauchements par paires avec les threads existants
        tid = f"{current_func.name}:{tid_base}@{stmt.line}"
        newt = threadinfo_from_effect(thr, tid, desc, stmt.line)

        for old in list(state.active.values()):
            add_all(check_thread_thread(newt, old, stmt.line))

        # Activer le nouveau thread
        state.active[tid] = newt

        # Lier le handle à cet identifiant de thread pour qu'un await ultérieur puisse le rejoindre
        if stmt.handle is not None:
            state.handle_env.setdefault(stmt.handle, set()).add(tid)
        else:
            # Autoriser await <nomFonction> pour la forme "spawn f(...);" (sucre syntaxique)
            if isinstance(stmt.target, SpawnCall):
                state.handle_env.setdefault(stmt.target.func, set()).add(tid)

        return state

    # Await d'un handle
    if isinstance(stmt, Await):
        tids = state.handle_env.get(stmt.handle, set())
        tids = state.handle_env.get(stmt.handle, set())
        for tid in list(tids):
            state.active.pop(tid, None)
        state.handle_env[stmt.handle] = set()
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
    Exécute l'analyse complète des data races sur un programme déjà parsé.
    
    Étapes
    1) Imposer la contrainte du projet interdisant spawn/await dans if/while,
       ce qui permet de garder le raisonnement sur le flot de contrôle simple et conservatif.
    2) Calculer les effets interprocéduraux pour chaque fonction (lectures/écritures et sites).
    3) Identifier les threads échappés pour chaque fonction à partir de ces effets.
    4) Pour chaque corps de fonction, parcourir les statements et accumuler les avertissements.
    
    Paramètres
    ----------
    prog: Program
        Programme parsé tel que produit par le parser.
    
    Retours
    -------
    List[RaceWarning]
        Liste triée des avertissements de data races pour un affichage stable.
    """
    # enforce project constraint
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
