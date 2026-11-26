#!/usr/bin/env python3
"""Demo and failure drills against the simulator's own admin API. Standard library only.

    python ops/demo/drill.py crisis    # the simulator's final_combined events, starting from the current tick
    python ops/demo/drill.py outage    # the simulator answers "unavailable" for 60 seconds
    python ops/demo/drill.py latency   # every simulator answer is 1.5 seconds late
    python ops/demo/drill.py errors    # about a quarter of simulator requests fail
    python ops/demo/drill.py stale     # the simulator marks its answers as stale
    python ops/demo/drill.py stream    # new tick-stream connections are refused (an open one stays open)
    python ops/demo/drill.py watch     # print the console's health mode every second (Ctrl+C to stop)
    python ops/demo/drill.py clear     # remove injected faults
    python ops/demo/drill.py reset     # remove faults and put the simulator back at tick 0

A fault lasts 60 seconds, or as many as you give after its name, for example `drill.py latency 30`.

Set SIMULATOR_URL if the simulator is not on http://localhost:8000 (and FUELWATCH_URL if the backend is not on
http://localhost:8080, for `watch`). These calls change the simulated world and are
not made by FuelWatch itself, so FuelWatch has to notice and cope.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

SIM = os.environ.get("SIMULATOR_URL", "http://localhost:8000").rstrip("/")
BACKEND = os.environ.get("FUELWATCH_URL", "http://localhost:8080").rstrip("/")

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


def fault(kind, seconds, parameters, what):
    call("POST", "/admin/faults", {"type": kind, "duration_seconds": seconds, "parameters": parameters})
    print(f"{what} for {seconds} seconds. 'clear' ends it sooner.")


def outage(seconds=60):
    fault("unavailable", seconds, {}, "The simulator is unavailable")


def latency(seconds=60):
    fault("latency", seconds, {"delay_ms": 1500}, "Every simulator answer is 1.5 seconds late")


def errors(seconds=60):
    fault("error_rate", seconds, {"rate": 0.25}, "About a quarter of simulator requests fail")


def stale(seconds=60):
    fault("stale_data", seconds, {}, "The simulator marks its answers as stale")


def stream(seconds=60):
    fault("stream_disconnect", seconds, {}, "New tick-stream connections are refused")


def watch():
    """One line per second: what FuelWatch itself reports about the data it is serving."""
    while True:
        try:
            with urllib.request.urlopen(f"{BACKEND}/api/status", timeout=5) as response:
                s = json.loads(response.read().decode())
            breakers = " ".join(f"{name} {info['state']}" for name, info in s["breakers"].items())
            age = s.get("age_seconds")
            print(f"{time.strftime('%H:%M:%S')}  {s['mode']:<8} tick {s['tick']}  data age {age if age is None else round(age, 1)} s  "
                  f"breakers: {breakers}  retries {s['retries']['retries_total']}  stream {s['stream']['state']}", flush=True)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
            print(f"{time.strftime('%H:%M:%S')}  no status from FuelWatch: {error}", flush=True)
        time.sleep(1)


def clear():
    call("POST", "/admin/faults/clear")
    print("Faults cleared.")


def reset():
    clear()
    call("POST", "/admin/reset")
    print("Simulator reset to tick 0.")


if __name__ == "__main__":
    faults = {"outage": outage, "latency": latency, "errors": errors, "stale": stale, "stream": stream}
    actions = {"crisis": crisis, "clear": clear, "reset": reset, "watch": watch, **faults}
    args = sys.argv[1:]
    timed = len(args) == 2 and args[0] in faults and args[1].isdigit()
    if not (timed or (len(args) == 1 and args[0] in actions)):
        sys.exit(__doc__)
    try:
        actions[args[0]](*([int(args[1])] if timed else []))
    except KeyboardInterrupt:
        pass
    except (urllib.error.URLError, OSError) as error:
        sys.exit(f"Could not reach the simulator at {SIM}: {error}")
