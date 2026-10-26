"""SIM test server: real sockets/rendering, logical 5 ms input samples.

Host descheduling must not impersonate a Pico loop stall in this transport test.
The normal simulator CLI retains wall time; core tests inject actual sample gaps.
No supervisor constants or policy decisions are replaced here.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import simulator


class SocketFixture(simulator.Simulation):
    def __init__(self, *args, **kwargs):
        self.fixture_ms = 0
        super().__init__(*args, **kwargs)
        self.log("SIM fixture clock", step_ms=5, source="logical input samples, not wall time")

    def now(self):
        return self.fixture_ms

    def poll(self):
        self.fixture_ms += 5
        super().poll()

    def control(self, data):
        result = super().control(data)
        result["fixture_ms"] = self.fixture_ms
        return result


if __name__ == "__main__":
    simulator.Simulation = SocketFixture
    simulator.main()
