"""Download the study-area OSM extract and build repeatable SUMO inputs."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

# Runnable directly from PowerShell, so make the project package importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sim.chennai.landscape import BOUNDING_BOX  # noqa: E402
from sim.chennai.landscape import tidy as tidy_landscape  # noqa: E402

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print(" ".join(command))
    subprocess.run(command, check=True, env=env)  # noqa: S603


def _sumo_home() -> Path:
    configured = os.getenv("SUMO_HOME")
    if configured:
        return Path(configured).resolve()
    specification = importlib.util.find_spec("sumo")
    if specification and specification.submodule_search_locations:
        return Path(next(iter(specification.submodule_search_locations))).resolve()
    raise SystemExit(
        "SUMO was not found. Install the project SUMO extra or set SUMO_HOME."
    )


def _binary(name: str, sumo_home: Path) -> str:
    discovered = shutil.which(name)
    if discovered:
        return discovered
    suffix = ".exe" if os.name == "nt" else ""
    candidates = [
        Path(sys.prefix) / "Scripts" / f"{name}{suffix}",
        sumo_home / "bin" / f"{name}{suffix}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"SUMO binary is missing: {name}")


def _download_osm(bbox: str, destination: Path) -> None:
    url = f"https://api.openstreetmap.org/api/0.6/map?bbox={bbox}"
    print(f"Downloading {url}")
    request = Request(url, headers={"User-Agent": "ResQ-College-SUMO/0.1"})
    with urlopen(request, timeout=120) as response:  # noqa: S310
        destination.write_bytes(response.read())
    print(f"Saved {destination} ({destination.stat().st_size:,} bytes)")


def build_landscape(
    polyconvert: str, network: Path, osm_file: Path, bbox: str, env: dict[str, str]
) -> None:
    """Import OSM areas as a background map, then tidy them for sumo-gui."""

    polygons = GENERATED / "landscape.poly.xml"
    _run(
        [
            polyconvert,
            "--net-file",
            str(network),
            "--osm-files",
            str(osm_file),
            "--type-file",
            str(HERE / "landscape.typ.xml"),
            "--osm.keep-full-type",
            "--osm.merge-relations",
            "50",
            "--discard",
            "--output-file",
            str(polygons),
        ],
        env=env,
    )
    print(f"Landscape tidied: {tidy_landscape(polygons, network, osm_file, bbox)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", default=BOUNDING_BOX)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--period", type=float, default=1.5)
    parser.add_argument(
        "--landscape-only",
        action="store_true",
        help="Rebuild only the background map from the existing download and network",
    )
    args = parser.parse_args()

    sumo_home = _sumo_home()
    random_trips = sumo_home / "tools" / "randomTrips.py"
    netconvert = _binary("netconvert", sumo_home)
    polyconvert = _binary("polyconvert", sumo_home)
    if not random_trips.exists():
        raise SystemExit("SUMO Python tools are missing from the installation")
    child_env = os.environ.copy()
    child_env["SUMO_HOME"] = str(sumo_home)

    GENERATED.mkdir(parents=True, exist_ok=True)
    osm_file = GENERATED / "thousand_lights.osm.xml"
    network = GENERATED / "thousand_lights.net.xml"
    if args.landscape_only:
        if not osm_file.exists() or not network.exists():
            raise SystemExit("Run a full build first; the OSM download or network is missing")
        build_landscape(polyconvert, network, osm_file, args.bbox, child_env)
        return
    _download_osm(args.bbox, osm_file)
    _run(
        [
            netconvert,
            "--osm-files",
            str(osm_file),
            "--output-file",
            str(network),
            "--geometry.remove",
            "--roundabouts.guess",
            "--ramps.guess",
            "--junctions.join",
            "--tls.guess",
            "--tls.guess-signals",
            "--tls.discard-simple",
            "--tls.join",
            "--tls.cycle.time",
            "120",
            "--tls.yellow.time",
            "3",
            "--tls.allred.time",
            "2",
        ],
        env=child_env,
    )
    _run(
        [
            sys.executable,
            str(random_trips),
            "-n",
            str(network),
            "-r",
            str(GENERATED / "background.rou.xml"),
            "--period",
            str(args.period),
            "--seed",
            str(args.seed),
            "--validate",
            "--vehicle-class",
            "passenger",
            "--trip-attributes",
            'departLane="best" departSpeed="max"',
        ],
        env=child_env,
    )
    build_landscape(polyconvert, network, osm_file, args.bbox, child_env)
    _run(
        [
            sys.executable,
            str(HERE / "build_ambulance_route.py"),
            "--net",
            str(network),
        ]
    )
    print(f"Network ready: {network}")


if __name__ == "__main__":
    main()
