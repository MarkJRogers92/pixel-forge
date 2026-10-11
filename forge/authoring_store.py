"""Immutable canonical library transactions for the bounded direct renderer."""
from __future__ import annotations

import base64
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import library
from .authoring_render import decode_png, source_palette
from .authoring_schema import (AuthoringError, CapabilitiesRequest, LIMITS, MAX_COLORS, MAX_REQUEST_BYTES,
                               OPERATIONS, RENDERER_VERSION, SCHEMA_VERSION, canonical, request_hash,
                               strict_json, validate_geometry, validate_model, validate_request)
from .readonly_mcp import ID_PATTERN, ROOT, ReadOnlyForge, _size
from .sprite import Sprite, parse_hex

APPROVED_PROFILES = ("dead-mall", "dead-mall-current-props-20260930")
CALLER_SCOPE = "pixel-forge-direct-v1"
ASSETS = ("sprite.png", "sprite@8x.png", "sprite.json", "meta.json", "authoring.json")
OWNED_STAGE_FILES = (*ASSETS, "source.png")
NOFOLLOW = os.O_NOFOLLOW


class Directory:
    def __init__(self, fd):
        self.fd = fd

    @classmethod
    def open(cls, path: Path):
        # Walk the complete absolute path with descriptors, refusing symlink races.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return cls(fd)
        except BaseException:
            os.close(fd)
            raise

    def child(self, name, create=False):
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=self.fd)
                os.fsync(self.fd)
            except FileExistsError:
                pass
        return Directory(os.open(name, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=self.fd))

    def close(self):
        os.close(self.fd)

    def exists(self, name):
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def read(self, name, maximum):
        fd = os.open(name, os.O_RDONLY | NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise AuthoringError("PATH_DENIED", "asset")
            if before.st_size > maximum:
                raise AuthoringError("LIMIT_EXCEEDED", "file_bytes")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(maximum + 1)
            after = os.fstat(fd)
            if len(data) > maximum:
                raise AuthoringError("LIMIT_EXCEEDED", "file_bytes")
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise AuthoringError("SOURCE_CHANGED", "asset")
            return data
        finally:
            os.close(fd)

    def write_new(self, name, data):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)

    def atomic_json(self, name, data):
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            self.write_new(temporary, canonical(data))
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            if self.exists(temporary):
                os.unlink(temporary, dir_fd=self.fd)


@contextmanager
def directory(path):
    value = Directory.open(path)
    try:
        yield value
    finally:
        value.close()


@contextmanager
def child(parent, name, create=False):
    value = parent.child(name, create)
    try:
        yield value
    finally:
        value.close()


def publish_exclusive(source: Directory, name: str, target: Directory, asset_id: str):
    """Same-filesystem atomic rename that cannot replace even an empty directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = libc.renameatx_np
        flag = 0x00000004  # RENAME_EXCL, macOS SDK sys/stdio.h
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        flag = 1  # RENAME_NOREPLACE
    else:
        raise AuthoringError("STORAGE_FAILURE", "exclusive_publication_unavailable")
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(source.fd, name.encode("ascii"), target.fd, asset_id.encode("ascii"), flag) != 0:
        raise OSError(ctypes.get_errno(), "exclusive publication failed")
    os.fsync(target.fd)
    os.fsync(source.fd)


class AuthoringStore:
    def __init__(self, reads: ReadOnlyForge, enabled: bool = False):
        self.reads, self.enabled = reads, enabled
        self.worker_timeout = LIMITS["render_deadline_seconds"]

    def profiles(self):
        out = []
        with directory(self.reads.repo) as repo, child(repo, "profiles") as profiles:
            for pid in APPROVED_PROFILES:
                if not profiles.exists(pid + ".json"):
                    continue
                data = strict_json(profiles.read(pid + ".json", MAX_REQUEST_BYTES))
                palette = data.get("palette", {})
                if not isinstance(palette, dict) or not 1 <= len(palette) <= MAX_COLORS:
                    continue
                try:
                    rgba = [list(parse_hex(value)) for value in palette.values()]
                except (ValueError, TypeError, AttributeError):
                    continue
                out.append({"id": pid, "name": str(data.get("name", pid))[:200],
                            "size": _size(data.get("size")), "palette_rgba": rgba,
                            "palette_mode": "prefer", "style": str(data.get("style", ""))[:2000]})
        return out

    def capabilities(self, base_asset=None):
        args = validate_model(CapabilitiesRequest, {"base_asset": base_asset})
        result = {"schema_version": SCHEMA_VERSION, "renderer_version": RENDERER_VERSION,
                  "authoring_enabled": self.enabled, "route": "direct", "external_model_calls": False,
                  "external_generation_cost": 0, "limits": LIMITS, "profiles": self.profiles(),
                  "operation_schemas": {model.model_json_schema()["properties"]["op"]["const"]:
                                        model.model_json_schema() for model in OPERATIONS},
                  "caller_scope": CALLER_SCOPE,
                  "semantics": {"coordinates": "integer pixel indices, top-left origin",
                                "rectangles": "exclusive far edge; fully inside canvas",
                                "polygon": "non-collinear vertices; legacy even-odd scan at y+0.5, round-to-even x; inclusive Bresenham edges",
                                "ellipse": "pixel-centre inclusion by integer ellipse equation within bounds",
                                "alpha": "straight source-over, integer nearest rounding; half rounds upward",
                                "replace_color": "exact RGBA replacement, not alpha composition",
                                "edit_palette": "sorted unique source RGBA tuples (including transparent pixels)",
                                "cleanup": "none", "layers": "raster only"}}
        if args.base_asset is not None:
            source = self._source(args.base_asset)
            result["source"] = {"base_asset": args.base_asset.model_dump(), "sha256": source["sha256"],
                                "size": list(source["image"].size), "palette_rgba": source["palette"],
                                "parent_asset_id": args.base_asset.id if args.base_asset.source == "library" else None}
            source["image"].close()
        return result

    def _source(self, ref):
        if ref.asset.startswith("/") or "\\" in ref.asset or any(p in ("", ".", "..") for p in ref.asset.split("/")):
            raise AuthoringError("PATH_DENIED", "base_asset.asset")
        if Path(ref.asset).suffix.lower() != ".png":
            raise AuthoringError("PATH_DENIED", "base_asset.asset")
        try:
            if ref.source == "library":
                if ref.asset != "sprite.png":
                    raise AuthoringError("PATH_DENIED", "base_asset.asset")
                root, parts = self.reads.repo, ["library", ref.id, ref.asset]
            else:
                root, parts = self.reads._project_root(ref.id), ref.asset.split("/")
            handles = []
            with directory(root) as current:
                try:
                    for part in parts[:-1]:
                        current = current.child(part)
                        handles.append(current)
                    data = current.read(parts[-1], LIMITS["source_png_bytes"])
                finally:
                    for handle in reversed(handles):
                        handle.close()
            image = self._decode(data)
            return {"bytes": data, "image": image, "sha256": hashlib.sha256(data).hexdigest(),
                    "palette": source_palette(image)}
        except FileNotFoundError:
            raise AuthoringError("ASSET_NOT_FOUND", "base_asset") from None
        except (ValueError, OSError) as exc:
            if isinstance(exc, AuthoringError):
                raise
            raise AuthoringError("PATH_DENIED", "base_asset") from None

    _decode = staticmethod(decode_png)

    @contextmanager
    def _state(self, create):
        with directory(self.reads.repo) as repo:
            if not create and not repo.exists("library"):
                yield None
                return
            with child(repo, "library", create) as lib:
                if not create and not lib.exists(".authoring"):
                    yield None
                    return
                with child(lib, ".authoring", create) as state:
                    if os.fstat(state.fd).st_uid != os.getuid() or os.fstat(state.fd).st_mode & 0o077:
                        raise AuthoringError("PATH_DENIED", "transaction_directory")
                    if not create and (not state.exists("requests") or not state.exists("staging")):
                        yield None
                        return
                    lock = os.open("lock", os.O_RDWR | os.O_CREAT | NOFOLLOW, 0o600, dir_fd=state.fd)
                    try:
                        if not stat.S_ISREG(os.fstat(lock).st_mode) or os.fstat(lock).st_nlink != 1:
                            raise AuthoringError("PATH_DENIED", "transaction_lock")
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            raise AuthoringError("BUSY", "transaction") from None
                        with child(state, "requests", create) as records, child(state, "staging", create) as stages:
                            yield lib, records, stages
                    finally:
                        os.close(lock)

    def _verify(self, lib, asset_id, record):
        if not ID_PATTERN.fullmatch(asset_id):
            raise AuthoringError("STORAGE_FAILURE", "transaction.asset_id")
        with child(lib, asset_id) as item:
            meta = strict_json(item.read("meta.json", MAX_REQUEST_BYTES))
            result = meta.get("authoring_result")
            if (meta.get("request_hash") != record["request_hash"] or meta.get("caller_scope") != CALLER_SCOPE
                    or meta.get("request_id") != record["request_id"] or result != record.get("result")):
                raise AuthoringError("STORAGE_FAILURE", "transaction.result")
            if not isinstance(result, dict) or result.get("asset_id") != record["asset_id"]:
                raise AuthoringError("STORAGE_FAILURE", "transaction.asset_id")
            png = item.read("sprite.png", LIMITS["source_png_bytes"])
            if hashlib.sha256(png).hexdigest() != result["output_sha256"]:
                raise AuthoringError("STORAGE_FAILURE", "transaction.png_hash")
            image = self._decode(png)
            if list(image.size) != result["dimensions"]:
                raise AuthoringError("STORAGE_FAILURE", "transaction.dimensions")
            document = strict_json(item.read("sprite.json", MAX_REQUEST_BYTES))
            if Sprite.from_dict(document).to_image().tobytes() != image.tobytes():
                raise AuthoringError("STORAGE_FAILURE", "transaction.sprite_json")
            image.close()
            raw_request = strict_json(item.read("authoring.json", MAX_REQUEST_BYTES))
            request = validate_request(raw_request["tool"], raw_request["request"])
            if request_hash(raw_request["tool"], request) != record["request_hash"]:
                raise AuthoringError("STORAGE_FAILURE", "transaction.request_hash")
            expected_assets = list(ASSETS) + (["source.png"] if raw_request["tool"] == "edit" else [])
            if result.get("assets") != expected_assets:
                raise AuthoringError("STORAGE_FAILURE", "transaction.assets")
            preview = item.read("sprite@8x.png", 16 * 1024 * 1024)
            # Verify the preview through the native PNG decoder, without its source-size limit.
            import io
            from PIL import Image
            with Image.open(io.BytesIO(preview)) as enlarged:
                if enlarged.format != "PNG" or enlarged.size != tuple(v * 8 for v in result["dimensions"]):
                    raise AuthoringError("STORAGE_FAILURE", "transaction.preview")
                enlarged.verify()
            if raw_request["tool"] == "edit":
                snapshot = item.read("source.png", LIMITS["source_png_bytes"])
                if hashlib.sha256(snapshot).hexdigest() != result["source_sha256"]:
                    raise AuthoringError("STORAGE_FAILURE", "transaction.source_hash")
            return result

    def _lookup(self, state, key, digest, request):
        if state is None:
            return None, None
        lib, records, _ = state
        name = key + ".json"
        if not records.exists(name):
            return None, None
        record = strict_json(records.read(name, MAX_REQUEST_BYTES))
        if record.get("request_hash") != digest:
            raise AuthoringError("REQUEST_CONFLICT", "request_id")
        if (record.get("request_id") != request.request_id or record.get("caller_scope") != CALLER_SCOPE
                or record.get("state") not in ("pending", "committed")
                or not isinstance(record.get("asset_id"), str) or not ID_PATTERN.fullmatch(record["asset_id"])):
            raise AuthoringError("STORAGE_FAILURE", "transaction")
        if lib.exists(record["asset_id"]):
            result = self._verify(lib, record["asset_id"], record)
            if record["state"] != "committed":
                record["state"] = "committed"
                records.atomic_json(name, record)
            return record, result
        if record["state"] == "committed":
            raise AuthoringError("STORAGE_FAILURE", "transaction.missing_output", committed=True)
        return record, None

    def _worker(self, kind, request, source, profile):
        packet = {"kind": kind, "request": request.model_dump(), "profile": profile,
                  "source_png": base64.b64encode(source["bytes"]).decode() if source else None}
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Only this fixed local module can be launched. No user-selected command/path.
        process = subprocess.Popen([sys.executable, "-m", "forge.authoring_worker"], cwd=ROOT, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, _ = process.communicate(json.dumps(packet, allow_nan=False).encode(), timeout=self.worker_timeout)
        except BaseException as exc:
            process.kill()
            process.communicate()  # Always reap; no abandoned worker.
            if isinstance(exc, subprocess.TimeoutExpired):
                raise AuthoringError("RENDER_TIMEOUT", "render") from None
            raise
        if process.returncode == -signal.SIGALRM:
            raise AuthoringError("RENDER_TIMEOUT", "render")
        if process.returncode or len(stdout) > 32 * 1024 * 1024:
            raise AuthoringError("STORAGE_FAILURE", "renderer")
        output = strict_json(stdout)
        if "error" in output:
            raise AuthoringError(output["error"]["code"], output["error"]["field"])
        output["png"] = base64.b64decode(output["png"], validate=True)
        output["preview"] = base64.b64decode(output["preview"], validate=True)
        return output

    def render(self, data):
        return self._execute("render", data)

    def edit(self, data):
        return self._execute("edit", data)

    def _execute(self, kind, data):
        if not self.enabled:
            raise AuthoringError("AUTHORING_DISABLED")
        request = validate_request(kind, data)
        digest = request_hash(kind, request)
        key = hashlib.sha256((CALLER_SCOPE + ":" + request.request_id).encode()).hexdigest()
        source = None
        record = None
        try:
            with self._state(create=False) as state:
                _, result = self._lookup(state, key, digest, request)
                if result is not None:
                    return result
            profiles = {p["id"]: p for p in self.profiles()}
            if kind == "render":
                if request.profile_id not in profiles:
                    raise AuthoringError("INVALID_REQUEST", "profile_id")
                profile = profiles[request.profile_id]
                width, height, palette = request.canvas.width, request.canvas.height, request.palette
            else:
                source = self._source(request.base_asset)
                if source["sha256"] != request.expected_source_sha256:
                    raise AuthoringError("SOURCE_CHANGED", "expected_source_sha256")
                width, height = source["image"].size
                palette = source["palette"]
                profile = profiles.get("dead-mall")
            validate_geometry(request, width, height, palette)
            with self._state(create=True) as state:
                record, result = self._lookup(state, key, digest, request)
                if result is not None:
                    return result
                lib, records, stages = state
                if record is None:
                    asset_id = library.allocate_id(request.title)
                    if not ID_PATTERN.fullmatch(asset_id) or lib.exists(asset_id):
                        raise AuthoringError("STORAGE_FAILURE", "asset_id_collision")
                    record = {"caller_scope": CALLER_SCOPE, "request_id": request.request_id,
                              "request_hash": digest, "asset_id": asset_id, "state": "pending"}
                    records.atomic_json(key + ".json", record)
                asset_id = record["asset_id"]
                # Staging is hidden from the canonical readers; clean only known files
                # owned by this exact pending request, never user library items.
                with child(stages, key, create=True) as stage:
                    names = os.listdir(stage.fd)
                    if any(name not in OWNED_STAGE_FILES for name in names):
                        raise AuthoringError("STORAGE_FAILURE", "staging_contents")
                    for name in names:
                        os.unlink(name, dir_fd=stage.fd)
                    output = self._worker(kind, request, source, profile)
                    result = {"request_id": request.request_id, "asset_id": asset_id,
                              "parent_asset_id": request.base_asset.id if kind == "edit" and
                                  request.base_asset.source == "library" else None,
                              "source_asset": request.base_asset.model_dump() if kind == "edit" else None,
                              "assets": list(ASSETS) + (["source.png"] if source else []), "dimensions": [width, height],
                              "palette_count": output["palette_count"],
                              "source_sha256": source["sha256"] if source else None,
                              "output_sha256": hashlib.sha256(output["png"]).hexdigest(),
                              "schema_version": SCHEMA_VERSION, "renderer_version": RENDERER_VERSION,
                              "pillow_version": output["pillow_version"], "qa": output["qa"],
                              "external_model_calls": 0, "external_generation_cost": 0}
                    meta = {"prompt": request.title, "size": [width, height], "kind": "direct-" + kind,
                            "profile_id": profile["id"] if profile else None, "request_id": request.request_id,
                            "request_hash": digest, "caller_scope": CALLER_SCOPE, "authoring_result": result}
                    stage.write_new("sprite.png", output["png"])
                    stage.write_new("sprite@8x.png", output["preview"])
                    stage.write_new("sprite.json", canonical(output["sprite"]))
                    stage.write_new("meta.json", canonical(meta))
                    stage.write_new("authoring.json", canonical({"tool": kind, "request": request.model_dump()}))
                    if source:
                        stage.write_new("source.png", source["bytes"])
                    os.fsync(stage.fd)
                    record["result"] = result
                    # Durably bind the verified result before atomic publication.
                    records.atomic_json(key + ".json", record)
                    self._verify(stages, key, record)
                    self._publish(stages, key, lib, asset_id)
                record["state"] = "committed"
                records.atomic_json(key + ".json", record)
                return result
        except AuthoringError as exc:
            if exc.code == "STORAGE_FAILURE" and record:
                exc.committed = self._published_result_exists(record)
            raise
        except OSError as exc:
            code = "PATH_DENIED" if exc.errno in (errno.ELOOP, errno.ENOTDIR) else "STORAGE_FAILURE"
            raise AuthoringError(code, "storage", committed=self._published_result_exists(record)) from None
        except (ValueError, KeyError, TypeError):
            raise AuthoringError("STORAGE_FAILURE", "transaction", committed=self._published_result_exists(record)) from None
        finally:
            if source:
                source["image"].close()

    def _publish(self, stages, key, lib, asset_id):
        publish_exclusive(stages, key, lib, asset_id)

    def _published_result_exists(self, record):
        if not record or not record.get("result"):
            return False
        try:
            with directory(self.reads.repo) as repo, child(repo, "library") as lib:
                if lib.exists(record["asset_id"]):
                    self._verify(lib, record["asset_id"], record)
                    return True
        except (ValueError, OSError, KeyError, TypeError):
            pass
        return False
