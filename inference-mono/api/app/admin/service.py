from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import Request
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin.dependencies import AdminContext
from app.admin.permissions import (
    ADMINS_OWNER_WRITE,
    AUDIT_SENSITIVE_READ,
    BILLING_SENSITIVE_READ,
    DEFAULT_ROLE_DEFINITIONS,
    OWNER_ROLE,
    USERS_SENSITIVE_READ,
    WALLETS_SENSITIVE_READ,
)
from app.admin.schemas import (
    AdminAPIKeyRead,
    AdminAPIKeysResponse,
    AdminAPIKeyUpdate,
    AdminAssignmentRead,
    AdminAuditLogRead,
    AdminAuditLogsResponse,
    AdminBillingDetailRead,
    AdminBillingPlanCreate,
    AdminBillingPlanRead,
    AdminBillingPlansResponse,
    AdminBillingPlanUpdate,
    AdminManualAdjustmentCreate,
    AdminManualAdjustmentRead,
    AdminMeRead,
    AdminRoleRead,
    AdminServiceClientCreate,
    AdminServiceClientCreateResponse,
    AdminServiceClientRead,
    AdminServiceClientsResponse,
    AdminStatsBillingRead,
    AdminStatsOverviewRead,
    AdminStatsUsageRead,
    AdminUsageDetailRead,
    AdminUserDetailRead,
    AdminUsersResponse,
    AdminUserSummaryRead,
    AdminUserUpdate,
    AdminWalletRead,
    AdminWalletSyncResponse,
)
from app.api_keys.schemas import validate_api_key_scopes, validate_future_expiration
from app.audit.service import write_audit_log
from app.billing.service import BillingService
from app.core.errors import bad_request, forbidden, not_found
from app.core.security import normalize_phone_number, utcnow
from app.db.models.admin_assignment import AdminAssignment
from app.db.models.admin_role import AdminRole
from app.db.models.admin_role_permission import AdminRolePermission
from app.db.models.api_key import APIKey
from app.db.models.audit_log import AuditLog
from app.db.models.billing_plan import BillingPlan
from app.db.models.evm_wallet import EVMWallet
from app.db.models.manual_token_adjustment import ManualTokenAdjustment
from app.db.models.refresh_token import RefreshToken
from app.db.models.service_client import ServiceClient, ServiceClientRole
from app.db.models.usage_period import UsagePeriod
from app.db.models.user import User
from app.db.models.user_billing_account import UserBillingAccount
from app.db.models.user_subscription import UserSubscription
from app.service_clients.schemas import ServiceClientCreate
from app.service_clients.service import ServiceClientService
from app.usage.service import UsageService
from app.wallets.service import WalletService


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "plan"


def _mask_email(value: str | None) -> str | None:
    if not value:
        return value
    local, separator, domain = value.partition("@")
    if not separator:
        return "***"
    prefix = local[:1] if local else ""
    return f"{prefix}***@{domain}"


def _mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    return f"***{value[-4:]}" if len(value) >= 4 else "***"


def _mask_identifier(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def _mask_wallet(value: str | None) -> str | None:
    return _mask_identifier(value)


def _masked_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: "[redacted]" for key in metadata}


def _role_read(role: AdminRole) -> AdminRoleRead:
    return AdminRoleRead(
        id=role.id,
        slug=role.slug,
        name=role.name,
        description=role.description,
        system=role.system,
        permissions=sorted(permission.permission for permission in role.permissions),
    )


def _api_key_read(api_key: APIKey) -> AdminAPIKeyRead:
    return AdminAPIKeyRead.model_validate(api_key)


def _service_client_read(client: ServiceClient) -> AdminServiceClientRead:
    return AdminServiceClientRead.model_validate(client)


async def seed_default_admin_roles(session: AsyncSession) -> None:
    for definition in DEFAULT_ROLE_DEFINITIONS:
        result = await session.execute(select(AdminRole).where(AdminRole.slug == definition.slug))
        role = result.scalar_one_or_none()
        if role is None:
            role = AdminRole(
                slug=definition.slug,
                name=definition.name,
                description=definition.description,
                system=True,
            )
            session.add(role)
            await session.flush()
        else:
            role.name = definition.name
            role.description = definition.description
            role.system = True
            session.add(role)

        existing_result = await session.execute(
            select(AdminRolePermission).where(AdminRolePermission.role_id == role.id)
        )
        existing = {permission.permission: permission for permission in existing_result.scalars()}
        for permission in definition.permissions - set(existing):
            session.add(AdminRolePermission(role_id=role.id, permission=permission))
        stale_permissions = set(existing) - definition.permissions
        if stale_permissions:
            await session.execute(
                delete(AdminRolePermission).where(
                    AdminRolePermission.role_id == role.id,
                    AdminRolePermission.permission.in_(stale_permissions),
                )
            )
    await session.flush()


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def me(self, context: AdminContext) -> AdminMeRead:
        return AdminMeRead(
            id=context.user.id,
            email=context.user.email,
            roles=[_role_read(role) for role in context.roles],
            permissions=sorted(context.permissions),
        )

    async def list_roles(self) -> list[AdminRoleRead]:
        result = await self.session.execute(
            select(AdminRole).options(selectinload(AdminRole.permissions)).order_by(AdminRole.slug)
        )
        return [_role_read(role) for role in result.scalars().unique()]

    async def grant_role(
        self,
        context: AdminContext,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        request: Request,
    ) -> AdminAssignmentRead:
        await seed_default_admin_roles(self.session)
        user = await self._get_user(user_id)
        role = await self._get_role(role_id)
        self._ensure_owner_role_change_allowed(context, role)
        existing = await self._active_assignment(user.id, role.id)
        if existing is not None:
            return self._assignment_read(existing)

        assignment = AdminAssignment(
            user_id=user.id,
            role_id=role.id,
            granted_by_user_id=context.user.id,
        )
        self.session.add(assignment)
        await self.session.flush()
        await self._audit(
            context,
            "admin.role.grant",
            request,
            user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"role_id": str(role.id), "role_slug": role.slug},
        )
        return self._assignment_read(await self._get_assignment(assignment.id))

    async def revoke_role(
        self,
        context: AdminContext,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        request: Request,
    ) -> AdminAssignmentRead:
        role = await self._get_role(role_id)
        self._ensure_owner_role_change_allowed(context, role)
        assignment = await self._active_assignment(user_id, role_id)
        if assignment is None:
            raise not_found("Admin role assignment not found")
        if role.slug == OWNER_ROLE and await self._active_owner_count() <= 1:
            raise forbidden("Cannot revoke the last active owner")
        assignment.revoked_at = utcnow()
        self.session.add(assignment)
        await self.session.flush()
        await self._audit(
            context,
            "admin.role.revoke",
            request,
            user_id=user_id,
            target_type="user",
            target_id=user_id,
            metadata={"role_id": str(role.id), "role_slug": role.slug},
        )
        return self._assignment_read(assignment)

    async def list_users(
        self,
        context: AdminContext,
        *,
        limit: int,
        offset: int,
        search: str | None,
        status: str | None,
        sort: str,
    ) -> AdminUsersResponse:
        statement = select(User)
        statement = self._filter_users(statement, search, status)
        total = await self._count(statement)
        statement = self._sort_users(statement, sort).limit(limit).offset(offset)
        result = await self.session.execute(statement)
        users = list(result.scalars())
        counts = await self._api_key_counts([user.id for user in users])
        return AdminUsersResponse(
            data=[self._user_summary(context, user, counts.get(user.id, (0, 0))) for user in users],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_user(self, context: AdminContext, user_id: uuid.UUID) -> AdminUserDetailRead:
        user = await self._get_user_with_related(user_id)
        counts = await self._api_key_counts([user.id])
        roles = await self._active_roles_for_user(user.id)
        latest_period = (
            await self.session.execute(
                select(UsagePeriod)
                .where(UsagePeriod.user_id == user.id)
                .order_by(UsagePeriod.calculated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        summary = self._user_summary(context, user, counts.get(user.id, (0, 0)))
        return AdminUserDetailRead(
            **summary.model_dump(),
            phone_verified_at=user.phone_verified_at,
            updated_at=user.updated_at,
            roles=[_role_read(role) for role in roles],
            oauth_providers=sorted({getattr(account, "oauth_name", "unknown") for account in user.oauth_accounts}),
            billing_status=user.subscription.status if user.subscription else "free",
            wallet_address=self._wallet_address(context, user.evm_wallet),
            current_period_remaining_tokens=latest_period.remaining_tokens if latest_period else None,
        )

    async def update_user(
        self,
        context: AdminContext,
        user_id: uuid.UUID,
        payload: AdminUserUpdate,
        request: Request,
    ) -> AdminUserDetailRead:
        user = await self._get_user(user_id)
        update_data = payload.model_dump(exclude_unset=True)
        if "email" in update_data and update_data["email"] is not None:
            email = str(update_data["email"]).lower()
            await self._ensure_unique_user_field(User.email, email, user.id, "email_exists")
            user.email = email
        if "full_name" in update_data:
            user.full_name = update_data["full_name"]
        if "phone_number" in update_data:
            phone_number = update_data["phone_number"]
            try:
                normalized = normalize_phone_number(phone_number) if phone_number else None
            except ValueError as exc:
                raise bad_request("invalid_phone_number", "Phone number is invalid") from exc
            if normalized is not None:
                await self._ensure_unique_user_field(User.phone_number, normalized, user.id, "phone_exists")
            user.phone_number = normalized
        if "is_verified" in update_data and update_data["is_verified"] is not None:
            user.is_verified = update_data["is_verified"]
        if "is_active" in update_data and update_data["is_active"] is not None:
            if user.id == context.user.id and update_data["is_active"] is False:
                raise forbidden("Admins cannot disable their own account")
            if user.is_active and update_data["is_active"] is False:
                await self._revoke_refresh_tokens(user.id)
                user.token_version += 1
            user.is_active = update_data["is_active"]
        self.session.add(user)
        await self.session.flush()
        await self._audit(
            context,
            "admin.user.update",
            request,
            user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"fields": sorted(update_data)},
        )
        return await self.get_user(context, user.id)

    async def disable_user(
        self, context: AdminContext, user_id: uuid.UUID, request: Request
    ) -> AdminUserDetailRead:
        user = await self._get_user(user_id)
        if user.id == context.user.id:
            raise forbidden("Admins cannot disable their own account")
        if user.is_active:
            user.is_active = False
            user.token_version += 1
            await self._revoke_refresh_tokens(user.id)
            self.session.add(user)
        await self.session.flush()
        await self._audit(
            context,
            "admin.user.disable",
            request,
            user_id=user.id,
            target_type="user",
            target_id=user.id,
        )
        return await self.get_user(context, user.id)

    async def enable_user(
        self, context: AdminContext, user_id: uuid.UUID, request: Request
    ) -> AdminUserDetailRead:
        user = await self._get_user(user_id)
        if not user.is_active:
            user.is_active = True
            self.session.add(user)
        await self.session.flush()
        await self._audit(context, "admin.user.enable", request, user_id=user.id, target_type="user", target_id=user.id)
        return await self.get_user(context, user.id)

    async def revoke_user_sessions(
        self, context: AdminContext, user_id: uuid.UUID, request: Request
    ) -> dict[str, int]:
        user = await self._get_user(user_id)
        revoked = await self._revoke_refresh_tokens(user.id)
        user.token_version += 1
        self.session.add(user)
        await self.session.flush()
        await self._audit(
            context,
            "admin.user.revoke_sessions",
            request,
            user_id=user.id,
            target_type="user",
            target_id=user.id,
            metadata={"revoked_refresh_tokens": revoked},
        )
        return {"revoked_refresh_tokens": revoked}

    async def list_api_keys(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None,
        status: str | None,
        sort: str,
        user_id: uuid.UUID | None = None,
    ) -> AdminAPIKeysResponse:
        statement = select(APIKey)
        if user_id is not None:
            statement = statement.where(APIKey.user_id == user_id)
        statement = self._filter_api_keys(statement, search, status)
        total = await self._count(statement)
        statement = self._sort_api_keys(statement, sort).limit(limit).offset(offset)
        result = await self.session.execute(statement)
        return AdminAPIKeysResponse(
            data=[_api_key_read(api_key) for api_key in result.scalars()],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def ensure_user_exists(self, user_id: uuid.UUID) -> None:
        await self._get_user(user_id)

    async def update_api_key(
        self,
        context: AdminContext,
        key_id: uuid.UUID,
        payload: AdminAPIKeyUpdate,
        request: Request,
    ) -> AdminAPIKeyRead:
        api_key = await self._get_api_key(key_id)
        update_data = payload.model_dump(exclude_unset=True)
        if "scopes" in update_data and update_data["scopes"] is not None:
            try:
                validate_api_key_scopes(update_data["scopes"])
            except ValueError as exc:
                raise bad_request("invalid_api_key_scopes", str(exc)) from exc
        if "expires_at" in update_data:
            try:
                validate_future_expiration(update_data["expires_at"])
            except ValueError as exc:
                raise bad_request("invalid_api_key_expiration", str(exc)) from exc
        for field, value in update_data.items():
            setattr(api_key, field, value)
        self.session.add(api_key)
        await self.session.flush()
        await self._audit(
            context,
            "admin.api_key.update",
            request,
            user_id=api_key.user_id,
            target_type="api_key",
            target_id=api_key.id,
            metadata={"fields": sorted(update_data)},
        )
        return _api_key_read(api_key)

    async def revoke_api_key(
        self, context: AdminContext, key_id: uuid.UUID, request: Request
    ) -> AdminAPIKeyRead:
        api_key = await self._get_api_key(key_id)
        if api_key.revoked_at is None:
            api_key.revoked_at = utcnow()
            self.session.add(api_key)
        await self.session.flush()
        await self._audit(
            context,
            "admin.api_key.revoke",
            request,
            user_id=api_key.user_id,
            target_type="api_key",
            target_id=api_key.id,
        )
        return _api_key_read(api_key)

    async def create_service_client(
        self,
        context: AdminContext,
        payload: AdminServiceClientCreate,
        request: Request,
    ) -> AdminServiceClientCreateResponse:
        try:
            validate_future_expiration(payload.expires_at)
        except ValueError as exc:
            raise bad_request("invalid_service_client_expiration", str(exc)) from exc
        response = await ServiceClientService(self.session).create_client(
            ServiceClientCreate(
                name=payload.name,
                role=payload.role,
                expires_at=payload.expires_at,
                rate_limit_per_minute=payload.rate_limit_per_minute,
            )
        )
        await self._audit(
            context,
            "admin.service_client.create",
            request,
            target_type="service_client",
            target_id=response.id,
            metadata={"role": response.role.value, "prefix": response.prefix},
        )
        return AdminServiceClientCreateResponse.model_validate(response.model_dump())

    async def list_service_clients(
        self,
        *,
        limit: int,
        offset: int,
        role: ServiceClientRole | None,
        status: str | None,
    ) -> AdminServiceClientsResponse:
        response = await ServiceClientService(self.session).list_clients(
            limit=limit,
            offset=offset,
            role=role,
            status=status,
        )
        return AdminServiceClientsResponse(
            data=[AdminServiceClientRead.model_validate(client.model_dump()) for client in response.data],
            total=response.total,
            limit=response.limit,
            offset=response.offset,
        )

    async def revoke_service_client(
        self,
        context: AdminContext,
        client_id: uuid.UUID,
        request: Request,
    ) -> AdminServiceClientRead:
        client = await ServiceClientService(self.session).revoke_client(client_id)
        await self._audit(
            context,
            "admin.service_client.revoke",
            request,
            target_type="service_client",
            target_id=client.id,
            metadata={"role": client.role.value, "prefix": client.prefix},
        )
        return _service_client_read(client)

    async def list_billing_plans(
        self, *, limit: int, offset: int, search: str | None, status: str | None, sort: str
    ) -> AdminBillingPlansResponse:
        statement = select(BillingPlan)
        if search:
            like = f"%{search}%"
            statement = statement.where(or_(BillingPlan.name.ilike(like), BillingPlan.slug.ilike(like)))
        if status == "active":
            statement = statement.where(BillingPlan.active.is_(True))
        elif status == "inactive":
            statement = statement.where(BillingPlan.active.is_(False))
        elif status == "visible":
            statement = statement.where(BillingPlan.visible.is_(True))
        elif status == "hidden":
            statement = statement.where(BillingPlan.visible.is_(False))
        elif status not in (None, "all"):
            raise bad_request("invalid_status", "Unsupported billing plan status")
        total = await self._count(statement)
        statement = self._sort_billing_plans(statement, sort).limit(limit).offset(offset)
        result = await self.session.execute(statement)
        return AdminBillingPlansResponse(
            data=[AdminBillingPlanRead.model_validate(plan) for plan in result.scalars()],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def create_billing_plan(
        self, context: AdminContext, payload: AdminBillingPlanCreate, request: Request
    ) -> AdminBillingPlanRead:
        slug = _slugify(payload.slug or payload.name)
        await self._ensure_unique_billing_plan(slug=slug, name=payload.name, stripe_price_id=payload.stripe_price_id)
        plan = BillingPlan(
            slug=slug,
            name=payload.name,
            description=payload.description,
            stripe_price_id=payload.stripe_price_id,
            monthly_token_allowance=payload.monthly_token_allowance,
            active=payload.active,
            visible=payload.visible,
            sort_order=payload.sort_order,
            features=payload.features,
        )
        self.session.add(plan)
        await self.session.flush()
        await self._audit(
            context,
            "admin.billing_plan.create",
            request,
            target_type="billing_plan",
            target_id=plan.id,
            metadata={"slug": plan.slug, "name": plan.name},
        )
        return AdminBillingPlanRead.model_validate(plan)

    async def update_billing_plan(
        self,
        context: AdminContext,
        plan_id: uuid.UUID,
        payload: AdminBillingPlanUpdate,
        request: Request,
    ) -> AdminBillingPlanRead:
        plan = await self._get_billing_plan(plan_id)
        update_data = payload.model_dump(exclude_unset=True)
        slug = _slugify(update_data["slug"]) if "slug" in update_data and update_data["slug"] else None
        await self._ensure_unique_billing_plan(
            slug=slug,
            name=update_data.get("name"),
            stripe_price_id=update_data.get("stripe_price_id"),
            exclude_id=plan.id,
        )
        if slug is not None:
            plan.slug = slug
        for field in (
            "name",
            "description",
            "stripe_price_id",
            "monthly_token_allowance",
            "active",
            "visible",
            "sort_order",
            "features",
        ):
            if field in update_data:
                setattr(plan, field, update_data[field])
        self.session.add(plan)
        await self.session.flush()
        await self._audit(
            context,
            "admin.billing_plan.update",
            request,
            target_type="billing_plan",
            target_id=plan.id,
            metadata={"fields": sorted(update_data), "slug": plan.slug},
        )
        return AdminBillingPlanRead.model_validate(plan)

    async def deactivate_billing_plan(
        self, context: AdminContext, plan_id: uuid.UUID, request: Request
    ) -> AdminBillingPlanRead:
        plan = await self._get_billing_plan(plan_id)
        plan.active = False
        plan.visible = False
        self.session.add(plan)
        await self.session.flush()
        await self._audit(
            context,
            "admin.billing_plan.deactivate",
            request,
            target_type="billing_plan",
            target_id=plan.id,
            metadata={"slug": plan.slug},
        )
        return AdminBillingPlanRead.model_validate(plan)

    async def activate_billing_plan(
        self, context: AdminContext, plan_id: uuid.UUID, request: Request
    ) -> AdminBillingPlanRead:
        plan = await self._get_billing_plan(plan_id)
        plan.active = True
        plan.visible = True
        self.session.add(plan)
        await self.session.flush()
        await self._audit(
            context,
            "admin.billing_plan.activate",
            request,
            target_type="billing_plan",
            target_id=plan.id,
            metadata={"slug": plan.slug},
        )
        return AdminBillingPlanRead.model_validate(plan)

    async def billing_detail(self, context: AdminContext, user_id: uuid.UUID) -> AdminBillingDetailRead:
        user = await self._get_user_with_related(user_id)
        subscription = user.subscription
        account = user.billing_account
        plan_name = subscription.plan.name if subscription and subscription.plan else None
        return AdminBillingDetailRead(
            user_id=user.id,
            stripe_customer_id=self._stripe_value(context, account.stripe_customer_id if account else None),
            stripe_subscription_id=self._stripe_value(
                context, subscription.stripe_subscription_id if subscription else None
            ),
            stripe_price_id=self._stripe_value(context, subscription.stripe_price_id if subscription else None),
            plan_id=subscription.plan_id if subscription else None,
            plan_name=plan_name,
            status=subscription.status if subscription else "free",
            current_period_start=subscription.current_period_start if subscription else None,
            current_period_end=subscription.current_period_end if subscription else None,
            cancel_at_period_end=subscription.cancel_at_period_end if subscription else False,
        )

    async def cancel_user_subscription(
        self,
        context: AdminContext,
        user_id: uuid.UUID,
        *,
        cancel_at_period_end: bool,
        request: Request,
    ) -> AdminBillingDetailRead:
        user = await self._get_user(user_id)
        subscription = await BillingService(self.session).cancel_subscription(
            user, cancel_at_period_end=cancel_at_period_end
        )
        await self._audit(
            context,
            "admin.billing.subscription_cancel",
            request,
            user_id=user.id,
            target_type="subscription",
            target_id=subscription.id,
            metadata={"cancel_at_period_end": cancel_at_period_end},
        )
        return await self.billing_detail(context, user.id)

    async def usage_detail(self, user_id: uuid.UUID) -> AdminUsageDetailRead:
        user = await self._get_user(user_id)
        period = await UsageService(self.session).recalculate_current_period(user)
        return AdminUsageDetailRead(
            user_id=user.id,
            period_start=period.period_start,
            period_end=period.period_end,
            stripe_allowance=period.stripe_allowance,
            ethereum_erc20_allowance=period.ethereum_erc20_allowance,
            substrate_native_allowance=period.substrate_native_allowance,
            manual_allowance=period.manual_allowance,
            total_allowance=period.total_allowance,
            used_tokens=period.used_tokens,
            remaining_tokens=period.remaining_tokens,
            calculated_at=period.calculated_at,
        )

    async def create_manual_adjustment(
        self,
        context: AdminContext,
        user_id: uuid.UUID,
        payload: AdminManualAdjustmentCreate,
        request: Request,
    ) -> AdminManualAdjustmentRead:
        user = await self._get_user(user_id)
        adjustment = ManualTokenAdjustment(
            user_id=user.id,
            amount=payload.amount,
            reason=payload.reason,
            admin_actor_id=context.user.id,
        )
        self.session.add(adjustment)
        await self.session.flush()
        await UsageService(self.session).recalculate_current_period(user)
        await self._audit(
            context,
            "admin.usage.manual_adjustment",
            request,
            user_id=user.id,
            target_type="manual_token_adjustment",
            target_id=adjustment.id,
            metadata={"amount": payload.amount, "reason": payload.reason},
        )
        return AdminManualAdjustmentRead.model_validate(adjustment)

    async def recalculate_usage(
        self, context: AdminContext, user_id: uuid.UUID, request: Request
    ) -> AdminUsageDetailRead:
        detail = await self.usage_detail(user_id)
        await self._audit(
            context,
            "admin.usage.recalculate",
            request,
            user_id=user_id,
            target_type="user",
            target_id=user_id,
        )
        return detail

    async def wallet(self, context: AdminContext, user_id: uuid.UUID) -> AdminWalletRead:
        user = await self._get_user_with_related(user_id)
        wallet = user.evm_wallet
        return AdminWalletRead(
            user_id=user.id,
            address=self._wallet_address(context, wallet),
            verified_at=wallet.verified_at if wallet else None,
            last_balance_sync_at=wallet.last_balance_sync_at if wallet else None,
        )

    async def sync_wallet_balances(
        self, context: AdminContext, user_id: uuid.UUID, request: Request
    ) -> AdminWalletSyncResponse:
        user = await self._get_user(user_id)
        wallet, snapshots = await WalletService(self.session).sync_balances(user)
        await self._audit(
            context,
            "admin.wallet.evm_sync_balances",
            request,
            user_id=user.id,
            target_type="evm_wallet",
            target_id=wallet.id,
            metadata={"address": wallet.address, "snapshots": len(snapshots)},
        )
        return AdminWalletSyncResponse(
            wallet=AdminWalletRead(
                user_id=user.id,
                address=self._wallet_address(context, wallet),
                verified_at=wallet.verified_at,
                last_balance_sync_at=wallet.last_balance_sync_at,
            ),
            snapshots_created=len(snapshots),
        )

    async def unlink_wallet(self, context: AdminContext, user_id: uuid.UUID, request: Request) -> AdminWalletRead:
        user = await self._get_user_with_related(user_id)
        wallet = user.evm_wallet
        address = wallet.address if wallet else None
        await WalletService(self.session).unlink_wallet(user)
        await self.session.flush()
        await self._audit(
            context,
            "admin.wallet.evm_unlink",
            request,
            user_id=user.id,
            target_type="evm_wallet",
            target_id=wallet.id if wallet else user.id,
            metadata={"address": address},
        )
        return AdminWalletRead(user_id=user.id, address=None, verified_at=None, last_balance_sync_at=None)

    async def list_audit_logs(
        self,
        context: AdminContext,
        *,
        limit: int,
        offset: int,
        search: str | None,
        status: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        sort: str,
    ) -> AdminAuditLogsResponse:
        statement = select(AuditLog)
        if search:
            like = f"%{search}%"
            statement = statement.where(
                or_(
                    AuditLog.action.ilike(like),
                    AuditLog.actor.ilike(like),
                    AuditLog.target_type.ilike(like),
                    AuditLog.target_id.ilike(like),
                    AuditLog.request_id.ilike(like),
                )
            )
        if status:
            statement = statement.where(AuditLog.result == status)
        if created_from:
            statement = statement.where(AuditLog.created_at >= created_from)
        if created_to:
            statement = statement.where(AuditLog.created_at <= created_to)
        total = await self._count(statement)
        if sort == "created_at":
            statement = statement.order_by(AuditLog.created_at.asc())
        elif sort in ("-created_at", "created_at_desc"):
            statement = statement.order_by(AuditLog.created_at.desc())
        else:
            raise bad_request("invalid_sort", "Unsupported audit log sort")
        result = await self.session.execute(statement.limit(limit).offset(offset))
        sensitive = context.has(AUDIT_SENSITIVE_READ)
        return AdminAuditLogsResponse(
            data=[self._audit_log_read(log, sensitive=sensitive) for log in result.scalars()],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def stats_overview(self) -> AdminStatsOverviewRead:
        users_total = await self._scalar_count(select(User))
        users_active = await self._scalar_count(select(User).where(User.is_active.is_(True)))
        users_disabled = await self._scalar_count(select(User).where(User.is_active.is_(False)))
        active_api_keys = await self._scalar_count(select(APIKey).where(APIKey.revoked_at.is_(None)))
        active_subscriptions = await self._scalar_count(
            select(UserSubscription).where(UserSubscription.status.in_(("active", "trialing")))
        )
        visible_plans = await self._scalar_count(
            select(BillingPlan).where(BillingPlan.active.is_(True), BillingPlan.visible.is_(True))
        )
        return AdminStatsOverviewRead(
            users_total=users_total,
            users_active=users_active,
            users_disabled=users_disabled,
            active_api_keys=active_api_keys,
            active_subscriptions=active_subscriptions,
            visible_plans=visible_plans,
        )

    async def stats_usage(self) -> AdminStatsUsageRead:
        now = utcnow()
        result = await self.session.execute(
            select(
                func.coalesce(func.sum(UsagePeriod.used_tokens), 0),
                func.coalesce(func.sum(UsagePeriod.remaining_tokens), 0),
                func.count(UsagePeriod.id),
            ).where(UsagePeriod.period_end > now)
        )
        used, remaining, periods = result.one()
        return AdminStatsUsageRead(
            total_used_tokens=int(used or 0),
            total_remaining_tokens=int(remaining or 0),
            current_periods=int(periods or 0),
        )

    async def stats_billing(self) -> AdminStatsBillingRead:
        billing_accounts = await self._scalar_count(select(UserBillingAccount))
        active = await self._scalar_count(select(UserSubscription).where(UserSubscription.status == "active"))
        trialing = await self._scalar_count(select(UserSubscription).where(UserSubscription.status == "trialing"))
        canceled = await self._scalar_count(select(UserSubscription).where(UserSubscription.status == "canceled"))
        return AdminStatsBillingRead(
            billing_accounts=billing_accounts,
            active_subscriptions=active,
            trialing_subscriptions=trialing,
            canceled_subscriptions=canceled,
        )

    async def _audit(
        self,
        context: AdminContext,
        action: str,
        request: Request,
        *,
        user_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: uuid.UUID | str | None = None,
        result: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await write_audit_log(
            self.session,
            action,
            user_id=user_id,
            actor_user_id=context.user.id,
            request=request,
            actor="admin",
            target_type=target_type,
            target_id=target_id,
            result=result,
            metadata=metadata,
        )

    async def _get_user(self, user_id: uuid.UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise not_found("User not found")
        return user

    async def _get_user_with_related(self, user_id: uuid.UUID) -> User:
        result = await self.session.execute(
            select(User)
            .options(
                selectinload(User.oauth_accounts),
                selectinload(User.billing_account),
                selectinload(User.subscription).selectinload(UserSubscription.plan),
                selectinload(User.evm_wallet),
            )
            .where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise not_found("User not found")
        return user

    async def _get_role(self, role_id: uuid.UUID) -> AdminRole:
        result = await self.session.execute(
            select(AdminRole).options(selectinload(AdminRole.permissions)).where(AdminRole.id == role_id)
        )
        role = result.scalar_one_or_none()
        if role is None:
            raise not_found("Admin role not found")
        return role

    async def _get_assignment(self, assignment_id: uuid.UUID) -> AdminAssignment:
        result = await self.session.execute(
            select(AdminAssignment)
            .options(selectinload(AdminAssignment.role).selectinload(AdminRole.permissions))
            .where(AdminAssignment.id == assignment_id)
        )
        return result.scalar_one()

    async def _get_api_key(self, key_id: uuid.UUID) -> APIKey:
        api_key = await self.session.get(APIKey, key_id)
        if api_key is None:
            raise not_found("API key not found")
        return api_key

    async def _get_billing_plan(self, plan_id: uuid.UUID) -> BillingPlan:
        plan = await self.session.get(BillingPlan, plan_id)
        if plan is None:
            raise not_found("Billing plan not found")
        return plan

    async def _active_assignment(self, user_id: uuid.UUID, role_id: uuid.UUID) -> AdminAssignment | None:
        result = await self.session.execute(
            select(AdminAssignment)
            .options(selectinload(AdminAssignment.role).selectinload(AdminRole.permissions))
            .where(
                AdminAssignment.user_id == user_id,
                AdminAssignment.role_id == role_id,
                AdminAssignment.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _active_roles_for_user(self, user_id: uuid.UUID) -> list[AdminRole]:
        result = await self.session.execute(
            select(AdminRole)
            .join(AdminAssignment, AdminAssignment.role_id == AdminRole.id)
            .options(selectinload(AdminRole.permissions))
            .where(AdminAssignment.user_id == user_id, AdminAssignment.revoked_at.is_(None))
            .order_by(AdminRole.slug)
        )
        return list(result.scalars().unique())

    async def _active_owner_count(self) -> int:
        result = await self.session.execute(
            select(func.count(AdminAssignment.id))
            .join(AdminRole, AdminRole.id == AdminAssignment.role_id)
            .where(AdminRole.slug == OWNER_ROLE, AdminAssignment.revoked_at.is_(None))
        )
        return int(result.scalar_one())

    def _assignment_read(self, assignment: AdminAssignment) -> AdminAssignmentRead:
        return AdminAssignmentRead(
            id=assignment.id,
            user_id=assignment.user_id,
            role=_role_read(assignment.role),
            granted_by_user_id=assignment.granted_by_user_id,
            granted_at=assignment.granted_at,
            revoked_at=assignment.revoked_at,
        )

    def _ensure_owner_role_change_allowed(self, context: AdminContext, role: AdminRole) -> None:
        if role.slug == OWNER_ROLE and not context.has(ADMINS_OWNER_WRITE):
            raise forbidden("Only owners can modify owner role assignments")

    async def _revoke_refresh_tokens(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def _ensure_unique_user_field(
        self,
        field,
        value: str,
        user_id: uuid.UUID,
        code: str,
    ) -> None:
        existing = (
            await self.session.execute(select(User).where(field == value, User.id != user_id))
        ).scalar_one_or_none()
        if existing is not None:
            raise bad_request(code, "A user with this value already exists")

    def _user_summary(
        self,
        context: AdminContext,
        user: User,
        api_key_counts: tuple[int, int],
    ) -> AdminUserSummaryRead:
        sensitive = context.has(USERS_SENSITIVE_READ)
        total_api_keys, active_api_keys = api_key_counts
        return AdminUserSummaryRead(
            id=user.id,
            email=user.email if sensitive else _mask_email(user.email) or "",
            full_name=user.full_name,
            phone_number=user.phone_number if sensitive else _mask_phone(user.phone_number),
            is_active=user.is_active,
            is_verified=user.is_verified,
            created_at=user.created_at,
            api_key_count=total_api_keys,
            active_api_key_count=active_api_keys,
        )

    def _wallet_address(self, context: AdminContext, wallet: EVMWallet | None) -> str | None:
        if wallet is None:
            return None
        return wallet.address if context.has(WALLETS_SENSITIVE_READ) else _mask_wallet(wallet.address)

    def _stripe_value(self, context: AdminContext, value: str | None) -> str | None:
        return value if context.has(BILLING_SENSITIVE_READ) else _mask_identifier(value)

    def _audit_log_read(self, log: AuditLog, *, sensitive: bool) -> AdminAuditLogRead:
        return AdminAuditLogRead(
            id=log.id,
            user_id=log.user_id,
            actor_user_id=log.actor_user_id,
            action=log.action,
            actor=log.actor,
            target_type=log.target_type,
            target_id=log.target_id,
            result=log.result,
            request_id=log.request_id,
            ip_address=log.ip_address,
            user_agent=log.user_agent,
            metadata=log.metadata_ if sensitive else _masked_metadata(log.metadata_),
            created_at=log.created_at,
        )

    async def _api_key_counts(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
        if not user_ids:
            return {}
        result = await self.session.execute(
            select(
                APIKey.user_id,
                func.count(APIKey.id),
                func.coalesce(func.sum(case((APIKey.revoked_at.is_(None), 1), else_=0)), 0),
            )
            .where(APIKey.user_id.in_(user_ids))
            .group_by(APIKey.user_id)
        )
        return {row[0]: (int(row[1]), int(row[2])) for row in result}

    def _filter_users(self, statement, search: str | None, status: str | None):
        if search:
            like = f"%{search}%"
            statement = statement.where(
                or_(User.email.ilike(like), User.full_name.ilike(like), User.phone_number.ilike(like))
            )
        if status == "active":
            return statement.where(User.is_active.is_(True))
        if status == "disabled":
            return statement.where(User.is_active.is_(False))
        if status == "verified":
            return statement.where(User.is_verified.is_(True))
        if status == "unverified":
            return statement.where(User.is_verified.is_(False))
        if status not in (None, "all"):
            raise bad_request("invalid_status", "Unsupported user status")
        return statement

    def _sort_users(self, statement, sort: str):
        if sort == "created_at":
            return statement.order_by(User.created_at.asc())
        if sort in ("-created_at", "created_at_desc"):
            return statement.order_by(User.created_at.desc())
        if sort == "email":
            return statement.order_by(User.email.asc())
        if sort == "-email":
            return statement.order_by(User.email.desc())
        raise bad_request("invalid_sort", "Unsupported user sort")

    def _filter_api_keys(self, statement, search: str | None, status: str | None):
        now = utcnow()
        if search:
            like = f"%{search}%"
            statement = statement.where(
                or_(APIKey.name.ilike(like), APIKey.prefix.ilike(like), APIKey.last_four.ilike(like))
            )
        if status == "active":
            return statement.where(
                APIKey.revoked_at.is_(None),
                or_(APIKey.expires_at.is_(None), APIKey.expires_at > now),
            )
        if status == "revoked":
            return statement.where(APIKey.revoked_at.is_not(None))
        if status == "expired":
            return statement.where(APIKey.revoked_at.is_(None), APIKey.expires_at <= now)
        if status not in (None, "all"):
            raise bad_request("invalid_status", "Unsupported API key status")
        return statement

    def _sort_api_keys(self, statement, sort: str):
        if sort == "created_at":
            return statement.order_by(APIKey.created_at.asc())
        if sort in ("-created_at", "created_at_desc"):
            return statement.order_by(APIKey.created_at.desc())
        if sort == "last_used_at":
            return statement.order_by(APIKey.last_used_at.asc())
        if sort == "-last_used_at":
            return statement.order_by(APIKey.last_used_at.desc())
        raise bad_request("invalid_sort", "Unsupported API key sort")

    def _sort_billing_plans(self, statement, sort: str):
        if sort == "sort_order":
            return statement.order_by(BillingPlan.sort_order.asc(), BillingPlan.monthly_token_allowance.asc())
        if sort == "-sort_order":
            return statement.order_by(BillingPlan.sort_order.desc(), BillingPlan.monthly_token_allowance.desc())
        if sort == "created_at":
            return statement.order_by(BillingPlan.created_at.asc())
        if sort in ("-created_at", "created_at_desc"):
            return statement.order_by(BillingPlan.created_at.desc())
        raise bad_request("invalid_sort", "Unsupported billing plan sort")

    async def _ensure_unique_billing_plan(
        self,
        *,
        slug: str | None,
        name: str | None,
        stripe_price_id: str | None,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        checks = []
        if slug:
            checks.append(BillingPlan.slug == slug)
        if name:
            checks.append(BillingPlan.name == name)
        if stripe_price_id:
            checks.append(BillingPlan.stripe_price_id == stripe_price_id)
        if not checks:
            return
        statement = select(BillingPlan).where(or_(*checks)).limit(1)
        if exclude_id is not None:
            statement = statement.where(BillingPlan.id != exclude_id)
        existing = (await self.session.execute(statement)).scalar_one_or_none()
        if existing is not None:
            raise bad_request(
                "billing_plan_exists",
                "A billing plan with this slug, name, or Stripe price already exists",
            )

    async def _count(self, statement) -> int:
        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        return int((await self.session.execute(count_statement)).scalar_one())

    async def _scalar_count(self, statement) -> int:
        return await self._count(statement)
