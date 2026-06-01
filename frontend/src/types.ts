export type PayloadType = 'clarification_required' | 'service_card' | 'no_verified_guide' | 'unsupported' | 'guidance_fallback' | 'agent_task_guidance' | 'error' | 'unknown';

export interface TimelineItem { label: string; status: string; message: string }
export interface ReasoningStep { label: string; summary: string }
export interface QuickReply { label: string; value: string; slot?: string | null; context?: Record<string, unknown> }
export interface Source { title: string; name: string; url: string }
export interface Action { type: string; label: string; url?: string | null; text?: string | null }
export interface ServiceCard {
  title?: string;
  summary?: string;
  service_item_code?: string;
  service_item_name?: string;
  conditions?: string[];
  materials?: string[];
  methods?: string[];
  steps?: string[];
  online_entry?: { name?: string; url?: string }[];
  offline_locations?: string[];
  processing_time?: string;
  fees?: string;
  tips?: string[];
  sources?: Source[];
  freshness_warning?: string;
  disclaimer?: string;
}
export interface TaskState {
  topic: string;
  domain: string;
  goal: string;
  city: string | null;
  identity_status: string | null;
  subitem: string | null;
  confirmed: Record<string, string>;
  missing_slots: string[];
  verified_guide_key: string | null;
  verified_guide_status: 'not_found' | 'found' | 'searching' | 'unverified';
  stage: string;
  sources: Source[];
}
export interface FrontendPayload {
  session_id: string;
  type: PayloadType | string;
  message: string;
  timeline: TimelineItem[];
  quick_replies: QuickReply[];
  card: ServiceCard | null;
  actions: Action[];
  sources: Source[];
  task_state?: TaskState;
  reasoning_steps?: ReasoningStep[];
}
export interface Message {
  role: 'user' | 'assistant';
  text: string;
  payload?: FrontendPayload;
}

// ── SSE event types ──
export interface SseNodeUpdate {
  session_id?: string;
  node_name: string;
  timeline: TimelineItem[];
  reasoning_steps?: ReasoningStep[];
}

export interface SseComplete {
  payload: FrontendPayload;
}

export interface SseError {
  session_id: string;
  message: string;
}

export interface SseStart {
  session_id: string;
}

export interface SseThinking {
  node: string;
  thinking: string;
  status: string;
}
