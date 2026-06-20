from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import AdminContext, current_admin_context, require_admin_permission
from app.admin.permissions import (
    ADMINS_READ,
    ADMINS_WRITE,
    API_KEYS_READ,
    API_KEYS_REVOKE,
    API_KEYS_WRITE,
    AUDIT_READ,
    BILLING_PLANS_READ,
    BILLING_PLANS_WRITE,
    BILLING_READ,
    BILLING_SUBSCRIPTIONS_WRITE,
    SERVICE_CLIENTS_READ,
    SERVICE_CLIENTS_REVOKE,
    SERVICE_CLIENTS_WRITE,
    STATS_READ,
    USAGE_READ,
    USAGE_WRITE,
    USERS_DISABLE,
    USERS_READ,
    USERS_SESSIONS_REVOKE,
    USERS_WRITE,
    WALLETS_READ,
    WALLETS_WRITE,
)
from app.admin.schemas import (
    AdminAPIKeyRead,
    AdminAPIKeysResponse,
    AdminAPIKeyUpdate,
    AdminAssignmentRead,
    AdminAuditLogsResponse,
    AdminBillingDetailRead,
    AdminBillingPlanCreate,
    AdminBillingPlanRead,
    AdminBillingPlansResponse,
    AdminBillingPlanUpdate,
    AdminManualAdjustmentCreate,
    AdminManualAdjustmentRead,
    AdminMeRead,
    AdminRoleAssignmentCreate,
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
    AdminUserUpdate,
    AdminWalletRead,
    AdminWalletSyncResponse,
)
from app.admin.service import AdminService
from app.billing.schemas import CancelSubscriptionRequest
from app.db.models.service_client import ServiceClientRole
from app.db.session import get_async_session

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/me", response_model=AdminMeRead)
async def admin_me(
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(current_admin_context),
) -> AdminMeRead:
    return await AdminService(session).me(context)


@router.get("/roles", response_model=list[AdminRoleRead])
async def list_admin_roles(
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(ADMINS_READ)),
) -> list[AdminRoleRead]:
    return await AdminService(session).list_roles()


@router.post("/users/{user_id}/roles", response_model=AdminAssignmentRead)
async def grant_admin_role(
    request: Request,
    user_id: uuid.UUID,
    payload: AdminRoleAssignmentCreate,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(ADMINS_WRITE)),
) -> AdminAssignmentRead:
    assignment = await AdminService(session).grant_role(context, user_id, payload.role_id, request)
    await session.commit()
    return assignment


@router.delete("/users/{user_id}/roles/{role_id}", response_model=AdminAssignmentRead)
async def revoke_admin_role(
    request: Request,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(ADMINS_WRITE)),
) -> AdminAssignmentRead:
    assignment = await AdminService(session).revoke_role(context, user_id, role_id, request)
    await session.commit()
    return assignment


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    status: str | None = None,
    sort: str = Query(default="-created_at"),
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USERS_READ)),
) -> AdminUsersResponse:
    return await AdminService(session).list_users(
        context, limit=limit, offset=offset, search=search, status=status, sort=sort
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailRead)
async def get_user(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USERS_READ)),
) -> AdminUserDetailRead:
    return await AdminService(session).get_user(context, user_id)


@router.patch("/users/{user_id}", response_model=AdminUserDetailRead)
async def update_user(
    request: Request,
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USERS_WRITE)),
) -> AdminUserDetailRead:
    user = await AdminService(session).update_user(context, user_id, payload, request)
    await session.commit()
    return user


@router.post("/users/{user_id}/disable", response_model=AdminUserDetailRead)
async def disable_user(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USERS_DISABLE)),
) -> AdminUserDetailRead:
    user = await AdminService(session).disable_user(context, user_id, request)
    await session.commit()
    return user


@router.post("/users/{user_id}/enable", response_model=AdminUserDetailRead)
async def enable_user(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USERS_DISABLE)),
) -> AdminUserDetailRead:
    user = await AdminService(session).enable_user(context, user_id, request)
    await session.commit()
    return user


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USERS_SESSIONS_REVOKE)),
) -> dict[str, int]:
    result = await AdminService(session).revoke_user_sessions(context, user_id, request)
    await session.commit()
    return result


@router.get("/api-keys", response_model=AdminAPIKeysResponse)
async def list_api_keys(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    status: str | None = None,
    sort: str = Query(default="-created_at"),
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(API_KEYS_READ)),
) -> AdminAPIKeysResponse:
    return await AdminService(session).list_api_keys(
        limit=limit, offset=offset, search=search, status=status, sort=sort
    )


@router.get("/users/{user_id}/api-keys", response_model=AdminAPIKeysResponse)
async def list_user_api_keys(
    user_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    status: str | None = None,
    sort: str = Query(default="-created_at"),
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(API_KEYS_READ)),
) -> AdminAPIKeysResponse:
    service = AdminService(session)
    await service.ensure_user_exists(user_id)
    return await service.list_api_keys(
        limit=limit, offset=offset, search=search, status=status, sort=sort, user_id=user_id
    )


@router.patch("/api-keys/{key_id}", response_model=AdminAPIKeyRead)
async def update_api_key(
    request: Request,
    key_id: uuid.UUID,
    payload: AdminAPIKeyUpdate,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(API_KEYS_WRITE)),
) -> AdminAPIKeyRead:
    api_key = await AdminService(session).update_api_key(context, key_id, payload, request)
    await session.commit()
    return api_key


@router.post("/api-keys/{key_id}/revoke", response_model=AdminAPIKeyRead)
async def revoke_api_key(
    request: Request,
    key_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(API_KEYS_REVOKE)),
) -> AdminAPIKeyRead:
    api_key = await AdminService(session).revoke_api_key(context, key_id, request)
    await session.commit()
    return api_key


@router.post(
    "/service-clients",
    response_model=AdminServiceClientCreateResponse,
    status_code=201,
)
async def create_service_client(
    request: Request,
    payload: AdminServiceClientCreate,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(SERVICE_CLIENTS_WRITE)),
) -> AdminServiceClientCreateResponse:
    service_client = await AdminService(session).create_service_client(context, payload, request)
    await session.commit()
    return service_client


@router.get("/service-clients", response_model=AdminServiceClientsResponse)
async def list_service_clients(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    role: ServiceClientRole | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(SERVICE_CLIENTS_READ)),
) -> AdminServiceClientsResponse:
    return await AdminService(session).list_service_clients(
        limit=limit,
        offset=offset,
        role=role,
        status=status,
    )


@router.post("/service-clients/{client_id}/revoke", response_model=AdminServiceClientRead)
async def revoke_service_client(
    request: Request,
    client_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(SERVICE_CLIENTS_REVOKE)),
) -> AdminServiceClientRead:
    service_client = await AdminService(session).revoke_service_client(context, client_id, request)
    await session.commit()
    return service_client


@router.get("/billing/plans", response_model=AdminBillingPlansResponse)
async def list_billing_plans(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    status: str | None = None,
    sort: str = Query(default="sort_order"),
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(BILLING_PLANS_READ)),
) -> AdminBillingPlansResponse:
    return await AdminService(session).list_billing_plans(
        limit=limit, offset=offset, search=search, status=status, sort=sort
    )


@router.post("/billing/plans", response_model=AdminBillingPlanRead)
async def create_billing_plan(
    request: Request,
    payload: AdminBillingPlanCreate,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(BILLING_PLANS_WRITE)),
) -> AdminBillingPlanRead:
    plan = await AdminService(session).create_billing_plan(context, payload, request)
    await session.commit()
    return plan


@router.patch("/billing/plans/{plan_id}", response_model=AdminBillingPlanRead)
async def update_billing_plan(
    request: Request,
    plan_id: uuid.UUID,
    payload: AdminBillingPlanUpdate,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(BILLING_PLANS_WRITE)),
) -> AdminBillingPlanRead:
    plan = await AdminService(session).update_billing_plan(context, plan_id, payload, request)
    await session.commit()
    return plan


@router.post("/billing/plans/{plan_id}/deactivate", response_model=AdminBillingPlanRead)
async def deactivate_billing_plan(
    request: Request,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(BILLING_PLANS_WRITE)),
) -> AdminBillingPlanRead:
    plan = await AdminService(session).deactivate_billing_plan(context, plan_id, request)
    await session.commit()
    return plan


@router.post("/billing/plans/{plan_id}/activate", response_model=AdminBillingPlanRead)
async def activate_billing_plan(
    request: Request,
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(BILLING_PLANS_WRITE)),
) -> AdminBillingPlanRead:
    plan = await AdminService(session).activate_billing_plan(context, plan_id, request)
    await session.commit()
    return plan


@router.get("/users/{user_id}/billing", response_model=AdminBillingDetailRead)
async def get_user_billing(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(BILLING_READ)),
) -> AdminBillingDetailRead:
    return await AdminService(session).billing_detail(context, user_id)


@router.post("/users/{user_id}/billing/subscription/cancel", response_model=AdminBillingDetailRead)
async def cancel_user_subscription(
    request: Request,
    user_id: uuid.UUID,
    payload: CancelSubscriptionRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(BILLING_SUBSCRIPTIONS_WRITE)),
) -> AdminBillingDetailRead:
    payload = payload or CancelSubscriptionRequest()
    detail = await AdminService(session).cancel_user_subscription(
        context, user_id, cancel_at_period_end=payload.cancel_at_period_end, request=request
    )
    await session.commit()
    return detail


@router.get("/users/{user_id}/usage", response_model=AdminUsageDetailRead)
async def get_user_usage(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USAGE_READ)),
) -> AdminUsageDetailRead:
    detail = await AdminService(session).usage_detail(user_id)
    await session.commit()
    return detail


@router.post("/users/{user_id}/usage/manual-adjustments", response_model=AdminManualAdjustmentRead)
async def create_manual_adjustment(
    request: Request,
    user_id: uuid.UUID,
    payload: AdminManualAdjustmentCreate,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USAGE_WRITE)),
) -> AdminManualAdjustmentRead:
    adjustment = await AdminService(session).create_manual_adjustment(context, user_id, payload, request)
    await session.commit()
    return adjustment


@router.post("/users/{user_id}/usage/recalculate", response_model=AdminUsageDetailRead)
async def recalculate_user_usage(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(USAGE_WRITE)),
) -> AdminUsageDetailRead:
    detail = await AdminService(session).recalculate_usage(context, user_id, request)
    await session.commit()
    return detail


@router.get("/users/{user_id}/wallets/evm", response_model=AdminWalletRead)
async def get_user_evm_wallet(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(WALLETS_READ)),
) -> AdminWalletRead:
    return await AdminService(session).wallet(context, user_id)


@router.post("/users/{user_id}/wallets/evm/sync-balances", response_model=AdminWalletSyncResponse)
async def sync_user_evm_wallet_balances(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(WALLETS_WRITE)),
) -> AdminWalletSyncResponse:
    result = await AdminService(session).sync_wallet_balances(context, user_id, request)
    await session.commit()
    return result


@router.post("/users/{user_id}/wallets/evm/unlink", response_model=AdminWalletRead)
async def unlink_user_evm_wallet(
    request: Request,
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(WALLETS_WRITE)),
) -> AdminWalletRead:
    result = await AdminService(session).unlink_wallet(context, user_id, request)
    await session.commit()
    return result


@router.get("/audit-logs", response_model=AdminAuditLogsResponse)
async def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    status: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort: str = Query(default="-created_at"),
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(AUDIT_READ)),
) -> AdminAuditLogsResponse:
    return await AdminService(session).list_audit_logs(
        context,
        limit=limit,
        offset=offset,
        search=search,
        status=status,
        created_from=created_from,
        created_to=created_to,
        sort=sort,
    )


@router.get("/stats/overview", response_model=AdminStatsOverviewRead)
async def stats_overview(
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(STATS_READ)),
) -> AdminStatsOverviewRead:
    return await AdminService(session).stats_overview()


@router.get("/stats/usage", response_model=AdminStatsUsageRead)
async def stats_usage(
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(STATS_READ)),
) -> AdminStatsUsageRead:
    return await AdminService(session).stats_usage()


@router.get("/stats/billing", response_model=AdminStatsBillingRead)
async def stats_billing(
    session: AsyncSession = Depends(get_async_session),
    context: AdminContext = Depends(require_admin_permission(STATS_READ)),
) -> AdminStatsBillingRead:
    return await AdminService(session).stats_billing()
