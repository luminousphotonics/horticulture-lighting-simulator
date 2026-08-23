from __future__ import annotations

import numpy as np
import pytest

from fspm_optics.transport.basis.parsing import (
    parse_and_stack_basis_columns,
    parse_basis_column,
    stack_basis_columns,
)


def test_parse_scalar_trace_output_into_one_basis_column() -> None:
    column = parse_basis_column(
        "1 1 1\n2.5 2.5 2.5\n3 3 3\n",
        expected_sensor_count=3,
    )
    np.testing.assert_allclose(column, [1.0, 2.5, 3.0])
    assert column.shape == (3,)


def test_parsed_columns_stack_as_sensors_by_control_zones() -> None:
    matrix = parse_and_stack_basis_columns(
        (
            "1 1 1\n2 2 2\n3 3 3\n",
            "10 10 10\n20 20 20\n30 30 30\n",
        ),
        expected_sensor_count=3,
        expected_control_zone_count=2,
    )
    np.testing.assert_allclose(
        matrix,
        [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]],
    )
    assert matrix.shape == (3, 2)


def test_column_parser_preserves_scalar_rgb_equality_contract() -> None:
    with pytest.raises(ValueError, match="grey scalar channels"):
        parse_basis_column("1 2 1\n")


def test_column_parser_rejects_sensor_count_mismatch() -> None:
    with pytest.raises(ValueError, match="expected 3, got 2"):
        parse_basis_column("1 1 1\n2 2 2\n", expected_sensor_count=3)


def test_column_stacking_rejects_mismatched_lengths_and_counts() -> None:
    with pytest.raises(ValueError, match="same sensor count"):
        stack_basis_columns(([1.0, 2.0], [3.0]))
    with pytest.raises(ValueError, match="control-zone count mismatch"):
        stack_basis_columns(
            ([1.0, 2.0], [3.0, 4.0]),
            expected_control_zone_count=3,
        )
