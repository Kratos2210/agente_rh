# `orquestacion/` — Orquestación (LangChain)

Componente **Orquestación** de la rúbrica: coordina las llamadas al LLM y arma las cadenas de prompts.
Es la abstracción intercambiable del modelo (Groq/Qwen3 u otro compatible-OpenAI).

| Archivo | Rol |
|---|---|
| `llm.py` | Protocolo `LLM` + `LangChainLLM` + `build_default_llm`/`build_stage_overrides` (routing barato por etapa) + `MeteredLLM` (metering + trazas + atribución de modelo por etapa) + `complete_staged`/`parse_json_object`. |
| `qa_chain.py` | Cadena RAG clásica (retrieval → re-ranker → generación) heredada de agente_pro. |
| `classifier.py` | Sugerencia de preguntas / clasificación auxiliar. |

Layering: `agente/` consume `orquestacion/`, que a su vez usa `retrieval/` + `ranking/` + `core/`.
El metering (`MeteredLLM`) alimenta la `observabilidad/`. LLM inyectable (fake en tests).
