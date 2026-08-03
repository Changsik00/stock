"""positioning_snapshot table

Revision ID: cb6ce553dc11
Revises: 5bb43f225335
Create Date: 2026-08-03 12:37:03.262357

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb6ce553dc11'
down_revision: Union[str, None] = '5bb43f225335'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('positioning_snapshot',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('snapshot_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('regime', sa.String(length=20), nullable=True),
    sa.Column('concentration_pct', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('foreign_spot_cum', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('foreign_futures_cum', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('sox_change_rate', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('nasdaq_futures_change_pct', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('hynix_price_at_snapshot', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('hynix_change_rate_at_snapshot', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('kospi_change_rate_at_snapshot', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('relative_strength_pct', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('risk_alert_count', sa.SmallInteger(), nullable=True),
    sa.Column('same_day_remaining_change_rate', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('next_day_change_rate', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.PrimaryKeyConstraint('date')
    )


def downgrade() -> None:
    op.drop_table('positioning_snapshot')
