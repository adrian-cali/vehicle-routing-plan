"""initial schema

Revision ID: 001_init
Revises: 
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_init'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create users table
    op.create_table('users',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    # Create tasks table
    op.create_table('tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('address', sa.Text(), nullable=False),
        sa.Column('latitude', sa.Double(), nullable=True),
        sa.Column('longitude', sa.Double(), nullable=True),
        sa.Column('priority', sa.Float(), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tasks_status', 'tasks', ['status'])
    op.create_index('ix_tasks_user_id', 'tasks', ['user_id'])
    op.create_index('ix_tasks_priority', 'tasks', ['priority'])
    op.create_index('ix_tasks_lat_long', 'tasks', ['latitude', 'longitude'])

    # Create fm_home_locations table
    op.create_table('fm_home_locations',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('home_lat', sa.Double(), nullable=False),
        sa.Column('home_long', sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id')
    )

    # Create areas table
    op.create_table('areas',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # Create fm_assigned_areas table
    op.create_table('fm_assigned_areas',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('area_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['area_id'], ['areas.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id', 'area_id')
    )
    op.create_index('ix_fm_assigned_areas_area_id', 'fm_assigned_areas', ['area_id'])

    # Create vrp_jobs table
    op.create_table('vrp_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('requestor_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(['requestor_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_vrp_jobs_requestor_user_id', 'vrp_jobs', ['requestor_user_id'])
    op.create_index('ix_vrp_jobs_status', 'vrp_jobs', ['status'])
    op.create_index('ix_vrp_jobs_created_at', 'vrp_jobs', ['created_at'])

    # Create vrp_assignments table
    op.create_table('vrp_assignments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('vrp_job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('fm_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sequence_no', sa.Integer(), nullable=False),
        sa.Column('eta_seconds', sa.Integer(), nullable=True),
        sa.Column('distance_meters', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['fm_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
        sa.ForeignKeyConstraint(['vrp_job_id'], ['vrp_jobs.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_vrp_assignments_unique', 'vrp_assignments', ['vrp_job_id', 'task_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_vrp_assignments_unique', table_name='vrp_assignments')
    op.drop_table('vrp_assignments')
    op.drop_index('ix_vrp_jobs_created_at', table_name='vrp_jobs')
    op.drop_index('ix_vrp_jobs_status', table_name='vrp_jobs')
    op.drop_index('ix_vrp_jobs_requestor_user_id', table_name='vrp_jobs')
    op.drop_table('vrp_jobs')
    op.drop_index('ix_fm_assigned_areas_area_id', table_name='fm_assigned_areas')
    op.drop_table('fm_assigned_areas')
    op.drop_table('areas')
    op.drop_table('fm_home_locations')
    op.drop_index('ix_tasks_lat_long', table_name='tasks')
    op.drop_index('ix_tasks_priority', table_name='tasks')
    op.drop_index('ix_tasks_user_id', table_name='tasks')
    op.drop_index('ix_tasks_status', table_name='tasks')
    op.drop_table('tasks')
    op.drop_table('users')
