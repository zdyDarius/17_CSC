"""流程步骤模型：start / collect / action / response / end。

- 确认步骤通过带 options 的 collect 实现（项目计划 3.1）。
- 条件跳转通过条件表达式（见 conditions.py，白名单解析，禁止 eval）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edu_assistant.task.flow.conditions import Condition, parse_condition


@dataclass
class FlowStepLink:
    """步骤跳转链接。"""

    target: str


@dataclass
class StaticLink(FlowStepLink):
    pass


@dataclass
class ConditionalLink(FlowStepLink):
    condition: str = ""
    _compiled: Condition | None = field(default=None, repr=False, compare=False)

    def compiled(self) -> Condition:
        if self._compiled is None:
            self._compiled = parse_condition(self.condition)
        return self._compiled


@dataclass
class FlowStep:
    id: str
    next: list[FlowStepLink] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowStep":
        raise NotImplementedError


def build_links(next_data: Any) -> list[FlowStepLink]:
    if isinstance(next_data, str):
        return [StaticLink(target=next_data)]
    if isinstance(next_data, list):
        links: list[FlowStepLink] = []
        for item in next_data:
            if not isinstance(item, dict):
                continue
            if "if" in item:
                links.append(
                    ConditionalLink(condition=str(item["if"]), target=str(item["then"]))
                )
            elif "else" in item:
                links.append(StaticLink(target=str(item["else"])))
        return links
    return []


@dataclass
class StartFlowStep(FlowStep):
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StartFlowStep":
        return cls(id=data["id"], next=build_links(data.get("next")))


@dataclass
class CollectSlotStep(FlowStep):
    """收集槽位；可选 options（静态选项，用于确认步骤）。"""

    slot_name: str = ""
    options: list[str] = field(default_factory=list)
    template: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CollectSlotStep":
        options = data.get("options") or []
        if isinstance(options, str):
            options = [options]
        return cls(
            id=data["id"],
            next=build_links(data.get("next")),
            slot_name=str(data.get("slot_name", "")),
            options=[str(x) for x in options],
            template=data.get("template") or {},
        )


@dataclass
class ActionFlowStep(FlowStep):
    action: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionFlowStep":
        return cls(
            id=data["id"],
            next=build_links(data.get("next")),
            action=str(data.get("action", "")),
        )


@dataclass
class ResponseFlowStep(FlowStep):
    template: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResponseFlowStep":
        return cls(
            id=data["id"],
            next=build_links(data.get("next")),
            template=data.get("template") or {},
        )


@dataclass
class EndFlowStep(FlowStep):
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EndFlowStep":
        return cls(id=data["id"], next=[])


STEP_CLASSES: dict[str, type[FlowStep]] = {
    "start": StartFlowStep,
    "collect": CollectSlotStep,
    "action": ActionFlowStep,
    "response": ResponseFlowStep,
    "end": EndFlowStep,
}
