"""investor_warning_event table (PLAN.md §5.39)

Revision ID: cfce0c8583f9
Revises: bb39bbfc5f0c
Create Date: 2026-07-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cfce0c8583f9'
down_revision: Union[str, None] = 'bb39bbfc5f0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('investor_warning_event',
    sa.Column('tier', sa.String(length=10), nullable=False),
    sa.Column('raw_name', sa.String(length=100), nullable=False),
    sa.Column('designated_date', sa.Date(), nullable=False),
    sa.Column('code', sa.String(length=20), nullable=True),
    sa.Column('market', sa.String(length=10), nullable=True),
    sa.Column('warning_type', sa.String(length=50), nullable=True),
    sa.Column('notice_date', sa.Date(), nullable=True),
    sa.Column('released_date', sa.Date(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['code'], ['stocks.code'], ),
    sa.PrimaryKeyConstraint('tier', 'raw_name', 'designated_date')
    )
    op.create_index('ix_investor_warning_event_code', 'investor_warning_event', ['code'])


def downgrade() -> None:
    op.drop_index('ix_investor_warning_event_code', table_name='investor_warning_event')
    op.drop_table('investor_warning_event')
