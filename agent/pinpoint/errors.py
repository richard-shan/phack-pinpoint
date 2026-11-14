"""Exception hierarchy for Pinpoint failure modes.

Any bad-input path should raise one of these subclasses. The orchestrator
catches them and produces an UNABLE or DEGRADED packet instead of crashing.
"""


class PinpointFailure(Exception):
    """Base for all expected failure paths the orchestrator handles."""
    failure_mode = "unknown"


class MatterportUnavailable(PinpointFailure):
    """No Matterport twin exists for this building, or match_photo cannot run."""
    failure_mode = "matterport_unavailable"


class VexcelUnavailable(PinpointFailure):
    """Vexcel aerial fetch failed. Site layer falls back to fixture data."""
    failure_mode = "vexcel_unavailable"


class PropertyNotFound(PinpointFailure):
    """Address not in CoStar. Agent stops before locator and site fire."""
    failure_mode = "property_not_found"


class IntakeIncomplete(PinpointFailure):
    """Required intake fields are missing. Agent never fires subagents."""
    failure_mode = "intake_incomplete"

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__(f"intake missing: {', '.join(self.missing)}")


class EmptyCandidates(PinpointFailure):
    """Locator returned zero candidates (unreadable photo, no match, etc.)."""
    failure_mode = "empty_candidates"


class ExhaustedCandidates(PinpointFailure):
    """Dispatcher rejected all plausible candidates; nothing left to propose."""
    failure_mode = "exhausted_candidates"
