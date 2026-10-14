"""Tunable knobs for the knowledge model. Values are documented so the scoring is explainable."""

DAY = 86400.0

# How much one piece of evidence says about someone's knowledge of an asset.
EVIDENCE_WEIGHTS = {
    "commit": 1.0,          # wrote code in this area
    "pr": 1.5,              # authored a merged PR touching this area
    "review": 1.4,          # approved/reviewed changes here -> knows what "good" looks like
    "issue_answer": 1.2,    # answered someone's issue about this area
    "slack_answer": 1.3,    # answered a question in chat
    "ticket": 1.0,          # resolved a ticket
    "incident": 2.2,        # responded to / led an incident
    "decision": 2.5,        # made (or wrote down) a decision
    "doc": 1.6,             # wrote documentation
    "customer_owner": 3.0,  # owns the relationship with a customer
    "oncall": 0.8,          # on-call shift for this system
    "interview": 3.0,       # targeted exit-interview answer captured by Last Day
    "handover": 2.5,        # successor completed a handover for this asset
}

# Evidence kinds that survive the author leaving (written down somewhere).
PERSISTENT_KINDS = {"doc", "decision", "interview"}

# Knowledge fades: half-life of ~250 days for people, slower for documents.
TAU_PERSON = 360 * DAY      # e-folding time (exp(-age/tau))
TAU_DOC = 720 * DAY

# score = 1 - exp(-raw / SATURATION), capped so log(1-score) stays finite.
# SATURATION adapts to each graph: it is set so that someone at the 90th percentile of evidence
# for an area scores ~0.9 (SATURATION = SAT_FACTOR * p90(raw)). That makes a 20-person startup
# and a 600-contributor open-source repo comparable. MIN_SATURATION guards tiny graphs.
SAT_FACTOR = 0.45
MIN_SATURATION = 2.0
MAX_SCORE = 0.97

# Written knowledge can never fully replace a person.
DOC_COVERAGE_CAP = 0.5
DOC_SATURATION = 5.0

# A completed handover is worth this many "saturations" of evidence for the successor.
HANDOVER_STRENGTH = 1.2

# Coverage thresholds (coverage = probability that at least one remaining
# person or document can answer a question about the asset; noisy-OR).
ORPHAN_BELOW = 0.40
FRAGILE_BELOW = 0.65
MIN_DROP = 0.12             # a departure must remove at least this much coverage to count
SINGLE_KNOWER_BELOW = 0.75  # bus factor drops to 1 and coverage is below this -> fragile
SHARE_LOST_FRAGILE = 0.5    # the leavers hold at least half of all evidence-weighted knowledge -> fragile

# Minimum KNOWS score for someone to count as "knows this" (bus factor, bridges).
KNOWS_THRESHOLD = 0.30
