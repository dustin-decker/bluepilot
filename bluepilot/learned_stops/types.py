"""Typed planner state; wire JSON is validated before it enters this boundary."""
from typing import NotRequired, TypedDict


class Target(TypedDict):
  id: str
  distance: float
  accuracy: float
  ready: bool
  confirmed: NotRequired[bool | str]
  visits: NotRequired[int]


class Latch(TypedDict):
  id: str
  distance: float
  hold: bool
  setback: NotRequired[float]


class StopState(TypedDict):
  mode: str
  state: str
  apply: bool
  hold: bool
  reason: NotRequired[str]
  distance: NotRequired[float | None]
  target: NotRequired[str | None]
  takeover: NotRequired[bool]
  at_reference: NotRequired[bool]
  confirmed: NotRequired[bool]
  visits: NotRequired[int]
