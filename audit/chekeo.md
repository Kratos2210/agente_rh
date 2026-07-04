Instrucciones para el Desarrollo del Repositorio de IA en Producción
Estructura del Repositorio:

Crea carpetas separadas para cada componente: retrieval/, ranking/, orquestacion/, agente/, adaptadores_mcp/, observabilidad/, despliegue/.
Incluye un README.md general que describa la arquitectura y cómo ejecutar cada módulo.
Implementación de Componentes:

Retrieval: Integra una base de datos vectorial para búsqueda semántica.
Ranking: Implementa un re-ranker para priorizar resultados.
Orquestación (LangChain): Construye pipelines que coordinen llamadas a LLMs y herramientas.
Agente Cíclico (LangGraph): Modela flujos iterativos y estados para decisiones dinámicas.
Adaptadores MCP: Desarrolla scripts que implementen el protocolo MCP para llamadas seguras a herramientas externas.
Scripts y Manifiestos de Despliegue:

Crea manifiestos para Kubernetes (.yaml) y/o funciones serverless para cada componente.
Incluye scripts de automatización para despliegue y escalado.
Instrumentación de Observabilidad:

Integra LangSmith y Arize para recolectar logs, métricas y trazas.
Define métricas clave: latencia, tasa de alucinaciones, uso de recursos.
Documentación:

Documenta las decisiones arquitectónicas y las métricas definidas.
Describe el proceso de despliegue y monitoreo.
Checklist de Evaluación:

Verifica que el repositorio sea funcional y modular.
Asegura que la integración MCP sea segura y conforme al protocolo.
Confirma que la observabilidad esté correctamente instrumentada.
Valida que los scripts de despliegue funcionen en entornos reales.
Entregable
Un repositorio completo con la estructura y componentes descritos.
Documentación clara y concisa.
Scripts y manifiestos listos para despliegue.
