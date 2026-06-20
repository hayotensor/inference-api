"""admin rbac

Revision ID: 20260617_0003
Revises: 20260614_0002
Create Date: 2026-06-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260617_0003"
down_revision: str | None = "20260614_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_admin_roles_slug", "admin_roles", ["slug"])

    op.create_table(
        "admin_role_permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["role_id"], ["admin_roles.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission", name="uq_admin_role_permissions_role_permission"),
    )
    op.create_index("ix_admin_role_permissions_permission", "admin_role_permissions", ["permission"])
    op.create_index("ix_admin_role_permissions_role_id", "admin_role_permissions", ["role_id"])

    op.create_table(
        "admin_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="set null"),
        sa.ForeignKeyConstraint(["role_id"], ["admin_roles.id"], ondelete="cascade"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_assignments_granted_at", "admin_assignments", ["granted_at"])
    op.create_index("ix_admin_assignments_granted_by_user_id", "admin_assignments", ["granted_by_user_id"])
    op.create_index("ix_admin_assignments_revoked_at", "admin_assignments", ["revoked_at"])
    op.create_index("ix_admin_assignments_role_id", "admin_assignments", ["role_id"])
    op.create_index("ix_admin_assignments_user_id", "admin_assignments", ["user_id"])

    op.add_column("audit_logs", sa.Column("actor_user_id", sa.Uuid(), nullable=True))
    op.add_column("audit_logs", sa.Column("target_type", sa.String(length=80), nullable=True))
    op.add_column("audit_logs", sa.Column("target_id", sa.String(length=120), nullable=True))
    op.add_column(
        "audit_logs",
        sa.Column("result", sa.String(length=32), nullable=False, server_default="success"),
    )
    op.add_column("audit_logs", sa.Column("request_id", sa.String(length=128), nullable=True))
    op.create_foreign_key(
        "fk_audit_logs_actor_user_id_users",
        "audit_logs",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="set null",
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_result", "audit_logs", ["result"])
    op.create_index("ix_audit_logs_target_id", "audit_logs", ["target_id"])
    op.create_index("ix_audit_logs_target_type", "audit_logs", ["target_type"])

    op.add_column("billing_plans", sa.Column("slug", sa.String(length=80), nullable=True))
    op.add_column("billing_plans", sa.Column("description", sa.String(length=512), nullable=True))
    op.add_column(
        "billing_plans",
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "billing_plans",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "billing_plans",
        sa.Column("features", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.execute(
        """
        UPDATE billing_plans
        SET slug = trim(both '-' from regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g'))
        WHERE slug IS NULL
        """
    )
    op.alter_column("billing_plans", "slug", nullable=False)
    op.create_unique_constraint("uq_billing_plans_slug", "billing_plans", ["slug"])
    op.create_index("ix_billing_plans_slug", "billing_plans", ["slug"])
    op.create_index("ix_billing_plans_visible", "billing_plans", ["visible"])


def downgrade() -> None:
    op.drop_index("ix_billing_plans_visible", table_name="billing_plans")
    op.drop_index("ix_billing_plans_slug", table_name="billing_plans")
    op.drop_constraint("uq_billing_plans_slug", "billing_plans", type_="unique")
    op.drop_column("billing_plans", "features")
    op.drop_column("billing_plans", "sort_order")
    op.drop_column("billing_plans", "visible")
    op.drop_column("billing_plans", "description")
    op.drop_column("billing_plans", "slug")

    op.drop_index("ix_audit_logs_target_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_target_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_result", table_name="audit_logs")
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_actor_user_id_users", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "request_id")
    op.drop_column("audit_logs", "result")
    op.drop_column("audit_logs", "target_id")
    op.drop_column("audit_logs", "target_type")
    op.drop_column("audit_logs", "actor_user_id")

    op.drop_index("ix_admin_assignments_user_id", table_name="admin_assignments")
    op.drop_index("ix_admin_assignments_role_id", table_name="admin_assignments")
    op.drop_index("ix_admin_assignments_revoked_at", table_name="admin_assignments")
    op.drop_index("ix_admin_assignments_granted_by_user_id", table_name="admin_assignments")
    op.drop_index("ix_admin_assignments_granted_at", table_name="admin_assignments")
    op.drop_table("admin_assignments")
    op.drop_index("ix_admin_role_permissions_role_id", table_name="admin_role_permissions")
    op.drop_index("ix_admin_role_permissions_permission", table_name="admin_role_permissions")
    op.drop_table("admin_role_permissions")
    op.drop_index("ix_admin_roles_slug", table_name="admin_roles")
    op.drop_table("admin_roles")
