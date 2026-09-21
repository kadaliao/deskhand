"""A hand for desktop agents.

Accessibility is the skeleton, pixels are an overlay, and the loop survives
failures instead of ending on them.
"""

from .errors import BadChoice, CannotDo, DeskhandError, NoPermission, StaleTarget
from .fingerprint import Fingerprint
from .fusion import Fusion, fuse
from .protocols import Decider, HitTester, Sensor, Source, Verifier
from .rehearse import Finding, Rehearsal, rehearse
from .runner import Runner
from .types import (
    ANYWHERE,
    FROM_INPUT,
    ON_A_TARGET,
    Action,
    Box,
    CheckResult,
    Choice,
    Limits,
    Report,
    Status,
    Step,
    Target,
    Task,
    Times,
    Verb,
    View,
)
from .validate import build_action
from .verify import NoVerifier, PredicateVerifier

__all__ = [
    "ANYWHERE",
    "FROM_INPUT",
    "ON_A_TARGET",
    "Action",
    "BadChoice",
    "Box",
    "CannotDo",
    "CheckResult",
    "Choice",
    "Decider",
    "DeskhandError",
    "Finding",
    "Fingerprint",
    "Fusion",
    "HitTester",
    "Limits",
    "NoPermission",
    "NoVerifier",
    "PredicateVerifier",
    "Rehearsal",
    "Report",
    "Runner",
    "Sensor",
    "Source",
    "StaleTarget",
    "Status",
    "Step",
    "Target",
    "Task",
    "Times",
    "Verb",
    "Verifier",
    "View",
    "build_action",
    "fuse",
    "rehearse",
]
