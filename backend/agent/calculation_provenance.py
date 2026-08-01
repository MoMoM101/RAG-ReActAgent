"""Deterministic provenance checks for calculator expressions."""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Iterable

_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?%?")


def numeric_values(text: str) -> set[float]:
    """Extract comparable numeric values, normalizing percentages to ratios."""
    values: set[float] = set()
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0).replace(",", "")
        is_percent = raw.endswith("%")
        try:
            value = float(raw.rstrip("%"))
        except ValueError:
            continue
        values.add(value / 100 if is_percent else value)
    return values


def expression_has_arithmetic(expression: str) -> bool:
    node = _allowed_expression_node(expression)
    return node is not None and any(isinstance(item, ast.BinOp) for item in ast.walk(node))


def expression_operation_count(expression: str) -> int:
    """Count binary arithmetic operations in a safe calculator expression."""
    node = _allowed_expression_node(expression)
    if node is None:
        return 0
    return sum(isinstance(item, ast.BinOp) for item in ast.walk(node))


def _allowed_expression_node(expression: str) -> ast.expr | None:
    try:
        node = ast.parse(expression.strip(), mode="eval").body
    except SyntaxError:
        return None

    def allowed(item: ast.expr) -> bool:
        if isinstance(item, ast.Constant):
            return (
                isinstance(item.value, int | float)
                and not isinstance(item.value, bool)
                and math.isfinite(float(item.value))
            )
        if isinstance(item, ast.UnaryOp):
            return isinstance(item.op, ast.USub) and allowed(item.operand)
        if isinstance(item, ast.BinOp):
            return (
                isinstance(item.op, ast.Add | ast.Sub | ast.Mult | ast.Div)
                and allowed(item.left)
                and allowed(item.right)
            )
        return False

    return node if allowed(node) else None


def _expression_operands(expression: str) -> set[float]:
    node = _allowed_expression_node(expression)
    if node is None:
        return set()
    return {
        float(item.value)
        for item in ast.walk(node)
        if isinstance(item, ast.Constant)
        and isinstance(item.value, int | float)
        and not isinstance(item.value, bool)
    }


def expression_uses_known_values(
    expression: str,
    known_values: Iterable[float],
) -> bool:
    """Require every literal operand to have an established provenance."""
    known = set(known_values) | {1.0, 100.0}
    operands = _expression_operands(expression)
    return bool(operands) and all(
        any(math.isclose(value, item, rel_tol=1e-12, abs_tol=1e-9) for item in known)
        for value in operands
    )


def evaluate_expression(expression: str) -> float | None:
    """Safely evaluate the arithmetic subset accepted by the calculator."""
    node = _allowed_expression_node(expression)
    if node is None:
        return None

    def evaluate(item: ast.expr) -> float:
        if isinstance(item, ast.Constant) and isinstance(item.value, int | float):
            return float(item.value)
        if isinstance(item, ast.UnaryOp) and isinstance(item.op, ast.USub):
            return -evaluate(item.operand)
        if isinstance(item, ast.BinOp) and isinstance(item.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left = evaluate(item.left)
            right = evaluate(item.right)
            if isinstance(item.op, ast.Add):
                return left + right
            if isinstance(item.op, ast.Sub):
                return left - right
            if isinstance(item.op, ast.Mult):
                return left * right
            return left / right
        raise ValueError

    try:
        result = evaluate(node)
        return result if math.isfinite(result) else None
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def arithmetic_expressions(text: str) -> list[str]:
    """Extract visible arithmetic expressions that precede an equals sign.

    This is used only as a recovery path when a model writes a valid equation
    in its draft but forgets to emit a calculator tool call.  Prose numbers are
    ignored, and callers must still enforce operand provenance.
    """
    normalized = (
        text.replace("＝", "=")
        .replace("×", "*")
        .replace("÷", "/")
        .replace("（", "(")
        .replace("）", ")")
    )
    normalized = re.sub(
        r"(\d+(?:\.\d+)?)\s*%",
        lambda match: f"({match.group(1)}/100)",
        normalized,
    )
    expressions: list[str] = []
    seen: set[str] = set()
    for line in re.split(r"[。！？!?；;\n]+", normalized):
        segments = line.split("=")
        for segment in segments[:-1]:
            match = re.search(r"[\d().+\-*/\s]+$", segment)
            if not match:
                continue
            expression = "".join(match.group(0).split())
            if not expression_has_arithmetic(expression) or expression in seen:
                continue
            seen.add(expression)
            expressions.append(expression)
    return expressions
