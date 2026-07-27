"""short_selling_market/short_selling_stock tables (PLAN.md §5.32)

Revision ID: 34587cfc50da
Revises: a22f6b66dffe
Create Date: 2026-07-27 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '34587cfc50da'
down_revision: Union[str, None] = 'a22f6b66dffe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('short_selling_market',
    sa.Column('market', sa.String(length=10), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('short_volume', sa.BigInteger(), nullable=True),
    sa.Column('short_value', sa.BigInteger(), nullable=True),
    sa.Column('total_volume', sa.BigInteger(), nullable=True),
    sa.Column('total_value', sa.BigInteger(), nullable=True),
    sa.Column('volume_ratio', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('value_ratio', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.PrimaryKeyConstraint('market', 'date')
    )
    op.create_table('short_selling_stock',
    sa.Column('code', sa.String(length=20), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=True),
    sa.Column('value', sa.BigInteger(), nullable=True),
    sa.Column('balance_qty', sa.BigInteger(), nullable=True),
    sa.Column('balance_value', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['code'], ['stocks.code'], ),
    sa.PrimaryKeyConstraint('code', 'date')
    )


def downgrade() -> None:
    op.drop_table('short_selling_stock')
    op.drop_table('short_selling_market')
