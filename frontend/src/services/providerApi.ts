import { APP_CONFIG } from "@/config/app";

const api = APP_CONFIG.apiBaseUrl;

// ── Types ──────────────────────────────────────────────────────────

export interface Provider {
  id: string;
  name: string;
  base_url: string;
  api_key: string; // masked on GET, full on POST/PUT
  thinking_mode?: "pair" | "params" | "none";
}

export interface ModelOption {
  id: string;
  display_name: string;
  provider_id: string;
  provider_name?: string;
  is_thinking?: boolean;
  thinking_pair?: string | null;
  thinking_mode: "pair" | "params" | "none";
  thinking_extra_params?: Record<string, unknown> | null;
}

export interface RemoteModel {
  id: string;
  owned_by: string;
}

// ── Provider CRUD ──────────────────────────────────────────────────

export async function fetchProviders(): Promise<Provider[]> {
  const res = await fetch(`${api}/api/providers`);
  if (!res.ok) throw new Error(`fetchProviders failed: ${res.status}`);
  const data = await res.json();
  return data.providers;
}

export async function createProvider(body: {
  name: string;
  base_url: string;
  api_key: string;
}): Promise<Provider> {
  const res = await fetch(`${api}/api/providers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`createProvider failed: ${res.status}`);
  const data = await res.json();
  return data.provider;
}

export async function updateProvider(
  id: string,
  body: { name?: string; base_url?: string; api_key?: string }
): Promise<Provider> {
  const res = await fetch(`${api}/api/providers/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`updateProvider failed: ${res.status}`);
  const data = await res.json();
  return data.provider;
}

export async function deleteProvider(id: string): Promise<void> {
  const res = await fetch(`${api}/api/providers/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`deleteProvider failed: ${res.status}`);
}

// ── Model discovery ────────────────────────────────────────────────

export async function fetchRemoteModels(
  providerId: string
): Promise<RemoteModel[]> {
  const res = await fetch(`${api}/api/providers/${providerId}/fetch-models`, {
    method: "POST",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `fetchRemoteModels failed: ${res.status}`);
  }
  const data = await res.json();
  return data.models;
}

// ── Enabled models ─────────────────────────────────────────────────

export async function fetchEnabledModels(): Promise<{
  models: ModelOption[];
  default_model: string | null;
}> {
  const res = await fetch(`${api}/api/models`);
  if (!res.ok) throw new Error(`fetchEnabledModels failed: ${res.status}`);
  return res.json();
}

export async function updateEnabledModels(
  models: Omit<ModelOption, "provider_name" | "thinking_mode">[]
): Promise<void> {
  const res = await fetch(`${api}/api/models`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled_models: models }),
  });
  if (!res.ok) throw new Error(`updateEnabledModels failed: ${res.status}`);
}
