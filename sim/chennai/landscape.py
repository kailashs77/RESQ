"""Tidy polyconvert output into a clean background map for sumo-gui."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from math import hypot
from pathlib import Path

BOUNDING_BOX = "80.2370,13.0520,80.2600,13.0700"  # west,south,east,north
HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"

# OSM waterways are centrelines. Draw them at roughly their real width in metres.
WATERWAY_WIDTH_M = {
    "waterway.river": 32.0,
    "waterway.canal": 10.0,
    "waterway.stream": 4.0,
    "waterway.drain": 3.0,
}
# Wider waterways are drawn as filled outlines: sumo-gui draws thick lines as
# separate boxes, which leaves notches at every bend.
OUTLINE_MIN_WIDTH_M = 8.0
# Keep line vertices this far outside the study area so rivers run off its edge.
CLIP_MARGIN_M = 150.0

Point = tuple[float, float]
Box = tuple[float, float, float, float]


def _solve3(matrix: list[list[float]], vector: list[float]) -> list[float]:
    def det(m: list[list[float]]) -> float:
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    base = det(matrix)
    solution = []
    for column in range(3):
        replaced = [row[:] for row in matrix]
        for row in range(3):
            replaced[row][column] = vector[row]
        solution.append(det(replaced) / base)
    return solution


def bbox_to_network_xy(bbox: str, net_path: Path, osm_path: Path) -> Box:
    """Project a west,south,east,north bbox into network coordinates.

    netconvert keeps OSM node ids for most junctions, so fit x/y as an affine
    function of lon/lat over those matches. Over a few kilometres UTM is affine
    to well under a metre, and this avoids needing pyproj.
    """

    junctions = {
        item.get("id"): (float(item.get("x", "0")), float(item.get("y", "0")))
        for _, item in ET.iterparse(net_path)
        if item.tag == "junction" and not item.get("id", ":").startswith(":")
    }
    pairs = []
    for _, node in ET.iterparse(osm_path):
        if node.tag == "node" and node.get("id") in junctions:
            lon_lat = (float(node.get("lon", "0")), float(node.get("lat", "0")))
            pairs.append((lon_lat, junctions[node.get("id")]))
    if len(pairs) < 3:
        raise ValueError("Too few OSM nodes match network junctions to place the bbox")

    normal = [[0.0] * 3 for _ in range(3)]
    rhs_x, rhs_y = [0.0] * 3, [0.0] * 3
    for (lon, lat), (x, y) in pairs:
        row = (lon, lat, 1.0)
        for i in range(3):
            for j in range(3):
                normal[i][j] += row[i] * row[j]
            rhs_x[i] += row[i] * x
            rhs_y[i] += row[i] * y
    ax, bx, cx = _solve3(normal, rhs_x)
    ay, by, cy = _solve3(normal, rhs_y)

    west, south, east, north = map(float, bbox.split(","))
    corners = [(lon, lat) for lon in (west, east) for lat in (south, north)]
    xs = [ax * lon + bx * lat + cx for lon, lat in corners]
    ys = [ay * lon + by * lat + cy for lon, lat in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _parse_shape(shape: str) -> list[Point]:
    return [tuple(map(float, pair.split(",")[:2])) for pair in shape.split()]  # type: ignore[misc]


def _format_shape(points: list[Point]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def _inside(point: Point, box: Box) -> bool:
    x, y = point
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def clip_line(points: list[Point], box: Box) -> list[list[Point]]:
    """Split a polyline into the runs that fall inside ``box``.

    Each run keeps one neighbouring vertex on either side so it still reaches the
    box edge instead of stopping short of it.
    """

    runs: list[list[Point]] = []
    current: list[int] = []
    for index, point in enumerate(points):
        if _inside(point, box):
            current.append(index)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    clipped: list[list[Point]] = []
    for run in runs:
        start = max(run[0] - 1, 0)
        stop = min(run[-1] + 1, len(points) - 1)
        segment = points[start : stop + 1]
        if len(segment) >= 2:
            clipped.append(segment)
    return clipped


def buffer_line(points: list[Point], width: float) -> list[Point]:
    """Return a closed outline ``width`` wide around a polyline (mitred joins)."""

    half = width / 2
    left: list[Point] = []
    right: list[Point] = []
    for index, (x, y) in enumerate(points):
        normals = []
        for a, b in ((index - 1, index), (index, index + 1)):
            if 0 <= a and b < len(points):
                dx, dy = points[b][0] - points[a][0], points[b][1] - points[a][1]
                length = hypot(dx, dy)
                if length:
                    normals.append((-dy / length, dx / length))
        nx = sum(n[0] for n in normals)
        ny = sum(n[1] for n in normals)
        length = hypot(nx, ny) or 1.0
        nx, ny = nx / length, ny / length
        # Stretch the offset at bends so the band keeps its width, capped for spikes.
        if len(normals) == 2:
            cosine = max(normals[0][0] * nx + normals[0][1] * ny, 0.5)
        else:
            cosine = 1.0
        offset = half / cosine
        left.append((x + nx * offset, y + ny * offset))
        right.append((x - nx * offset, y - ny * offset))
    return left + right[::-1]


def tidy(poly_path: Path, net_path: Path, osm_path: Path, bbox: str) -> dict[str, int]:
    """Drop POIs and far-off shapes; clip waterways to the study area."""

    tree = ET.parse(poly_path)
    root = tree.getroot()
    xmin, ymin, xmax, ymax = bbox_to_network_xy(bbox, net_path, osm_path)
    box = (xmin - CLIP_MARGIN_M, ymin - CLIP_MARGIN_M, xmax + CLIP_MARGIN_M, ymax + CLIP_MARGIN_M)
    stats = {"pois_removed": 0, "waterways": 0, "outside_removed": 0, "open_removed": 0}

    for child in list(root):
        if child.tag == "poi":
            # Shop/amenity points clutter the view; scenario.add.xml adds the
            # labels that matter (ambulance base and hospital).
            root.remove(child)
            stats["pois_removed"] += 1
            continue
        if child.tag != "poly":
            continue
        points = _parse_shape(child.get("shape", ""))
        width = WATERWAY_WIDTH_M.get(child.get("type", ""))
        if width is None:
            if child.get("fill") == "0":
                # An area polyconvert could not close, e.g. a multipolygon with
                # member ways missing from the bbox download; it draws as a stray line.
                root.remove(child)
                stats["open_removed"] += 1
            elif not any(_inside(point, box) for point in points):
                root.remove(child)
                stats["outside_removed"] += 1
            continue

        position = list(root).index(child)
        root.remove(child)
        for number, segment in enumerate(clip_line(points, box)):
            line = ET.Element("poly", dict(child.attrib))
            if number:
                line.set("id", f"{child.get('id')}#{number}")
            if width >= OUTLINE_MIN_WIDTH_M:
                line.set("shape", _format_shape(buffer_line(segment, width)))
                line.set("fill", "1")
            else:
                line.set("shape", _format_shape(segment))
                line.set("fill", "0")
                line.set("lineWidth", f"{width:.1f}")
            root.insert(position + number, line)
            stats["waterways"] += 1

    ET.indent(root, space="    ")
    tree.write(poly_path, encoding="UTF-8", xml_declaration=True)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--polys", type=Path, default=GENERATED / "landscape.poly.xml")
    parser.add_argument("--net", type=Path, default=GENERATED / "thousand_lights.net.xml")
    parser.add_argument("--osm", type=Path, default=GENERATED / "thousand_lights.osm.xml")
    parser.add_argument("--bbox", default=BOUNDING_BOX)
    args = parser.parse_args()
    print(f"Landscape tidied: {tidy(args.polys, args.net, args.osm, args.bbox)}")


if __name__ == "__main__":
    main()
