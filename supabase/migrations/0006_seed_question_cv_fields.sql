-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Mapeo pregunta↔campo del CV para la vacante demo                            ║
-- ║ Permite que el motor reformule como revalidación las preguntas cuyo dato     ║
-- ║ ya viene en el CV (Q5, el caso end-to-end, queda en frío).                   ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

update vacancy_questions q
set cv_field = m.cv_field
from (values
    (1, 'education'),
    (2, 'years_experience'),
    (3, 'location'),
    (4, 'skills'),
    (6, 'salary_expectation')
) as m(position, cv_field)
where q.position = m.position
  and q.vacancy_id in (select id from vacancies where title = 'Analista de Automatizaciones e IA');
