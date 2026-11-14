"""Tunable constants + paths. Plan 4A step 2: 'put the constants in one config file'.
These are ASSUMPTIONS — say so on the slide. Ask the interviewee for real stair-with-gear times."""
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent          # agent/
FIXTURES = AGENT_DIR / "pinpoint" / "fixtures"
INCIDENTS = AGENT_DIR / "incidents"
BUILDINGS = AGENT_DIR / "buildings"
AGENTS_MD = AGENT_DIR / "AGENTS.md"                          # per-building learned corrections

# --- edge time costs (seconds), per profile. Plan 4A step 2 table. ---
WALK_SPEED = {"foot": 1.4, "stretcher": 0.9}                 # m/s on open corridor
VEHICLE_SPEED = 8.0                                          # m/s in a lot
DOOR_DELAY = {"foot": 3.0, "stretcher": 6.0}                 # fixed seconds
LOCKED_DOOR_DELAY = {"foot": 30.0, "stretcher": 45.0}        # forcing / badge
STAIR_SECONDS_PER_FLIGHT = {"foot": 22.0, "stretcher": None} # None = impassable
ELEVATOR_WAIT = 25.0
ELEVATOR_SECONDS_PER_FLOOR = 6.0

# --- localization question loop ---
CONFIDENCE_THRESHOLD = 0.80      # stop asking once top candidate passes this
MAX_QUESTIONS = 3                # then fall back to two-thumbnail confirm
ANSWER_RELIABILITY = 0.85        # P(caller answers correctly) for update_belief soft reweight

DEMO_BUILDING = "bayou-medical-plaza"
