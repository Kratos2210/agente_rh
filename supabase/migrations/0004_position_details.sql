-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Detalle del puesto — mensaje que el bot envía al candidato tras "Acepto".    ║
-- ║ Es el análogo de intro_message: texto plano (sin markdown) listo para enviar ║
-- ║ por Telegram, con descripción + requisitos + funciones + beneficios.         ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

alter table vacancies
    add column if not exists details_message text not null default '';

-- Rellena el detalle de la vacante demo (reproduce el aviso real de Sifrah).
update vacancies set details_message = E'📌 Analista de Automatizaciones e IA — Empresa del sector retail\n\n'
    'Diseñar, desarrollar e implementar soluciones de automatización e inteligencia artificial que '
    'optimicen los procesos internos y de negocio de la organización, contribuyendo a la reducción de '
    'tiempos operativos, la mejora continua y la innovación tecnológica sostenible.\n\n'
    '📋 Requisitos\n'
    '- Bachiller en Ingeniería de Sistemas, Software, Computación e Informática o carreras afines.\n'
    '- Mínimo 2 años de experiencia como Analista de Automatizaciones e IA.\n'
    '- Dominio de herramientas RPA, lenguajes de programación, Automatización Inteligente & IA, Cloud '
    'Computing, bases de datos, Integraciones & APIs, Metodologías Ágiles, Modelamiento de Procesos y '
    'DevOps & Control de Versiones.\n'
    '- Disponibilidad para laborar presencialmente de lunes a viernes en Santiago de Surco.\n\n'
    '🛠️ Funciones\n'
    '- Análisis y levantamiento de procesos con metodologías BPM (BPMN) y mejora continua.\n'
    '- Diseño y desarrollo de automatizaciones con RPA (UiPath, Power Automate, Blue Prism) y lenguajes '
    'como Python y JavaScript.\n'
    '- Implementación de agentes de IA para NLP, clasificación, predicción y automatización cognitiva.\n'
    '- Integración de sistemas (ERP, CRM, plataformas cloud) vía APIs REST y conectores; monitoreo y '
    'mantenimiento de automatizaciones en producción.\n'
    '- Documentación técnica y gestión de proyectos bajo metodologías ágiles (SCRUM/Kanban); soporte '
    'técnico y capacitación a usuarios.\n\n'
    '🎁 Beneficios\n'
    '- Planilla completa.\n'
    '- EPS al 50%.\n'
    '- Utilidades.\n'
    '- Descuentos en la marca del 40%.\n\n'
    '💖 En Sifrah creemos en el talento sin etiquetas. Promovemos un entorno diverso, inclusivo y '
    'respetuoso. Invitamos a postular a todas las personas sin distinción de género, edad, origen, '
    'discapacidad u otra condición, fomentando la igualdad de oportunidades para todas y todos.'
where title = 'Analista de Automatizaciones e IA';
