from __future__ import annotations

from dataclasses import dataclass

OWNER_ROLE = "owner"

ADMINS_READ = "admins.read"
ADMINS_WRITE = "admins.write"
ADMINS_OWNER_WRITE = "admins.owner.write"
USERS_READ = "users.read"
USERS_WRITE = "users.write"
USERS_DISABLE = "users.disable"
USERS_SESSIONS_REVOKE = "users.sessions.revoke"
USERS_SENSITIVE_READ = "users.sensitive.read"
API_KEYS_READ = "api_keys.read"
API_KEYS_WRITE = "api_keys.write"
API_KEYS_REVOKE = "api_keys.revoke"
SERVICE_CLIENTS_READ = "service_clients.read"
SERVICE_CLIENTS_WRITE = "service_clients.write"
SERVICE_CLIENTS_REVOKE = "service_clients.revoke"
BILLING_READ = "billing.read"
BILLING_SENSITIVE_READ = "billing.sensitive.read"
BILLING_PLANS_READ = "billing.plans.read"
BILLING_PLANS_WRITE = "billing.plans.write"
BILLING_SUBSCRIPTIONS_WRITE = "billing.subscriptions.write"
USAGE_READ = "usage.read"
USAGE_WRITE = "usage.write"
WALLETS_READ = "wallets.read"
WALLETS_WRITE = "wallets.write"
WALLETS_SENSITIVE_READ = "wallets.sensitive.read"
AUDIT_READ = "audit.read"
AUDIT_SENSITIVE_READ = "audit.sensitive.read"
STATS_READ = "stats.read"

ALL_PERMISSIONS = frozenset(
    {
        ADMINS_READ,
        ADMINS_WRITE,
        ADMINS_OWNER_WRITE,
        USERS_READ,
        USERS_WRITE,
        USERS_DISABLE,
        USERS_SESSIONS_REVOKE,
        USERS_SENSITIVE_READ,
        API_KEYS_READ,
        API_KEYS_WRITE,
        API_KEYS_REVOKE,
        SERVICE_CLIENTS_READ,
        SERVICE_CLIENTS_WRITE,
        SERVICE_CLIENTS_REVOKE,
        BILLING_READ,
        BILLING_SENSITIVE_READ,
        BILLING_PLANS_READ,
        BILLING_PLANS_WRITE,
        BILLING_SUBSCRIPTIONS_WRITE,
        USAGE_READ,
        USAGE_WRITE,
        WALLETS_READ,
        WALLETS_WRITE,
        WALLETS_SENSITIVE_READ,
        AUDIT_READ,
        AUDIT_SENSITIVE_READ,
        STATS_READ,
    }
)


@dataclass(frozen=True)
class RoleDefinition:
    slug: str
    name: str
    description: str
    permissions: frozenset[str]


DEFAULT_ROLE_DEFINITIONS = (
    RoleDefinition(
        slug=OWNER_ROLE,
        name="Owner",
        description="Full platform administration, including owner role management.",
        permissions=ALL_PERMISSIONS,
    ),
    RoleDefinition(
        slug="admin_manager",
        name="Admin Manager",
        description="Manage admin role assignments, excluding owners.",
        permissions=frozenset({ADMINS_READ, ADMINS_WRITE, USERS_READ}),
    ),
    RoleDefinition(
        slug="support",
        name="Support",
        description="Masked support access for users, API keys, billing, usage, and wallets.",
        permissions=frozenset(
            {
                USERS_READ,
                API_KEYS_READ,
                API_KEYS_REVOKE,
                BILLING_READ,
                USAGE_READ,
                WALLETS_READ,
                STATS_READ,
            }
        ),
    ),
    RoleDefinition(
        slug="billing_admin",
        name="Billing Admin",
        description="Manage plans, subscriptions, and usage adjustments.",
        permissions=frozenset(
            {
                USERS_READ,
                BILLING_READ,
                BILLING_SENSITIVE_READ,
                BILLING_PLANS_READ,
                BILLING_PLANS_WRITE,
                BILLING_SUBSCRIPTIONS_WRITE,
                USAGE_READ,
                USAGE_WRITE,
                STATS_READ,
            }
        ),
    ),
    RoleDefinition(
        slug="security_admin",
        name="Security Admin",
        description="Manage security-sensitive user, key, wallet, session, and audit operations.",
        permissions=frozenset(
            {
                USERS_READ,
                USERS_SENSITIVE_READ,
                USERS_DISABLE,
                USERS_SESSIONS_REVOKE,
                API_KEYS_READ,
                API_KEYS_REVOKE,
                SERVICE_CLIENTS_READ,
                SERVICE_CLIENTS_WRITE,
                SERVICE_CLIENTS_REVOKE,
                WALLETS_READ,
                WALLETS_WRITE,
                WALLETS_SENSITIVE_READ,
                AUDIT_READ,
                AUDIT_SENSITIVE_READ,
                STATS_READ,
            }
        ),
    ),
    RoleDefinition(
        slug="analyst",
        name="Analyst",
        description="Masked read-only access to platform lists and stats.",
        permissions=frozenset({USERS_READ, BILLING_READ, USAGE_READ, STATS_READ}),
    ),
)
