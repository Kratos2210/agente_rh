# auth-tenancy Specification

## Purpose
Identidad, roles y aislamiento multi-tenant de toda la superficie (API, MCP, datos).
Manual de dominio: `spec/Seguridad.md` y `spec/APIS.md`.

## Requirements

### Requirement: Autenticación obligatoria
Toda ruta `/api/*` fuera de la allowlist pública (`/api/health`, `/api/auth/login`) DEBE (MUST)
exigir Bearer JWT válido; el login DEBE (SHALL) limitarse a 5 intentos/min por IP (429).

#### Scenario: Request sin token
- **GIVEN** un cliente sin Authorization
- **WHEN** llama a `GET /api/vacancies`
- **THEN** recibe 401

### Requirement: Roles jerárquicos
Los roles DEBEN (SHALL) ser jerárquicos `viewer < recruiter < admin`: operaciones del proceso
exigen recruiter+, configuración/observabilidad/usuarios/erasure exigen admin; rol insuficiente
DEBE (MUST) responder 403.

#### Scenario: Viewer intenta cambiar settings
- **GIVEN** un usuario con rol viewer autenticado
- **WHEN** hace `PUT /api/settings/inactivity`
- **THEN** recibe 403 (y el GET correspondiente 200)

### Requirement: Aislamiento por tenant
Ningún recurso DEBE (MUST) servirse cross-tenant: cargar por id un recurso de otro tenant DEBE
(SHALL) responder 404. La invariante DEBE (MUST) estar impuesta por un test estructural que
recorre todas las rutas (un endpoint futuro sin guard rompe CI).

#### Scenario: Tenant ACME consulta una vacante del tenant default
- **GIVEN** un token del tenant ACME
- **WHEN** pide `GET /api/vacancies/{id}` de una vacante del tenant default
- **THEN** recibe 404

### Requirement: Rotación grácil del secreto JWT
El sistema DEBE (SHALL) firmar siempre con `JWT_SECRET` y validar contra el actual más los
retirados (`JWT_SECRET_PREVIOUS`), permitiendo rotar sin cerrar sesiones vivas; la expiración
DEBE (MUST) ser definitiva aunque la firma valide.

#### Scenario: Rotación sin corte
- **GIVEN** sesiones firmadas con el secreto viejo y el secreto nuevo desplegado con previous
- **WHEN** esas sesiones llaman a la API
- **THEN** siguen válidas hasta su expiración natural

### Requirement: Revocación de sesión
Desactivar un usuario DEBE (SHALL) invalidar su acceso: en el login de inmediato y en requests
con token vivo en ≤ 60 s (caché TTL de `users.active`, fail-open ante DB caída). Un admin NO
DEBE (MUST) poder desactivarse ni degradarse a sí mismo.

#### Scenario: Desactivan a un usuario con sesión abierta
- **GIVEN** un usuario activo con token vigente
- **WHEN** un admin lo desactiva
- **THEN** en menos de un minuto sus requests reciben 401

### Requirement: Gate de configuración en producción
Con `ENVIRONMENT=production`, el arranque DEBE (MUST) fallar (RuntimeError) si `JWT_SECRET`,
`JWT_SECRET_PREVIOUS` o `ADMIN_PASSWORD` son default o débiles.

#### Scenario: Deploy con secretos default
- **GIVEN** un pod prod con `JWT_SECRET` vacío
- **WHEN** arranca el backend
- **THEN** el proceso no levanta y el deploy falla visiblemente
