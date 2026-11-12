"""Typed, bounded comparison of complete query results, independent of preview limits."""

import base64
import datetime
from decimal import Decimal, localcontext
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from grading_values import JsonValue, ArrayValue


MAX_GRADE_ROWS = 100000
MAX_GRADE_BYTES = 32 * 1024 * 1024
MAX_TOLERANT_ROWS = 1000


class GradingLimitError(ValueError):
    pass


class GradingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_order: Literal["auto", "required", "ignore"] = "auto"
    mode: Literal["automatic", "manual"] = "automatic"
    column_order: bool = True
    column_names: Literal["exact", "case_insensitive", "ignore"] = "exact"
    numeric_absolute_tolerance: Decimal = Field(default=Decimal(0), ge=0)
    numeric_relative_tolerance: Decimal = Field(default=Decimal(0), ge=0)

    @field_validator("numeric_absolute_tolerance", "numeric_relative_tolerance")
    @classmethod
    def finite_tolerance(cls, value):
        if not value.is_finite():
            raise ValueError("Toleransi harus berupa angka finite.")
        return value


def policy_from(value=None):
    if isinstance(value, GradingPolicy):
        return value
    return GradingPolicy.model_validate(json.loads(value) if isinstance(value, str) else value or {})


def number(value):
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def canonical(value):
    if isinstance(value,JsonValue):
        return ['json',canonical(value.value)]
    if isinstance(value,ArrayValue):
        return ['array_bounds',value.bounds,canonical(value.value)]
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if number(value):
        numeric = Decimal(str(value))
        if numeric.is_finite():
            with localcontext() as context:
                context.prec = max(28, len(numeric.as_tuple().digits))
                return ["number", str(numeric.normalize()) if numeric else "0"]
        return ["number", str(numeric)]
    if isinstance(value, str):
        return ["text", value]
    if isinstance(value, datetime.datetime):
        if value.tzinfo:
            value = value.astimezone(datetime.timezone.utc)
        return ["datetime", value.isoformat()]
    if isinstance(value, (datetime.date, datetime.time)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ["binary", base64.b64encode(bytes(value)).decode("ascii")]
    if isinstance(value, (list, tuple)):
        return ["array", [canonical(item) for item in value]]
    if isinstance(value, dict):
        return ["object", [[key, canonical(value[key])] for key in sorted(value)]]
    return [type(value).__name__, str(value)]


def row_key(row):
    return json.dumps([canonical(value) for value in row], ensure_ascii=True, separators=(",", ":")).encode()


def rows_equal(first, second, policy):
    for a, b in zip(first, second):
        if number(a) and number(b):
            a, b = Decimal(str(a)), Decimal(str(b))
            if a.is_finite() and b.is_finite():
                with localcontext() as context:
                    context.prec = max(50, len(a.as_tuple().digits), len(b.as_tuple().digits)) + 10
                    if abs(a - b) <= max(policy.numeric_absolute_tolerance,
                                         policy.numeric_relative_tolerance * max(abs(a), abs(b))):
                        continue
            elif str(a) == str(b):
                continue
            return False
        if canonical(a) != canonical(b):
            return False
    return len(first) == len(second)


def type_family(oid):
    if oid in {20, 21, 23, 26, 700, 701, 1700}:
        return "numeric"
    if oid in {18, 19, 25, 1042, 1043}:
        return "text"
    if oid in {114, 3802}:
        return "json"
    return oid


def column_mapping(reference_columns, student_columns, reference_types, student_types, policy):
    if len(reference_columns) != len(student_columns) or not reference_columns:
        return None
    normalize = str.lower if policy.column_names == "case_insensitive" else lambda value: value
    ref_names = [normalize(value) for value in reference_columns]
    stud_names = [normalize(value) for value in student_columns]
    mapping = list(range(len(stud_names)))
    if not policy.column_order:
        if policy.column_names == "ignore" or len(set(ref_names)) != len(ref_names) or len(set(stud_names)) != len(stud_names):
            raise ValueError("Pengabaian urutan kolom memerlukan alias unik dan pemeriksaan nama kolom.")
        if set(ref_names) != set(stud_names):
            return None
        mapping = [stud_names.index(name) for name in ref_names]
    elif policy.column_names != "ignore" and ref_names != stud_names:
        return None
    if [type_family(value) for value in reference_types] != [type_family(student_types[i]) for i in mapping]:
        return None
    return mapping


def tolerant_bag_equal(reference, student, policy, check, order_keys=None):
    edges = []
    for row in reference:
        check()
        edges.append([index for index, candidate in enumerate(student) if rows_equal(row, candidate, policy)
            and (not order_keys or row_key(tuple(row[i] for i in order_keys))==row_key(tuple(candidate[i] for i in order_keys)))])
    matched = {}
    for root in range(len(reference)):
        pending = [root]
        parents = {root: None}
        visited_right = set()
        found = None
        while pending and found is None:
            check()
            left = pending.pop()
            for right in edges[left]:
                if right in visited_right:
                    continue
                visited_right.add(right)
                if right not in matched:
                    found = (left, right)
                    break
                other = matched[right]
                if other not in parents:
                    parents[other] = (left, right)
                    pending.append(other)
        if found is None:
            return False
        left, right = found
        while True:
            matched[right] = left
            if parents[left] is None:
                break
            left, right = parents[left]
    return True


def compare_complete(reference, student, policy, ordered, check=lambda: None, preview=None, order_keys=None,
                     limited=False):
    """Inputs are iterables of native PostgreSQL rows; exact bags spill to a private file."""
    tolerant = bool(policy.numeric_absolute_tolerance or policy.numeric_relative_tolerance)
    reference_saved, student_saved = [], []
    counts = [0, 0]
    sizes = [0, 0]
    ordered_equal = True
    order_keys_equal = bool(order_keys)
    sentinel = object()
    iterators = [iter(reference), iter(student)]
    with tempfile.TemporaryDirectory(prefix="sqlexam_compare_") as directory:
        conn = sqlite3.connect(Path(directory) / "rows.db")
        try:
            conn.execute("CREATE TABLE differences (row BLOB PRIMARY KEY, count INTEGER NOT NULL)")
            while True:
                check()
                pair = [next(iterator, sentinel) for iterator in iterators]
                if pair[0] is sentinel and pair[1] is sentinel:
                    break
                if pair[0] is sentinel or pair[1] is sentinel:
                    ordered_equal = False
                    order_keys_equal = False
                elif ordered and not rows_equal(pair[0], pair[1], policy):
                    ordered_equal = False
                if order_keys_equal and row_key(tuple(pair[0][i] for i in order_keys)) != row_key(tuple(pair[1][i] for i in order_keys)):
                    order_keys_equal = False
                for side, row in enumerate(pair):
                    if row is sentinel:
                        continue
                    counts[side] += 1
                    key = row_key(row)
                    sizes[side] += len(key)
                    if counts[side] > MAX_GRADE_ROWS or sizes[side] > MAX_GRADE_BYTES:
                        raise GradingLimitError("Hasil lengkap melampaui batas evaluator; perlu penilaian pengawas.")
                    if side == 1 and preview:
                        preview(row)
                    if tolerant:
                        if counts[side] > MAX_TOLERANT_ROWS:
                            if not ordered or not ordered_equal:
                                raise GradingLimitError("Pencocokan toleransi dengan permutasi dibatasi 1.000 baris.")
                        else:
                            (reference_saved if side == 0 else student_saved).append(row)
                    else:
                        conn.execute("INSERT INTO differences VALUES (?,?) ON CONFLICT(row) DO UPDATE SET count=count+excluded.count",
                                     (key, 1 if side == 0 else -1))
            if counts[0] != counts[1]:
                return False, "row_count_mismatch", counts[1]
            if ordered and ordered_equal:
                return True, "correct", counts[1]
            if tolerant:
                if max(counts)>MAX_TOLERANT_ROWS:
                    raise GradingLimitError("Pencocokan toleransi dengan permutasi dibatasi 1.000 baris.")
                equal = tolerant_bag_equal(reference_saved, student_saved, policy, check,order_keys if ordered and order_keys_equal else None)
            else:
                equal = conn.execute("SELECT 1 FROM differences WHERE count<>0 LIMIT 1").fetchone() is None
            if ordered and equal:
                return (True, "correct", counts[1]) if order_keys_equal else (False, "wrong_order", counts[1])
            # A limited slice can select different rows tied on the same sort keys.
            if not equal and limited and (not order_keys or order_keys_equal or tolerant):
                return False, "manual_review", counts[1]
            return equal, "correct" if equal else "wrong_data", counts[1]
        finally:
            conn.close()
