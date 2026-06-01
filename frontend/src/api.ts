import type { FrontendPayload, SseNodeUpdate, SseComplete, SseError, SseStart, SseThinking } from './types';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000';
const REQUEST_TIMEOUT_MS = 180_000;

async function postJson(path: string, body: unknown): Promise<FrontendPayload> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: controller.signal,
  });
  window.clearTimeout(timer);
  if (!res.ok) {
    return {
      session_id: '',
      type: 'error',
      message: '服务暂时不可用，请稍后重试。',
      timeline: [],
      quick_replies: [],
      card: null,
      actions: [],
      sources: [],
      reasoning_steps: [],
    };
  }
  return res.json();
}

export function sendChat(message: string, sessionId?: string | null) {
  return postJson('/chat', { session_id: sessionId || undefined, message });
}

export function chooseReply(sessionId: string, reply: string) {
  return postJson('/choose', { session_id: sessionId, reply });
}

export async function sendChatStream(
  message: string,
  sessionId?: string | null,
  onStart?: (data: SseStart) => void,
  onThinking?: (data: SseThinking) => void,
  onNodeUpdate?: (data: SseNodeUpdate) => void,
  onComplete?: (data: SseComplete) => void,
  onError?: (data: SseError) => void,
): Promise<void> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const res = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId || undefined, message }),
      signal: controller.signal,
    });

    if (!res.ok || !res.body) {
      onError?.({ session_id: sessionId || '', message: '服务暂时不可用，请稍后重试。' });
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            if (currentEvent === 'start') {
              onStart?.(data as SseStart);
            } else if (currentEvent === 'thinking') {
              onThinking?.(data as SseThinking);
            } else if (currentEvent === 'node_update') {
              onNodeUpdate?.(data as SseNodeUpdate);
            } else if (currentEvent === 'complete') {
              onComplete?.(data as SseComplete);
            } else if (currentEvent === 'error') {
              onError?.(data as SseError);
            }
          } catch {
            // ignore malformed chunks
          }
          currentEvent = '';
        }
      }
    }
  } catch (err) {
    const message = err instanceof DOMException && err.name === 'AbortError'
      ? '服务响应超时，请稍后重试。'
      : '服务暂时不可用，请稍后重试。';
    onError?.({ session_id: sessionId || '', message });
  } finally {
    window.clearTimeout(timer);
  }
}
