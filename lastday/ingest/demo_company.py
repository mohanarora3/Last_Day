"""Acme Pay: a fictional fintech used for the Last Day demo.

Everything here is invented. The dataset mixes every source Last Day understands
(GitHub-style commits/PRs/reviews, Slack answers, docs, tickets, incidents,
decisions and customer ownership) and contains a few deliberate storylines:

* Priya Raman is the hidden single point of failure: she is the only person who
  really knows ledger reconciliation, made the idempotency decision, owns the
  Zenith Retail escalations and is the only real link between Payments and Risk.
* Arjun and Meera both know the payments gateway; take both out for Diwali week
  and see who is left.
* Vikram Nair already left 8 months ago; the bank SFTP integration has been
  quietly orphaned ever since (pre-existing knowledge debt).
"""
from __future__ import annotations

import math
import random
import time

DAY = 86400

TEAMS = [
    {"id": "payments", "name": "Payments"},
    {"id": "risk", "name": "Risk & Fraud"},
    {"id": "platform", "name": "Platform"},
    {"id": "data", "name": "Data"},
    {"id": "cs", "name": "Customer Success"},
]

# id, name, team, role, active, {asset: activity per month}
PEOPLE = [
    ("priya", "Priya Raman", "payments", "Staff Engineer", True,
     {"ledger-recon": 7.0, "settlement-batch": 2.5, "chargeback-ops": 1.6, "payments-gateway": 0.7,
      "zenith-retail": 1.2, "month-end-close": 1.0, "refunds-service": 0.4}),
    ("arjun", "Arjun Mehta", "payments", "Senior Engineer",
     True, {"payments-gateway": 6.0, "refunds-service": 1.5, "notification-svc": 0.8, "merchant-dashboard": 0.6,
            "bluebird-airlines": 0.5}),
    ("meera", "Meera Iyer", "payments", "Engineer", True,
     {"payments-gateway": 4.0, "refunds-service": 3.0, "notification-svc": 0.6}),
    ("rahul", "Rahul Verma", "payments", "Engineer", True,
     {"refunds-service": 3.5, "settlement-batch": 1.2, "ledger-recon": 0.35, "payments-gateway": 0.3}),
    ("nisha", "Nisha Kapoor", "payments", "Engineering Manager", True,
     {"payments-gateway": 0.25, "month-end-close": 0.8, "pci-audit": 1.2}),
    ("daniel", "Daniel Okafor", "risk", "Risk Lead", True,
     {"fraud-engine": 5.0, "chargeback-ops": 2.5, "kyc-service": 1.0}),
    ("sofia", "Sofia Lindqvist", "risk", "ML Engineer", True,
     {"fraud-engine": 4.5, "chargeback-ops": 1.2, "data-warehouse": 0.6}),
    ("hassan", "Hassan Ali", "risk", "Engineer", True,
     {"kyc-service": 4.0, "auth-service": 1.0, "fraud-engine": 0.8}),
    ("kenji", "Kenji Watanabe", "platform", "Principal SRE", True,
     {"kubernetes": 5.0, "observability": 3.0, "ci-cd": 2.0, "payments-gateway": 0.3}),
    ("lucas", "Lucas Moreau", "platform", "Platform Engineer", True,
     {"ci-cd": 4.0, "kubernetes": 2.0, "auth-service": 0.8}),
    ("aisha", "Aisha Bello", "platform", "Security Engineer", True,
     {"auth-service": 4.0, "pci-audit": 2.5, "kubernetes": 0.5}),
    ("ananya", "Ananya Gupta", "data", "Data Engineer", True,
     {"data-warehouse": 5.0, "finance-reporting": 3.0, "month-end-close": 0.5}),
    ("marco", "Marco Rossi", "data", "Analytics Engineer", True,
     {"finance-reporting": 3.5, "data-warehouse": 2.0, "merchant-dashboard": 1.0}),
    ("fatima", "Fatima Zahra", "cs", "Account Manager", True,
     {"bluebird-airlines": 3.0, "cobalt-foods": 2.5, "merchant-dashboard": 0.8}),
    ("tom", "Tom Becker", "cs", "Support Lead", True,
     {"merchant-dashboard": 2.5, "cobalt-foods": 1.0, "refunds-service": 0.5, "zenith-retail": 0.15}),
    ("chloe", "Chloe Martin", "payments", "Frontend Engineer", True,
     {"merchant-dashboard": 4.5, "notification-svc": 1.5}),
    ("vikram", "Vikram Nair", "payments", "Senior Engineer (left)", False,
     {"bank-sftp": 4.0, "settlement-batch": 2.0, "ledger-recon": 0.6}),
]

ASSETS = [
    # id, name, kind, team, business weight, aliases, description
    ("payments-gateway", "Payments Gateway", "system", "payments", 0.9, ["gateway", "payments", "checkout"],
     "Accepts card/UPI payments from merchants and routes them to acquirers."),
    ("ledger-recon", "Ledger Reconciliation", "system", "payments", 0.9, ["recon", "ledger", "reconciliation"],
     "Matches our internal ledger against bank and acquirer settlement files every night."),
    ("settlement-batch", "Settlement Batch", "system", "payments", 0.7, ["settlement", "payouts"],
     "Nightly job that pays merchants out (T+1)."),
    ("refunds-service", "Refunds Service", "system", "payments", 0.5, ["refunds", "refund"],
     "Handles full and partial refunds."),
    ("notification-svc", "Notification Service", "system", "payments", 0.2, ["notifications", "webhooks"],
     "Merchant webhooks, emails and SMS."),
    ("merchant-dashboard", "Merchant Dashboard", "system", "payments", 0.5, ["dashboard", "frontend"],
     "Web dashboard merchants use to see payments and payouts."),
    ("bank-sftp", "Bank Partner SFTP", "system", "payments", 0.6, ["sftp", "bank integration", "unity bank"],
     "Exchanges settlement files with Unity Bank (fictional) over SFTP."),
    ("fraud-engine", "Fraud Engine", "system", "risk", 0.8, ["fraud", "risk rules", "fraud model"],
     "Scores every payment for fraud risk."),
    ("chargeback-ops", "Chargeback Operations", "process", "risk", 0.6, ["chargebacks", "disputes"],
     "Dispute handling, including the ledger entries for chargebacks."),
    ("kyc-service", "KYC Service", "system", "risk", 0.5, ["kyc", "onboarding checks"],
     "Merchant onboarding verification."),
    ("auth-service", "Auth Service", "system", "platform", 0.6, ["auth", "login", "sso"],
     "Authentication for merchants and staff."),
    ("kubernetes", "Kubernetes Platform", "system", "platform", 0.7, ["k8s", "cluster", "infra"],
     "Production clusters everything runs on."),
    ("ci-cd", "CI/CD Pipeline", "system", "platform", 0.4, ["ci", "deploys", "pipeline"],
     "Build and deploy pipeline."),
    ("observability", "Observability", "system", "platform", 0.3, ["monitoring", "alerts", "grafana"],
     "Metrics, logs and alerting."),
    ("data-warehouse", "Data Warehouse", "system", "data", 0.5, ["warehouse", "dwh", "bigquery"],
     "Analytics warehouse fed from all services."),
    ("finance-reporting", "Finance Reporting", "system", "data", 0.6, ["finance reports", "reporting"],
     "Reports finance uses for revenue and month-end."),
    ("month-end-close", "Month-end Close", "process", "payments", 0.8, ["month end", "close"],
     "The monthly books-closing process with Finance."),
    ("pci-audit", "PCI DSS Audit", "process", "platform", 0.7, ["pci", "compliance"],
     "Yearly PCI compliance audit."),
    ("zenith-retail", "Zenith Retail", "customer", "cs", 1.0, ["zenith"],
     "Largest merchant (fictional). 31% of processed volume."),
    ("bluebird-airlines", "Bluebird Airlines", "customer", "cs", 0.7, ["bluebird"],
     "Airline merchant (fictional); heavy refunds."),
    ("cobalt-foods", "Cobalt Foods", "customer", "cs", 0.4, ["cobalt"],
     "Food delivery merchant (fictional)."),
]

DEPENDS = [
    ("payments-gateway", "fraud-engine"), ("payments-gateway", "auth-service"),
    ("payments-gateway", "notification-svc"), ("payments-gateway", "kubernetes"),
    ("refunds-service", "payments-gateway"), ("refunds-service", "ledger-recon"),
    ("settlement-batch", "ledger-recon"), ("settlement-batch", "bank-sftp"),
    ("ledger-recon", "bank-sftp"), ("ledger-recon", "data-warehouse"), ("ledger-recon", "kubernetes"),
    ("finance-reporting", "ledger-recon"), ("finance-reporting", "data-warehouse"),
    ("month-end-close", "ledger-recon"), ("month-end-close", "finance-reporting"),
    ("chargeback-ops", "ledger-recon"), ("chargeback-ops", "fraud-engine"),
    ("merchant-dashboard", "payments-gateway"), ("merchant-dashboard", "finance-reporting"),
    ("merchant-dashboard", "auth-service"),
    ("fraud-engine", "data-warehouse"), ("fraud-engine", "kubernetes"),
    ("kyc-service", "auth-service"), ("observability", "kubernetes"), ("ci-cd", "kubernetes"),
    ("auth-service", "kubernetes"), ("notification-svc", "kubernetes"),
    ("zenith-retail", "payments-gateway"), ("zenith-retail", "settlement-batch"),
    ("bluebird-airlines", "refunds-service"), ("bluebird-airlines", "payments-gateway"),
    ("cobalt-foods", "merchant-dashboard"), ("pci-audit", "auth-service"), ("pci-audit", "payments-gateway"),
]

CHANNEL = {
    "payments": "#payments", "risk": "#risk-eng", "platform": "#platform", "data": "#data", "cs": "#cs-escalations",
}

# Hand-written evidence that carries the storylines. days_ago, kind, author, assets, title, text, counterpart
STORY = [
    # ---- Priya: ledger reconciliation & the idempotency decision -------------------------------------
    (410, "decision", "priya", ["ledger-recon", "payments-gateway"],
     "ADR-014: Idempotency keys scoped to merchant + amount + 24h window",
     "We key ledger writes on (merchant_id, amount_minor, currency, 24h bucket) instead of the gateway "
     "request id, because two acquirers retry with different request ids. Using request ids caused double "
     "postings in March. Revisit only if we add an acquirer that guarantees exactly-once callbacks.", None),
    (380, "slack_answer", "priya", ["ledger-recon"],
     "Recon mismatch equal to the FX fee",
     "If recon shows a mismatch of exactly the FX fee, it's the T+1 rate snapshot. Rerun recon with "
     "--fx-snapshot=settlement-date. Don't adjust the ledger manually.", "rahul"),
    (350, "slack_answer", "priya", ["ledger-recon", "bank-sftp"],
     "Unity Bank file arrived twice",
     "Unity Bank sometimes re-sends the settlement file with a new name after 02:00. Recon dedupes on the "
     "file's trailer hash, not the filename. If you see duplicates, check the trailer, not the name.", "arjun"),
    (330, "slack_answer", "priya", ["ledger-recon"],
     "Why recon runs at 03:40 and not 02:00",
     "03:40 because the acquirer's late-presentment file lands at 03:15 IST. Moving it earlier silently "
     "drops ~0.4% of transactions into next day's recon and Finance sees phantom breaks.", "ananya"),
    (300, "slack_answer", "priya", ["ledger-recon", "chargeback-ops"],
     "Chargeback ledger entries",
     "Chargebacks must post to the suspense account first (acct 2190), never directly against merchant "
     "balance. Risk marks the dispute outcome, then recon moves it. Otherwise month-end won't balance.", "daniel"),
    (280, "incident", "priya", ["ledger-recon", "settlement-batch", "zenith-retail"],
     "INC-231: Double settlement to Zenith Retail on Diwali sale day",
     "Root cause: settlement batch was re-run manually after a timeout and recon's idempotency guard was "
     "bypassed by the --force flag. Fix: --force now requires a recon checkpoint id. First thing to check if "
     "it happens again: the checkpoint table, then the bank file trailer hashes.", None),
    (260, "slack_answer", "priya", ["ledger-recon"],
     "Recon breaks after a partial refund",
     "Partial refunds create two ledger lines; recon pairs them by refund_group_id. If refunds-service sends "
     "an empty refund_group_id (older clients do), recon flags a break. Backfill the id, rerun.", "meera"),
    (240, "slack_answer", "priya", ["ledger-recon", "month-end-close"],
     "Month-end: what Finance needs from recon",
     "Finance needs the 'unmatched > 72h' report by day 2 of the month. It's generated by recon_report.py "
     "with --aging=72; anything under 72h is normal timing noise.", "nisha"),
    (220, "slack_answer", "priya", ["ledger-recon"],
     "Where recon config lives", "Tolerance rules live in recon/rules.yaml, but the acquirer-specific "
     "overrides are in the DB table recon_overrides, not in the repo. That's on purpose so ops can hotfix.",
     "rahul"),
    (200, "slack_answer", "priya", ["chargeback-ops", "fraud-engine"],
     "Fraud flag vs chargeback status",
     "A payment can be fraud-flagged AND later disputed. Risk's dispute webhook must carry the original "
     "ledger txn id, else recon can't link it. Daniel's team added it in v3 of the webhook.", "sofia"),
    (180, "slack_answer", "priya", ["ledger-recon", "settlement-batch"],
     "Settlement holds", "If a merchant is on settlement hold, recon still matches their transactions; the "
     "hold is applied in settlement-batch. Don't pause recon for holds.", "arjun"),
    (150, "slack_answer", "priya", ["ledger-recon"],
     "Recon alert fatigue", "The 'recon_break_count' alert is noisy on the 1st of every month due to "
     "month-end accruals. Silence it for 6 hours on day 1, never longer.", "kenji"),
    (120, "slack_answer", "priya", ["ledger-recon", "data-warehouse"],
     "Recon numbers vs warehouse numbers",
     "Warehouse numbers lag recon by one run because the export job reads the previous checkpoint. A "
     "difference of exactly one day's volume is expected.", "marco"),
    (90, "slack_answer", "priya", ["ledger-recon"],
     "Adding a new acquirer to recon",
     "New acquirer = new parser in recon/parsers + a row in recon_overrides + a 2-week shadow mode. "
     "Never skip shadow mode, the first acquirer we skipped it for cost us 3 days of manual matching.", "rahul"),
    (60, "slack_answer", "priya", ["ledger-recon"],
     "Recon replay", "To replay a day: recon replay --date YYYY-MM-DD --dry-run first. The replay writes "
     "to recon_shadow until you pass --commit.", "meera"),
    (30, "slack_answer", "priya", ["ledger-recon", "zenith-retail"],
     "Zenith Retail settlement question",
     "Zenith settles in two batches because they have two legal entities. Recon groups by entity_id, "
     "so 'missing half the volume' usually means someone filtered on merchant_id only.", "tom"),
    (20, "customer_owner", "priya", ["zenith-retail"],
     "Primary technical contact for Zenith Retail escalations",
     "Zenith's head of finance escalates directly to Priya on settlement disputes. Their contract has a "
     "4-hour settlement-incident SLA.", None),
    (12, "review", "priya", ["ledger-recon"], "Reviewed: recon parser for new acquirer", "", "rahul"),
    # ---- Arjun / Meera: gateway ----------------------------------------------------------------------
    (300, "doc", "arjun", ["payments-gateway"], "Gateway runbook",
     "Runbook: 1) check acquirer status page 2) check gateway_error_rate dashboard 3) fail over routing "
     "with routing set --acquirer secondary. Circuit breaker opens at 8% errors over 2 minutes.", None),
    (200, "decision", "arjun", ["payments-gateway", "fraud-engine"],
     "ADR-021: Fraud check is synchronous with a 150ms budget",
     "Fraud scoring runs inline with a 150ms timeout; on timeout we approve and flag for async review. "
     "Chosen because the async approach doubled chargebacks in the pilot.", None),
    (45, "slack_answer", "meera", ["payments-gateway", "refunds-service"],
     "Refund on a payment still in auth state",
     "If the payment is only authorized, refunds-service issues a void, not a refund. Gateway exposes "
     "/void for that.", "fatima"),
    # ---- Vikram (already left): bank SFTP ------------------------------------------------------------
    (420, "slack_answer", "vikram", ["bank-sftp"], "SFTP key rotation",
     "Unity Bank rotates our SFTP key every 180 days and emails the old ops alias that nobody reads. "
     "Next rotation is due around the 20th. Steps are in my notes, I'll write them up.", "priya"),
    (260, "incident", "vikram", ["bank-sftp", "settlement-batch"], "INC-198: Settlement files not picked up",
     "Cause: SFTP host key changed after the bank's DC migration. Fixed by updating known_hosts on the "
     "batch runner.", None),
    # ---- Risk side ------------------------------------------------------------------------------------
    (150, "doc", "daniel", ["fraud-engine"], "Fraud rules: how to ship a rule",
     "Rules are deployed via the rules console with a 10% shadow rollout for 48h. Sofia owns the model, "
     "Daniel signs off on rule changes.", None),
    (100, "decision", "sofia", ["fraud-engine", "data-warehouse"], "ADR-030: Retrain fraud model weekly",
     "Weekly retrain on 90 days of labelled data from the warehouse; monthly was too slow for festival "
     "season fraud patterns.", None),
    # ---- Platform -------------------------------------------------------------------------------------
    (140, "doc", "kenji", ["kubernetes", "observability"], "Cluster upgrade runbook",
     "Upgrade node pools one at a time, payments namespace last. PDBs keep 2 gateway pods minimum.", None),
    (70, "doc", "aisha", ["pci-audit", "auth-service"], "PCI evidence checklist",
     "Evidence lives in the compliance drive: access reviews, key rotation logs, pentest report.", None),
    # ---- Customers ------------------------------------------------------------------------------------
    (200, "customer_owner", "fatima", ["bluebird-airlines"], "Account owner: Bluebird Airlines",
     "Monthly business review; their refunds spike after schedule changes.", None),
    (180, "customer_owner", "fatima", ["cobalt-foods"], "Account owner: Cobalt Foods", "", None),
    # ---- Data -----------------------------------------------------------------------------------------
    (110, "doc", "ananya", ["finance-reporting", "month-end-close"], "Month-end reporting guide",
     "Finance reports are built from the warehouse after recon's day-2 export. If recon is late, reports "
     "are late.", None),
]

KIND_MIX = {
    # role-ish profile -> (kind, probability)
    "eng": [("commit", 0.42), ("pr", 0.14), ("review", 0.2), ("slack_answer", 0.1), ("ticket", 0.08),
            ("oncall", 0.06)],
    "mgr": [("review", 0.3), ("ticket", 0.3), ("slack_answer", 0.25), ("doc", 0.15)],
    "cs": [("ticket", 0.55), ("slack_answer", 0.35), ("doc", 0.1)],
}

TITLES = {
    "commit": ["fix: {a} edge case", "refactor {a} client", "chore: bump {a} deps", "feat({a}): add metrics",
               "fix({a}): retry on timeout", "perf({a}): batch writes"],
    "pr": ["{a}: support new acquirer", "{a}: cleanup legacy flags", "Improve {a} error handling",
           "{a}: add idempotency tests"],
    "review": ["Reviewed change to {a}", "Approved {a} PR", "Requested changes on {a} PR"],
    "slack_answer": ["Answered a question about {a}", "Explained {a} behaviour", "Helped debug {a}"],
    "ticket": ["Resolved ticket on {a}", "Closed support ticket about {a}"],
    "oncall": ["On-call shift covering {a}"],
    "doc": ["Updated {a} docs", "Wrote notes on {a}"],
}


def _profile(team: str, role: str) -> str:
    if team == "cs":
        return "cs"
    if "Manager" in role:
        return "mgr"
    return "eng"


def _poisson(rng: random.Random, lam: float) -> int:
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def build(now: float | None = None, seed: int = 7) -> dict:
    now = float(now or time.time())
    rng = random.Random(seed)
    people_team = {p[0]: p[2] for p in PEOPLE}
    by_team: dict[str, list[str]] = {}
    for pid, _, team, _, active, _ in PEOPLE:
        if active:
            by_team.setdefault(team, []).append(pid)
    asset_name = {a[0]: a[1] for a in ASSETS}
    asset_team = {a[0]: a[3] for a in ASSETS}

    evidence = []
    n = 0
    for days_ago, kind, author, assets, title, text, counterpart in STORY:
        n += 1
        evidence.append({
            "id": f"ev{n}", "kind": kind, "author": author, "assets": assets, "title": title, "text": text,
            "ts": now - days_ago * DAY, "channel": CHANNEL.get(asset_team.get(assets[0], ""), ""),
            "url": f"acme://{kind}/{n}", "counterpart": counterpart,
        })

    months = 18
    for pid, name, team, role, active, interests in PEOPLE:
        prof = _profile(team, role)
        kinds = KIND_MIX[prof]
        for asset, rate in interests.items():
            for m in range(months):
                # departed people stopped 8 months ago
                if not active and m < 8:
                    continue
                count = _poisson(rng, rate * (0.8 + 0.4 * rng.random()))
                for _ in range(count):
                    r, acc, kind = rng.random(), 0.0, kinds[-1][0]
                    for k, pr in kinds:
                        acc += pr
                        if r <= acc:
                            kind = k
                            break
                    if asset in ("zenith-retail", "bluebird-airlines", "cobalt-foods") and kind in (
                            "commit", "pr", "review", "oncall"):
                        kind = "ticket"
                    days_ago = m * 30 + rng.random() * 30
                    n += 1
                    cp = None
                    if kind in ("review", "slack_answer", "ticket"):
                        if pid == "priya" and rng.random() < 0.45:
                            pool = by_team["risk"]  # Priya is the Payments <-> Risk bridge
                        elif rng.random() < 0.9:
                            pool = by_team.get(team, [])
                        else:
                            pool = by_team.get(asset_team.get(asset, team), [])
                        pool = [x for x in pool if x != pid]
                        cp = rng.choice(pool) if pool else None
                    evidence.append({
                        "id": f"ev{n}", "kind": kind, "author": pid, "assets": [asset],
                        "title": rng.choice(TITLES[kind]).format(a=asset_name[asset]), "text": "",
                        "ts": now - days_ago * DAY, "channel": CHANNEL.get(team, ""),
                        "url": f"acme://{kind}/{n}", "counterpart": cp,
                    })

    return {
        "meta": {"name": "Acme Pay (fictional demo company)", "source": "demo", "now": now,
                 "sources": "github,slack,docs,tickets,incidents,decisions,crm"},
        "teams": TEAMS,
        "people": [{"id": p[0], "name": p[1], "team": p[2], "role": p[3], "active": p[4], "handle": "@" + p[0]}
                   for p in PEOPLE],
        "assets": [{"id": a[0], "name": a[1], "kind": a[2], "team": a[3], "business": a[4], "aliases": a[5],
                    "description": a[6]} for a in ASSETS],
        "depends": DEPENDS,
        "evidence": evidence,
    }
