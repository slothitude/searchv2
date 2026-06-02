"""Curiosity engine — background scheduler that scans for knowledge gaps
and autonomously queues ingest/search missions to fill them."""

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from config import settings
from models.base import async_session
from models.knowledge import Entity, Claim, Hypothesis, Prediction
from models.queue import SearchJob, QueueStore
from core.events import bus
from core.knowledge import KnowledgeGraph
from core.scientist import Scientist
from core.skills import SkillGenerator

log = logging.getLogger("searchv2.curiosity")


@dataclass
class CuriosityFinding:
    category: str       # decayed, thin_hypothesis, stale_prediction, degrading_skill, knowledge_desert
    query: str
    reason: str
    priority: float
    job_type: str       # "ingest" or "search"


@dataclass
class ScanResult:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    findings: list[CuriosityFinding] = field(default_factory=list)
    queued: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    scan_counts: dict = field(default_factory=dict)


class CuriosityScheduler:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._last_scan: ScanResult | None = None
        self._scanning = False

    async def start(self):
        if self._task is not None:
            log.debug("Curiosity scheduler already running")
            return
        if not settings.curiosity_enabled:
            log.info("Curiosity scheduler disabled by config")
            return
        self._task = asyncio.create_task(self._loop())
        log.info("Curiosity scheduler started (interval %ds)", settings.curiosity_interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            log.info("Curiosity scheduler stopped")

    async def trigger(self) -> ScanResult:
        """Force an immediate scan, return results."""
        return await self._scan()

    async def _loop(self):
        await asyncio.sleep(30)  # initial delay
        while True:
            try:
                await self._scan()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Curiosity scan error: %s", e, exc_info=True)
            await asyncio.sleep(settings.curiosity_interval)

    async def _is_recently_queued(self, query: str) -> bool:
        """Check if this query was already queued within the dedup window."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.curiosity_dedup_window)
        async with async_session() as db:
            result = await db.execute(
                select(func.count())
                .select_from(SearchJob)
                .where(
                    SearchJob.query == query,
                    SearchJob.created_at >= cutoff,
                )
            )
            return result.scalar() > 0

    def _normalize(self, s: str) -> str:
        return s.strip().lower()

    async def _scan(self) -> ScanResult:
        if self._scanning:
            log.debug("Curiosity scan already in progress, skipping")
            return self._last_scan or ScanResult()

        self._scanning = True
        result = ScanResult()
        queue = QueueStore()
        counts = Counter()

        try:
            # Collect findings from all 5 triggers
            findings: list[CuriosityFinding] = []

            findings.extend(await self._scan_decayed_claims())
            findings.extend(await self._scan_thin_hypotheses())
            findings.extend(await self._scan_stale_predictions())
            findings.extend(await self._scan_degrading_skills())
            findings.extend(await self._scan_knowledge_deserts())

            result.findings = findings
            counts = Counter(f.category for f in findings)
            result.scan_counts = dict(counts)

            # Deduplicate and queue
            seen_queries: set[str] = set()
            for finding in findings:
                norm = self._normalize(finding.query)
                if norm in seen_queries or await self._is_recently_queued(finding.query):
                    result.skipped.append(finding.query)
                    continue
                if len(result.queued) >= settings.curiosity_max_per_cycle:
                    result.skipped.append(finding.query)
                    continue
                seen_queries.add(norm)

                job = await queue.enqueue(
                    finding.job_type, finding.query,
                    priority=finding.priority,
                )
                result.queued.append({
                    "job_id": job.id, "category": finding.category,
                    "query": finding.query, "reason": finding.reason,
                    "priority": finding.priority,
                })
                counts["queued"] += 1
                log.info("Curiosity: queued %s for '%s' (%s)",
                          finding.job_type, finding.query, finding.category)

            self._last_scan = result

            # Publish event
            await bus.publish("curiosity.scan_completed", {
                "timestamp": result.timestamp.isoformat(),
                "total_findings": len(findings),
                "queued": len(result.queued),
                "skipped": len(result.skipped),
                "counts": dict(counts),
            })

            if findings:
                log.info("Curiosity scan: %d findings, %d queued, %d skipped",
                         len(findings), len(result.queued), len(result.skipped))
            else:
                log.debug("Curiosity scan: no findings")

        except Exception as e:
            log.error("Curiosity scan failed: %s", e, exc_info=True)
        finally:
            self._scanning = False

        return result

    # ── Scan triggers ──────────────────────────────────────

    async def _scan_decayed_claims(self) -> list[CuriosityFinding]:
        """Priority 0.8 — claims below re-verification threshold."""
        findings: list[CuriosityFinding] = []
        try:
            async with async_session() as db:
                kg = KnowledgeGraph(db)
                decayed = await kg.check_decay()

            # Group by entity, take worst per entity up to limit
            entities_seen: set[str] = set()
            for d in decayed:
                if d["entity"] in entities_seen:
                    continue
                if len(entities_seen) >= settings.curiosity_decay_limit:
                    break
                entities_seen.add(d["entity"])
                findings.append(CuriosityFinding(
                    category="decayed",
                    query=d["entity"],
                    reason=f"Claim '{d['key']}' decayed to {d['effective_confidence']:.2f}",
                    priority=0.8,
                    job_type="ingest",
                ))
        except Exception as e:
            log.error("Decayed claims scan error: %s", e)
        return findings

    async def _scan_thin_hypotheses(self) -> list[CuriosityFinding]:
        """Priority 0.6 — active hypotheses with low confidence, no evidence, or no predictions."""
        findings: list[CuriosityFinding] = []
        try:
            async with async_session() as db:
                scientist = Scientist(db)
                hypotheses = await scientist.list_hypotheses(status="active", limit=20)

                from models.knowledge import Evidence
                for h in hypotheses:
                    # Check for thinness: low confidence
                    if h["confidence"] < settings.curiosity_hypothesis_threshold:
                        findings.append(CuriosityFinding(
                            category="thin_hypothesis",
                            query=h["claim"][:100],
                            reason=f"Hypothesis #{h['id']} confidence {h['confidence']:.2f}",
                            priority=0.6,
                            job_type="ingest",
                        ))
                        continue

                    # Check for no evidence
                    if h.get("evidence_count", 0) == 0:
                        findings.append(CuriosityFinding(
                            category="thin_hypothesis",
                            query=h["claim"][:100],
                            reason=f"Hypothesis #{h['id']} has no evidence",
                            priority=0.6,
                            job_type="ingest",
                        ))
                        continue

                    # Check for no predictions
                    if h.get("prediction_count", 0) == 0:
                        findings.append(CuriosityFinding(
                            category="thin_hypothesis",
                            query=h["claim"][:100],
                            reason=f"Hypothesis #{h['id']} has no predictions",
                            priority=0.6,
                            job_type="search",
                        ))
        except Exception as e:
            log.error("Thin hypotheses scan error: %s", e)
        return findings

    async def _scan_stale_predictions(self) -> list[CuriosityFinding]:
        """Priority 0.5 — predictions pending > N days old."""
        findings: list[CuriosityFinding] = []
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.curiosity_prediction_stale_days)
            async with async_session() as db:
                result = await db.execute(
                    select(Prediction).where(
                        Prediction.status == "pending",
                    )
                )
                predictions = result.scalars().all()

                for p in predictions:
                    dt = p.created_at
                    if dt and dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt and dt < cutoff:
                        findings.append(CuriosityFinding(
                            category="stale_prediction",
                            query=p.prediction_text[:100],
                            reason=f"Prediction #{p.id} pending since {dt.isoformat()}",
                            priority=0.5,
                            job_type="search",
                        ))
        except Exception as e:
            log.error("Stale predictions scan error: %s", e)
        return findings

    async def _scan_degrading_skills(self) -> list[CuriosityFinding]:
        """Priority 0.4 — skills with low success rate and sufficient usage."""
        findings: list[CuriosityFinding] = []
        try:
            async with async_session() as db:
                gen = SkillGenerator(db)
                skills = await gen.list_skills(limit=50)
                for s in skills:
                    if (s["usage_count"] >= settings.curiosity_skill_min_usage
                            and s["success_rate"] < settings.curiosity_skill_min_rate):
                        findings.append(CuriosityFinding(
                            category="degrading_skill",
                            query=s["domain"],
                            reason=f"Skill '{s['name']}' success rate {s['success_rate']:.2f} after {s['usage_count']} uses",
                            priority=0.4,
                            job_type="ingest",
                        ))
        except Exception as e:
            log.error("Degrading skills scan error: %s", e)
        return findings

    async def _scan_knowledge_deserts(self) -> list[CuriosityFinding]:
        """Priority 0.3 — entities with fewer than N claims."""
        findings: list[CuriosityFinding] = []
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(Entity.name, func.count(Claim.id).label("claim_count"))
                    .outerjoin(Claim, Claim.entity_id == Entity.id)
                    .group_by(Entity.id)
                    .having(func.count(Claim.id) < settings.curiosity_desert_claim_threshold)
                    .limit(10)
                )
                for name, count in result.all():
                    findings.append(CuriosityFinding(
                        category="knowledge_desert",
                        query=name,
                        reason=f"Entity '{name}' has only {count} claim(s)",
                        priority=0.3,
                        job_type="ingest",
                    ))
        except Exception as e:
            log.error("Knowledge deserts scan error: %s", e)
        return findings


scheduler = CuriosityScheduler()
