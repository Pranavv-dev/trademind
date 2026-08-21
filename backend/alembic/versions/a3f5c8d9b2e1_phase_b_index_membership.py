"""phase B — index_membership for point-in-time backtests

Revision ID: a3f5c8d9b2e1
Revises: 9b2e4d1a3c5f
Create Date: 2026-05-30 13:00:00.000000

Adds the index_membership table that captures a symbol's tenure in a named
index over a date range. The backtest engine filters with as_of_date so
historical signals are computed on the universe that existed AT THAT TIME —
fixing the ~9%/yr survivorship bias from running backtests on today's roster.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f5c8d9b2e1'
down_revision: Union[str, None] = '9b2e4d1a3c5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'index_membership',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('index_name', sa.String(length=30), nullable=False),
        sa.Column('symbol', sa.String(length=30), nullable=False),
        sa.Column('from_date', sa.Date(), nullable=False),
        sa.Column('to_date', sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('index_name', 'symbol', 'from_date', name='uq_index_member_period'),
    )
    op.create_index(
        'ix_idx_member_lookup',
        'index_membership',
        ['index_name', 'symbol', 'from_date', 'to_date'],
    )


def downgrade() -> None:
    op.drop_index('ix_idx_member_lookup', table_name='index_membership')
    op.drop_table('index_membership')
