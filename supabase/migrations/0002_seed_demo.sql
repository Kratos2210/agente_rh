-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Seed demo — vacante "Analista de Automatizaciones e IA"                     ║
-- ║ Reproduce la entrevista real que SofIA (Sifrah) le hizo a Alberto el        ║
-- ║ 16/06/2026. Idempotente: borra la vacante demo previa y la recrea.          ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

delete from vacancies where title = 'Analista de Automatizaciones e IA';

with v as (
    insert into vacancies (title, description, requirements, intro_message, company_info, semaphore_thresholds)
    values (
        'Analista de Automatizaciones e IA',
        'Diseñar, desarrollar e implementar soluciones de automatización e inteligencia artificial '
        'que optimicen los procesos internos y de negocio, reduciendo tiempos operativos e impulsando '
        'la innovación tecnológica sostenible.',
        E'- Bachiller en Ingeniería de Sistemas, Software, Computación e Informática o afines.\n'
        '- Mínimo 2 años de experiencia como Analista de Automatizaciones e IA.\n'
        '- Dominio de herramientas RPA, lenguajes de programación, Automatización Inteligente & IA, '
        'Cloud Computing, bases de datos, Integraciones & APIs, Metodologías Ágiles, Modelamiento de '
        'Procesos y DevOps.',
        E'Hola 👋 Te habla el equipo de Atracción de Talento.\n\n'
        'Vimos que aplicaste a nuestra vacante de *Analista de Automatizaciones e IA* y nos gustaría '
        'hacerte unas preguntas.\n\nSi deseas continuar, toca *Acepto*. Si en cualquier momento no '
        'quieres seguir, toca *No interesado*.',
        E'Empresa del sector retail. Modalidad presencial en Santiago de Surco, Lima. El rol diseña e '
        'implementa automatizaciones e IA para optimizar procesos internos y de negocio. Stack esperado: '
        'RPA, Python, IA/LLMs, cloud, bases de datos, APIs, metodologías ágiles, modelado de procesos y DevOps.',
        '{"green_min": 75, "yellow_min": 50}'::jsonb
    )
    returning id
)
insert into vacancy_questions (vacancy_id, position, text, criterion, weight, max_follow_ups)
select v.id, q.position, q.text, q.criterion, q.weight, q.max_follow_ups
from v, (values
    (1,
     'Para comenzar, ¿cuál es tu nivel de estudios (bachiller, técnico o estudiante) y qué carrera cursaste o estás cursando?',
     'Formación académica afín al puesto: Ingeniería de Sistemas/Software/Computación e Informática o carreras relacionadas. Puntúa alto si es titulado/bachiller en carrera afín; bajo si no tiene relación.',
     1.0, 1),
    (2,
     '¿Cuánto tiempo de experiencia tienes como analista en el área de automatizaciones e IA?',
     'Años de experiencia específica en automatización e IA. Requisito: mínimo 2 años. Puntúa alto si cumple o supera con experiencia verificable; bajo si es menor o difusa.',
     1.5, 1),
    (3,
     '¿Estás disponible para trabajar de forma presencial en Santiago de Surco, Lima?',
     'Disponibilidad para modalidad presencial en la ubicación indicada. Es un filtro: alto si confirma disponibilidad; bajo si no puede o pone condiciones que lo impiden.',
     1.0, 0),
    (4,
     '¿Puedes detallar y sustentar tu dominio en herramientas RPA, lenguajes de programación, automatización inteligente e IA, cloud, bases de datos, integraciones/APIs, metodologías ágiles, modelamiento de procesos y DevOps? Indica herramientas y ejemplos de proyectos donde los aplicaste.',
     'Amplitud y profundidad técnica sustentada con herramientas concretas y ejemplos de proyectos reales. Puntúa alto si menciona herramientas específicas (n8n, UiPath, Python, cloud, etc.) con casos aplicados; bajo si es vago o genérico.',
     2.0, 1),
    (5,
     'Cuéntame una automatización o solución de IA que implementaste recientemente: cuál era el problema de negocio, qué herramientas y arquitectura usaste, cómo la integraste con otros sistemas y qué resultados obtuviste.',
     'Capacidad de explicar un caso real end-to-end con problema de negocio, arquitectura, integración y resultados/impacto medible. Puntúa alto si el caso es concreto y muestra impacto; bajo si es difuso o sin resultados.',
     2.0, 1),
    (6,
     'Para cerrar, ¿cuál es tu pretensión salarial mínima? Indica el monto, si es bruto o neto y la moneda.',
     'Pretensión salarial expresada con claridad (monto, bruto/neto, moneda) y dentro de un rango razonable para el rol. Mayormente informativo para el reclutador.',
     0.5, 0)
) as q(position, text, criterion, weight, max_follow_ups);
