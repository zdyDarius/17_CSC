"""组件装配：流程目录启动加载一次；全部组件通过 lifespan 装配/释放。"""

from __future__ import annotations

from dataclasses import dataclass

from edu_assistant.chitchat.chit_chat import ChitChatHandler
from edu_assistant.clarify.clarify_response import ClarifyResponse
from edu_assistant.conf.config import Settings, get_settings
from edu_assistant.engine.dialogue_engine import DialogueEngine
from edu_assistant.integrations.edu_data.client import EduDataClient
from edu_assistant.integrations.edu_data.order_item_adapter import OrderItemAdapter
from edu_assistant.knowledge.api_providers import CohortApiProvider, SeriesApiProvider
from edu_assistant.knowledge.intents import intents_catalog
from edu_assistant.knowledge.knowledge_handler import KnowledgeHandler
from edu_assistant.knowledge.provider import FaqProvider, RagProvider
from edu_assistant.knowledge.responder import KnowledgeResponder
from edu_assistant.plan.turn_plan_validator import TurnPlanValidator
from edu_assistant.plan.turn_planner import TurnPlanner
from edu_assistant.task.action.action_runner import ActionRunner
from edu_assistant.task.action.registry import registered_action_names
from edu_assistant.task.command.command_processor import CommandProcessor
from edu_assistant.task.flow.loader import FlowLoader
from edu_assistant.task.flowexecutor.flow_executor import FlowExecutor
from edu_assistant.task.response.response_render import ResponseRender
from edu_assistant.task.task_handler import TaskHandler


@dataclass
class EngineComponents:
    engine: DialogueEngine
    order_item_adapter: OrderItemAdapter
    flow_catalog: object


def build_engine(settings: Settings | None = None) -> EngineComponents:
    settings = settings or get_settings()

    # 导入 edu action 模块（触发注册，须在流程加载校验之前）
    import edu_assistant.task.action.edu.order_actions  # noqa: F401,E402
    import edu_assistant.task.action.edu.progress_actions  # noqa: F401,E402
    import edu_assistant.task.action.edu.refund_actions  # noqa: F401,E402
    import edu_assistant.task.action.edu.ticket_actions  # noqa: F401,E402

    # 流程目录：启动加载一次并校验（action 注册、跳转目标、槽位声明）
    catalog = FlowLoader().load(settings.flow_config_path, registered_action_names())

    # 集成层
    edu_client = EduDataClient()
    order_item_adapter = OrderItemAdapter()

    # 任务轨道
    command_processor = CommandProcessor(max_paused=settings.max_paused_tasks)
    response_render = ResponseRender()
    action_runner = ActionRunner(settings, edu_client, order_item_adapter)
    flow_executor = FlowExecutor(
        response_render=response_render,
        action_runner=action_runner,
        command_processor=command_processor,
        flow_catalog=catalog,
        max_steps=settings.max_flow_steps_per_turn,
    )
    task_handler = TaskHandler(
        command_processor=command_processor,
        flow_catalog=catalog,
        flow_executor=flow_executor,
    )

    # 知识轨道
    providers = {
        "faq.edu": FaqProvider(settings.faq_path),
        "rag.edu": RagProvider(settings.docs_path),
        "api.series": SeriesApiProvider(edu_client),
        "api.cohort": CohortApiProvider(edu_client),
    }
    knowledge_handler = KnowledgeHandler(providers, KnowledgeResponder())

    # 规划与校验
    intents = intents_catalog()
    turn_planner = TurnPlanner(intents)
    validator = TurnPlanValidator(intents, max_paused=settings.max_paused_tasks)

    engine = DialogueEngine(
        turn_planner=turn_planner,
        turn_plan_validator=validator,
        task_handler=task_handler,
        knowledge_handler=knowledge_handler,
        chit_chat=ChitChatHandler(),
        clarify_response=ClarifyResponse(),
        flow_catalog=catalog,
        action_deps=action_runner,
    )
    return EngineComponents(
        engine=engine,
        order_item_adapter=order_item_adapter,
        flow_catalog=catalog,
    )
