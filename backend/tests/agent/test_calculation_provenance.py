"""Calculator expression provenance and safe-evaluation tests."""

from agent.calculation_provenance import (
    arithmetic_expressions,
    evaluate_expression,
    expression_has_arithmetic,
    expression_operation_count,
    expression_uses_known_values,
    numeric_values,
)


def test_extracts_visible_equations_for_calculator_recovery():
    text = (
        "超额费用：（75 - 40） × 36 = 1260 元。\n"
        "订阅原价 = (1280 + 1260) * 12 = 30480 元；"
        "折后订阅 = 30480 × (1 - 10%) = 27432 元。"
    )

    assert arithmetic_expressions(text) == [
        "(75-40)*36",
        "(1280+1260)*12",
        "30480*(1-(10/100))",
    ]


def test_numeric_values_normalize_currency_and_percentages():
    assert numeric_values("总额43,032元，折扣10%，比例0.1") == {43032.0, 0.1}


def test_expression_validation_matches_calculator_arithmetic_subset():
    assert expression_has_arithmetic("1e3 + 2")
    assert expression_has_arithmetic("-(5 + 3) / 2")
    assert not expression_has_arithmetic("7600")
    assert not expression_has_arithmetic("2 ** 3")
    assert not expression_has_arithmetic("abs(-2)")
    assert expression_operation_count("75 - 40") == 1
    assert expression_operation_count("(1280 + (75 - 40) * 36) * 12") == 4
    assert expression_operation_count("30480 * (1 - 0.1)") == 2
    assert expression_operation_count("7600") == 0


def test_scientific_notation_uses_its_numeric_value_for_provenance():
    assert expression_uses_known_values("1e3 + 2", {1000.0, 2.0})
    assert not expression_uses_known_values("1e3 + 2", {1.0, 2.0})


def test_safe_evaluation_rejects_invalid_or_non_finite_results():
    assert evaluate_expression("-(5 + 3) / 2") == -4.0
    assert evaluate_expression("1 / 0") is None
    assert evaluate_expression("1e308 * 1e308") is None
    assert evaluate_expression("2 ** 3") is None
