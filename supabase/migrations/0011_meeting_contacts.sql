-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ Datos de contacto en la reunión agendada.                                   ║
-- ║ Guardamos teléfono del candidato y del reclutador (+ su nombre) para que    ║
-- ║ figuren en el dashboard, el evento de Calendar y la confirmación.           ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

alter table meetings add column if not exists candidate_phone text not null default '';
alter table meetings add column if not exists recruiter_phone text not null default '';
alter table meetings add column if not exists recruiter_name  text not null default '';
