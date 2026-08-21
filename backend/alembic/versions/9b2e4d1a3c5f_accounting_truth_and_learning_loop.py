"""accounting truth + learning loop (entry_charges, highest_price, signal_outcomes)

Revision ID: 9b2e4d1a3c5f
Revises: 07c8a3a5cc1c
Create Date: 2026-05-30 03:30:00.000000

Adds:
  - positions.entry_charges  (Numeric(12,4), default 0)
      Stores the opening-leg brokerage so realized P&L can deduct BOTH legs honestly.
      Fixes the silent ~₹100-120/trade overstatement.

  - positions.highest_price  (Numeric(12,2), nullable)
      High-water mark for the trailing-stop ratchet (updated each monitor cycle).

  - signal_outcomes table
      One row per position close. Foundation of the learning loop. Lets us answer
      "is agent X profitable?" / "do score≥80 picks beat 60-70?" with data, not guesses.

All changes are forward-compatible: new columns are NOT NULL with sensible defaults
(0 for entry_charges) or nullable (highest_price). Old positions can be left as-is.
"""
from typing import Sequence, Union
from decimal import Decimal

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9b2e4d1a3c5f'
down_revision: Union[str, None] = '07c8a3a5cc1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── positions: add accounting + trailing-stop columns ──
    op.add_column(
        'positions',
        sa.Column(
            'entry_charges',
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )
    op.add_column(
        'positions',
        sa.Column(
            'highest_price',
            sa.Numeric(precision=12, scale=2),
            nullable=True,
        ),
    )

    # Backfill highest_price for currently-open positions to the higher of
    # avg_price (entry) or current_price. Without this, the trailing stop
    # wouldn't kick in on positions opened pre-migration until their next tick.
    op.execute(
        """
        UPDATE positions
        SET highest_price = GREATEST(avg_price, COALESCE(current_price, avg_price))
        WHERE closed_at IS NULL AND highest_price IS NULL
        """
    )

    # ── signal_outcomes: learning-loop table ──
    op.create_table(
        'signal_outcomes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('position_id', sa.UUID(), nullable=False),
        sa.Column('symbol', sa.String(length=30), nullable=False),
        sa.Column('exchange', sa.String(length=10), nullable=False),
        sa.Column('strategy_type', sa.String(length=50), nullable=False),
        sa.Column('agent_name', sa.String(length=100), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('days_held', sa.Integer(), nullable=False),
        sa.Column('close_reason', sa.String(length=30), nullable=False),
        sa.Column('entry_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('exit_price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('stop_loss_set', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('take_profit_set', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('highest_price_seen', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('entry_value', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('gross_pnl', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('total_charges', sa.Numeric(precision=12, scale=4), nullable=False, server_default=sa.text('0')),
        sa.Column('net_pnl', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('net_pnl_pct', sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column('signal_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('confidence_at_entry', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('expected_pnl_pct', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('r_multiple', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id']),
        sa.ForeignKeyConstraint(['position_id'], ['positions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_signal_outcomes_agent_strategy',
        'signal_outcomes',
        ['agent_id', 'strategy_type', 'closed_at'],
    )
    op.create_index(
        'ix_signal_outcomes_close_reason',
        'signal_outcomes',
        ['close_reason', 'closed_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_signal_outcomes_close_reason', table_name='signal_outcomes')
    op.drop_index('ix_signal_outcomes_agent_strategy', table_name='signal_outcomes')
    op.drop_table('signal_outcomes')
    op.drop_column('positions', 'highest_price')
    op.drop_column('positions', 'entry_charges')
