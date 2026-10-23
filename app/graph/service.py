import hashlib

from sqlalchemy import select

from app.auth.service import authorize, profile, visible_profiles
from app.core import tables as t
from contracts.ports import ProfileProvider, RelationsProvider
from contracts.v1 import Quality, RelationPath


class CompanyTools(ProfileProvider, RelationsProvider):
    """Contract v1 tools over server grants, with no model calls or inferred ownership.

    CSV membership rows use [U, F/group, U]. They are expanded only between
    authorized endpoints at read time. Other stored paths keep their DTO shape.
    Self-transfers remain auditable in storage, never returned as exposure edges.
    """

    def __init__(self, engine):
        self.engine = engine

    async def get_profile(self, context, company_id):
        async with self.engine.connect() as conn:
            return await profile(conn, context, company_id)

    async def get_relations(self, context, company_id):
        async with self.engine.connect() as conn:
            await authorize(conn, context, company_id, "relations.read")
            allowed = {
                p.company_id for p in await visible_profiles(conn, context, "relations.read")
            }
            rows = (
                await conn.execute(
                    select(t.relations.c.payload)
                    .where(
                        t.relations.c.payload["nodes"].contains([company_id]),
                        t.relations.c.kind != "self_transfer",
                    )
                    .order_by(t.relations.c.id)
                )
            ).scalars()
            paths = {}
            for raw in rows:
                rel = RelationPath.model_validate(raw)
                if (
                    len(rel.nodes) < 2
                    or rel.nodes[0] not in allowed
                    or rel.nodes[-1] not in allowed
                ):
                    continue
                if rel.nodes[0] != company_id and rel.nodes[-1] != company_id:
                    continue
                if rel.kind in ("group", "shared_person") and len(rel.nodes) == 3:
                    if rel.nodes[0] == rel.nodes[-1]:
                        middle = rel.nodes[1]
                        # Only same-source memberships may form a path; hidden endpoints are
                        # filtered in SQL before DTO construction or provenance aggregation.
                        others = (
                            await conn.execute(
                                select(t.relations.c.payload)
                                .where(
                                    t.relations.c.kind == rel.kind,
                                    t.relations.c.source_id == rel.provenance[0].source_version_id,
                                    t.relations.c.payload["nodes"][1].astext == middle,
                                    t.relations.c.payload["nodes"][0].astext.in_(
                                        allowed - {company_id}
                                    ),
                                )
                                .order_by(t.relations.c.id)
                            )
                        ).scalars()
                        for other_raw in others:
                            other = RelationPath.model_validate(other_raw)
                            if other.nodes != [other.nodes[0], middle, other.nodes[0]]:
                                continue
                            nodes = [company_id, middle, other.nodes[0]]
                            key = (rel.kind, tuple(nodes))
                            if key not in paths:
                                rid = hashlib.sha256((rel.id + other.id).encode()).hexdigest()
                                paths[key] = RelationPath(
                                    id="path_" + rid,
                                    kind=rel.kind,
                                    nodes=nodes,
                                    provenance=rel.provenance + other.provenance,
                                    quality=Quality.unverified,
                                )
                        continue
                paths[(rel.kind, tuple(rel.nodes))] = rel
            return sorted(paths.values(), key=lambda path: (path.kind, path.nodes, path.id))
