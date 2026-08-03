"""paper_trade table

Revision ID: 5bb43f225335
Revises: cfce0c8583f9
Create Date: 2026-08-03 11:22:12.436090

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5bb43f225335'
down_revision: Union[str, None] = 'cfce0c8583f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('paper_trade',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('side', sa.String(length=10), nullable=False),
    sa.Column('entry_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('entry_qty', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('entry_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('exit_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('exit_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_paper_trade_code', 'paper_trade', ['code'])
    op.create_index('ix_paper_trade_status', 'paper_trade', ['status'])


def downgrade() -> None:
    op.drop_index('ix_paper_trade_status', table_name='paper_trade')
    op.drop_index('ix_paper_trade_code', table_name='paper_trade')
    op.drop_table('paper_trade')
