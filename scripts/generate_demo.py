"""Generate the deterministic, entirely synthetic data center and broken fixtures."""

import argparse
import json
from pathlib import Path

from eas_hmi.demo.synthetic import write_demo

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parents[1] / "examples/datacenter"
    )
    args = parser.parse_args()
    print(json.dumps(write_demo(args.output), indent=2))
