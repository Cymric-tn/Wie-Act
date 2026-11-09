"""Diagram spec: the only contract between builders and renderers."""
from typing import Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class DiagramError(ValueError):
    """Raised for any refusal (bad input, non-differentiable point, failed check)."""


class Curve(BaseModel):
    role: Literal["f", "derivative", "tangent"]
    label: str
    segments: list[list[list[float]]]


class Point(BaseModel):
    x: float
    y: float
    label: str = ""
    open: bool = False
    kind: Optional[Literal["max", "min", "tangent"]] = None


class Asymptote(BaseModel):
    kind: Literal["vertical", "horizontal"] = "vertical"
    at: float


class DerivativeSamples(BaseModel):
    x: list[float]
    f: list[Optional[float]]
    df: list[Optional[float]]


class FunctionGraphPayload(BaseModel):
    x_range: list[float]
    y_range: list[float]
    curves: list[Curve]
    points: list[Point] = []
    asymptotes: list[Asymptote] = []
    derivative_samples: Optional[DerivativeSamples] = None
    tangent_at: Optional[float] = None


class VarValue(BaseModel):
    at: str
    f: str
    f_right: Optional[str] = None  # only on forbidden columns (limit on the right side)
    kind: Optional[Literal["max", "min"]] = None


class VariationTablePayload(BaseModel):
    columns: list[str]
    derivative_signs: list[str]
    values: list[VarValue]
    trends: list[Literal["up", "down"]]
    mode: Optional[Literal["practice"]] = None


class Node(BaseModel):
    id: str
    label: str
    mastery: Optional[float] = None


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    kind: Literal["requires", "related"]


class ConceptMapPayload(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    focus: Optional[str] = None


class Diagram(BaseModel):
    version: Literal[1, 2] = 2
    type: Literal["function_graph", "variation_table", "concept_map"]
    lang: Literal["fr", "en", "ar"]
    title: str
    warnings: list[str] = []
    payload: Union[FunctionGraphPayload, VariationTablePayload, ConceptMapPayload]


def make(type_, lang, title, payload, warnings=None) -> dict:
    d = Diagram(version=2, type=type_, lang=lang, title=title, warnings=warnings or [], payload=payload)
    return d.model_dump(by_alias=True, exclude_none=True)
