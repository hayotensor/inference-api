"""miner_models: add e2ee_public_key (talaris-e2ee/v1)

Purely additive, nullable column. Holds the enclave's attested E2EE public key
(ML-KEM-768 || X25519, 1216 bytes, lowercase hex) exactly as
``talaris_attest.verify_attestation`` returned it in
``VerificationResult.e2ee_key_hex`` -- i.e. only after the ``identity_binding``
check proved the key hashes into the attested fingerprint. NULL for pre-E2EE
enclaves, which are simply never offered on ``/v1/e2e/*``.

Revision ID: 20261019_0019
Revises: 20261019_0018
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261019_0019"
down_revision: str | None = "20261019_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("miner_models", sa.Column("e2ee_public_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("miner_models", "e2ee_public_key")
