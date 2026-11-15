
# These tests were written for routes that may take elevators (the toy building has one).
# The product now avoids elevators by default; test_no_elevators.py covers that.
from env.config import ROUTING as _ROUTING
_ROUTING["avoid_elevators"] = False
