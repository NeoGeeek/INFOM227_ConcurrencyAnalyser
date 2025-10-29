"""Minimal interpreter for the example language defined in README grammar."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, TextIO, Tuple, Union
import sys


class LexerError(Exception):
    pass


class ParserError(Exception):
    pass


class InterpreterError(Exception):
    pass


TokenType = str


@dataclass
class Token:
    type: TokenType
    value: Optional[str]
    position: int

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Token({self.type!r}, {self.value!r}, pos={self.position})"


KEYWORDS = {
    "int": "INT",
    "bool": "BOOL",
    "print": "PRINT",
    "if": "IF",
    "else": "ELSE",
    "while": "WHILE",
    "True": "TRUE",
    "False": "FALSE",
    "not": "NOT",
    "and": "AND",
    "or": "OR",
}

SINGLE_CHAR_TOKENS = {
    "{" : "LBRACE",
    "}": "RBRACE",
    "(": "LPAR",
    ")": "RPAR",
    ";": "SEMICOLON",
    "=": "ASSIGN",
    "+": "ADD",
    "-": "SUBTRACT",
    "*": "MULTIPLY",
    "/": "DIVIDE",
    ">": "GREATER",
    "<": "LESS",
}

DOUBLE_CHAR_TOKENS = {
    "==": "EQUAL",
    "!=": "DIFFERENT",
    ">=": "GREATER_EQUAL",
    "<=": "LESS_EQUAL",
}


class Lexer:
    def __init__(self, source: str) -> None:
        self.source = source
        self.length = len(source)
        self.position = 0

    def tokens(self) -> List[Token]:
        tokens: List[Token] = []
        while True:
            token = self.next_token()
            tokens.append(token)
            if token.type == "EOF":
                break
        return tokens

    def next_token(self) -> Token:
        self._skip_ignored()
        if self.position >= self.length:
            return Token("EOF", None, self.position)

        ch = self.source[self.position]

        # Identifiers / keywords / booleans
        if ch.isalpha():
            start = self.position
            self.position += 1
            while self.position < self.length and (
                self.source[self.position].isalnum()
            ):
                self.position += 1
            value = self.source[start:self.position]
            token_type = KEYWORDS.get(value, "IDENTIFIER")
            return Token(token_type, value, start)

        # Numbers
        if ch.isdigit():
            start = self.position
            self.position += 1
            while self.position < self.length and self.source[self.position].isdigit():
                self.position += 1
            value = self.source[start:self.position]
            return Token("NUMBER", value, start)

        # Double char operators
        if self.position + 1 < self.length:
            maybe = self.source[self.position : self.position + 2]
            if maybe in DOUBLE_CHAR_TOKENS:
                self.position += 2
                return Token(DOUBLE_CHAR_TOKENS[maybe], maybe, self.position - 2)

        # Single char tokens
        if ch in SINGLE_CHAR_TOKENS:
            self.position += 1
            return Token(SINGLE_CHAR_TOKENS[ch], ch, self.position - 1)

        raise LexerError(f"Unexpected character {ch!r} at position {self.position}")

    def _skip_ignored(self) -> None:
        while self.position < self.length:
            ch = self.source[self.position]
            if ch in " \t\r\n":
                self.position += 1
                continue

            if self.source.startswith("//", self.position):
                self.position += 2
                while (
                    self.position < self.length
                    and self.source[self.position] not in "\r\n"
                ):
                    self.position += 1
                continue

            if self.source.startswith("/*", self.position):
                end = self.source.find("*/", self.position + 2)
                if end == -1:
                    raise LexerError("Unterminated block comment")
                self.position = end + 2
                continue

            break


# AST nodes


class Statement:
    pass


@dataclass
class Declare(Statement):
    name: str
    typ: str  # "int" or "bool"


@dataclass
class Assignment(Statement):
    name: str
    scope: Optional["Scope"]
    expression: "Expression"


@dataclass
class Print(Statement):
    expression: "Expression"


@dataclass
class IfElse(Statement):
    condition: "Expression"
    if_statements: List[Statement]
    else_statements: List[Statement]


@dataclass
class While(Statement):
    condition: "Expression"
    statements: List[Statement]


@dataclass
class Scope:
    declarations: List[Declare]
    statements: List[Statement]


class Expression:
    pass


@dataclass
class Literal(Expression):
    value: Union[int, bool]


@dataclass
class Identifier(Expression):
    name: str


@dataclass
class UnaryOp(Expression):
    operator: str
    operand: Expression


@dataclass
class BinaryOp(Expression):
    operator: str
    left: Expression
    right: Expression


class Parser:
    def __init__(self, tokens: List[Token]) -> None:
        self.tokens = tokens
        self.position = 0

    def parse(self) -> Scope:
        scope = self._parse_scope()
        self._expect("EOF")
        return scope

    def _current(self) -> Token:
        return self.tokens[self.position]

    def _advance(self) -> Token:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def _expect(self, token_type: TokenType) -> Token:
        token = self._current()
        if token.type != token_type:
            raise ParserError(
                f"Expected {token_type} but found {token.type} at position {token.position}"
            )
        self.position += 1
        return token

    def _match(self, token_type: TokenType) -> Optional[Token]:
        if self._current().type == token_type:
            return self._advance()
        return None

    def _peek(self, offset: int = 0) -> Token:
        idx = self.position + offset
        if idx >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[idx]

    def _parse_scope(self) -> Scope:
        declarations: List[Declare] = []
        while self._current().type in {"INT", "BOOL"}:
            declarations.append(self._parse_declaration())

        statements: List[Statement] = []
        while self._can_start_statement():
            statements.append(self._parse_statement())

        return Scope(declarations, statements)

    def _parse_declaration(self) -> Declare:
        token = self._advance()
        typ = "int" if token.type == "INT" else "bool"
        name = self._expect("IDENTIFIER").value  # type: ignore[arg-type]
        self._expect("SEMICOLON")
        return Declare(name=name, typ=typ)

    def _can_start_statement(self) -> bool:
        token = self._current().type
        if token in {"PRINT", "IF", "WHILE"}:
            return True
        if token == "IDENTIFIER" and self._peek(1).type == "ASSIGN":
            return True
        return False

    def _parse_statement(self) -> Statement:
        token = self._current().type
        if token == "PRINT":
            return self._parse_print()
        if token == "IF":
            return self._parse_if()
        if token == "WHILE":
            return self._parse_while()
        if token == "IDENTIFIER":
            return self._parse_assignment()
        raise ParserError(f"Unexpected token {token} at position {self._current().position}")

    def _parse_assignment(self) -> Assignment:
        name = self._expect("IDENTIFIER").value  # type: ignore[arg-type]
        self._expect("ASSIGN")
        if self._match("LBRACE"):
            inner_scope = self._parse_scope()
            expr = self._parse_expression()
            self._expect("RBRACE")
            self._expect("SEMICOLON")
            return Assignment(name=name, scope=inner_scope, expression=expr)
        expr = self._parse_expression()
        self._expect("SEMICOLON")
        return Assignment(name=name, scope=None, expression=expr)

    def _parse_print(self) -> Print:
        self._expect("PRINT")
        expr = self._parse_expression()
        self._expect("SEMICOLON")
        return Print(expression=expr)

    def _parse_if(self) -> IfElse:
        self._expect("IF")
        self._expect("LPAR")
        condition = self._parse_expression()
        self._expect("RPAR")
        if_statements = self._parse_block_statements()
        self._expect("ELSE")
        else_statements = self._parse_block_statements()
        return IfElse(condition=condition, if_statements=if_statements, else_statements=else_statements)

    def _parse_while(self) -> While:
        self._expect("WHILE")
        self._expect("LPAR")
        condition = self._parse_expression()
        self._expect("RPAR")
        statements = self._parse_block_statements()
        return While(condition=condition, statements=statements)

    def _parse_block_statements(self) -> List[Statement]:
        self._expect("LBRACE")
        statements: List[Statement] = []
        while self._can_start_statement():
            statements.append(self._parse_statement())
        self._expect("RBRACE")
        return statements

    # Expression parsing -------------------------------------------------

    def _parse_expression(self) -> Expression:
        return self._parse_disjunction()

    def _parse_disjunction(self) -> Expression:
        expr = self._parse_conjunction()
        while self._current().type == "OR":
            self._advance()
            right = self._parse_conjunction()
            expr = BinaryOp("or", expr, right)
        return expr

    def _parse_conjunction(self) -> Expression:
        expr = self._parse_inversion()
        while self._current().type == "AND":
            self._advance()
            right = self._parse_inversion()
            expr = BinaryOp("and", expr, right)
        return expr

    def _parse_inversion(self) -> Expression:
        if self._current().type == "NOT":
            self._advance()
            return UnaryOp("not", self._parse_inversion())
        return self._parse_comparison()

    def _parse_comparison(self) -> Expression:
        expr = self._parse_sum()
        while self._current().type in {
            "LESS",
            "GREATER",
            "EQUAL",
            "DIFFERENT",
            "GREATER_EQUAL",
            "LESS_EQUAL",
        }:
            token = self._advance()
            op_map = {
                "LESS": "<",
                "GREATER": ">",
                "EQUAL": "==",
                "DIFFERENT": "!=",
                "GREATER_EQUAL": ">=",
                "LESS_EQUAL": "<=",
            }
            right = self._parse_sum()
            expr = BinaryOp(op_map[token.type], expr, right)
        return expr

    def _parse_sum(self) -> Expression:
        expr = self._parse_product()
        while self._current().type in {"ADD", "SUBTRACT"}:
            token = self._advance()
            operator = "+" if token.type == "ADD" else "-"
            right = self._parse_product()
            expr = BinaryOp(operator, expr, right)
        return expr

    def _parse_product(self) -> Expression:
        expr = self._parse_factor()
        while self._current().type in {"MULTIPLY", "DIVIDE"}:
            token = self._advance()
            operator = "*" if token.type == "MULTIPLY" else "/"
            right = self._parse_factor()
            expr = BinaryOp(operator, expr, right)
        return expr

    def _parse_factor(self) -> Expression:
        token = self._current().type
        if token == "ADD":
            self._advance()
            return UnaryOp("+", self._parse_factor())
        if token == "SUBTRACT":
            self._advance()
            return UnaryOp("-", self._parse_factor())
        return self._parse_atom()

    def _parse_atom(self) -> Expression:
        token = self._current()
        if token.type == "NUMBER":
            self._advance()
            return Literal(int(token.value))  # type: ignore[arg-type]
        if token.type == "IDENTIFIER":
            self._advance()
            return Identifier(token.value)  # type: ignore[arg-type]
        if token.type == "TRUE":
            self._advance()
            return Literal(True)
        if token.type == "FALSE":
            self._advance()
            return Literal(False)
        if token.type == "LPAR":
            self._advance()
            expr = self._parse_expression()
            self._expect("RPAR")
            return expr
        raise ParserError(f"Unexpected token {token.type} at position {token.position}")


class Environment:
    def __init__(self, parent: Optional["Environment"] = None, stdout: Optional[TextIO] = None) -> None:
        self.parent = parent
        self.values: Dict[str, Union[int, bool]] = {}
        self.types: Dict[str, str] = {}
        self.stdout = stdout if stdout is not None else (parent.stdout if parent else sys.stdout)

    def create_child(self) -> "Environment":
        return Environment(parent=self, stdout=self.stdout)

    def define(self, name: str, typ: str) -> None:
        if name in self.values:
            raise InterpreterError(f"Variable already defined: {name}")
        if typ == "int":
            self.values[name] = 0
        else:
            self.values[name] = False
        self.types[name] = typ

    def set(self, name: str, value: Union[int, bool]) -> None:
        if name in self.values:
            expected = self.types[name]
            if expected == "int" and not isinstance(value, int):
                raise InterpreterError(f"Expected integer for variable {name}")
            if expected == "bool" and not isinstance(value, bool):
                raise InterpreterError(f"Expected boolean for variable {name}")
            self.values[name] = value
            return
        raise InterpreterError(f"Undefined variable: {name}")

    def get(self, name: str) -> Union[int, bool]:
        if name in self.values:
            return self.values[name]
        if self.parent is not None:
            return self.parent.get(name)
        raise InterpreterError(f"Undefined variable: {name}")

    def print(self, value: Union[int, bool]) -> None:
        if isinstance(value, bool):
            text = "True" if value else "False"
        else:
            text = str(value)
        self.stdout.write(text + "\n")
        self.stdout.flush()


class Interpreter:
    def __init__(self, scope: Scope, stdout: Optional[TextIO] = None) -> None:
        self.scope = scope
        self.environment = Environment(stdout=stdout)

    def run(self) -> None:
        self._execute_scope(self.scope, self.environment)

    def _execute_scope(self, scope: Scope, environment: Environment) -> None:
        for declaration in scope.declarations:
            environment.define(declaration.name, declaration.typ)
        for statement in scope.statements:
            self._execute_statement(statement, environment)

    def _execute_statement(self, statement: Statement, environment: Environment) -> None:
        if isinstance(statement, Declare):
            environment.define(statement.name, statement.typ)
        elif isinstance(statement, Assignment):
            self._execute_assignment(statement, environment)
        elif isinstance(statement, Print):
            value = self._evaluate_expression(statement.expression, environment)
            environment.print(value)
        elif isinstance(statement, IfElse):
            condition = self._evaluate_expression(statement.condition, environment)
            if not isinstance(condition, bool):
                raise InterpreterError("If condition must be boolean")
            body = statement.if_statements if condition else statement.else_statements
            for stmt in body:
                self._execute_statement(stmt, environment)
        elif isinstance(statement, While):
            while True:
                condition = self._evaluate_expression(statement.condition, environment)
                if not isinstance(condition, bool):
                    raise InterpreterError("While condition must be boolean")
                if not condition:
                    break
                for stmt in statement.statements:
                    self._execute_statement(stmt, environment)
        else:  # pragma: no cover - defensive programming
            raise InterpreterError(f"Unsupported statement: {statement}")

    def _execute_assignment(self, statement: Assignment, environment: Environment) -> None:
        target_env = environment
        if statement.scope is not None and (
            statement.scope.declarations or statement.scope.statements
        ):
            target_env = environment
            scope_env = environment.create_child()
            self._execute_scope(statement.scope, scope_env)
            value = self._evaluate_expression(statement.expression, scope_env)
        else:
            scope_env = environment
            value = self._evaluate_expression(statement.expression, scope_env)

        target_env.set(statement.name, value)

    def _evaluate_expression(self, expression: Expression, environment: Environment) -> Union[int, bool]:
        if isinstance(expression, Literal):
            return expression.value
        if isinstance(expression, Identifier):
            return environment.get(expression.name)
        if isinstance(expression, UnaryOp):
            value = self._evaluate_expression(expression.operand, environment)
            if expression.operator == "not":
                if not isinstance(value, bool):
                    raise InterpreterError("Operator 'not' expects a boolean")
                return not value
            if expression.operator in {"+", "-"}:
                if not isinstance(value, int):
                    raise InterpreterError(f"Unary {expression.operator} expects an integer")
                return value if expression.operator == "+" else -value
            raise InterpreterError(f"Unsupported unary operator {expression.operator}")
        if isinstance(expression, BinaryOp):
            left = self._evaluate_expression(expression.left, environment)
            right = self._evaluate_expression(expression.right, environment)
            op = expression.operator
            if op in {"+", "-", "*", "/"}:
                if not isinstance(left, int) or not isinstance(right, int):
                    raise InterpreterError(f"Operator '{op}' expects integers")
                if op == "+":
                    return left + right
                if op == "-":
                    return left - right
                if op == "*":
                    return left * right
                if right == 0:
                    raise InterpreterError("Division by zero")
                return int(left / right)
            if op in {"and", "or"}:
                if not isinstance(left, bool) or not isinstance(right, bool):
                    raise InterpreterError(f"Operator '{op}' expects booleans")
                return (left and right) if op == "and" else (left or right)
            if op in {"==", "!="}:
                if type(left) is not type(right):
                    raise InterpreterError("Cannot compare values of different types")
                return (left == right) if op == "==" else (left != right)
            if op in {"<", ">", "<=", ">="}:
                if not isinstance(left, int) or not isinstance(right, int):
                    raise InterpreterError(f"Operator '{op}' expects integers")
                if op == "<":
                    return left < right
                if op == ">":
                    return left > right
                if op == "<=":
                    return left <= right
                if op == ">=":
                    return left >= right
            raise InterpreterError(f"Unsupported binary operator {op}")
        raise InterpreterError(f"Unsupported expression: {expression}")


def interpret(source: str, stdout: Optional[TextIO] = None) -> None:
    lexer = Lexer(source)
    tokens = lexer.tokens()
    parser = Parser(tokens)
    scope = parser.parse()
    interpreter = Interpreter(scope, stdout=stdout)
    interpreter.run()


def interpret_file(path: str, stdout: Optional[TextIO] = None) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        interpret(handle.read(), stdout=stdout)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python interpreter.py <file>")
        sys.exit(1)
    interpret_file(sys.argv[1])
