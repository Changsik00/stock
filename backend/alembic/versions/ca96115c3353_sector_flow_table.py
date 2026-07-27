"""sector_flow table (PLAN.md §5.33-1)

Revision ID: ca96115c3353
Revises: 34587cfc50da
Create Date: 2026-07-28 05:10:29.372047

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ca96115c3353'
down_revision: Union[str, None] = '34587cfc50da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sector_flow',
    sa.Column('market', sa.String(length=20), nullable=False),
    sa.Column('sector_code', sa.String(length=20), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('sector_name', sa.String(length=50), nullable=True),
    sa.Column('frgnr_net_value', sa.BigInteger(), nullable=True),
    sa.Column('orgn_net_value', sa.BigInteger(), nullable=True),
    sa.Column('ind_net_value', sa.BigInteger(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=True),
    sa.PrimaryKeyConstraint('market', 'sector_code', 'date')
    )


def downgrade() -> None:
    op.drop_table('sector_flow')
