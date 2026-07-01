// Cliente del backend FastAPI del agente de selección.
import { AuthUser, clearSession, getToken } from "./auth";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export type Semaphore = "green" | "yellow" | "red";

export interface Question {
  id?: string;
  position: number;
  text: string;
  criterion: string;
  weight: number;
  max_follow_ups: number;
}

export interface Vacancy {
  id: string;
  title: string;
  description: string;
  requirements: string;
  intro_message: string;
  details_message: string;
  company_info: string;
  semaphore_thresholds: { green_min: number; yellow_min: number };
  status: string;
  created_at: string;
  candidate_count?: number;
  stage_counts?: Record<string, number>;
  questions?: Question[];
  recruiter_id?: string | null;
  lead_recruiter_id?: string | null;
  manager_recruiter_id?: string | null;
  meeting_duration_minutes?: number;
  recruiter?: Recruiter | null;
  lead_recruiter?: Recruiter | null;
  manager_recruiter?: Recruiter | null;
  // Deep-link del bot (t.me/<bot>?start=<vacancy_id>) — vacío si el username no está configurado.
  telegram_deep_link?: string;
  // Datos del aviso (migración 0012).
  area?: string;
  modality?: string;
  location?: string;
  salary_min?: number | null;
  salary_max?: number | null;
  benefits?: string[];
  portals?: string[];
  auto_agent?: boolean;
}

export interface Recruiter {
  id: string;
  name: string;
  email: string;
  company: string;
  role: string;
  phone: string;
  telegram_chat_id: string;
  calendar_id: string;
  location?: string;
  active: boolean;
  created_at?: string;
  open_vacancies?: number;
  active_candidates?: number;
}

export type MeetingStage = "hr" | "lead" | "manager";

export interface Meeting {
  id: string;
  stage: MeetingStage;
  modality: "virtual" | "onsite";
  location: string;
  attendance: "" | "attended" | "no_show";
  scheduled_at: string;
  end_at: string | null;
  meet_link: string;
  candidate_email: string;
  candidate_phone: string;
  recruiter_email: string;
  recruiter_phone: string;
  recruiter_name: string;
  status: string;
}

export interface StageFeedback {
  id: string;
  stage: MeetingStage;
  feedback: string;
  decision: "approved" | "rejected" | "";
  decided_email: string;
  created_at: string;
}

export interface PsychExam {
  link: string;
  code: string;
  key: string;
  sent_at: string;
  sent_by: string;
}

export interface SchedulingConfig {
  enabled: boolean;
  provider: string;
  slot_minutes: number;
  work_days: number[];
  work_start: string;
  work_end: string;
  work_windows: string[][];
  timezone: string;
  horizon_days: number;
  options: number;
}

export type Verdict = "pass" | "borderline" | "reject";

export interface CandidateRow {
  id: string;
  name: string;
  status: string;
  channel: string;
  source: string;
  created_at: string;
  conversation_id: string | null;
  vacancy_id?: string;
  vacancy_title?: string;
  semaphore: Semaphore | null;
  total_score: number | null;
  prescreen_score: number | null;
  prescreen_verdict: Verdict | null;
}

// Página de candidatos (U1): items + total para armar los controles de paginación.
export interface CandidatePage {
  items: CandidateRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface ListOpts {
  q?: string;
  limit?: number;
  offset?: number;
}

function listQuery(opts?: ListOpts): string {
  const p = new URLSearchParams();
  if (opts?.q) p.set("q", opts.q);
  if (opts?.limit != null) p.set("limit", String(opts.limit));
  if (opts?.offset != null) p.set("offset", String(opts.offset));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export interface CvProfile {
  name?: string;
  email?: string;
  phone?: string;
  headline?: string;
  education?: { level?: string; career?: string };
  years_experience?: number | string;
  skills?: string[];
  location?: string;
  salary_expectation?: string;
  raw_cv_text?: string;
}

export interface PrescreenReq {
  requirement: string;
  met: boolean;
  note: string;
}

export interface Prescreen {
  pre_score?: number;
  verdict?: Verdict;
  summary?: string;
  per_requirement?: PrescreenReq[];
}

export interface Doc {
  type: string;
  filename: string;
  file_id?: string;
  received_at?: string;
}

export interface SyncReport {
  imported: number;
  passed: number;
  rejected: number;
  contacted: number;
}

export interface Metrics {
  funnel: Record<string, number>;
  tokens: { total: number; input: number; output: number; by_stage: Record<string, number> };
  est_cost: number;
}

export interface PerCriterion {
  question: string;
  label?: string;
  criterion: string;
  score: number | null;
  weight: number;
  justification: string;
  low_confidence?: boolean;
}

export interface Scorecard {
  total_score: number;
  semaphore: Semaphore;
  summary: string;
  recommendation: string;
  per_criterion: PerCriterion[];
  review_required?: boolean;
}

export interface Message {
  role: string;
  content: string;
  created_at: string;
}

export interface CandidateDetail {
  candidate: {
    id: string;
    name: string;
    status: string;
    channel: string;
    source?: string;
    cv_profile?: CvProfile;
    prescreen?: Prescreen;
    documents?: Doc[];
  };
  vacancy: { id: string; title: string } | null;
  thresholds?: { green_min: number; yellow_min: number };
  scorecard: Scorecard | null;
  transcript: Message[];
  meetings?: Meeting[];
  stage_feedback?: StageFeedback[];
  psych_exam?: PsychExam | null;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const { headers: initHeaders, ...rest } = init || {};
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...((initHeaders as Record<string, string>) || {}),
    },
  });
  // Sesión expirada o ausente: limpiar y mandar al login (salvo que ya estemos ahí).
  if (res.status === 401) {
    clearSession();
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("401 no autenticado");
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

// Login: no pasa por req() (aún no hay token y no queremos el redirect del 401).
async function loginRequest(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error("Credenciales inválidas");
  return res.json() as Promise<LoginResponse>;
}

export const api = {
  login: (email: string, password: string) => loginRequest(email, password),
  me: () => req<AuthUser>("/api/auth/me"),
  listVacancies: () => req<Vacancy[]>("/api/vacancies"),
  getVacancy: (id: string) => req<Vacancy>(`/api/vacancies/${id}`),
  createVacancy: (body: Partial<Vacancy> & { questions?: Question[] }) =>
    req<Vacancy>("/api/vacancies", { method: "POST", body: JSON.stringify(body) }),
  listCandidates: (vacancyId: string, opts?: ListOpts) =>
    req<CandidatePage>(`/api/vacancies/${vacancyId}/candidates${listQuery(opts)}`),
  listAllCandidates: (opts?: ListOpts) => req<CandidatePage>(`/api/candidates${listQuery(opts)}`),
  getCandidate: (id: string) => req<CandidateDetail>(`/api/candidates/${id}`),
  decide: (id: string, decision: "advance" | "reject") =>
    req<{
      status: string;
      notified?: boolean;
      scheduling_started?: boolean;
      messages_sent?: boolean;
      messages?: string[];
    }>(`/api/candidates/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  getMeeting: (id: string) => req<Meeting | null>(`/api/candidates/${id}/meeting`),
  listMeetings: (id: string) => req<Meeting[]>(`/api/candidates/${id}/meetings`),
  sendPsychExam: (id: string, body: { link: string; code?: string; key?: string }) =>
    req<{ sent: boolean; psych_exam: PsychExam }>(`/api/candidates/${id}/psych-exam`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  markAttendance: (
    id: string,
    body: { stage: MeetingStage; attended: "attended" | "no_show"; reschedule?: boolean },
  ) =>
    req<{ attendance: string; status?: string; rescheduled?: boolean; notified?: boolean; messages?: string[] }>(
      `/api/candidates/${id}/attendance`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  advanceStage: (
    id: string,
    body: { stage: MeetingStage; decision: "approved" | "rejected"; feedback?: string; modality?: "virtual" | "onsite" },
  ) =>
    req<{
      status: string;
      notified?: boolean;
      scheduling_started?: boolean;
      messages_sent?: boolean;
      messages?: string[];
    }>(`/api/candidates/${id}/advance-stage`, { method: "POST", body: JSON.stringify(body) }),
  listRecruiters: () => req<Recruiter[]>("/api/recruiters"),
  createRecruiter: (body: Partial<Recruiter>) =>
    req<Recruiter>("/api/recruiters", { method: "POST", body: JSON.stringify(body) }),
  updateRecruiter: (id: string, body: Partial<Recruiter>) =>
    req<Recruiter>(`/api/recruiters/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  getScheduling: () => req<SchedulingConfig>("/api/settings/scheduling"),
  setScheduling: (body: SchedulingConfig) =>
    req<SchedulingConfig>("/api/settings/scheduling", { method: "PUT", body: JSON.stringify(body) }),
  contactCandidate: (id: string) =>
    req<{ contacted: boolean; note: string; status: string }>(
      `/api/candidates/${id}/contact`,
      { method: "POST" },
    ),
  syncApplicants: (vacancyId: string) =>
    req<SyncReport>(`/api/vacancies/${vacancyId}/sync-applicants`, { method: "POST" }),
  getVacancyMetrics: (vacancyId: string) => req<Metrics>(`/api/vacancies/${vacancyId}/metrics`),
  getMetrics: () => req<Metrics>("/api/metrics"),
  documentUrl: (candidateId: string, type: string) =>
    `${BASE}/api/candidates/${candidateId}/documents/${type}`,
  getAutoContact: () => req<AutoContactConfig>("/api/settings/auto-contact"),
  setAutoContact: (body: AutoContactConfig) =>
    req<AutoContactConfig>("/api/settings/auto-contact", { method: "PUT", body: JSON.stringify(body) }),
  getInactivity: () => req<InactivityConfig>("/api/settings/inactivity"),
  setInactivity: (body: InactivityConfig) =>
    req<InactivityConfig>("/api/settings/inactivity", { method: "PUT", body: JSON.stringify(body) }),
  // Observabilidad (solo admin).
  getAudit: () => req<AuditEntry[]>("/api/audit"),
  getOutbox: () => req<OutboxHealth>("/api/outbox"),
  retryOutbox: (id: string) =>
    req<{ requeued: boolean }>(`/api/outbox/${id}/retry`, { method: "POST" }),
  eraseCandidate: (id: string) =>
    req<{ deleted: boolean }>(`/api/candidates/${id}`, { method: "DELETE" }),
};

export interface AuditEntry {
  id: string;
  actor_email: string;
  action: string;
  entity_type: string;
  entity_id: string;
  summary: string;
  created_at: string;
}

export interface OutboxItem {
  id: string;
  kind: string;
  status: "pending" | "failed" | "sent" | string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string;
  last_error: string;
  candidate_id: string | null;
  created_at: string;
}

export interface OutboxHealth {
  counts: Record<string, number>;
  items: OutboxItem[];
}

export interface AutoContactConfig {
  enabled: boolean;
  times: string[];
  timezone: string;
}

export interface InactivityConfig {
  enabled: boolean;
  reminder_minutes: number;
  max_reminders: number;
}

export const semaphoreMeta: Record<Semaphore, { emoji: string; label: string; color: string }> = {
  green: { emoji: "🟢", label: "Avanza", color: "#16a34a" },
  yellow: { emoji: "🟡", label: "Revisar", color: "#d97706" },
  red: { emoji: "🔴", label: "No avanza", color: "#dc2626" },
};

export const statusLabel: Record<string, string> = {
  pending: "Pendiente",
  sourced: "Importado",
  prescreen_passed: "Apto · por contactar",
  prescreen_rejected: "Descartado en CV",
  invited: "Contactado",
  consented: "Aceptó",
  interviewing: "En entrevista",
  finished: "Entrevista completa",
  scheduling: "Coordinando con RR.HH.",
  scheduled: "Entrevista RR.HH. agendada",
  lead_scheduling: "Coordinando con líder",
  lead_scheduled: "Entrevista con líder agendada",
  mgr_scheduling: "Coordinando con gerencia",
  mgr_scheduled: "Entrevista con gerencia agendada",
  hired: "Contratado",
  advanced: "Avanzado",
  rejected: "Rechazado",
  no_show: "No asistió",
  declined: "Declinó",
  no_response: "No respondió",
};

export const verdictMeta: Record<Verdict, { label: string; color: string }> = {
  pass: { label: "Apto", color: "#16a34a" },
  borderline: { label: "Dudoso", color: "#d97706" },
  reject: { label: "Descartado", color: "#dc2626" },
};

// Fase del proceso: etiqueta + color para badge y stepper. `step` ordena el stepper
// (los off-path: descartado/declinó usan step -1).
export const phaseMeta: Record<string, { label: string; color: string; step: number }> = {
  sourced: { label: "Importado", color: "#8b95a1", step: 0 },
  prescreen_passed: { label: "Apto · por contactar", color: "#0e9d8e", step: 1 },
  invited: { label: "Contactado", color: "#2563eb", step: 2 },
  consented: { label: "Aceptó", color: "#2563eb", step: 2 },
  interviewing: { label: "En entrevista", color: "#d97706", step: 3 },
  finished: { label: "Entrevista completa", color: "#16a34a", step: 4 },
  scheduling: { label: "Coordinando con RR.HH.", color: "#d97706", step: 5 },
  scheduled: { label: "Entrevista RR.HH. agendada", color: "#16a34a", step: 5 },
  advanced: { label: "Avanzado", color: "#16a34a", step: 5 },
  lead_scheduling: { label: "Coordinando con líder", color: "#d97706", step: 6 },
  lead_scheduled: { label: "Entrevista con líder agendada", color: "#16a34a", step: 6 },
  mgr_scheduling: { label: "Coordinando con gerencia", color: "#d97706", step: 7 },
  mgr_scheduled: { label: "Entrevista con gerencia agendada", color: "#16a34a", step: 7 },
  hired: { label: "Contratado", color: "#16a34a", step: 8 },
  rejected: { label: "Rechazado", color: "#dc2626", step: -1 },
  no_show: { label: "No asistió", color: "#dc2626", step: -1 },
  prescreen_rejected: { label: "Descartado en CV", color: "#dc2626", step: -1 },
  declined: { label: "Declinó", color: "#dc2626", step: -1 },
  no_response: { label: "No respondió", color: "#dc2626", step: -1 },
  pending: { label: "Pendiente", color: "#8b95a1", step: 0 },
};

// Etapas del stepper (en orden), para el detalle del candidato.
export const PHASE_STEPS: { key: string; label: string }[] = [
  { key: "sourced", label: "Importado" },
  { key: "prescreen_passed", label: "Apto" },
  { key: "invited", label: "Contactado" },
  { key: "interviewing", label: "Entrevista" },
  { key: "finished", label: "Evaluado" },
  { key: "scheduled", label: "RR.HH." },
  { key: "lead_scheduled", label: "Líder" },
  { key: "mgr_scheduled", label: "Gerencia" },
  { key: "hired", label: "Decisión" },
];
