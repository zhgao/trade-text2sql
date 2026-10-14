"""护栏单元测试：拦截危险 SQL，自动补/钳制 LIMIT。"""
import pytest

from backend.agent.guardrail import GuardrailError, validate_and_fix


def test_reject_drop_table():
    with pytest.raises(GuardrailError):
        validate_and_fix("SELECT * FROM corporates; DROP TABLE corporates;")


def test_reject_delete_statement():
    with pytest.raises(GuardrailError):
        validate_and_fix("DELETE FROM payments WHERE id = 1")


def test_reject_update_statement():
    with pytest.raises(GuardrailError):
        validate_and_fix("UPDATE letters_of_credit SET status = '已付款'")


def test_reject_multi_statement():
    with pytest.raises(GuardrailError):
        validate_and_fix("SELECT * FROM corporates; SELECT * FROM payments")


def test_reject_non_whitelisted_table():
    with pytest.raises(GuardrailError):
        validate_and_fix("SELECT * FROM query_audit")


def test_reject_table_not_in_whitelist_join():
    with pytest.raises(GuardrailError):
        validate_and_fix(
            "SELECT * FROM corporates c JOIN sqlite_master m ON 1 = 1"
        )


def test_reject_banned_keyword_in_subquery():
    with pytest.raises(GuardrailError):
        validate_and_fix(
            "SELECT * FROM corporates WHERE id IN (SELECT id FROM payments); "
            "INSERT INTO payments(amount) VALUES (1)"
        )


def test_auto_add_limit():
    fixed = validate_and_fix("SELECT * FROM corporates")
    assert fixed.rstrip().upper().endswith("LIMIT 50")


def test_clamp_limit_over_max():
    fixed = validate_and_fix("SELECT * FROM corporates LIMIT 500")
    assert "LIMIT 200" in fixed.upper()
    assert "500" not in fixed


def test_keep_reasonable_limit():
    fixed = validate_and_fix("SELECT * FROM corporates LIMIT 10")
    assert "LIMIT 10" in fixed.upper()


def test_banned_word_inside_string_literal_is_ok():
    # 值里的 "DROP" 不应误杀
    fixed = validate_and_fix(
        "SELECT * FROM corporates WHERE name_cn = 'DROP测试公司'"
    )
    assert "corporates" in fixed


def test_valid_select_with_where_passes():
    fixed = validate_and_fix(
        "SELECT lc_number, status FROM letters_of_credit WHERE status = '逾期'"
    )
    assert "letters_of_credit" in fixed
    assert "LIMIT 50" in fixed.upper()


def test_empty_sql_rejected():
    with pytest.raises(GuardrailError):
        validate_and_fix("   ")
