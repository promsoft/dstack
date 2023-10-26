"""Remove nullable fields

Revision ID: 3fd330898368
Revises: 4e739fa3ee54
Create Date: 2023-10-26 10:11:57.453478

"""
import sqlalchemy as sa
import sqlalchemy_utils
from alembic import op

# revision identifiers, used by Alembic.
revision = "3fd330898368"
down_revision = "4e739fa3ee54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.alter_column("owner_id", existing_type=sa.CHAR(length=32), nullable=False)

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("projects_quota", existing_type=sa.INTEGER(), nullable=False)

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("projects_quota", existing_type=sa.INTEGER(), nullable=True)

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.alter_column("owner_id", existing_type=sa.CHAR(length=32), nullable=True)

    # ### end Alembic commands ###
