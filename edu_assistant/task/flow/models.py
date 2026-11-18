"""流程与槽位模型。"""

from __future__ import annotations

from dataclasses import dataclass, field

from edu_assistant.task.flow.steps import FlowStep

SLOT_TYPES = ("text", "int", "enum", "json")


@dataclass
class FlowSlot:
    """流程槽位声明。"""

    name: str
    type: str = "text"  # text / int / enum / json
    label: str = ""
    description: str = ""
    choices: list[str] = field(default_factory=list)  # enum 类型的合法值


@dataclass
class Flow:
    """一个任务流程：步骤图 + 声明槽位。"""

    id: str
    name: str
    description: str = ""
    steps: dict[str, FlowStep] = field(default_factory=dict)
    start_step_id: str = ""
    slots: dict[str, FlowSlot] = field(default_factory=dict)  # 本流程使用的槽位声明

    def get_step(self, step_id: str | None) -> FlowStep:
        if step_id is None or step_id not in self.steps:
            raise KeyError(f"流程 {self.id} 不存在步骤 {step_id}")
        return self.steps[step_id]

    def summary(self) -> dict:
        """给规划器的摘要（不含步骤详情）。"""
        return {
            "flow_id": self.id,
            "name": self.name,
            "description": self.description,
            "slots": [
                {"name": s.name, "label": s.label, "type": s.type, "choices": s.choices}
                for s in self.slots.values()
            ],
        }


@dataclass
class FlowCatalog:
    """全部流程目录（启动时加载一次）。"""

    flows: dict[str, Flow] = field(default_factory=dict)
    slots: dict[str, FlowSlot] = field(default_factory=dict)  # 全局槽位声明

    def get_flow(self, flow_id: str) -> Flow:
        if flow_id not in self.flows:
            raise KeyError(f"流程不存在: {flow_id}")
        return self.flows[flow_id]

    def flow_names(self) -> dict[str, str]:
        return {fid: flow.name for fid, flow in self.flows.items()}
