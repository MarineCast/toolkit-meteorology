"""Shared physical point-block layout for bounded native normalization."""

from __future__ import annotations

import numpy as np

from .resources import POINT_BATCH


def write_point_blocks(writer, table, groups, row_group, *, before_write=None):
    """Address each point block directly; time-major predicates are insufficient."""
    if not np.array_equal(table["SOURCE_GRID_INDEX"].to_numpy(), np.arange(table.num_rows)):
        raise ValueError("Normalization requires ordered complete native point indices.")
    for offset in range(0, table.num_rows, POINT_BATCH):
        block = table.slice(offset, min(POINT_BATCH, table.num_rows - offset))
        if before_write is not None:
            before_write(block.num_rows)
        writer.write_table(block, row_group_size=POINT_BATCH)
        groups.setdefault(offset, []).append(row_group)
        row_group += 1
    return row_group
