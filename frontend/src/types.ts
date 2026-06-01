export type PayloadType =
  | 'clarification_required'
  | 'service_card'
  | 'no_verified_guide'
  | 'unsupported'
  | 'guidance_fallback'
  | 'agent_task_guidance'
  | 'error'
  | 'unknown';

export type TimelineItemKind = 'thinking' | 'public';

export interface TimelineItem {
  label: string;
  status: string;
  message: string;
  kind?: TimelineItemKind;
  compact?: boolean;
}

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

// SSE event types
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
  public_status?: string;
}

export function createAssistantPayload(sessionId: string, overrides: Partial<FrontendPayload> = {}): FrontendPayload {
  return {
    session_id: sessionId,
    type: 'unknown',
    message: '',
    timeline: [],
    quick_replies: [],
    card: null,
    actions: [],
    sources: [],
    reasoning_steps: [],
    ...overrides,
  };
}

export function timelineItemKey(item: TimelineItem): string {
  return `${item.label}\u0000${item.status}\u0000${item.message}`;
}

export function normalizeTimelineItem(item: TimelineItem, kind?: TimelineItemKind): TimelineItem {
  const label = item.label?.trim() || '处理步骤';
  const status = item.status?.trim() || 'done';
  const message = item.message?.trim() || '';
  const timelineKind = item.kind || kind;
  const compactHint = /context|reuse|reused|复用|共享|上下文|沿用|继续/i.test(`${label} ${message}`);

  return {
    ...item,
    label,
    status,
    message,
    ...(timelineKind ? { kind: timelineKind } : {}),
    compact: item.compact ?? (compactHint || timelineKind === 'thinking'),
  };
}

export function mergeTimelineItems(existing: TimelineItem[] = [], incoming: TimelineItem[] = []): TimelineItem[] {
  const merged = existing.map(item => normalizeTimelineItem(item, item.kind || 'public'));

  for (const item of incoming) {
    const normalized = normalizeTimelineItem(item, item.kind || 'public');
    const duplicate = merged.some(existingItem => (
      existingItem.kind === normalized.kind &&
      timelineItemKey(existingItem) === timelineItemKey(normalized)
    ));
    if (!duplicate) {
      merged.push(normalized);
    }
  }

  return merged;
}

export function thinkingEventToTimelineItem(event: SseThinking): TimelineItem {
  const publicStatus = event.public_status?.trim() || event.thinking?.trim() || '';
  const label = event.node?.trim() || '思考中';
  const status = event.status?.trim() || (publicStatus ? 'running' : 'unknown');
  return normalizeTimelineItem({
    label,
    status,
    message: publicStatus,
    kind: 'thinking',
  }, 'thinking');
}
