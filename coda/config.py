"""Project-local configuration helpers."""

import os
import re
from pathlib import Path


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# 处理 strip quotes 相关逻辑，支撑当前模块的主要流程。
def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


# 解析 parse env line 相关输入，把原始内容转成结构化数据。
def _parse_env_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].strip()
    if "=" not in line:
        raise ValueError(f"invalid .env line: {line}")
    name, value = line.split("=", 1)
    name = name.strip()
    if not ENV_KEY_PATTERN.match(name):
        raise ValueError(f"invalid .env variable name: {name}")
    return name, _strip_quotes(value)


# 处理 find project env 相关逻辑，支撑当前模块的主要流程。
def find_project_env(start):
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    for path in (current, *current.parents):
        env_path = path / ".env"
        if env_path.exists():
            return env_path
    return None


# 读写 load project env 相关的持久化数据，保证状态可以复用。
def load_project_env(start, override=True):
    env_path = find_project_env(start)
    if env_path is None:
        return {}
    loaded = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        name, value = parsed
        loaded[name] = value
        if override or name not in os.environ:
            os.environ[name] = value
    return loaded


# 处理 provider env 相关逻辑，支撑当前模块的主要流程。
def provider_env(name, legacy_names=(), default=""):
    for env_name in (name, *legacy_names):
        value = os.environ.get(env_name)
        if value:
            return value
    return default
