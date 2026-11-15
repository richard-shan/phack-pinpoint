# Expected failure modes — orchestrator turns these into UNABLE/DEGRADED packets.


class PinpointFailure(Exception):
    failure_mode = "unknown"


class MatterportUnavailable(PinpointFailure):
    failure_mode = "matterport_unavailable"


class VexcelUnavailable(PinpointFailure):
    failure_mode = "vexcel_unavailable"


class PropertyNotFound(PinpointFailure):
    failure_mode = "property_not_found"


class IntakeIncomplete(PinpointFailure):
    failure_mode = "intake_incomplete"

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__(f"intake missing: {', '.join(self.missing)}")


class EmptyCandidates(PinpointFailure):
    failure_mode = "empty_candidates"


class ExhaustedCandidates(PinpointFailure):
    failure_mode = "exhausted_candidates"
