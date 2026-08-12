"""
Per-bot configuration loader.

Each bot ships a `config/bot.yaml` describing:
    bot_id:        unique slug, e.g. "linkedin_it"
    label:         human-readable label
    gate:          {enabled: bool, model_preference: [..]}
    save:          {on_external: bool, on_easy_apply: bool}
    glassdoor:     {enabled: bool}
    ai:            {primary: "groq", fallback: "ollama", ...}
    mongodb:       {database: "linkedin_it_db", ...}
    paths:         {data_dir, logs_dir, training_dir, snapshots_dir, state_dir}

We use only the standard library (no PyYAML required) to keep the vendored core
dependency-light. The format we accept is intentionally minimal YAML-ish:
- key: value
- nested via two-space indentation
- lists via "- value" or inline [a, b, c]
- comments with #
If a bot needs richer YAML, drop in PyYAML and replace `_parse_yaml`.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Any


def _coerce(value: str) -> Any:
    v = value.strip()
    if v == "":
        return ""
    if v.lower() in ("true", "yes", "on"):
        return True
    if v.lower() in ("false", "no", "off"):
        return False
    if v.lower() in ("null", "none", "~"):
        return None
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_coerce(x) for x in _split_top_level(inner, ",")]
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split on `sep` ignoring separators inside [], {} or quotes."""
    out, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _parse_yaml(text: str) -> dict:
    """Tiny YAML subset: maps + scalars + simple lists. Two-space indent."""
    root: dict = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending_list_key: tuple[int, str, dict] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        # pop stack until parent indent is strictly less than current
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if content.startswith("- "):
            item_value = content[2:].strip()
            if not isinstance(parent, list):
                # parent should already be a list created by previous "key:" with nothing on the same line
                raise ValueError(f"Unexpected list item under non-list at line: {raw_line!r}")
            parent.append(_coerce(item_value))
            continue
        if ":" not in content:
            raise ValueError(f"Cannot parse line: {raw_line!r}")
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            # could be a map or list — peek at the next non-empty line
            new_container: Any = {}
            parent[key] = new_container
            stack.append((indent, new_container))
            # convert to list lazily on first "- " we see
            # We do this by replacing parent[key] when needed.
            # Trick: store a sentinel that swaps to list when first "- " arrives.
            # Simpler approach: pre-scan once.
        else:
            parent[key] = _coerce(value)
    return root


def _yaml_with_lists(text: str) -> dict:
    """Two-pass: first detect which empty-value keys become lists by scanning for child '- '."""
    lines = text.splitlines()
    list_keys: set[tuple[int, str]] = set()
    for i, raw in enumerate(lines):
        stripped = raw.split("#", 1)[0].rstrip()
        if not stripped.strip() or ":" not in stripped:
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        key, _, value = stripped.strip().partition(":")
        if value.strip():
            continue
        # look ahead for the first non-empty line at greater indent
        for j in range(i + 1, len(lines)):
            nxt = lines[j].split("#", 1)[0].rstrip()
            if not nxt.strip():
                continue
            nxt_indent = len(nxt) - len(nxt.lstrip(" "))
            if nxt_indent <= indent:
                break
            if nxt.strip().startswith("- "):
                list_keys.add((indent, key.strip()))
            break

    root: dict = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    for raw in lines:
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if content.startswith("- "):
            item_value = content[2:].strip()
            if not isinstance(parent, list):
                raise ValueError(f"List item under non-list: {raw!r}")
            parent.append(_coerce(item_value))
            continue
        if ":" not in content:
            raise ValueError(f"Cannot parse line: {raw!r}")
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if value == "":
            container: Any = [] if (indent, key) in list_keys else {}
            if isinstance(parent, list):
                raise ValueError(f"Map key under list: {raw!r}")
            parent[key] = container
            stack.append((indent, container))
        else:
            if isinstance(parent, list):
                raise ValueError(f"Map key under list: {raw!r}")
            parent[key] = _coerce(value)
    return root


@dataclass
class BotConfig:
    bot_id: str
    label: str
    gate_enabled: bool
    save_on_external: bool
    save_on_easy_apply: bool
    glassdoor_enabled: bool
    ai_primary: str
    ai_fallback: str
    mongodb_uri: str
    mongodb_database: str
    paths: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def data_dir(self) -> pathlib.Path:
        override = os.environ.get("JOBBOTS_DATA_DIR", "").strip()
        if override:
            return pathlib.Path(override).resolve()
        return pathlib.Path(self.paths.get("data_dir", "data")).resolve()

    @property
    def logs_dir(self) -> pathlib.Path:
        override = os.environ.get("JOBBOTS_DATA_DIR", "").strip()
        if override:
            return (pathlib.Path(override) / "logs").resolve()
        return pathlib.Path(self.paths.get("logs_dir", "data/logs")).resolve()

    @property
    def training_dir(self) -> pathlib.Path:
        override = os.environ.get("JOBBOTS_DATA_DIR", "").strip()
        if override:
            return (pathlib.Path(override) / "training").resolve()
        return pathlib.Path(self.paths.get("training_dir", "data/training")).resolve()

    @property
    def snapshots_dir(self) -> pathlib.Path:
        override = os.environ.get("JOBBOTS_DATA_DIR", "").strip()
        if override:
            return (pathlib.Path(override) / "snapshots").resolve()
        return pathlib.Path(self.paths.get("snapshots_dir", "data/snapshots")).resolve()

    @property
    def state_dir(self) -> pathlib.Path:
        override = os.environ.get("JOBBOTS_DATA_DIR", "").strip()
        if override:
            return (pathlib.Path(override) / "state").resolve()
        return pathlib.Path(self.paths.get("state_dir", "data/state")).resolve()

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.logs_dir, self.training_dir,
                  self.snapshots_dir, self.state_dir):
            p.mkdir(parents=True, exist_ok=True)


def load_bot_config(path: str | os.PathLike) -> BotConfig:
    """Load and validate `config/bot.yaml` for a bot. Resolves env-var overrides:
        MONGODB_URI, MONGODB_DB_NAME (if set, override the YAML).
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    data = _yaml_with_lists(text)

    gate = data.get("gate", {}) or {}
    save = data.get("save", {}) or {}
    glass = data.get("glassdoor", {}) or {}
    ai = data.get("ai", {}) or {}
    mongo = data.get("mongodb", {}) or {}
    paths = data.get("paths", {}) or {}

    return BotConfig(
        bot_id=str(data["bot_id"]),
        label=str(data.get("label", data["bot_id"])),
        gate_enabled=bool(gate.get("enabled", False)),
        save_on_external=bool(save.get("on_external", False)),
        save_on_easy_apply=bool(save.get("on_easy_apply", False)),
        glassdoor_enabled=bool(glass.get("enabled", False)),
        ai_primary=str(ai.get("primary", "groq")),
        ai_fallback=str(ai.get("fallback", "ollama")),
        mongodb_uri=os.getenv("MONGODB_URI", str(mongo.get("uri", "mongodb://localhost:27017"))),
        mongodb_database=os.getenv("JOBBOTS_MONGO_DATABASE", os.getenv("MONGODB_DB_NAME", "jobbots")),
        paths={k: str(v) for k, v in paths.items()},
        raw=data,
    )
