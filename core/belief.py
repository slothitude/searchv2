"""Belief Propagation Engine — 4-phase sweep: detect contradictions,
resolve them, propagate confidence through the relationship graph,
and trigger curiosity re-ingest on significant shifts."""

import asyncio
import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from config import settings
from models.base import async_session
from models.knowledge import (
    Entity, Relationship, Claim, Contradiction, PropagationLog,
)
from models.queue import QueueStore
from core.events import bus
from core.ollama import call_ollama

log = logging.getLogger("searchv2.belief")

# Antonym sets for structural contradiction detection
_STATUS_ANTONYMS = [
    {"active", "discontinued"},
    {"active", "deprecated"},
    {"active", "inactive"},
    {"discontinued", "deprecated"},
    {"discontinued", "inactive"},
    {"deprecated", "inactive"},
]

# Propagation rules: relation_type → (factor, direction)
# direction: "forward" = from→to (DB stored direction),
#            "reverse" = to→from, "both" = both ways
# Semantics: when the SOURCE entity has a problem, which neighbors get affected?
# "depends_on" stored as (dependent → dependency). Forward = dependent → dependency.
# But when the dependency breaks, the dependent suffers — so we need REVERSE.
_PROPAGATION_RULES: dict[str, tuple[float, str]] = {
    "depends_on": (0.8, "reverse"),  # dependency breaks → dependent suffers
    "part_of":    (0.6, "reverse"),  # parent breaks → child suffers
    "uses":       (0.4, "reverse"),  # tool breaks → user suffers
    "owns":       (0.3, "forward"),  # owned thing breaks → owner suffers
    "has":        (0.2, "forward"),  # possession breaks → owner suffers
    "located_in": (0.1, "reverse"),  # location problem → entity suffers
    "related":    (0.05, "both"),
}

# Numeric key suffixes that support range-conflict detection
_NUMERIC_KEYS = {"version", "price", "count", "amount", "size", "weight", "age", "year", "date"}


@dataclass
class SweepResult:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sweep_id: int = 0
    contradictions_found: int = 0
    contradictions_resolved: int = 0
    entities_affected: int = 0
    curiosity_triggered: list[dict] = field(default_factory=list)
    phase_stats: dict = field(default_factory=dict)


class BeliefPropagation:
    """Runs a single 4-phase belief propagation sweep."""

    def __init__(self, db):
        self.db = db
        self._llm_calls_used = 0
        self._sweep_counter = 0
        self._sweep_entity_ids: set[int] = set()

    async def sweep(self) -> SweepResult:
        result = SweepResult()
        try:
            # Phase 1: Detect contradictions
            found = await self._detect_contradictions()
            result.contradictions_found = len(found)
            result.phase_stats["contradictions_detected"] = len(found)
            self._sweep_entity_ids: set[int] = {c.entity_id for c in found}
            await self.db.flush()  # flush new contradictions so Phase 2 can query them

            # Phase 2: Resolve contradictions
            resolved = await self._resolve_contradictions()
            result.contradictions_resolved = resolved
            result.phase_stats["contradictions_resolved"] = resolved

            # Phase 3: Propagate confidence
            affected = await self._propagate_confidence(result)
            result.entities_affected = affected
            result.phase_stats["entities_affected"] = affected

            # Phase 4: Curiosity integration
            result.curiosity_triggered = await self._check_curiosity_triggers()

            # Publish completion event
            await bus.publish("belief.propagation_completed", {
                "timestamp": result.timestamp.isoformat(),
                "contradictions_found": result.contradictions_found,
                "resolved": result.contradictions_resolved,
                "entities_affected": result.entities_affected,
                "curiosity_triggered": len(result.curiosity_triggered),
            })

            log.info("Belief sweep: %d contradictions found, %d resolved, %d entities affected",
                     result.contradictions_found, result.contradictions_resolved,
                     result.entities_affected)

        except Exception as e:
            log.error("Belief sweep error: %s", e, exc_info=True)

        return result

    # ── Phase 1: Contradiction Detection ──────────────────

    async def _detect_contradictions(self) -> list[Contradiction]:
        # Get all claims grouped by entity
        result = await self.db.execute(
            select(Claim.entity_id, Claim.id, Claim.claim_key, Claim.claim_value,
                   Claim.confidence, Claim.claim_type)
        )
        rows = result.all()

        claims_by_entity: dict[int, list] = defaultdict(list)
        for entity_id, claim_id, key, value, confidence, claim_type in rows:
            claims_by_entity[entity_id].append({
                "id": claim_id, "key": key, "value": value,
                "confidence": confidence, "type": claim_type,
            })

        # Get existing pairs for dedup (resolved AND unresolved)
        existing = await self.db.execute(
            select(Contradiction.claim_a_id, Contradiction.claim_b_id)
        )
        existing_pairs: set[tuple[int, int]] = set()
        for a_id, b_id in existing.all():
            existing_pairs.add(tuple(sorted((a_id, b_id))))

        new_contradictions: list[Contradiction] = []
        for entity_id, claims in claims_by_entity.items():
            if len(claims) < 2:
                continue

            # Tier 1: Structural detection
            structural = self._detect_structural(entity_id, claims)
            new_contradictions.extend(structural)

            # Tier 2: Semantic detection (LLM, gated)
            if not structural and 2 <= len(claims) <= 5:
                semantic = await self._detect_semantic(entity_id, claims)
                new_contradictions.extend(semantic)

        # Dedup against existing and add new to session
        added = []
        for contra in new_contradictions:
            pair = tuple(sorted((contra.claim_a_id, contra.claim_b_id)))
            if pair not in existing_pairs:
                self.db.add(contra)
                existing_pairs.add(pair)
                added.append(contra)

        return added

    def _detect_structural(self, entity_id: int, claims: list[dict]) -> list[Contradiction]:
        found: list[Contradiction] = []
        n = len(claims)

        # Group by claim_key
        by_key: dict[str, list[dict]] = defaultdict(list)
        for c in claims:
            by_key[c["key"]].append(c)

        # Key collisions: same key, different value
        for key, group in by_key.items():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    if group[i]["value"].lower() != group[j]["value"].lower():
                        sev = self._severity_for_key_collision(group[i], group[j])
                        found.append(self._make_contradiction(
                            entity_id, group[i]["id"], group[j]["id"],
                            "key_collision", sev,
                        ))

        # Status antonym conflicts
        status_claims = [c for c in claims if c["type"] == "status"]
        for i in range(len(status_claims)):
            for j in range(i + 1, len(status_claims)):
                val_i = status_claims[i]["value"].lower().strip()
                val_j = status_claims[j]["value"].lower().strip()
                for antonym_set in _STATUS_ANTONYMS:
                    if val_i in antonym_set and val_j in antonym_set and val_i != val_j:
                        found.append(self._make_contradiction(
                            entity_id, status_claims[i]["id"], status_claims[j]["id"],
                            "value_conflict", 0.9,
                        ))

        return found

    async def _detect_semantic(self, entity_id: int, claims: list[dict]) -> list[Contradiction]:
        """LLM-gated semantic contradiction detection."""
        if self._llm_calls_used >= settings.belief_max_llm_checks:
            return []

        # Gate: all claims must be above threshold
        if not all(c["confidence"] >= settings.belief_contradiction_llm_threshold for c in claims):
            return []

        self._llm_calls_used += 1
        try:
            claims_text = "\n".join(
                f"[{c['id']}] {c['key']}: {c['value']} (confidence: {c['confidence']:.2f})"
                for c in claims
            )
            prompt = f"""Analyze these claims about the same entity for contradictions.
Only flag TRUE contradictions (mutually exclusive statements). Similar but compatible facts are NOT contradictions.
Claims:
{claims_text}

Return JSON only: {{"contradictions": [{{"claim_a_id": N, "claim_b_id": N, "severity": 0.0-1.0, "reason": "..."}}]}}
If no contradictions, return: {{"contradictions": []}}"""
            response = await call_ollama(
                prompt,
                model=settings.model_attention,
                max_tokens=1024,
            )
            # Robust JSON extraction: find outermost braces
            start = response.find("{")
            end = response.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return []
            # Handle nested braces by matching
            depth = 0
            for i in range(start, len(response)):
                if response[i] == "{":
                    depth += 1
                elif response[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            data = json.loads(response[start:end + 1])

            found: list[Contradiction] = []
            for c in data.get("contradictions", []):
                sev = min(1.0, max(0.0, c.get("severity", 0.5)))
                found.append(self._make_contradiction(
                    entity_id, c["claim_a_id"], c["claim_b_id"],
                    "semantic", sev,
                ))
            return found

        except Exception as e:
            log.warning("Semantic contradiction detection failed: %s", e)
            return []

    def _make_contradiction(self, entity_id, claim_a_id, claim_b_id, ctype, severity) -> Contradiction | None:
        # Dedup: check if unresolved version already exists
        return Contradiction(
            entity_id=entity_id,
            claim_a_id=claim_a_id,
            claim_b_id=claim_b_id,
            contradiction_type=ctype,
            severity=severity,
        )

    def _severity_for_key_collision(self, a: dict, b: dict) -> float:
        """Calculate severity based on confidence and key type."""
        avg_conf = (a["confidence"] + b["confidence"]) / 2
        key_lower = a["key"].lower()
        for suffix in _NUMERIC_KEYS:
            if suffix in key_lower:
                return min(1.0, avg_conf + 0.2)
        return min(1.0, avg_conf * 0.8 + 0.3)

    # ── Phase 2: Contradiction Resolution ────────────────

    async def _resolve_contradictions(self) -> int:
        result = await self.db.execute(
            select(Contradiction).where(Contradiction.resolved == False)
        )
        unresolved = result.scalars().all()
        if not unresolved:
            return 0

        resolved_count = 0

        for contra in unresolved:
            claim_a = await self.db.get(Claim, contra.claim_a_id)
            claim_b = await self.db.get(Claim, contra.claim_b_id)
            if not claim_a or not claim_b:
                contra.resolved = True
                contra.method = "orphaned"
                continue

            winner, loser, method = await self._resolve_pair(claim_a, claim_b, contra.severity)

            if winner and loser:
                winner.confidence = min(1.0, winner.confidence + loser.confidence * 0.3)
                loser.confidence *= 0.5
                loser.disputed = True
                winner.updated_at = datetime.now(timezone.utc)
                loser.updated_at = datetime.now(timezone.utc)

                contra.resolved = True
                contra.winner_claim_id = winner.id
                contra.method = method
                resolved_count += 1

                entity = await self.db.get(Entity, contra.entity_id)
                entity_name = entity.name if entity else "unknown"

                await bus.publish("belief.contradiction_resolved", {
                    "entity": entity_name,
                    "winner_claim_id": winner.id,
                    "method": method,
                })

        return resolved_count

    async def _resolve_pair(self, a: Claim, b: Claim, severity: float) -> tuple:
        """Resolve contradiction between two claims. Returns (winner, loser, method)."""
        # 1. Evidence count
        if a.evidence_count > b.evidence_count:
            return a, b, "evidence"
        if b.evidence_count > a.evidence_count:
            return b, a, "evidence"

        # 2. Last verified (more recent wins)
        a_dt = a.last_verified or datetime.min.replace(tzinfo=timezone.utc)
        b_dt = b.last_verified or datetime.min.replace(tzinfo=timezone.utc)
        if a_dt > b_dt:
            return a, b, "last_verified"
        if b_dt > a_dt:
            return b, a, "last_verified"

        # 3. Confidence
        if a.confidence > b.confidence:
            return a, b, "confidence"
        if b.confidence > a.confidence:
            return b, a, "confidence"

        # 4. Escalate to LLM for high-severity ties
        if severity > 0.8:
            try:
                prompt = f"""Two claims contradict each other with equal evidence, recency, and confidence.
Claim A [{a.id}]: {a.claim_key}: {a.claim_value}
Claim B [{b.id}]: {b.claim_key}: {b.claim_value}
Which is more likely correct? Return JSON: {{"winner": "A" or "B", "reasoning": "..."}}"""
                response = await call_ollama(
                    prompt,
                    model=settings.model_reasoning,
                    max_tokens=512,
                )
                start = response.index("{")
                end = response.rindex("}") + 1
                data = json.loads(response[start:end])
                if data.get("winner") == "A":
                    return a, b, "llm_judge"
                elif data.get("winner") == "B":
                    return b, a, "llm_judge"
            except Exception as e:
                log.warning("LLM judge failed: %s", e)

        # Default: first claim wins
        return a, b, "default"

    # ── Phase 3: Confidence Propagation (BFS) ─────────────

    async def _propagate_confidence(self, result: SweepResult) -> int:
        """BFS propagation from entities involved in this sweep's contradictions."""
        dirty_entity_ids: set[int] = set()
        for eid in getattr(self, '_sweep_entity_ids', set()):
            dirty_entity_ids.add(eid)

        if not dirty_entity_ids:
            return 0

        # Get all relationships for BFS
        rels_result = await self.db.execute(select(Relationship))
        all_rels = rels_result.scalars().all()

        # Build reverse adjacency: if dirty entity X is the target of a
        # "depends_on" (forward) relationship, the source entity depends on X
        # and should receive propagated impact.
        # adj_out: from_entity → [(to_entity, ...)]
        # adj_in: to_entity → [(from_entity, ...)]
        adj_out: dict[int, list[tuple]] = defaultdict(list)
        adj_in: dict[int, list[tuple]] = defaultdict(list)
        for rel in all_rels:
            rule = _PROPAGATION_RULES.get(rel.relation_type, (0.05, "both"))
            factor, direction = rule
            entry = (rel.from_entity_id, rel.to_entity_id, rel.relation_type, rel.id, factor)
            if direction in ("forward", "both"):
                adj_out[rel.from_entity_id].append(entry)
            if direction in ("reverse", "both"):
                adj_in[rel.to_entity_id].append(entry)

        # Compute initial delta per dirty entity from disputed claims
        dirty_deltas: dict[int, float] = {}
        for eid in dirty_entity_ids:
            claims_result = await self.db.execute(
                select(Claim).where(
                    Claim.entity_id == eid,
                    Claim.disputed == True,
                )
            )
            disputed = claims_result.scalars().all()
            if disputed:
                dirty_deltas[eid] = sum(c.confidence * 0.2 for c in disputed)

        if not dirty_deltas:
            return 0

        # BFS from dirty entities outward
        visited: set[int] = set(dirty_entity_ids)
        queue: deque[tuple[int, int, float]] = deque()  # (entity_id, hop, incoming_delta)
        for eid, delta in dirty_deltas.items():
            queue.append((eid, 0, delta))

        affected = 0
        propagation_entries: list[PropagationLog] = []

        while queue:
            current_id, hop, incoming_delta = queue.popleft()
            if hop >= settings.belief_max_hops:
                continue

            if abs(incoming_delta) < settings.belief_min_delta:
                continue

            # Forward: current -> targets (via adj_out)
            for from_id, to_id, rel_type, rel_id, factor in adj_out.get(current_id, []):
                if to_id in visited:
                    continue
                visited.add(to_id)
                damping = settings.belief_damping ** (hop + 1)
                delta = incoming_delta * factor * damping
                if abs(delta) >= settings.belief_min_delta:
                    propagation_entries.append(PropagationLog(
                        source_entity_id=current_id,
                        target_entity_id=to_id,
                        relationship_id=rel_id,
                        relation_type=rel_type,
                        delta=-delta,  # negative = confidence decrease
                        reason=f"propagation hop={hop+1} factor={factor} damping={damping:.3f}",
                    ))
                    affected += 1
                    queue.append((to_id, hop + 1, delta))

            # Reverse: current <- sources (via adj_in) — i.e., entities pointing at current
            for from_id, to_id, rel_type, rel_id, factor in adj_in.get(current_id, []):
                if from_id in visited:
                    continue
                visited.add(from_id)
                damping = settings.belief_damping ** (hop + 1)
                delta = incoming_delta * factor * damping
                if abs(delta) >= settings.belief_min_delta:
                    propagation_entries.append(PropagationLog(
                        source_entity_id=current_id,
                        target_entity_id=from_id,
                        relationship_id=rel_id,
                        relation_type=rel_type,
                        delta=-delta,
                        reason=f"reverse_propagation hop={hop+1} factor={factor} damping={damping:.3f}",
                    ))
                    affected += 1
                    queue.append((from_id, hop + 1, delta))

        # Apply deltas to affected entities' claims
        target_deltas: dict[int, float] = defaultdict(float)
        for entry in propagation_entries:
            target_deltas[entry.target_entity_id] += abs(entry.delta)

        for entity_id, total_abs_delta in target_deltas.items():
            claims_result = await self.db.execute(
                select(Claim).where(Claim.entity_id == entity_id)
            )
            claims = claims_result.scalars().all()
            if claims:
                per_claim = total_abs_delta / len(claims)
                for c in claims:
                    c.confidence = max(0.0, c.confidence - per_claim)
                    c.updated_at = datetime.now(timezone.utc)

        for entry in propagation_entries:
            self.db.add(entry)

        return affected

    # ── Phase 4: Curiosity Integration ───────────────────

    async def _check_curiosity_triggers(self) -> list[dict]:
        """Trigger curiosity re-ingest for entities with significant confidence drops."""
        triggered: list[dict] = []
        queue = QueueStore()

        result = await self.db.execute(
            select(PropagationLog)
            .order_by(PropagationLog.created_at.desc())
            .limit(100)
        )
        recent_logs = result.scalars().all()

        # Group by target entity to find significant drops
        entity_deltas: dict[int, float] = defaultdict(float)
        for log_entry in recent_logs:
            entity_deltas[log_entry.target_entity_id] += abs(log_entry.delta)

        for entity_id, total_delta in entity_deltas.items():
            if total_delta > settings.belief_curiosity_threshold:
                entity = await self.db.get(Entity, entity_id)
                if not entity:
                    continue

                job = await queue.enqueue(
                    "ingest", f"Belief re-ingest: {entity.name}",
                    priority=0.7,
                )
                triggered.append({
                    "entity": entity.name,
                    "entity_id": entity_id,
                    "delta": round(total_delta, 3),
                    "job_id": job.id,
                })

                # Update entity confidence baseline for next comparison
                claims_result = await self.db.execute(
                    select(Claim).where(Claim.entity_id == entity_id)
                )
                claims = claims_result.scalars().all()
                if claims:
                    new_conf = sum(c.confidence for c in claims) / len(claims)
                    old_conf = new_conf + total_delta  # approximate

                    await bus.publish("belief.significant_shift", {
                        "entity": entity.name,
                        "old_confidence": round(old_conf, 3),
                        "new_confidence": round(new_conf, 3),
                        "delta": round(total_delta, 3),
                    })

        return triggered


class BeliefScheduler:
    """Background scheduler for periodic belief propagation sweeps."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._last_sweep: SweepResult | None = None
        self._sweeping = False

    async def start(self):
        if self._task is not None:
            log.debug("Belief scheduler already running")
            return
        if not settings.belief_enabled:
            log.info("Belief scheduler disabled by config")
            return
        self._task = asyncio.create_task(self._loop())
        log.info("Belief scheduler started (interval %ds)", settings.belief_interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("Belief scheduler stopped")

    async def trigger(self) -> SweepResult:
        """Force an immediate sweep, return results."""
        return await self._sweep()

    async def _loop(self):
        await asyncio.sleep(60)  # initial delay (let knowledge accumulate)
        while True:
            try:
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Belief sweep error: %s", e, exc_info=True)
            await asyncio.sleep(settings.belief_interval)

    async def _sweep(self) -> SweepResult:
        if self._sweeping:
            log.debug("Belief sweep already in progress, skipping")
            return self._last_sweep or SweepResult()

        self._sweeping = True
        try:
            async with async_session() as db:
                engine = BeliefPropagation(db)
                result = await engine.sweep()
                await db.commit()
                self._last_sweep = result
            return result
        except Exception as e:
            log.error("Belief sweep failed: %s", e, exc_info=True)
            return SweepResult()
        finally:
            self._sweeping = False


scheduler = BeliefScheduler()
