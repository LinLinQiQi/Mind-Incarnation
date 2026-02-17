from __future__ import annotations

from typing import Any


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def validate_json_schema(obj: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    """Best-effort validator for the subset of JSON Schema used in MI schemas.

    Supported keywords: type, properties, required, additionalProperties, items,
    enum, minimum, maximum, anyOf.
    """

    errors: list[str] = []

    if not isinstance(schema, dict):
        return [f"{path}: schema is not an object"]

    if "anyOf" in schema:
        subs = schema.get("anyOf")
        if not isinstance(subs, list) or not subs:
            return [f"{path}: anyOf must be a non-empty array"]
        sub_errs: list[list[str]] = []
        for i, sub in enumerate(subs):
            if not isinstance(sub, dict):
                sub_errs.append([f"{path}: anyOf[{i}] is not an object schema"])
                continue
            e = validate_json_schema(obj, sub, path=path)
            if not e:
                return []
            sub_errs.append(e)
        # None matched; return the shortest error list to help repair.
        sub_errs.sort(key=len)
        return sub_errs[0] if sub_errs else [f"{path}: anyOf did not match"]

    if "enum" in schema:
        enum = schema.get("enum")
        if isinstance(enum, list) and obj not in enum:
            errors.append(f"{path}: expected one of {enum}, got {_type_name(obj)}={obj!r}")
            return errors

    expected_type = schema.get("type")
    if isinstance(expected_type, str):
        t = expected_type
        if t == "object":
            if not isinstance(obj, dict):
                return [f"{path}: expected object, got {_type_name(obj)}"]
            props = schema.get("properties")
            required = schema.get("required")
            additional = schema.get("additionalProperties", True)

            if isinstance(required, list):
                for k in required:
                    if isinstance(k, str) and k not in obj:
                        errors.append(f"{path}: missing required key {k!r}")

            if additional is False and isinstance(props, dict):
                allowed = set(str(k) for k in props.keys())
                for k in obj.keys():
                    if str(k) not in allowed:
                        errors.append(f"{path}: unexpected key {k!r}")

            if isinstance(props, dict):
                for k, sub_schema in props.items():
                    if k not in obj:
                        continue
                    if not isinstance(sub_schema, dict):
                        errors.append(f"{path}.{k}: invalid subschema")
                        continue
                    errors.extend(validate_json_schema(obj[k], sub_schema, path=f"{path}.{k}"))
            return errors

        if t == "array":
            if not isinstance(obj, list):
                return [f"{path}: expected array, got {_type_name(obj)}"]
            items = schema.get("items")
            if isinstance(items, dict):
                for i, item in enumerate(obj):
                    errors.extend(validate_json_schema(item, items, path=f"{path}[{i}]"))
            return errors

        if t == "string":
            if not isinstance(obj, str):
                return [f"{path}: expected string, got {_type_name(obj)}"]
            return []

        if t == "number":
            if not isinstance(obj, (int, float)) or isinstance(obj, bool):
                return [f"{path}: expected number, got {_type_name(obj)}"]
            mn = schema.get("minimum")
            mx = schema.get("maximum")
            if isinstance(mn, (int, float)) and obj < mn:
                errors.append(f"{path}: expected >= {mn}, got {obj}")
            if isinstance(mx, (int, float)) and obj > mx:
                errors.append(f"{path}: expected <= {mx}, got {obj}")
            return errors

        if t == "boolean":
            if not isinstance(obj, bool):
                return [f"{path}: expected boolean, got {_type_name(obj)}"]
            return []

        if t == "null":
            if obj is not None:
                return [f"{path}: expected null, got {_type_name(obj)}"]
            return []

    # If schema doesn't specify type, accept (we only use typed schemas in MI).
    return errors

