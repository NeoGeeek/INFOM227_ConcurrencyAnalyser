from __future__ import annotations
from typing import List, Optional, Dict, Tuple
from lexer import Token
from abstract_syntax_tree import *


class ParserError(Exception):
    pass

class Parser:
    def __init__(self, toks: List[Token]):
        self.toks = toks
        self.i = 0

    def peek(self) -> Token:
        return self.toks[self.i]

    def match(self, kind: Optional[str] = None, value: Optional[str] = None) -> bool:
        t = self.peek()
        if kind and t.kind != kind:
            return False
        if value and t.value != value:
            return False
        return True

    def consume(self, kind: Optional[str] = None, value: Optional[str] = None) -> Token:
        t = self.peek()
        if kind and t.kind != kind:
            raise ParserError(f"Expected {kind} at line {t.line}, got {t.kind}:{t.value}")
        if value and t.value != value:
            raise ParserError(f"Expected {value!r} at line {t.line}, got {t.value!r}")
        self.i += 1
        return t

    def parse_program(self) -> Program:
        funcs: Dict[str, FunctionDef] = {}
        while not self.match("EOF"):
            f = self.parse_function()
            if f.name in funcs:
                raise ParserError(f"Duplicate function {f.name} at line {f.line}")
            funcs[f.name] = f
        return Program(funcs)

    def parse_function(self) -> FunctionDef:
        start = self.consume("KW", "function")
        name = self.consume("ID").value
        self.consume("SYM", "(")

        params: List[str] = []
        if not self.match("SYM", ")"):
            params = self.parse_param_list()

        self.consume("SYM", ")")
        self.consume("SYM", "{")
        stmts = self.parse_stmt_list(until="}")
        self.consume("SYM", "}")
        return FunctionDef(name=name, params=params, body=Seq(line=start.line, stmts=stmts), line=start.line)

    def parse_param_list(self) -> List[str]:
        params = [self.consume("ID").value]
        while self.match("SYM", ","):
            self.consume("SYM", ",")
            params.append(self.consume("ID").value)
        return params

    def parse_stmt_list(self, until: str) -> List[Stmt]:
        out: List[Stmt] = []
        while not self.match("SYM", until):
            out.append(self.parse_stmt())
        return out

    def parse_stmt(self) -> Stmt:
        t = self.peek()

        if t.kind == "KW" and t.value == "if":
            return self.parse_if()
        if t.kind == "KW" and t.value == "while":
            return self.parse_while()
        if t.kind == "SYM" and t.value == "{":
            return self.parse_seq()
        if t.kind == "KW" and t.value == "return":
            return self.parse_return()
        if t.kind == "KW" and t.value == "spawn":
            return self.parse_spawn(handle=None)
        if t.kind == "KW" and t.value == "await":
            return self.parse_await()

        # identifier-led: assignment or call statement
        if t.kind == "ID":
            # assignment?
            if self.toks[self.i + 1].kind == "SYM" and self.toks[self.i + 1].value == "=":
                lhs = self.consume("ID")
                self.consume("SYM", "=")

                # spawn assignment?
                if self.match("KW", "spawn"):
                    return self.parse_spawn(handle=lhs.value)

                # function-call assignment?
                if self.match("ID") and self.toks[self.i + 1].kind == "SYM" and self.toks[self.i + 1].value == "(":
                    fn, args = self.parse_func_call()
                    self.consume("SYM", ";")
                    return AssignCall(line=lhs.line, target=lhs.value, func=fn, args=args)

                # plain expression assignment
                expr = self.parse_expr()
                self.consume("SYM", ";")
                return Assign(line=lhs.line, target=lhs.value, expr=expr)

            # call statement?
            if self.toks[self.i + 1].kind == "SYM" and self.toks[self.i + 1].value == "(":
                fn, args = self.parse_func_call()
                self.consume("SYM", ";")
                return CallStmt(line=t.line, func=fn, args=args)

        raise ParserError(f"Unexpected token {t.kind}:{t.value} at line {t.line}")

    def parse_seq(self) -> Seq:
        start = self.consume("SYM", "{")
        stmts = self.parse_stmt_list(until="}")
        self.consume("SYM", "}")
        return Seq(line=start.line, stmts=stmts)

    def parse_if(self) -> If:
        start = self.consume("KW", "if")
        self.consume("SYM", "(")
        cond = self.parse_expr()
        self.consume("SYM", ")")
        then_s = self.parse_stmt()
        self.consume("KW", "else")
        else_s = self.parse_stmt()
        return If(line=start.line, cond=cond, then_s=then_s, else_s=else_s)

    def parse_while(self) -> While:
        start = self.consume("KW", "while")
        self.consume("SYM", "(")
        cond = self.parse_expr()
        self.consume("SYM", ")")
        body = self.parse_stmt()
        return While(line=start.line, cond=cond, body=body)

    def parse_return(self) -> Return:
        start = self.consume("KW", "return")
        expr = self.parse_expr()
        self.consume("SYM", ";")
        return Return(line=start.line, expr=expr)

    def parse_await(self) -> Await:
        start = self.consume("KW", "await")
        h = self.consume("ID").value
        self.consume("SYM", ";")
        return Await(line=start.line, handle=h)

    def parse_spawn(self, handle: Optional[str]) -> Spawn:
        kw = self.consume("KW", "spawn")
        line = kw.line

        # spawn { ... } ;
        if self.match("SYM", "{"):
            body = self.parse_seq()
            self.consume("SYM", ";")
            return Spawn(line=line, handle=handle, target=SpawnBlock(body=body, line=line))

        # spawn f(e,...) ;
        fn, args = self.parse_func_call()
        self.consume("SYM", ";")
        return Spawn(line=line, handle=handle, target=SpawnCall(func=fn, args=args, line=line))

    def parse_func_call(self) -> Tuple[str, List[Expr]]:
        name = self.consume("ID").value
        self.consume("SYM", "(")

        args: List[Expr] = []
        if not self.match("SYM", ")"):
            args.append(self.parse_expr())
            while self.match("SYM", ","):
                self.consume("SYM", ",")
                args.append(self.parse_expr())

        self.consume("SYM", ")")
        return name, args

    def parse_expr(self) -> Expr:
        left = self.parse_operand()
        t = self.peek()

        # optional binary operator (SMALL-style simple expressions)
        if t.kind == "OP" or (t.kind == "KW" and t.value in ("and", "or")):
            op = self.consume(t.kind).value
            right = self.parse_operand()
            if op in ("+", "-", "*", "/"):
                return BinOp(line=left.line, op=op, left=left, right=right)
            return RelOp(line=left.line, op=op, left=left, right=right)

        return left

    def parse_operand(self) -> Expr:
        t = self.peek()

        if t.kind == "NUM":
            tok = self.consume("NUM")
            return Num(line=tok.line, value=int(tok.value))

        if t.kind == "KW" and t.value in ("True", "False"):
            tok = self.consume("KW")
            return Bool(line=tok.line, value=(tok.value == "True"))

        if t.kind == "ID":
            tok = self.consume("ID")
            return Var(line=tok.line, name=tok.value)

        raise ParserError(f"Expected operand at line {t.line}, got {t.kind}:{t.value}")

