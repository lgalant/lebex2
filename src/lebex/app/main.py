import logging
from typing import Any
import base64
import logging
import zoneinfo
from typing import Any
import pytz
import jwt
import sqlalchemy as sa
import phonenumbers
import phonenumbers.phonenumberutil
import phonenumbers.timezone

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.messages import trim_messages
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from langgraph.graph import END
from langgraph.graph import StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command
from langchain_core.messages import RemoveMessage

from lebex.app.state import AppState
from lebex.app.state import LebaneUserContext
from lebex.tools import ALL_TOOLS


logger = logging.getLogger(__name__)
_RESET_KEYWORDS = {"reset", "nueva conversación", "nueva conversacion", "empezar de cero", "reiniciar"}


_SYSTEM_PROMPT = """Sos un asistente del sistema ERP Lebane.
Ayudás a los usuarios a consultar información financiera, comercial y operativa de su organización con informacion \
provista por el ERP. No puedes proporcionar información que no te haya sido dada por el ERP, y si el usuario te hace\
 una pregunta que no puede ser respondida con la información del ERP, debes decirle que no puedes ayudarlo con esa pregunta.
Tenés acceso a herramientas para obtener datos del sistema. Antes de llamar una herramienta, \
asegurate de tener todos los parámetros que necesita — si falta información, preguntá al usuario.
Respondé siempre en español, con formato claro y conciso.
Si el pedido que te hacen no puede ser resuelto con las tools, le debes explicar al usuario que no podés ayudarlo con ese pedido.
Y lo puedes dirigir a un canal de atención al cliente humano de lebane, que es el siguiente: https://lebane.app/"""

_core_agent = create_agent(
    model="openai:gpt-4o",
    tools=ALL_TOOLS,
    system_prompt=_SYSTEM_PROMPT,
)


async def agent_node(state: AppState, config: RunnableConfig) -> dict:
    """Wraps the create_agent subgraph, injecting organization_id into configurable."""
    organization_id = (state.get("lebane_user_context") or {}).get("organization_id")
    core_config: RunnableConfig = {
        **config,
        "configurable": {
            **config.get("configurable", {}),
            "organization_id": organization_id,
        },
    }

    # LG esto me permite trimear el historial de mensajes que le paso al llm, para no pasarle todo el historial
    '''
        trimmed = trim_messages(
        state.get("messages", []),
        strategy="last",
        token_counter=len,   # cuenta mensajes, no tokens
        max_tokens=20,       # máximo 20 mensajes enviados al LLM
        include_system=True,
        allow_partial=False,
        
    )
      result = await _core_agent.ainvoke(
        {"messages": trimmed},
        config=core_config,
    )
    '''

    
    result = await _core_agent.ainvoke(
        {"messages": state.get("messages", [])},
        config=core_config,
    )
    for msg in result["messages"]:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                logger.info("Tool llamada: %s | args: %s", tc["name"], tc["args"])

    return {"messages": result["messages"]}


async def load_lebane_user_context_node(
    state: AppState, config: RunnableConfig
) -> AppState:
    lsessionmaker = config["configurable"].get("lsessionmaker")
    ldbsessionmaker = config["configurable"].get("ldbsessionmaker")
    if not ldbsessionmaker or not lsessionmaker:
        logger.warning(
            "No Lebane sessionmaker configured - db: %s http: %s",
            bool(lsessionmaker),
            bool(ldbsessionmaker),
        )
        return {
            "messages": [
                AIMessage(content="Hubo un problema al conectar con Lebane.")
            ]
        }

    settings = config["configurable"]["settings"]
    async with lsessionmaker() as lebane_client:
        token = lebane_client.token
    jwtdecoded = jwt.decode(
        token,
        base64.b64decode(settings.LEBANE_JWT_SECRET),
        algorithms=["HS256"],
    )

    email = jwtdecoded["sub"]
    assert email
    organization_id = int(jwtdecoded["organizacion"])
    assert organization_id

    async with ldbsessionmaker() as dbsession:
        result = await dbsession.execute(
            sa.text(
                """
                SELECT
                    u.id AS user_id,
                    sdu.telefono AS phone,
                    l.nombre AS location,
                    p.iso_pais AS country
                FROM
                    sesion_de_usuario sdu
                JOIN
                    usuario u
                    ON u.sesion_de_usuario_id = sdu.id
                LEFT JOIN
                    contacto_direcciones cd
                    ON cd.contacto_id = u.contacto_id
                LEFT JOIN
                    ubicacion ubi
                    ON cd.direcciones_id = ubi.id
                LEFT JOIN
                    localidad l
                    ON ubi.localidad_id = l.id
                LEFT JOIN
                    pais p
                    ON p.id = l.pais_id
                WHERE
                    u.organizacion_id = :organization_id
                    AND u.correo_electronico = :email
                """
            ).bindparams(organization_id=organization_id, email=email)
        )
        user_id: None | int
        phone: None | str
        location: None | str
        country: None | str
        user_id, phone, location, country = next(result)

    assert phone, "Phone not found"

    timezone = None
    if location and country:
        timezones = pytz.country_timezones.get(country)
        for timezone in timezones:
            if location in timezone:
                break
    else:
        parsed_number = phonenumbers.parse(f"+{phone}")
        timezones = phonenumbers.timezone.time_zones_for_number(parsed_number)
        if timezones:
            timezone = next(iter(timezones))
        country = phonenumbers.phonenumberutil.region_code_for_number(
            parsed_number
        )

    if timezone:
        try:
            zoneinfo.ZoneInfo(timezone)
        except zoneinfo.ZoneInfoNotFoundError:
            logger.warning(f"Timezone not found: {timezone}")

    if not timezone:
        timezone = "UTC"

    permissions = [str(permission) for permission in jwtdecoded["permisos"]]
    assert permissions
    projects = [int(project) for project in jwtdecoded["proyectos"]]
    assert projects

    return {
        "lebane_user_context": LebaneUserContext(
            user_id=user_id,
            email=email,
            phone=phone,
            location=location,
            country=country,
            timezone=timezone,
            organization_id=organization_id,
            permissions=permissions,
            projects=projects,
        )
    }


async def _reset_thread(graph, config: RunnableConfig) -> None:
    """Borra todos los mensajes del thread via LangGraph."""
    thread_id = config["configurable"].get("thread_id", "?")
    try:
        state = await graph.aget_state(config=config)
        messages = state.values.get("messages", [])
        if not messages:
            logger.info("Thread %s ya estaba vacío", thread_id)
            return
        await graph.aupdate_state(
            config,
            {"messages": [RemoveMessage(id=m.id) for m in messages]},
        )
        logger.info("Thread reseteado: %s (%d mensajes borrados)", thread_id, len(messages))
    except Exception:
        logger.exception("Error al resetear thread %s", thread_id)

async def build_graph(config: None | RunnableConfig = None):
    configurable = (config or {}).get("configurable", {})

    graph = StateGraph(state_schema=AppState)
    #graph.add_node("normalize_messages", normalize_messages_node)
    #graph.add_node("handle_command", command_handler_node)
    graph.add_node("load_lebane_user_context", load_lebane_user_context_node)
    graph.add_node("agent", agent_node)
    graph.set_entry_point("load_lebane_user_context")
    #graph.add_edge("normalize_messages", "handle_command")
    graph.add_edge("load_lebane_user_context", "agent")
    graph.set_finish_point("agent")

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

    # LG - Reset de conversación
    if text.strip().lower() in _RESET_KEYWORDS:
        await _reset_thread(graph, config)
        return "Conversación reiniciada. ¿En qué te puedo ayudar?"

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

    '''tool_calls_list = [
        msg.tool_calls
        for msg in state2.values['messages']
        if hasattr(msg, "tool_calls") and msg.tool_calls
    ]
    print("*En main. ultima tool call: ", tool_calls_list[-1]) # imprimo la ultima tool_call
    '''

    if "__interrupt__" in result:
        return result["__interrupt__"][-1].value

    last_message = result["messages"][-1]
    assert isinstance(last_message, AIMessage)
    return last_message.content


'''

El checkpointer **está siendo usado correctamente**. Aquí el análisis:

**Arquitectura de dos niveles:**

1. **Grafo externo** (construido en `build_graph`): `load_lebane_user_context` → `agent` → END. Este se compila **con** el checkpointer en main.py:
   ```python
   configurable["compile_kwargs"]["checkpointer"] = checkpointer
   # ...
   graph.compile(**compile_kwargs)  # ← checkpointer incluido aquí
   ```

2. **`_core_agent`** (de `create_agent`): Es un subgrafo pre-compilado **sin** checkpointer propio. Se invoca dentro de `agent_node` con `_core_agent.ainvoke(...)`.

**¿Por qué funciona bien sin checkpointer en `create_agent`?**

- El checkpointer del **grafo externo** es el que persiste el estado completo (incluyendo `messages`) entre invocaciones del usuario, usando el `thread_id` (el teléfono).
- `_core_agent` no necesita su propio checkpointer porque ejecuta su loop interno de tool-calling **dentro de una sola invocación**. Corre hasta completarse y devuelve todos los mensajes acumulados.
- En la siguiente interacción del usuario, el grafo externo carga el checkpoint previo, el reducer `add_and_trim_messages` de state.py agrega el nuevo `HumanMessage` al historial existente, y se lo pasa completo a `_core_agent`.

**Flujo resumido:**

1. `aanswer()` extrae el checkpointer del configurable y lo pasa a `compile_kwargs`
2. `build_graph()` compila el grafo externo **con** checkpointer
3. Si hay estado previo (checkpoint), lo carga automáticamente para ese `thread_id`
4. `agent_node` recibe el historial completo y lo pasa a `_core_agent`
5. Al finalizar, el grafo externo guarda el nuevo estado via checkpointer

**En resumen:** No hace falta pasar checkpointer a `create_agent`. El checkpointer en el grafo externo es suficiente y es el patrón correcto cuando encapsulás un agente como nodo dentro de otro grafo.
'''