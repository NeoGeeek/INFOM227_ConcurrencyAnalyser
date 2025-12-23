from __future__ import annotations
from dataclasses import dataclass, field

from src.abstract_syntax_tree import *


# -----------------------------------------------------------------------------
# Variable extraction
# -----------------------------------------------------------------------------


def vars_in_expr(e: Expr) -> set[str]:
    """
    Retourne l'ensemble des noms de variables utilisés dans une expression.

    :param e: expression à analyser (Var, Num, Bool, BinOp, RelOp)
    :return: set de noms de variables (strings)
    """

    # Si c'est une variable, on retourne un set contenant son nom
    if isinstance(e, Var):
        return {e.name}

    # Si c'est un nombre ou un booléen, aucune variable n'est utilisée
    if isinstance(e, (Num, Bool)):
        return set()

    # Si c'est une opération binaire ou relationnelle,
    # on prend l'union des variables dans l'opérande gauche et droite
    if isinstance(e, (BinOp, RelOp)):
        return vars_in_expr(e.left) | vars_in_expr(e.right)

    # Si l'expression n'est pas reconnue, on lève une erreur
    raise TypeError(e)


# -----------------------------------------------------------------------------
# Interprocedural effect analysis (R/W footprints with line sites)
# -----------------------------------------------------------------------------

@dataclass
class Effect:
    """
    Représente les effets d'une portion de code :
      - lectures et écritures de variables
      - lignes où chaque variable est lue ou écrite
    """
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    read_sites: dict[str, set[int]] = field(default_factory=dict)
    write_sites: dict[str, set[int]] = field(default_factory=dict)

    def add_read(self, var: str, line: int) -> None:
        """Enregistre une lecture de variable à la ligne donnée"""
        self.reads.add(var)
        self.read_sites.setdefault(var, set()).add(line)

    def add_write(self, var: str, line: int) -> None:
        """Enregistre une écriture de variable à la ligne donnée"""
        self.writes.add(var)
        self.write_sites.setdefault(var, set()).add(line)

    def union(self, other: "Effect") -> "Effect":
        """
        Retourne un nouvel effet correspondant à l'union de self et other
        (fusion des lectures, écritures et des sites associés)
        """
        out = Effect()
        out.reads = set(self.reads) | set(other.reads)
        out.writes = set(self.writes) | set(other.writes)

        out.read_sites = {k: set(v) for k, v in self.read_sites.items()}
        for k, v in other.read_sites.items():
            out.read_sites.setdefault(k, set()).update(v)

        out.write_sites = {k: set(v) for k, v in self.write_sites.items()}
        for k, v in other.write_sites.items():
            out.write_sites.setdefault(k, set()).update(v)

        return out

    def equals(self, other: "Effect") -> bool:
        """Vérifie si deux effets sont identiques"""
        return (
            self.reads == other.reads
            and self.writes == other.writes
            and self.read_sites == other.read_sites
            and self.write_sites == other.write_sites
        )


def substitute_effect(callee: Effect, callee_def: FunctionDef, actual_args: list[Expr]) -> Effect:
    """
    Substitution conservatrice des paramètres formels par les variables réelles
    lors d'un appel de fonction.

    :param callee: effet de la fonction appelée
    :param callee_def: définition de la fonction appelée
    :param actual_args: expressions passées en arguments
    :return: nouvel effet adapté aux arguments réels
    """
    mapping: dict[str, set[str]] = {}
    for i, p in enumerate(callee_def.params):
        # On mappe chaque paramètre aux variables contenues dans l'argument réel
        mapping[p] = vars_in_expr(actual_args[i]) if i < len(actual_args) else set()

    out = Effect()

    # Substituer les lectures
    for v in callee.reads:
        targets = mapping.get(v, {v})
        for tv in targets:
            for ln in callee.read_sites.get(v, set()):
                out.add_read(tv, ln)

    # Substituer les écritures
    for v in callee.writes:
        targets = mapping.get(v, {v})
        for tv in targets:
            for ln in callee.write_sites.get(v, set()):
                out.add_write(tv, ln)

    return out


def compute_effect_seq(seq: Seq, prog: Program, effects: dict[str, Effect], current_func: FunctionDef) -> Effect:
    """
    Calcule l'effet d'une séquence de statements.

    :param seq: séquence de statements
    :param prog: programme complet (pour récupérer les définitions de fonctions)
    :param effects: effets connus des fonctions
    :param current_func: fonction courante
    :return: effet combiné de tous les statements de la séquence
    """
    eff = Effect()
    for s in seq.stmts:
        eff = eff.union(compute_effect_stmt(s, prog, effects, current_func))
    return eff


def compute_effect_stmt(stmt: Stmt, prog: Program, effects: dict[str, Effect], current_func: FunctionDef) -> Effect:
    """
    Calcule l'effet d'un statement unique.

    :param stmt: statement à analyser
    :param prog: programme complet
    :param effects: effets connus des fonctions
    :param current_func: fonction courante
    :return: effet du statement
    """
    eff = Effect()

    # Assignation simple
    if isinstance(stmt, Assign):
        for v in vars_in_expr(stmt.expr):
            eff.add_read(v, stmt.line)
        eff.add_write(stmt.target, stmt.line)
        return eff

    # Assignation avec appel de fonction
    if isinstance(stmt, AssignCall):
        for a in stmt.args:
            for v in vars_in_expr(a):
                eff.add_read(v, stmt.line)
        callee_def = prog.functions[stmt.func]
        eff = eff.union(substitute_effect(effects[stmt.func], callee_def, stmt.args))
        eff.add_write(stmt.target, stmt.line)
        return eff

    # Appel de fonction sans assignation
    if isinstance(stmt, CallStmt):
        for a in stmt.args:
            for v in vars_in_expr(a):
                eff.add_read(v, stmt.line)
        callee_def = prog.functions[stmt.func]
        return eff.union(substitute_effect(effects[stmt.func], callee_def, stmt.args))

    # Spawn (asynchrone)
    if isinstance(stmt, Spawn):
        if stmt.handle is not None:
            eff.add_write(stmt.handle, stmt.line)

        if isinstance(stmt.target, SpawnCall):
            for a in stmt.target.args:
                for v in vars_in_expr(a):
                    eff.add_read(v, stmt.line)
            callee_def = prog.functions[stmt.target.func]
            thr = substitute_effect(effects[stmt.target.func], callee_def, stmt.target.args)
            return eff.union(thr)

        if isinstance(stmt.target, SpawnBlock):
            thr = compute_effect_seq(stmt.target.body, prog, effects, current_func)
            return eff.union(thr)

        raise TypeError(stmt.target)

    # Await : aucun effet supplémentaire
    if isinstance(stmt, Await):
        return eff

    # Return
    if isinstance(stmt, Return):
        for v in vars_in_expr(stmt.expr):
            eff.add_read(v, stmt.line)
        return eff

    # Séquence de statements
    if isinstance(stmt, Seq):
        for s in stmt.stmts:
            eff = eff.union(compute_effect_stmt(s, prog, effects, current_func))
        return eff

    # If
    if isinstance(stmt, If):
        for v in vars_in_expr(stmt.cond):
            eff.add_read(v, stmt.line)
        return (
            eff
            .union(compute_effect_stmt(stmt.then_s, prog, effects, current_func))
            .union(compute_effect_stmt(stmt.else_s, prog, effects, current_func))
        )

    # While
    if isinstance(stmt, While):
        for v in vars_in_expr(stmt.cond):
            eff.add_read(v, stmt.line)
        return eff.union(compute_effect_stmt(stmt.body, prog, effects, current_func))

    raise TypeError(stmt)


def compute_function_effects(prog: Program) -> dict[str, Effect]:
    """
    Calcule les effets de toutes les fonctions du programme
    en utilisant un fixpoint monotone pour propager les effets inter-fonctions.

    :param prog: programme complet
    :return: dictionnaire mapping nom de fonction -> effet
    """
    effs: dict[str, Effect] = {name: Effect() for name in prog.functions}

    # On itère jusqu'à ce que les effets convergent ou qu'on atteigne 50 itérations
    for _ in range(50):
        changed = False
        for fname, fdef in prog.functions.items():
            new_eff = compute_effect_seq(fdef.body, prog, effs, fdef)
            if not new_eff.equals(effs[fname]):
                effs[fname] = effs[fname].union(new_eff)
                changed = True
        if not changed:
            break

    return effs
