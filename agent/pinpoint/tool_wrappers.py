"""LLM-facing tool wrappers.

Every tool a subagent or the Commander can call is wrapped here with:
- A Pydantic `args_schema` whose Field(description=...) teaches the LLM
  the exact shape and names common mistakes up front.
- A ToolException translation layer: typed PinpointFailure exceptions
  and shape errors become ToolMessages the LLM can read and self-correct
  from, instead of Python tracebacks that crash the chain.

The underlying functions in stubs.py / localization.py / routing.py
stay unchanged. The deterministic fallback orchestrator in pipeline.py
calls them directly; only the deepagents real-llm path routes through
these wrappers.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from . import localization, routing, stubs
from .errors import (
    EmptyCandidates,
    ExhaustedCandidates,
    IntakeIncomplete,
    MatterportUnavailable,
    PinpointFailure,
    PropertyNotFound,
    VexcelUnavailable,
)

# langchain + pydantic are only present under the deepagents real-llm path
# (installed via requirements.txt into the uv-managed Python 3.11 venv).
# On stdlib-only sandboxes (Python 3.10, fallback orchestrator tests),
# we import-guard and degrade to passthroughs so other modules can still
# import this file without a hard crash.
try:
    from langchain_core.tools import ToolException, tool
    from pydantic import BaseModel, Field
    WRAPPERS_AVAILABLE = True
except Exception:  # pragma: no cover - stdlib-only fallback
    WRAPPERS_AVAILABLE = False

    class ToolException(Exception):  # type: ignore[no-redef]
        pass

    class BaseModel:  # type: ignore[no-redef]
        """Degraded stand-in used only so this module imports."""

        def __init_subclass__(cls, **kw):  # noqa: D401
            return None

        def model_dump(self, *a, **kw):  # noqa: D401
            return {}

    def Field(*a, **kw):  # type: ignore[no-redef]
        return None

    def tool(name=None, args_schema=None):  # type: ignore[no-redef]
        def deco(fn):
            # Preserve the args_schema attribute so tests can still inspect it
            # when deps are present; on the stdlib fallback it is None.
            fn.args_schema = args_schema
            # Provide a .invoke(dict) shim so code paths that call .invoke still work.
            def _invoke(payload):
                return fn(**payload)
            fn.invoke = _invoke
            return fn
        return deco

# Any typed failure from the underlying functions becomes a ToolMessage
# the LLM can read. Shape / key errors do too.
_WRAP_EXCEPTIONS = (
    PinpointFailure,
    MatterportUnavailable,
    VexcelUnavailable,
    PropertyNotFound,
    IntakeIncomplete,
    EmptyCandidates,
    ExhaustedCandidates,
    KeyError,
    ValueError,
    TypeError,
)


def _raise_tool(name: str, exc: BaseException) -> None:
    """Translate an inner exception into a helpful ToolException."""
    raise ToolException(f"{name} failed: {type(exc).__name__}: {exc}")


# ---------- shared sub-schemas ----------

class Candidate(BaseModel):
    """A weighted candidate room/position from the Matterport twin."""
    node_id: str = Field(
        description=(
            "Matterport graph node id returned by match_photo or carried through "
            "from the Locator's candidates field (for example 'suite210_interior'). "
            "NOT a room name, label, suite number, or street address."
        )
    )
    belief: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Posterior probability this candidate is the correct room. "
            "Defaults to 1.0 if omitted; values must sum roughly to 1.0 across "
            "the candidate set but will be renormalized if not."
        ),
    )
    attributes: Optional[dict] = Field(
        default=None,
        description=(
            "Optional per-candidate attribute dict (floor, has_window, near_elevator, etc.). "
            "Only needed for best_question or update_belief; omit otherwise."
        ),
    )


class Contact(BaseModel):
    """Point-of-contact for a building, from the Records brief."""
    name: Optional[str] = Field(default=None, description="Contact name.")
    method: Optional[str] = Field(
        default=None,
        description="Contact method string (phone number or email). Alternative to the phone/email fields.",
    )
    phone: Optional[str] = Field(default=None, description="Phone number if separate from method.")
    email: Optional[str] = Field(default=None, description="Email if separate from method.")
    role: Optional[str] = Field(default=None, description="Role at the building (e.g. 'building manager').")


# ---------- tool: get_property ----------

class GetPropertyArgs(BaseModel):
    address: str = Field(
        description=(
            "The dispatcher-typed street address from the intake. "
            "Example: '1200 Smith St, Houston, TX 77002'. "
            "Partial addresses are tolerated; the CoStar lookup is best-effort."
        )
    )


@tool("get_property", args_schema=GetPropertyArgs)
def get_property(address: str):
    """Look up a commercial property in CoStar by street address. Returns a dict with building_id, use_type, stories, year_built, sqft, tenants list, and a primary contact dict."""
    try:
        return stubs.get_property(address)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("get_property", e)


# ---------- tool: match_photo ----------

class MatchPhotoArgs(BaseModel):
    building_id: str = Field(
        description=(
            "Canonical building id from the Records subagent's brief "
            "(for example 'bayou-medical-plaza'). NOT the street address, NOT a tenant name."
        )
    )
    image_path: str = Field(
        description=(
            "Path to the caller's photo, from intake.caller_photo_path. "
            "The matcher is responsible for reading the bytes; just forward the path string."
        )
    )
    k: int = Field(
        default=10,
        ge=1,
        le=50,
        description="How many top candidates to return (default 10, max 50).",
    )


@tool("match_photo", args_schema=MatchPhotoArgs)
def match_photo(building_id: str, image_path: str, k: int = 10):
    """Match a caller's photo to candidate positions in the building's Matterport twin. Returns up to k ranked candidates, each with node_id, score, evidence_path, and attributes."""
    try:
        return stubs.match_photo(building_id, image_path, k)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("match_photo", e)


# ---------- tool: look ----------

class LookArgs(BaseModel):
    node_id: str = Field(
        description=(
            "Matterport graph node id from match_photo or best_question. "
            "NOT a room name or label."
        )
    )


@tool("look", args_schema=LookArgs)
def look(node_id: str):
    """Fetch a specific node's pano image_path, attributes, and neighbors from the Matterport twin."""
    try:
        return stubs.look(node_id)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("look", e)


# ---------- tool: ask_caller ----------

class AskCallerArgs(BaseModel):
    incident_id: str = Field(
        description=(
            "The current incident id (for example 'inc-20261114-183714'). "
            "Available in the Commander's state; pass it through."
        )
    )
    text: str = Field(
        description=(
            "The exact question to ask the caller, in plain English. "
            "Keep it under 15 words and specific (for example "
            "'What floor are you on, or how many flights did you come up?')."
        )
    )
    images: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional list of image paths to show the caller (for example two "
            "pano thumbnails for 'which room looks right?'). Rarely used."
        ),
    )


@tool("ask_caller", args_schema=AskCallerArgs)
def ask_caller(incident_id: str, text: str, images: Optional[list[str]] = None):
    """Ask the caller a text question and optionally show them images. Use sparingly, only when the answer changes what to do next."""
    try:
        return stubs.ask_caller(incident_id, text, images)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("ask_caller", e)


# ---------- tool: detect_site_features ----------

class DetectSiteFeaturesArgs(BaseModel):
    building_id: str = Field(
        description=(
            "Canonical building id from Records (for example 'bayou-medical-plaza'). "
            "NOT the street address."
        )
    )
    address: Optional[str] = Field(
        default=None,
        description=(
            "Optional street address, used as a Vexcel lookup key when lat/lon "
            "aren't available. Omit if the building fixtures already have coords."
        ),
    )
    lat: Optional[float] = Field(
        default=None, description="Optional latitude override. Omit to use the building's stored coords."
    )
    lon: Optional[float] = Field(
        default=None, description="Optional longitude override. Omit to use the building's stored coords."
    )


@tool("detect_site_features", args_schema=DetectSiteFeaturesArgs)
def detect_site_features(
    building_id: str,
    address: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
):
    """Identify entrances, staging areas, and obstructions around a building from the Vexcel aerial tile. Falls back to fixture data when Vexcel is unavailable."""
    try:
        return stubs.detect_site_features(building_id, address=address, lat=lat, lon=lon)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("detect_site_features", e)


# ---------- tool: hazards_along ----------

class HazardsAlongArgs(BaseModel):
    building_id: str = Field(
        description="Canonical building id from Records. NOT the street address."
    )
    path: list[str] = Field(
        min_length=1,
        description=(
            "Ordered list of graph node_ids forming a path, typically the 'path' field "
            "returned by the route tool. Each entry is a node_id string, not a room name."
        ),
    )


@tool("hazards_along", args_schema=HazardsAlongArgs)
def hazards_along(building_id: str, path: list[str]):
    """Scan a graph path for hazards such as locked doors, obstructions, or stairs on a stretcher profile. Returns a list of hazard records."""
    try:
        return stubs.hazards_along(building_id, path)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("hazards_along", e)


# ---------- tool: send_packet ----------

class SendPacketArgs(BaseModel):
    incident_id: str = Field(
        description="The current incident id, carried from the intake."
    )


@tool("send_packet", args_schema=SendPacketArgs)
def send_packet(incident_id: str):
    """Mark the responder packet as sent. Gated by dispatcher approval via the framework interrupt."""
    try:
        return stubs.send_packet(incident_id)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("send_packet", e)


# ---------- tool: notify_contact ----------

class NotifyContactArgs(BaseModel):
    incident_id: str = Field(
        description="The current incident id, carried from the intake."
    )
    contact: Optional[Contact] = Field(
        default=None,
        description=(
            "The building point-of-contact dict from the Records brief "
            "(contact.name plus one of contact.method/phone/email). "
            "Pass None or omit if no contact is available; the tool will "
            "return status='no_contact_available' rather than failing."
        ),
    )


@tool("notify_contact", args_schema=NotifyContactArgs)
def notify_contact(incident_id: str, contact: Optional[Contact] = None):
    """Notify a point-of-contact at the building. Gated by dispatcher approval. Skips cleanly when no contact is available."""
    try:
        payload = contact.model_dump(exclude_none=True) if contact else None
        return stubs.notify_contact(incident_id, payload)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("notify_contact", e)


# ---------- tool: best_question ----------

class BestQuestionArgs(BaseModel):
    candidates: list[Candidate] = Field(
        min_length=2,
        description=(
            "At least two weighted candidate rooms from the Locator. Each must have "
            "node_id, belief, and attributes (otherwise there's nothing to split on). "
            "Pass the Locator's candidates field directly."
        ),
    )
    exclude: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional list of attribute names to skip (because they've already been asked). "
            "Example: ['floor'] after the caller already answered the floor question."
        ),
    )


@tool("best_question", args_schema=BestQuestionArgs)
def best_question(candidates: list[Candidate], exclude: Optional[list[str]] = None):
    """Choose the caller question whose answer is expected to shrink the belief entropy the most across the current candidate set. Returns the attribute, a phrasing hint, and the expected information gain."""
    try:
        raw = [c.model_dump(exclude_none=True) for c in candidates]
        return localization.best_question(raw, exclude=exclude)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("best_question", e)


# ---------- tool: update_belief ----------

class UpdateBeliefArgs(BaseModel):
    candidates: list[Candidate] = Field(
        min_length=1,
        description=(
            "Current candidate rooms with their beliefs and attributes, from the Locator. "
            "Pass the full list; this function returns a re-sorted list with updated beliefs."
        ),
    )
    attribute: str = Field(
        description=(
            "The attribute the caller just answered about (for example 'floor', "
            "'has_window'). Must be one of the attribute keys on the candidates."
        )
    )
    answer: str = Field(
        description=(
            "The caller's answer, as a string. Booleans should be 'yes' or 'no'; "
            "numbers can be passed as strings ('2' for floor 2). "
            "Case and surrounding whitespace are tolerated."
        )
    )


@tool("update_belief", args_schema=UpdateBeliefArgs)
def update_belief(candidates: list[Candidate], attribute: str, answer: str):
    """Soft-update candidate beliefs given the caller's answer. Matching candidates boosted by answer-reliability; non-matching dampened; nothing zeroed. Returns the re-sorted candidates."""
    try:
        raw = [c.model_dump(exclude_none=True) for c in candidates]
        return localization.update_belief(raw, attribute, answer)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("update_belief", e)


# ---------- tool: best_entry_under_belief ----------

class BestEntryUnderBeliefArgs(BaseModel):
    building_id: str = Field(
        description=(
            "Canonical building id from the Records subagent's brief "
            "(for example 'bayou-medical-plaza'). NOT the street address, NOT a tenant name."
        )
    )
    candidates: list[Candidate] = Field(
        min_length=1,
        description=(
            "Weighted candidate rooms from the Locator's candidates field. "
            "Each needs node_id (a graph node, not a room label)."
        ),
    )
    profile: Literal["foot", "stretcher"] = Field(
        description=(
            "Responder profile. 'foot' means responders can take stairs; "
            "'stretcher' means stairs are impassable and elevators must be used."
        )
    )


@tool("best_entry_under_belief", args_schema=BestEntryUnderBeliefArgs)
def best_entry_under_belief(
    building_id: str, candidates: list[Candidate], profile: Literal["foot", "stretcher"]
):
    """Pick the responder-staging entrance that minimizes expected graph distance under weighted candidate rooms."""
    try:
        raw = [c.model_dump(exclude_none=True) for c in candidates]
        return routing.best_entry_under_belief(building_id, raw, profile)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("best_entry_under_belief", e)


# ---------- tool: route ----------

class RouteArgs(BaseModel):
    building_id: str = Field(
        description=(
            "Canonical building id from the Records brief. NOT the street address."
        )
    )
    from_node: str = Field(
        description=(
            "Graph node_id to start the path from (typically the best entrance for the responder, "
            "returned by best_entry_under_belief as entrance_id)."
        )
    )
    to_node: str = Field(
        description=(
            "Graph node_id for the destination (typically the Locator's top candidate.node_id)."
        )
    )
    profile: Literal["foot", "stretcher"] = Field(
        description="Responder profile. 'foot' allows stairs; 'stretcher' forces elevators and bans stairs."
    )
    blocked_edges: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional list of edge ids to treat as impassable (for example, after a hazard check "
            "flagged a locked door). Omit on the initial route call."
        ),
    )


@tool("route", args_schema=RouteArgs)
def route(
    building_id: str,
    from_node: str,
    to_node: str,
    profile: Literal["foot", "stretcher"],
    blocked_edges: Optional[list[str]] = None,
):
    """Compute the shortest path from from_node to to_node on the building graph under a stretcher or foot profile. Returns path_nodes, meters, and seconds."""
    try:
        return routing.route(building_id, from_node, to_node, profile, blocked_edges)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("route", e)


# ---------- tool: backup_route ----------

class BackupRouteArgs(BaseModel):
    building_id: str = Field(
        description="Canonical building id from the Records brief. NOT the street address."
    )
    from_node: str = Field(description="Graph node_id to start the alternative path from.")
    to_node: str = Field(description="Graph node_id for the destination.")
    profile: Literal["foot", "stretcher"] = Field(
        description="Responder profile. Same semantics as the route tool."
    )
    avoid_path: list[str] = Field(
        min_length=1,
        description=(
            "List of node_ids forming the original route to avoid. "
            "Pass the 'path' field returned by the route tool. The backup "
            "gets 3x cost on those edges to force an alternative."
        ),
    )


@tool("backup_route", args_schema=BackupRouteArgs)
def backup_route(
    building_id: str,
    from_node: str,
    to_node: str,
    profile: Literal["foot", "stretcher"],
    avoid_path: list[str],
):
    """Compute an alternative path from from_node to to_node that avoids the given nodes. Returns the alternative or None if no path exists."""
    try:
        return routing.backup_route(building_id, from_node, to_node, profile, avoid_path)
    except _WRAP_EXCEPTIONS as e:
        _raise_tool("backup_route", e)


# ---------- exported registry (convenience for agent_spec and deep_agent) ----------

ALL_WRAPPED_TOOLS = {
    "get_property": get_property,
    "match_photo": match_photo,
    "look": look,
    "ask_caller": ask_caller,
    "detect_site_features": detect_site_features,
    "hazards_along": hazards_along,
    "send_packet": send_packet,
    "notify_contact": notify_contact,
    "best_question": best_question,
    "update_belief": update_belief,
    "best_entry_under_belief": best_entry_under_belief,
    "route": route,
    "backup_route": backup_route,
}
