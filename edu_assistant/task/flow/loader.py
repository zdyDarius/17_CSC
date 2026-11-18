"""流程 YAML 加载与启动期校验。

启动时加载一次（不在每轮请求中重复读盘），校验：
- 槽位声明（类型合法、enum 必须带 choices、步骤引用的槽位已声明）
- 步骤 ID 唯一、跳转目标存在、有且仅有一个 start、至少一个 end
- action 步骤引用的 action 必须已注册
- 条件表达式语法合法（白名单解析）
非法定义直接启动失败，不允许带病运行。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from edu_assistant.task.flow.models import (
    SLOT_TYPES,
    Flow,
    FlowCatalog,
    FlowSlot,
)
from edu_assistant.task.flow.steps import (
    STEP_CLASSES,
    CollectSlotStep,
    ConditionalLink,
    FlowStep,
)
from edu_assistant.utils.errors import FlowDefinitionError


class FlowLoader:
    def load(self, path: Path, registered_actions: set[str] | None = None) -> FlowCatalog:
        registered_actions = registered_actions or set()
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise FlowDefinitionError(f"YAML 解析失败: {exc}") from exc
        if not isinstance(data, dict) or "flows" not in data or "slots" not in data:
            raise FlowDefinitionError("流程文件必须包含顶层 slots 和 flows 字段")

        slots = self._load_slots(data["slots"])
        flows = {}
        for flow_id, flow_data in (data["flows"] or {}).items():
            flows[flow_id] = self._load_flow(flow_id, flow_data or {}, slots, registered_actions)

        self._validate_catalog(flows)
        catalog = FlowCatalog(flows=flows, slots=slots)
        self._compile_conditions(catalog)
        return catalog

    # —— 槽位 ——

    def _load_slots(self, slots_data: dict) -> dict[str, FlowSlot]:
        slots: dict[str, FlowSlot] = {}
        for name, raw in (slots_data or {}).items():
            raw = raw or {}
            slot_type = str(raw.get("type", "text"))
            if slot_type not in SLOT_TYPES:
                raise FlowDefinitionError(f"槽位 {name} 类型非法: {slot_type}")
            choices = [str(c) for c in (raw.get("choices") or [])]
            if slot_type == "enum" and not choices:
                raise FlowDefinitionError(f"enum 槽位 {name} 必须声明 choices")
            slots[str(name)] = FlowSlot(
                name=str(name),
                type=slot_type,
                label=str(raw.get("label", name)),
                description=str(raw.get("description", "")),
                choices=choices,
            )
        return slots

    # —— 流程 ——

    def _load_flow(
        self,
        flow_id: str,
        flow_data: dict,
        slots: dict[str, FlowSlot],
        registered_actions: set[str],
    ) -> Flow:
        raw_steps = flow_data.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise FlowDefinitionError(f"流程 {flow_id} 必须声明非空 steps")

        steps: dict[str, FlowStep] = {}
        start_ids: list[str] = []
        end_ids: list[str] = []
        used_slots: dict[str, FlowSlot] = {}

        for raw_step in raw_steps:
            if not isinstance(raw_step, dict) or "id" not in raw_step or "type" not in raw_step:
                raise FlowDefinitionError(f"流程 {flow_id} 存在缺少 id/type 的步骤")
            step_id = str(raw_step["id"])
            if step_id in steps:
                raise FlowDefinitionError(f"流程 {flow_id} 步骤 ID 重复: {step_id}")
            step_type = str(raw_step["type"])
            clz = STEP_CLASSES.get(step_type)
            if clz is None:
                raise FlowDefinitionError(f"流程 {flow_id} 步骤类型非法: {step_type}")
            step = clz.from_dict(raw_step)
            steps[step_id] = step
            if step_type == "start":
                start_ids.append(step_id)
            if step_type == "end":
                end_ids.append(step_id)
            if isinstance(step, CollectSlotStep):
                slot_name = step.slot_name
                if not slot_name or slot_name not in slots:
                    raise FlowDefinitionError(
                        f"流程 {flow_id} 步骤 {step_id} 引用未声明槽位: {slot_name!r}"
                    )
                used_slots[slot_name] = slots[slot_name]
                if step_type == "collect" and not step.template:
                    raise FlowDefinitionError(
                        f"流程 {flow_id} 步骤 {step_id} 缺少追问模板"
                    )
            from edu_assistant.task.flow.steps import ActionFlowStep  # noqa: PLC0415

            if isinstance(step, ActionFlowStep):
                if step.action not in registered_actions:
                    raise FlowDefinitionError(
                        f"流程 {flow_id} 步骤 {step_id} 引用未注册 action: {step.action!r}"
                    )

        if len(start_ids) != 1:
            raise FlowDefinitionError(f"流程 {flow_id} 必须有且仅有一个 start 步骤")
        if not end_ids:
            raise FlowDefinitionError(f"流程 {flow_id} 缺少 end 步骤")

        # 跳转目标存在性
        for step in steps.values():
            for link in step.next:
                if link.target not in steps:
                    raise FlowDefinitionError(
                        f"流程 {flow_id} 步骤 {step.id} 跳转到不存在的步骤: {link.target}"
                    )

        return Flow(
            id=flow_id,
            name=str(flow_data.get("name", flow_id)),
            description=str(flow_data.get("description", "")),
            steps=steps,
            start_step_id=start_ids[0],
            slots=used_slots,
        )

    def _validate_catalog(self, flows: dict[str, Flow]) -> None:
        if not flows:
            raise FlowDefinitionError("流程目录为空")

    def _compile_conditions(self, catalog: FlowCatalog) -> None:
        for flow in catalog.flows.values():
            for step in flow.steps.values():
                for link in step.next:
                    if isinstance(link, ConditionalLink):
                        link.compiled()  # 启动期立即解析，语法错误直接失败
