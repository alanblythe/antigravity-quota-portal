import {
  AppConfig,
  AuditEvent,
  EvaluationResult,
  KPIStats,
  ModelPricing,
  User,
  UserUpdatePayload,
} from '../types';

const API_BASE = '/api';

export async function fetchUsers(params?: {
  search?: string;
  status?: string;
  exempt_only?: boolean;
  sort_by?: string;
  sort_order?: string;
}): Promise<User[]> {
  const url = new URL(`${window.location.origin}${API_BASE}/users`);
  if (params?.search) url.searchParams.set('search', params.search);
  if (params?.status) url.searchParams.set('status', params.status);
  if (params?.exempt_only !== undefined) url.searchParams.set('exempt_only', String(params.exempt_only));
  if (params?.sort_by) url.searchParams.set('sort_by', params.sort_by);
  if (params?.sort_order) url.searchParams.set('sort_order', params.sort_order);

  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`Failed to fetch users: ${res.statusText}`);
  return res.json();
}

export async function fetchUser(email: string): Promise<User> {
  const res = await fetch(`${API_BASE}/users/${encodeURIComponent(email)}`);
  if (!res.ok) throw new Error(`Failed to fetch user: ${res.statusText}`);
  return res.json();
}

export async function updateUser(email: string, payload: UserUpdatePayload): Promise<User> {
  const res = await fetch(`${API_BASE}/users/${encodeURIComponent(email)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Failed to update user: ${res.statusText}`);
  return res.json();
}

export async function toggleUserLock(email: string): Promise<User> {
  const res = await fetch(`${API_BASE}/users/${encodeURIComponent(email)}/lock`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to toggle user lock: ${res.statusText}`);
  return res.json();
}

export async function createUser(payload: {
  email: string;
  is_exempt?: boolean;
  has_custom_quota?: boolean;
  custom_quota_usd?: number | null;
  custom_overage_usd?: number | null;
}): Promise<User> {
  const res = await fetch(`${API_BASE}/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to create user: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchConfig(): Promise<AppConfig> {
  const res = await fetch(`${API_BASE}/config`);
  if (!res.ok) throw new Error(`Failed to fetch config: ${res.statusText}`);
  return res.json();
}

export async function updateConfig(payload: Partial<AppConfig>): Promise<AppConfig> {
  const res = await fetch(`${API_BASE}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Failed to update config: ${res.statusText}`);
  return res.json();
}

export async function fetchPricing(): Promise<Record<string, ModelPricing>> {
  const res = await fetch(`${API_BASE}/config/pricing`);
  if (!res.ok) throw new Error(`Failed to fetch pricing: ${res.statusText}`);
  return res.json();
}

export async function updatePricing(matrix: Record<string, ModelPricing>): Promise<Record<string, ModelPricing>> {
  const res = await fetch(`${API_BASE}/config/pricing`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(matrix),
  });
  if (!res.ok) throw new Error(`Failed to update pricing: ${res.statusText}`);
  return res.json();
}

export async function fetchKPIs(): Promise<KPIStats> {
  const res = await fetch(`${API_BASE}/evaluator/kpis`);
  if (!res.ok) throw new Error(`Failed to fetch KPIs: ${res.statusText}`);
  return res.json();
}

export async function publishAndSync(): Promise<EvaluationResult> {
  const res = await fetch(`${API_BASE}/evaluator/publish`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`Failed to publish and sync: ${res.statusText}`);
  return res.json();
}

export async function fetchAuditEvents(limit: number = 50, action?: string, user?: string): Promise<AuditEvent[]> {
  const url = new URL(`${window.location.origin}${API_BASE}/audit`);
  url.searchParams.set('limit', String(limit));
  if (action) url.searchParams.set('action', action);
  if (user) url.searchParams.set('user', user);

  const res = await fetch(url.toString());
  if (!res.ok) throw new Error(`Failed to fetch audit events: ${res.statusText}`);
  return res.json();
}
