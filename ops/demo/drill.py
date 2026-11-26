#!/usr/bin/env python3
"""Demo and failure drills against the simulator's own admin API. Standard library only.

    python ops/demo/drill.py crisis    # the simulator's final_combined events, starting from the current tick
    python ops/demo/drill.py outage    # the simulator answers "unavailable" for 60 seconds
    python ops/demo/drill.py clear     # remove injected faults
    python ops/demo/drill.py reset     # remove faults and put the simulator back at tick 0

Set SIMULATOR_URL if the simulator is not on http://localhost:8000. These calls change the simulated world and are
not made by FuelWatch itself, so FuelWatch has to notice and cope.
"""

import json
import os
import sys
import urllib.error
import urllib.request

SIM = os.environ.get("SIMULATOR_URL", "http://localhost:8000").rstrip("/")

# (ticks from now, type, duration in ticks, parameters). Same events and spacing as the final_combined scenario.
CRISIS = [
    (1, "demand_spike", 16, {"region_ids": ["region-dhaka"], "multiplier": 1.7}),
    (3, "shipment_delay", 1, {"depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL", "PETROL"], "delay_ticks": 8}),
    (7, "route_disruption", 10, {"route_ids": ["route-gazipur-mirpur"]}),
]


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(SIM + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        text = response.read().decode()
    return json.loads(text) if text[:1] in "[{" else text


def crisis():
    # /v1 is the simulator's public API; it is subject to injected faults, so clear them first if this fails.
    now = call("GET", "/v1/instance")["tick"]
    for offset, kind, duration, parameters in CRISIS:
        call("POST", "/admin/events", {"type": kind, "start_tick": now + offset, "duration_ticks": duration, "parameters": parameters})
        print(f"{kind}: ticks {now + offset} to {now + offset + duration}")
    print(f"Now at tick {now}. Run or step the simulator to let the events happen.")


def outage():
    call("POST", "/admin/faults", {"type": "unavailable", "duration_seconds": 60})
    print("The simulator is unavailable for 60 seconds. 'clear' ends it sooner.")


def clear():
    call("POST", "/admin/faults/clear")
    print("Faults cleared.")


def reset():
    clear()
    call("POST", "/admin/reset")
    print("Simulator reset to tick 0.")


if __name__ == "__main__":
    actions = {"crisis": crisis, "outage": outage, "clear": clear, "reset": reset}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        sys.exit(__doc__)
    try:
        actions[sys.argv[1]]()
    except (urllib.error.URLError, OSError) as error:
        sys.exit(f"Could not reach the simulator at {SIM}: {error}")
