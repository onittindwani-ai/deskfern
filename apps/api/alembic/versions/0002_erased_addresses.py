"""erased addresses (a salted hash per address that asked to be erased)

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
import sendora_core.db


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('lh_erased',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email_hash', sa.String(length=64), nullable=False),
    sa.Column('erased_at', sendora_core.db.UTCDateTime(), nullable=False),
    sa.Column('erased_by', sa.String(length=254), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('lh_erased', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lh_erased_email_hash'), ['email_hash'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('lh_erased', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lh_erased_email_hash'))

    op.drop_table('lh_erased')
