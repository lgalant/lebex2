import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from langgraph.graph import END
from langgraph.graph import StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command

from lebex.app.state import AppState
from lebex.app.state import LebaneUserContext


logger = logging.getLogger(__name__)

async def build_graph(config: None | RunnableConfig = None):
    configurable = (config or {}).get("configurable", {})

    graph = StateGraph(state_schema=AppState)
    graph.add_node("normalize_messages", normalize_messages_node)
    graph.add_node("handle_command", command_handler_node)
    graph.add_node("load_lebane_user_context", load_lebane_user_context_node)
    #graph.add_node("core", core.build_graph())
    graph.set_entry_point("normalize_messages")
    graph.add_edge("normalize_messages", "handle_command")
    graph.add_edge("load_lebane_user_context", "core")
    for node in graph.nodes.keys():
        if node in (
            "normalize_messages",
            "handle_command",
            "load_lebane_user_context",
            "core",
        ):
            continue

        graph.set_finish_point(node)

    compile_kwargs = configurable.get("compile_kwargs", {})
    return graph.compile(**compile_kwargs)


async def aanswer(
    text: str, *, configurable: dict[str, Any] | None = None
) -> str | list[str | dict]:
    if configurable is None:
        configurable = {}

    configurable["compile_kwargs"] = {}
    checkpointer = configurable.get("checkpointer")
    if checkpointer:
        configurable["compile_kwargs"]["checkpointer"] = checkpointer
    store = configurable.get("store")
    if store:
        configurable["compile_kwargs"]["store"] = store

    config: RunnableConfig = {
        "configurable": configurable,
        "recursion_limit": 10,
    }

    graph = await build_graph(config=config)

    state = None
    if checkpointer:
        state = await graph.aget_state(config=config)

    if state and state.interrupts:
        result = await graph.ainvoke(
            input=Command(resume=text),
            config=config,
        )
    else:
        result = await graph.ainvoke(
            input={"messages": HumanMessage(content=text)},
            config=config,
        )
     
    # LG PARA VER A QUE TOOL LLAMO PUEDO VER EL HISTORIAL EN STATE
    state2 = await graph.aget_state(config=config)
    #print("STATE2 messages: ", state2.values['messages'])
    #tool_calls = state2.values['messages'][10].tool_calls

    # Al terminar la ejecucion, el state va acumulando todos los idas y vueltas con el llm
    # tambien queda guardado TODO el historial del chat usuario/llm
    # en general las ultimas 1 o 2 son la respuesta de la LLM luego de que el back le paso datos
    # Si quiero ver cual es la tool que el llm llamo, tengo que ir un poco mas atras
    # Aca busco de los mensajes, solo los que indican que fueron tool_calls, y muestro el ultimo

    tool_calls_list = [
        msg.tool_calls
        for msg in state2.values['messages']
        if hasattr(msg, "tool_calls") and msg.tool_calls
    ]
    print("*En main. ultima tool call: ", tool_calls_list[-1]) # imprimo la ultima tool_call

    if "__interrupt__" in result:
        return result["__interrupt__"][-1].value

    last_message = result["messages"][-1]
    assert isinstance(last_message, AIMessage)
    return last_message.content
