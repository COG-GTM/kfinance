from typing import Any

from jsonschema import Draft202012Validator
import pytest

from kfinance.domains.line_items.line_item_tools import GetFinancialLineItemFromIdentifiersArgs
from kfinance.integrations.local_mcp.local_mcp import accept_stringified_numbers


class TestAcceptStringifiedNumbers:
    def test_integer_field_accepts_ints_and_stringified_ints(self) -> None:
        """
        GIVEN a schema with an integer field
        WHEN widening the schema
        THEN the field accepts integers and strings that hold an integer
        """
        widened = accept_stringified_numbers({"type": "integer", "minimum": 1})
        validator = Draft202012Validator(widened)

        assert validator.is_valid(2023)
        assert validator.is_valid("2023")
        assert not validator.is_valid("not an int")
        assert not validator.is_valid([2023])

    def test_number_field_accepts_floats_and_stringified_floats(self) -> None:
        """
        GIVEN a schema with a number field
        WHEN widening the schema
        THEN the field accepts numbers and strings that hold a number
        """
        widened = accept_stringified_numbers({"type": "number"})
        validator = Draft202012Validator(widened)

        assert validator.is_valid(1.5)
        assert validator.is_valid("-1.5e3")
        assert not validator.is_valid("1.5 dollars")

    def test_non_numeric_types_are_unchanged(self) -> None:
        """
        GIVEN a schema without numeric fields
        WHEN widening the schema
        THEN the schema is unchanged
        """
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"line_item": {"type": "string", "enum": ["revenue"]}},
            "required": ["line_item"],
        }

        assert accept_stringified_numbers(schema) == schema

    def test_nested_and_optional_integer_fields_are_widened(self) -> None:
        """
        GIVEN the args schema of a tool with optional integer fields
        WHEN widening the schema
        THEN nested integer types accept stringified integers
        """
        widened = accept_stringified_numbers(
            GetFinancialLineItemFromIdentifiersArgs.model_json_schema()
        )
        validator = Draft202012Validator(widened)

        assert validator.is_valid(
            {"identifiers": ["SPGI"], "line_item": "revenue", "start_year": "2023"}
        )
        assert validator.is_valid(
            {"identifiers": ["SPGI"], "line_item": "revenue", "start_year": 2023}
        )
        assert validator.is_valid(
            {"identifiers": ["SPGI"], "line_item": "revenue", "start_year": None}
        )

    @pytest.mark.parametrize("out_of_schema_line_item", ["a" * 10_000, "revenu"])
    def test_out_of_enum_line_items_are_rejected(self, out_of_schema_line_item: str) -> None:
        """
        GIVEN the args schema of a tool with a line item enum
        WHEN validating an out-of-enum line item
        THEN schema validation rejects it before any tool code runs
        """
        widened = accept_stringified_numbers(
            GetFinancialLineItemFromIdentifiersArgs.model_json_schema()
        )

        assert not Draft202012Validator(widened).is_valid(
            {"identifiers": ["SPGI"], "line_item": out_of_schema_line_item}
        )
