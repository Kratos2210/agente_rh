-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Etiqueta corta por pregunta — para los vértices del radar y la leyenda       ║
-- ║ (más intuitivo para el reclutador que un número).                            ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

alter table vacancy_questions
    add column if not exists label text;

update vacancy_questions q
set label = m.label
from (values
    (1, 'Formación'),
    (2, 'Experiencia'),
    (3, 'Disponibilidad'),
    (4, 'Dominio técnico'),
    (5, 'Caso real'),
    (6, 'Salario')
) as m(position, label)
where q.position = m.position
  and q.vacancy_id in (select id from vacancies where title = 'Analista de Automatizaciones e IA');
