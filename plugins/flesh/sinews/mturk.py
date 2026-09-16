"""sinew-mturk — Amazon MTurk HIT dispatch (perform_digital_task), Class A.

The heterogeneity test for the perform_* schema branch: no provider quote
round-trip (reward is composed locally: rate + 20% MTurk fee), task spec
instead of pickup/dropoff, and settle IS an action — approve_assignment
moves money, so the settle-approve step gets its own fresh T3 approval
(plan risk #3), surfaced by track() reporting `needs_settle_approval`.

boto3 is lazy-imported; sandbox endpoint by default (MTURK_SANDBOX=0 for prod).
"""

from __future__ import annotations

import os

from plugins.flesh.sinews.base import (
    CancelResult,
    DispatchResult,
    Quote,
    Sinew,
    SinewError,
    TrackResult,
)

_SANDBOX_ENDPOINT = "https://mturk-requester-sandbox.us-east-1.amazonaws.com"
_MTURK_FEE = 0.20  # MTurk commission on rewards


class MTurkSinew(Sinew):
    name = "mturk"
    verb = "perform_digital_task"

    def _client(self):
        try:
            import boto3
        except ImportError as exc:
            raise SinewError("mturk sinew requires boto3 (pip install boto3).") from exc
        kwargs = {"region_name": "us-east-1"}
        if os.environ.get("MTURK_SANDBOX", "1") != "0":
            kwargs["endpoint_url"] = _SANDBOX_ENDPOINT
        return boto3.client("mturk", **kwargs)

    def quote(self, request: dict) -> Quote:
        task = request.get("task") or {}
        rate = float(task.get("rate_usd", 0))
        if rate <= 0:
            raise SinewError("perform_digital_task requires task.rate_usd > 0.")
        # Local computation — no provider round-trip; still digest-bound and
        # T3-gated identically to courier quotes.
        price = round(rate * (1 + _MTURK_FEE), 2)
        try:
            balance = self._client().get_account_balance()
            available = float(balance.get("AvailableBalance", 0))
            if available < price:
                raise SinewError(
                    f"MTurk prepaid balance ${available:.2f} is below the "
                    f"quote ${price:.2f} — fund the requester account first."
                )
        except SinewError:
            raise
        except Exception as exc:
            raise SinewError(f"MTurk balance pre-flight failed: {exc}") from exc
        return Quote(
            sinew=self.name,
            price_usd=price,
            eta_minutes=int(task.get("duration_minutes", 60)),
            provider_quote_id=None,
            expires_at=None,  # no provider TTL; own TTL applies
            cancellation_terms=(
                "HIT can be expired before assignment; submitted work must be "
                "reviewed — approval of the assignment is a separate money-moving step."
            ),
            raw={"rate_usd": rate, "fee_pct": _MTURK_FEE},
        )

    def dispatch(self, request: dict, idempotency_key: str) -> DispatchResult:
        task = request.get("task") or {}
        spec = str(task.get("spec", "")).strip()
        if not spec:
            raise SinewError("perform_digital_task requires task.spec.")
        question = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<HTMLQuestion xmlns="http://mechanicalturk.amazonaws.com/'
            'AWSMechanicalTurkDataSchemas/2011-11-11/HTMLQuestion.xsd">'
            "<HTMLContent><![CDATA[<!DOCTYPE html><html><body>"
            f"<p>{spec}</p>"
            '<form action="https://www.mturk.com/mturk/externalSubmit" method="post">'
            '<input type="hidden" name="assignmentId" value="ASSIGNMENT_ID_NOT_AVAILABLE"/>'
            '<textarea name="answer" rows="8" cols="60"></textarea>'
            '<input type="submit" value="Submit"/></form>'
            "</body></html>]]></HTMLContent>"
            "<FrameHeight>450</FrameHeight></HTMLQuestion>"
        )
        try:
            hit = self._client().create_hit(
                Title=str(task.get("title", "Bounded task"))[:128],
                Description=spec[:2000],
                Keywords=str(task.get("keywords", "task")),
                Reward=str(task.get("rate_usd")),
                MaxAssignments=1,
                LifetimeInSeconds=int(task.get("lifetime_seconds", 86400)),
                AssignmentDurationInSeconds=int(task.get("duration_minutes", 60)) * 60,
                Question=question,
                UniqueRequestToken=idempotency_key[:64],
            )
        except Exception as exc:
            raise SinewError(f"MTurk create_hit failed: {exc}") from exc
        hit_id = hit["HIT"]["HITId"]
        return DispatchResult(provider_reference=hit_id, status="assigned", raw={"hit_id": hit_id})

    def track(self, provider_reference: str) -> TrackResult:
        try:
            client = self._client()
            assignments = client.list_assignments_for_hit(HITId=provider_reference)
        except Exception as exc:
            raise SinewError(f"MTurk track failed: {exc}") from exc
        items = assignments.get("Assignments", [])
        if not items:
            return TrackResult(status="in_progress", detail={"assignments": 0})
        assignment = items[0]
        status = assignment.get("AssignmentStatus", "")
        if status == "Submitted":
            # Work is in — settle (approve/reject) is a NEW money-moving
            # decision requiring a fresh T3 approval; not auto-completed.
            return TrackResult(
                status="arrived",
                detail={
                    "needs_settle_approval": True,
                    "assignment_id": assignment.get("AssignmentId"),
                    "answer_len": len(str(assignment.get("Answer", ""))),
                },
            )
        if status == "Approved":
            return TrackResult(status="completed", detail={"assignment_id": assignment.get("AssignmentId")})
        if status == "Rejected":
            return TrackResult(status="failed", detail={"rejected": True})
        return TrackResult(status="in_progress", detail={"assignment_status": status})

    def settle_approve(self, assignment_id: str, approve: bool, feedback: str = "") -> dict:
        """The money-moving settle step — caller must hold a fresh T3 approval."""
        client = self._client()
        try:
            if approve:
                client.approve_assignment(AssignmentId=assignment_id, RequesterFeedback=feedback[:1024])
            else:
                client.reject_assignment(AssignmentId=assignment_id, RequesterFeedback=feedback[:1024] or "Did not meet the acceptance criteria.")
        except Exception as exc:
            raise SinewError(f"MTurk settle failed: {exc}") from exc
        return {"approved": approve, "assignment_id": assignment_id}

    def cancel(self, provider_reference: str) -> CancelResult:
        try:
            client = self._client()
            import datetime

            client.update_expiration_for_hit(
                HITId=provider_reference,
                ExpireAt=datetime.datetime(2015, 1, 1),
            )
            client.delete_hit(HITId=provider_reference)
        except Exception as exc:
            return CancelResult(status="failed", raw={"error": str(exc)})
        return CancelResult(status="canceled", fee_usd=0.0, raw={})
