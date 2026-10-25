"""Named simulator profiles, shared by scripts/load_test.py and scripts/chat.py (/sim <name>).

Values override SimSettings fields on top of the env configuration. "calm" (no saturation, no added
latency: only the real model's latency) is meant for manual testing; load tests keep their profiles.
"""
from __future__ import annotations

PROFILES: dict[str, dict] = {
    "normal": {"saturation_prob": 0.15, "compact_saturation_prob": 0.0, "midsize_slots": 1},
    "calm": {"saturation_prob": 0.0, "compact_saturation_prob": 0.0, "midsize_slots": 0, "latency_scale": 0.0},
    "saturated": {"saturation_prob": 0.9, "compact_saturation_prob": 0.2, "midsize_slots": 1},
    "quota_low": {"saturation_prob": 0.15, "compact_saturation_prob": 0.0, "midsize_slots": 1,
                  "rate_per_min": 10.0, "burst": 4},
    "capacity": {"saturation_prob": 0.15, "compact_saturation_prob": 0.0, "midsize_slots": 1},
}
LOAD_TEST_PROFILES = ("normal", "saturated", "quota_low", "capacity")
CHAT_PROFILES = ("normal", "calm", "saturated", "quota_low", "off")


def env_overrides(name: str) -> dict[str, str]:
    """The same profile as SIM_* environment variables (load_test builds its Settings from env)."""
    return {f"SIM_{k.upper()}": str(v) for k, v in PROFILES[name].items()}
