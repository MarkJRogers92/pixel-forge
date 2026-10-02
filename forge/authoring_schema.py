"""Versioned, strict data contract for direct drawing. No executable inputs."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
RENDERER_VERSION = "direct-rgba-1"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_SIDE = 512
MAX_COLORS = 64
MAX_OPERATIONS = 2000
MAX_VERTICES = 128
DEADLINE_SECONDS = 10
LIMITS = {"canvas_side": [1, MAX_SIDE], "canvas_pixels": MAX_SIDE ** 2,
          "palette_entries": MAX_COLORS, "operations": MAX_OPERATIONS,
          "polygon_vertices": MAX_VERTICES, "request_bytes": MAX_REQUEST_BYTES,
          "title_characters": 120, "description_characters": 2000,
          "render_deadline_seconds": DEADLINE_SECONDS, "concurrent_transactions": 1,
          "source_png_bytes": 10 * 1024 * 1024}


class AuthoringError(ValueError):
    def __init__(self, code: str, field: str = "request", *, committed: bool = False):
        self.code, self.field, self.committed = code, field, committed
        super().__init__(code)

    def result(self) -> dict:
        return {"error": {"code": self.code, "field": self.field,
                          "committed_result_exists": self.committed,
                          "retry": "reuse the same request_id" if self.code in (
                              "BUSY", "RENDER_TIMEOUT", "STORAGE_FAILURE") else None}}


def strict_json(data: str | bytes):
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out:
                raise AuthoringError("INVALID_REQUEST", "duplicate_json_key")
            out[key] = value
        return out

    def invalid_constant(_):
        raise AuthoringError("INVALID_REQUEST", "non_finite_number")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AuthoringError):
            raise
        raise AuthoringError("INVALID_REQUEST", "json") from None


def canonical(data: dict) -> bytes:
    try:
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise AuthoringError("INVALID_REQUEST", "json") from None
    if len(encoded) > MAX_REQUEST_BYTES:
        raise AuthoringError("LIMIT_EXCEEDED", "request_bytes")
    return encoded


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


Coordinate = Annotated[int, Field(strict=True, ge=0, le=MAX_SIDE - 1)]
Length = Annotated[int, Field(strict=True, ge=1, le=MAX_SIDE)]
Color = Annotated[int, Field(strict=True, ge=0, le=MAX_COLORS - 1)]
Byte = Annotated[int, Field(strict=True, ge=0, le=255)]
RGBA = Annotated[list[Byte], Field(min_length=4, max_length=4)]
Point = Annotated[list[Coordinate], Field(min_length=2, max_length=2)]


class Pixel(StrictModel):
    op: Literal["pixel"]
    x: Coordinate
    y: Coordinate
    color: Color


class Line(StrictModel):
    op: Literal["line"]
    x1: Coordinate
    y1: Coordinate
    x2: Coordinate
    y2: Coordinate
    color: Color


class Rectangle(StrictModel):
    op: Literal["rectangle"]
    x: Coordinate
    y: Coordinate
    width: Length
    height: Length
    color: Color


class Ellipse(Rectangle):
    op: Literal["ellipse"]


class Polygon(StrictModel):
    op: Literal["polygon"]
    points: Annotated[list[Point], Field(min_length=3, max_length=MAX_VERTICES)]
    color: Color


class EraseRectangle(StrictModel):
    op: Literal["erase_rectangle"]
    x: Coordinate
    y: Coordinate
    width: Length
    height: Length


class ReplaceColor(StrictModel):
    op: Literal["replace_color"]
    from_color: Color
    to_color: Color


OPERATIONS = (Pixel, Line, Rectangle, Ellipse, Polygon, EraseRectangle, ReplaceColor)
Operation = Annotated[Union[Pixel, Line, Rectangle, Ellipse, Polygon, EraseRectangle, ReplaceColor],
                      Field(discriminator="op")]


class Canvas(StrictModel):
    width: Length
    height: Length


class AssetRef(StrictModel):
    source: Literal["library", "project"]
    id: Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")]
    asset: Annotated[str, Field(min_length=1, max_length=512)]


class CommonRequest(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    request_id: Annotated[str, Field(min_length=1, max_length=128,
                                   pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")]
    title: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] = ""
    operations: Annotated[list[Operation], Field(max_length=MAX_OPERATIONS)]


class RenderRequest(CommonRequest):
    profile_id: Annotated[str, Field(min_length=1, max_length=160)]
    canvas: Canvas
    palette: Annotated[list[RGBA], Field(min_length=1, max_length=MAX_COLORS)]


class EditRequest(CommonRequest):
    base_asset: AssetRef
    expected_source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CapabilitiesRequest(StrictModel):
    base_asset: AssetRef | None = None


def validate_model(model, data):
    canonical(data)
    try:
        result = model.model_validate(data)
    except ValidationError as exc:
        error = exc.errors(include_input=False, include_context=False)[0]
        location = ".".join(str(p) for p in error["loc"])
        code = "INVALID_REQUEST"
        if error["type"] == "union_tag_invalid":
            code = "UNSUPPORTED_OPERATION"
        elif error["type"] in ("too_long", "string_too_long", "less_than_equal"):
            code = "LIMIT_EXCEEDED"
        raise AuthoringError(code, location) from None
    return result


def validate_request(kind: str, data: dict):
    result = validate_model(RenderRequest if kind == "render" else EditRequest, data)
    if not result.title.strip():
        raise AuthoringError("INVALID_REQUEST", "title")
    return result


def validate_geometry(request, width: int, height: int, palette: list):
    for i, op in enumerate(request.operations):
        points = []
        if isinstance(op, (Rectangle, Ellipse, EraseRectangle)):
            if op.x + op.width > width or op.y + op.height > height:
                raise AuthoringError("INVALID_REQUEST", f"operations.{i}.bounds")
        elif isinstance(op, Pixel):
            points = [(op.x, op.y)]
        elif isinstance(op, Line):
            points = [(op.x1, op.y1), (op.x2, op.y2)]
        elif isinstance(op, Polygon):
            points = op.points
            unique = list(dict.fromkeys(tuple(point) for point in points))
            if len(unique) < 3:
                raise AuthoringError("INVALID_REQUEST", f"operations.{i}.polygon")
            ax, ay = unique[0]
            bx, by = unique[1]
            if all((bx-ax)*(y-ay) == (by-ay)*(x-ax) for x, y in unique[2:]):
                raise AuthoringError("INVALID_REQUEST", f"operations.{i}.polygon")
        if any(x >= width or y >= height for x, y in points):
            raise AuthoringError("INVALID_REQUEST", f"operations.{i}.bounds")
        indices = [getattr(op, key) for key in ("color", "from_color", "to_color") if hasattr(op, key)]
        if any(index >= len(palette) for index in indices):
            raise AuthoringError("INVALID_REQUEST", f"operations.{i}.color")


def request_hash(kind: str, request) -> str:
    return hashlib.sha256(canonical({"tool": kind, "request": request.model_dump()})).hexdigest()
