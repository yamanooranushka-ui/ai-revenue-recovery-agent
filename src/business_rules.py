"""
business_rules.py (Phase 1 + Phase 2)

Single home for every business rule the agent uses.

Rules that have NOT been decided yet are written as the string
"NOT_YET_SPECIFIED". No code may treat a NOT_YET_SPECIFIED rule as
approved - the workflows must refuse to act on it.

The vocabularies below describe the SHAPE of the CSV data
(which field values are known). They are not recovery behaviour.
"""

# --- Confirmed Phase 2 rules ---
INSUFFICIENT_FUNDS_MAX_NOTIFICATIONS = 3  # Confirmed: at most three SMS
INSUFFICIENT_FUNDS_SPACING_DAYS = 2  # Confirmed: approximately two days apart
TECHNICAL_ERROR_MAX_NOTIFICATIONS = 1  # Confirmed: exactly one SMS
BEGINNING_OF_MONTH_FIRST_DAY = 1  # Confirmed: beginning of month = days 1-10
BEGINNING_OF_MONTH_LAST_DAY = 10
SMS_CHANNEL = "SMS"  # Confirmed channel for Phase 2 notifications

# --- Confirmed Phase 4 rules (AI drafting is suggestion-only) ---
AI_DRAFT_MAX_LENGTH = 320  # Confirmed: maximum 320 characters per AI draft
AI_DRAFTING_REQUIRES_APPROVAL = (
    True  # Confirmed: AI drafts await explicit human approval
)

# --- Rules that are NOT_YET_SPECIFIED (to be decided by the business, not by code) ---
MONTH_ROLLOVER_RULE = "NOT_YET_SPECIFIED"  # Scheduling pauses at a month boundary instead of rolling over
BANK_ERROR_SMS_LIMIT = "NOT_YET_SPECIFIED"  # Historical note: limit is now confirmed above (1 SMS)
BEGINNING_OF_MONTH_THRESHOLD = "NOT_YET_SPECIFIED"  # Historical note: days 1-10 confirmed above
MID_MONTH_INSUFFICIENT_FUNDS_LIMIT = "NOT_YET_SPECIFIED"  # Historical note: 3-cap confirmed to apply mid-month too
INSUFFICIENT_FUNDS_CHANNEL = "NOT_YET_SPECIFIED"  # Historical note: SMS confirmed above
PHONE_NUMBER_SOURCE = "NOT_YET_SPECIFIED"  # Where do customer phone numbers come from? (CRM, later)
LOW_USAGE_RECOVERY_RULE = "NOT_YET_SPECIFIED"  # What to do for lapsed subs with low/mixed usage?
NO_USAGE_RECOVERY_RULE = "NOT_YET_SPECIFIED"  # What to do when there is no usage history?

# --- Approved data vocabularies (CSV field validation only) ---
PAYMENT_STATUSES = ("failed", "successful")
SUBSCRIPTION_STATUSES = ("active", "lapsed")
KNOWN_FAILURE_REASONS = ("insufficient_funds", "bank_error", "payment_processor_error")
USAGE_LEVELS = ("high", "low")
