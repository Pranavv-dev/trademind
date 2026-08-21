"""add reflection column to signal_outcomes (learning loop)

Revision ID: c7e1f2a4d8b9
Revises: a3f5c8d9b2e1
Create Date: 2026-06-15 09:00:00.000000

The "reflection" half of the learning loop (inspired by TauricResearch/TradingAgents'
memory mechanism). signal_outcomes already captures the data; this column stores a
one-line post-mortem per closed trade that SignalMemory surfaces back into future
ProactiveAgent decisions.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7e1f2a4d8b9'
down_revision: Union[str, None] = 'a3f5c8d9b2e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy.dialects import postgresql
    op.add_column(
        'signal_outcomes',
        sa.Column('reflection', sa.String(length=500), nullable=True),
    )
    # Snapshot of the opening signal's metadata, so the close-time reflection can
    # recall the entry thesis (context_score, RS, sector momentum, regime).
    op.add_column(
        'positions',
        sa.Column(
            'entry_metadata',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column('positions', 'entry_metadata')
    op.drop_column('signal_outcomes', 'reflection')
