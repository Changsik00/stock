"""volume_profile_daily table

Revision ID: 770ef1e191e5
Revises: ca96115c3353
Create Date: 2026-07-28 08:50:49.184364

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '770ef1e191e5'
down_revision: Union[str, None] = 'ca96115c3353'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('volume_profile_daily',
    sa.Column('entity_type', sa.String(length=10), nullable=False),
    sa.Column('entity_code', sa.String(length=20), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('poc_price', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('levels', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('bar_count', sa.SmallInteger(), nullable=False),
    sa.Column('total_volume', sa.Numeric(precision=24, scale=4), nullable=True),
    sa.Column('lookback_days', sa.SmallInteger(), nullable=False),
    sa.PrimaryKeyConstraint('entity_type', 'entity_code', 'date')
    )


def downgrade() -> None:
    op.drop_table('volume_profile_daily')
