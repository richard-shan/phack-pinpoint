# Paths + timing assumptions (say so on the slide).
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
FIXTURES = AGENT_DIR / "pinpoint" / "fixtures"
INCIDENTS = AGENT_DIR / "incidents"
BUILDINGS = AGENT_DIR / "buildings"
AGENTS_MD = AGENT_DIR / "AGENTS.md"

# edge costs (seconds), per profile
WALK_SPEED = {"foot": 1.4, "stretcher": 0.9}  # m/s
VEHICLE_SPEED = 8.0
DOOR_DELAY = {"foot": 3.0, "stretcher": 6.0}
LOCKED_DOOR_DELAY = {"foot": 30.0, "stretcher": 45.0}
STAIR_SECONDS_PER_FLIGHT = {"foot": 22.0, "stretcher": None}  # None = impassable
ELEVATOR_WAIT = 25.0
ELEVATOR_SECONDS_PER_FLOOR = 6.0

# localization loop
CONFIDENCE_THRESHOLD = 0.80
MAX_QUESTIONS = 3
ANSWER_RELIABILITY = 0.85  # P(caller answers correctly)
EARLY_EXIT_THRESHOLD = 0.95  # top candidate so dominant; stop asking even if MAX not reached

DEMO_BUILDING = "bayou-medical-plaza"
