# Secret manager (External Secrets Operator) — roadmap v2 · paso 4

Migración del `secret.yaml` **plano** a un **gestor de secretos** externo, para cerrar el
residuo "secretos planos" del Riesgo 2. Es un paso **opcional y config-gated**: dev sigue
usando `despliegue/k8s/secret.example.yaml` (Secret plano), y prod puede empezar plano y migrar
aquí cuando haya cuenta del gestor. El runbook completo de rotación está en
[`docs/gestion_secretos.md`](../../../docs/gestion_secretos.md).

## Qué resuelve

- **Rotación sin tocar el cluster:** rotás en el gestor → ESO re-sincroniza (`refreshInterval`)
  → el Secret `agente-rh-secrets` se actualiza solo. No hay YAML de secretos que editar ni
  commitear (ni siquiera cifrado).
- **Un solo origen de verdad** para los secretos, auditado por el gestor (quién leyó/rotó qué).
- **Sin cambios en la app ni en los deployments:** ESO materializa el MISMO Secret
  `agente-rh-secrets` que hoy consumen `backend`/`frontend` vía `envFrom.secretRef`.

## Por qué NO está en kustomize / CI

`SecretStore` y `ExternalSecret` son **CRDs** que el operador instala; `kubeconform` (el gate
del CI) no conoce su esquema y fallaría. Y el operador se instala una vez por cluster, fuera
del kustomize de la app. Por eso viven aquí como plantilla **"rellená y aplicá"**, no en los
overlays.

## Pasos

1. **Instalar el operador** (una vez por cluster):
   ```sh
   helm repo add external-secrets https://charts.external-secrets.io
   helm install external-secrets external-secrets/external-secrets \
     -n external-secrets --create-namespace
   ```
2. **Cargar los secretos en el gestor** con los MISMOS nombres que las env vars del backend
   (ver la lista en `externalsecret.example.yaml` y en `despliegue/k8s/secret.example.yaml`).
3. **Crear el credencial de acceso** del gestor como un Secret k8s mínimo (ejemplo Doppler):
   ```sh
   kubectl -n agente-rh-prod create secret generic doppler-token \
     --from-literal=dopplerToken='dp.st.prod.xxxxx'
   ```
4. **Aplicar la plantilla** al namespace del entorno:
   ```sh
   kubectl apply -f externalsecret.example.yaml   # ya trae namespace agente-rh-prod
   ```
5. **Verificar** que ESO materializó el Secret:
   ```sh
   kubectl -n agente-rh-prod get externalsecret agente-rh-secrets   # STATUS=SecretSynced
   kubectl -n agente-rh-prod get secret agente-rh-secrets
   ```
   Con el Secret sincronizado, **dejá de aplicar** el `secret.yaml` plano en prod.

## Cambiar de proveedor

El `ExternalSecret` no cambia: solo se reemplaza el bloque `provider` del `SecretStore`.

| Proveedor | Bloque `provider` | Credencial |
|---|---|---|
| **Doppler** | `doppler: { auth.secretRef.dopplerToken }` | token de servicio `dp.st.*` |
| **HashiCorp Vault** | `vault: { server, path, version, auth.kubernetes }` | rol de Kubernetes auth |
| **AWS Secrets Manager** | `aws: { service: SecretsManager, region, auth.jwt (IRSA) }` | IRSA / IAM role |
| **GCP Secret Manager** | `gcpsm: { projectID, auth.workloadIdentity }` | Workload Identity |
| **Supabase Vault** | vía Vault/`webhook` provider o `dataFrom` a la API | service key |

Snippets oficiales por proveedor: <https://external-secrets.io/latest/provider/> (documentar
la URL, no memorizarla — el esquema del provider cambia entre versiones del operador).

## Rotación de emergencia

Si se filtró un secreto: rotalo en el gestor y bajá `refreshInterval` (o forzá
`kubectl -n agente-rh-prod annotate externalsecret agente-rh-secrets force-sync=$(date +%s) --overwrite`)
para que ESO propague de inmediato. Para el JWT, además, la rotación grácil vía
`JWT_SECRET_PREVIOUS` (ver `docs/gestion_secretos.md`) evita cerrar sesiones vivas.
