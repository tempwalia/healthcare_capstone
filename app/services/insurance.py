import random
from typing import Tuple

# Mirrors mock_systems/payer_mock/main.py::PLANS exactly — keep these two in
# sync if a plan is ever added/renamed there. The single source of truth for
# "what a randomly assigned policy looks like" lives here so the patient
# creation route and scripts/seed_sample_insurance.py can't drift apart.
VERIFIED_POLICIES = {
    "ACME-991123": "Acme Health",
    "ACME-778890": "Acme Health",
    "HORIZON-556677": "Horizon Blue",
    "UNITEDCARE-334455": "UnitedCare",
}
UNVERIFIED_POLICIES = {
    "SELFPAY-000000": "Self-Pay",
    "LEGACY-PLAN-999": "Unknown / Lapsed Provider",
}
VERIFIED_WEIGHT = 0.75


def random_policy() -> Tuple[str, str]:
    """Returns (policy_number, provider_name). ~75% land on one of the four
    verified demo policies (so a referral's eligibility check sails
    through); the rest get an unrecognized policy on purpose, so the
    eligibility_denied path has real patients to test against too, not just
    the happy path — same trade-off scripts/seed_sample_insurance.py makes."""
    if random.random() < VERIFIED_WEIGHT:
        policy_number = random.choice(list(VERIFIED_POLICIES))
        return policy_number, VERIFIED_POLICIES[policy_number]
    policy_number = random.choice(list(UNVERIFIED_POLICIES))
    return policy_number, UNVERIFIED_POLICIES[policy_number]
