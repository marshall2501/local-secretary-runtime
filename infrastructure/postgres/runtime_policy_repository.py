"""PostgreSQL persistence adapter for owner-managed Runtime Policy Profiles.

Application policy decisions live in ritsuko/application/context_policy.py.
Do not reuse Service Connection classification roles as operation permissions.
"""
from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from ritsuko.application.context_policy import CONTEXT_LABELS, DECISIONS, EXPOSURES


class PostgresRuntimePolicyRepository:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    def list_profiles(self) -> list[dict]:
        with self._connect() as db, db.cursor() as cur:
            cur.execute(
                """SELECT p.id::text, p.name, p.purpose, p.exposure,
                          p.revision, p.updated_at, a.exposure
                   FROM secretary.policy_profiles p
                   LEFT JOIN secretary.policy_activations a ON a.profile_id=p.id
                   ORDER BY p.purpose, p.exposure, p.name"""
            )
            return [
                {"id": row[0], "name": row[1], "purpose": row[2],
                 "exposure": row[3], "revision": row[4],
                 "updated_at": row[5].isoformat(),
                 "active_exposure": row[6]}
                for row in cur.fetchall()
            ]

    def list_labels(self) -> list[dict]:
        with self._connect() as db, db.cursor() as cur:
            cur.execute(
                "SELECT label, category, description FROM secretary.permission_labels "
                "ORDER BY label"
            )
            return [
                {"label": label, "category": category, "description": description}
                for label, category, description in cur.fetchall()
            ]

    def list_grants(self, profile_id: str) -> list[dict]:
        profile_uuid = UUID(str(profile_id))
        with self._connect() as db, db.cursor() as cur:
            cur.execute(
                """SELECT label, destination_scope, decision, mask_rules
                   FROM secretary.policy_grants WHERE profile_id=%s
                   ORDER BY label, destination_scope""",
                (profile_uuid,),
            )
            return [
                {"label": row[0], "destination_scope": row[1],
                 "decision": row[2], "mask_rules": row[3]}
                for row in cur.fetchall()
            ]

    def load_snapshot(self, exposure: str = "lan") -> dict:
        """One DB transaction reads the active Profile and all its grants."""
        if exposure not in EXPOSURES:
            raise ValueError("invalid_policy_exposure")
        with self._connect() as db, db.cursor() as cur:
            cur.execute(
                """SELECT p.id::text, p.name, p.revision
                   FROM secretary.policy_activations a
                   JOIN secretary.policy_profiles p ON p.id=a.profile_id
                   WHERE a.exposure=%s""", (exposure,),
            )
            profile = cur.fetchone()
            if profile is None:
                raise ValueError("missing_policy_activation")
            cur.execute(
                """SELECT label, destination_scope, decision, mask_rules
                   FROM secretary.policy_grants
                   WHERE profile_id=%s ORDER BY label, destination_scope""",
                (UUID(profile[0]),),
            )
            grants = [
                {"label": row[0], "destination_scope": row[1],
                 "decision": row[2], "mask_rules": row[3]}
                for row in cur.fetchall()
            ]
        return {
            "profile_id": profile[0], "profile_name": profile[1],
            "revision": int(profile[2]), "exposure": exposure,
            "grants": grants,
        }

    def activate(self, exposure: str, profile_id: str) -> dict:
        if exposure not in EXPOSURES:
            raise ValueError("invalid_policy_exposure")
        profile_uuid = UUID(str(profile_id))
        with self._connect() as db, db.cursor() as cur:
            cur.execute(
                "SELECT exposure FROM secretary.policy_profiles WHERE id=%s",
                (profile_uuid,),
            )
            row = cur.fetchone()
            if row is None or row[0] != exposure:
                raise ValueError("profile_exposure_mismatch")
            cur.execute(
                """INSERT INTO secretary.policy_activations(
                       exposure, profile_id, revision, updated_at)
                   VALUES (%s,%s,1,now())
                   ON CONFLICT (exposure) DO UPDATE
                   SET profile_id=EXCLUDED.profile_id,
                       revision=secretary.policy_activations.revision + 1,
                       updated_at=now()""",
                (exposure, profile_uuid),
            )
        return self.load_snapshot(exposure)

    def save_grant(
        self, profile_id: str, label: str, destination_scope: str,
        decision: str, *, mask_tokens: list[str] | None = None,
    ) -> dict:
        if not isinstance(label, str) or not label:
            raise ValueError("invalid_policy_label")
        if destination_scope not in {"any", "local", "remote"}:
            raise ValueError("invalid_policy_destination")
        if decision not in DECISIONS:
            raise ValueError("invalid_policy_decision")
        tokens = mask_tokens if mask_tokens is not None else []
        if not isinstance(tokens, list) or len(tokens) > 20 or any(
            not isinstance(token, str) or not 1 <= len(token) <= 80
            for token in tokens
        ):
            raise ValueError("invalid_mask_tokens")
        if decision == "redact" and not tokens:
            raise ValueError("redact_requires_tokens")
        profile_uuid = UUID(str(profile_id))
        with self._connect() as db, db.cursor() as cur:
            cur.execute("SELECT id FROM secretary.policy_profiles WHERE id=%s FOR UPDATE",
                        (profile_uuid,))
            if cur.fetchone() is None:
                raise ValueError("unknown_policy_profile")
            cur.execute(
                """INSERT INTO secretary.policy_grants
                      (profile_id, label, destination_scope, decision, mask_rules)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (profile_id,label,destination_scope)
                   DO UPDATE SET decision=EXCLUDED.decision,
                                 mask_rules=EXCLUDED.mask_rules""",
                (profile_uuid, label, destination_scope, decision,
                 Jsonb({"tokens": tokens})),
            )
            cur.execute(
                """UPDATE secretary.policy_profiles
                   SET revision=revision+1, updated_at=now() WHERE id=%s""",
                (profile_uuid,),
            )
        return {"profile_id": str(profile_uuid), "label": label,
                "destination_scope": destination_scope, "decision": decision,
                "mask_rules": {"tokens": tokens}}

    def create_label(self, label: str, category: str, description: str = "") -> dict:
        if not label or not isinstance(label, str) or len(label) > 96:
            raise ValueError("invalid_policy_label")
        if not category or len(category) > 64 or len(description) > 500:
            raise ValueError("invalid_policy_label_metadata")
        with self._connect() as db, db.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary.permission_labels(label,category,description)
                   VALUES (%s,%s,%s) ON CONFLICT (label) DO NOTHING""",
                (label, category, description),
            )
        return {"label": label, "category": category, "description": description}
