Actúa como un Arquitecto Principal de IA, Líder de Gobierno de Datos y Auditor Experto en LLMOps de nivel Enterprise. Tu objetivo es realizar una auditoría técnica profunda y evaluar el nivel de madurez end-to-end del desarrollo basado en LLMs descrito en la sección de contexto. Debe evaluar la solución bajo los más altos estándares de ingeniería de software, seguridad de la información y eficiencia de costos (FinOps).

### 1. CONTEXTO DEL PROYECTO A EVALUAR
* **Caso de Uso:** Asistente virtual con capacidad de respuesta 24/7 basado en una arquitectura RAG (Retrieval-Augmented Generation) para la gestión y consulta de bases de conocimiento complejas (ej. normativas, documentos institucionales o atención ciudadana).
* **Stack Tecnológico:**
    - Frontend / Capa de Presentación: Next.js (App Router).
    - Base de Datos y Motor Vectorial: PostgreSQL (con extensión pgvector o similar para almacenamiento de embeddings).
    - Orquestación y Pipelines de Datos: Automatización y flujos lógicos gestionados mediante n8n / Airflow.
* **Componentes Core de IA:** Pipelines de ingesta de documentos (Chunking/Embedding), base de datos vectorial, orquestador de prompts y conexión a APIs de LLMs comerciales y/o open-source.

---

### 2. DIMENSIONES DE EVALUACIÓN (CRITERIOS DE AUDITORÍA)
Analiza la solución minuciosamente a través de las siguientes 6 dimensiones críticas:

#### Dimensión A: Métricas y Calidad RAG (Generación Aumentada por Recuperación)
* **Fase de Recuperación (Retrieval):** Evalúa la estrategia de chunking (tamaño, solapamiento), la calidad de los embeddings, el uso de técnicas híbridas (búsqueda léxica + semántica) y la optimización de métricas de *Context Precision* (relevancia de los fragmentos recuperados) y *Context Recall* (capacidad de extraer toda la información necesaria).
* **Fase de Generación (Generation):** Analiza la mitigación de alucinaciones mediante el control de la métrica *Faithfulness* (si la respuesta se sustenta estrictamente en el contexto recuperado) y la métrica *Answer Relevance* (si responde de manera directa y exacta a la intención del usuario).

#### Dimensión B: Observabilidad y Monitoreo End-to-End
* **Trazabilidad de Ejecución:** Auditoría de la trazabilidad completa del flujo, desde el evento disparado en el frontend (Next.js), el paso intermedio por los nodos lógicos del orquestador (n8n), hasta la consulta en la base de datos y la llamada al LLM.
* **Telemetría y Rendimiento:** Monitoreo y registro de latencias (Time to First Token - TTFT, latencia de búsqueda vectorial, latencia de generación) y tasas de error.
* **Logs de Interacción:** Estrategia para almacenar y estructurar inputs, outputs y metadatos para auditorías posteriores y detección de degradación del modelo (*Concept/Data Drift*).

#### Dimensión C: FinOps y Gestión de Costos
* **Optimización de Consumo:** Estrategias de compresión de contexto, poda de fragmentos irrelevantes y control del tamaño de la ventana de contexto.
* **Patrones de Arquitectura Financiera:** Implementación del patrón *Model Router* (enrutamiento inteligente de prompts: usar modelos ligeros/económicos para tareas de clasificación o extracción de entidades, y modelos avanzados solo para síntesis compleja).
* **Caché Semántica:** Arquitectura para almacenar respuestas previas mediante *Semantic Caching*, evitando llamadas redundantes a las APIs de LLM ante consultas recurrentes idénticas o semánticamente muy similares.

#### Dimensión D: Arquitectura, Orquestación e Integración
* **Resiliencia de Flujos:** Manejo de excepciones en los flujos de n8n, políticas de reintentos (*backoff exponencial*), gestión de límites de tasa (*rate limits*) de proveedores de LLMs y mecanismos de fallback (modelos alternativos en caso de caída).
* **Desacoplamiento:** Separación clara entre la lógica de negocio, la capa de datos y la capa de inferencia del LLM.

#### Dimensión E: Gobierno de Datos, Seguridad y Cumplimiento
* **Seguridad de Inferencia:** Mecanismos de protección contra ataques de inyección de prompts (*Prompt Injection*, *Jailbreaking*) y manipulación de variables del sistema.
* **Privacidad de la Información:** Estrategias para el descubrimiento, anonimización o enmascaramiento de PII (Información Personal Identificable) antes de que los datos salgan al proveedor del LLM.
* **Linaje y Calidad de Datos:** Trazabilidad del origen de la información que alimenta la base de conocimiento (Data Lineage) y mecanismos para asegurar que los documentos de origen estén actualizados, limpios y libres de contradicciones.

---

### 3. FORMATO DE SALIDA REQUERIDO
Entrega los resultados estructurados rigurosamente en las siguientes tres secciones:

#### 1. Matriz de Madurez LLMOps (Puntuación de 1 a 5)
Para cada una de las 5 dimensiones anteriores, asigna una calificación basada en los siguientes niveles (1: Inicial/Ad-hoc, 2: Repetible pero manual, 3: Definido y documentado, 4: Gestionado y medido, 5: Optimizado y automatizado). Cada nota debe venir acompañada de una **justificación técnica detallada** basada en el stack y contexto provisto.

#### 2. Análisis de Brechas Críticas (Gap Analysis & Risks)
Identifica un mínimo de 4 riesgos arquitectónicos, de seguridad o financieros latentes en este tipo de implementaciones (por ejemplo, vulnerabilidades en los nodos de n8n, trampas de facturación ocultas por concurrencia en Next.js, o pérdida de precisión por mala indexación en PostgreSQL).

#### 3. Roadmap de Remediación y Evolución Técnica
Proporciona un plan de acción concreto, priorizado y dividido en:
* **Corto Plazo (Quick Wins / Seguridad Crítica):** Acciones inmediatas para mitigar riesgos severos.
* **Mediano Plazo (Estabilización e Infraestructura):** Implementación de herramientas de observabilidad especializadas, automatización de pruebas de evaluación RAG e integración de caché semántica.
* **Largo Plazo (Optimización Continua):** Gobernanza avanzada, re-entrenamiento con datos propios (Fine-tuning si aplica), y automatización completa del pipeline CI/CD de prompts y flujos.
