import { FormEvent, useEffect, useRef, useState } from 'react';
import { sendChatStream } from '../api';
import type { FrontendPayload, Message, TimelineItem } from '../types';
import { createAssistantPayload, mergeTimelineItems, thinkingEventToTimelineItem } from '../types';
import ServiceCard from './ServiceCard';
import Sources from './Sources';
import QuickReplies from './QuickReplies';
import { getLoadingStatusText } from './loadingStatus';
import { getAssistantDisplayText } from './assistantDisplay';

interface Props {
  sessionId: string | null;
  setSessionId: (id: string | null) => void;
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  searchInputRef?: React.RefObject<HTMLInputElement | null>;
  pendingSubmit?: string | null;
  onSent?: () => void;
}

function useTypewriter(fullText: string, speed = 35) {
  const [displayed, setDisplayed] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!fullText) {
      setDisplayed('');
      setDone(true);
      return;
    }

    setDisplayed('');
    setDone(false);
    let i = 0;

    const timer = setInterval(() => {
      i++;
      setDisplayed(fullText.slice(0, i));
      if (i >= fullText.length) {
        clearInterval(timer);
        setDone(true);
      }
    }, speed);

    return () => clearInterval(timer);
  }, [fullText, speed]);

  return { displayed, done };
}

function statusClass(status: string) {
  if (['done', 'passed', 'success'].includes(status)) return 'is-done';
  if (status === 'warning') return 'is-warning';
  return 'is-running';
}

function statusIcon(status: string) {
  if (['done', 'passed', 'success'].includes(status)) return '✓';
  if (status === 'warning') return '!';
  return '·';
}

function isCompactTimelineItem(item: TimelineItem) {
  return Boolean(
    item.compact ||
    /context|reuse|reused|复用|共享|上下文|沿用|继续/i.test(`${item.label} ${item.message}`),
  );
}

function TimelinePanel({ items }: { items: TimelineItem[] }) {
  if (!items?.length) return null;

  return (
    <section className="panel timeline-panel">
      <h3>处理步骤</h3>
      <ul className="timeline">
        {items.map((item, idx) => {
          const compact = isCompactTimelineItem(item);
          return (
            <li
              key={`${item.label}-${item.status}-${idx}`}
              className={`${statusClass(item.status)}${compact ? ' timeline-item--compact' : ''}${item.kind === 'thinking' ? ' timeline-item--thinking' : ''}`}
            >
              <span className="tick" aria-hidden="true">{statusIcon(item.status)}</span>
              <span className="timeline-text">
                <strong>{item.label}</strong>
                {item.message ? <span className="timeline-message">{item.message}</span> : null}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default function ChatWindow({
  sessionId,
  setSessionId,
  messages,
  setMessages,
  searchInputRef,
  pendingSubmit,
  onSent,
}: Props) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [liveThinking, setLiveThinking] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (pendingSubmit) {
      sendMessage(pendingSubmit);
      onSent?.();
    }
  }, [pendingSubmit]);

  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].payload) return i;
    }
    return -1;
  })();

  const latestMsg = lastAssistantIdx >= 0 ? messages[lastAssistantIdx] : null;
  const latestPayload = latestMsg?.payload;
  const hasMessages = messages.length > 0;
  const loadingStatusText = getLoadingStatusText(liveThinking);
  const showGlobalLoading = loading && lastAssistantIdx < 0 && !!loadingStatusText;
  const latestFullText = latestPayload
    ? getAssistantDisplayText(latestPayload, { loading, liveThinking })
    : '';

  const { displayed, done: textDone } = useTypewriter(
    lastAssistantIdx >= 0 && latestPayload ? latestFullText : '',
    30,
  );

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
    });
  }, [displayed, messages]);

  async function sendMessage(text: string) {
    text = text.trim();
    if (!text || loading) return;
    setInput('');
    setMessages(prev => [...prev, { role: 'user', text }]);
    setLoading(true);

    try {
      let assistantAdded = false;

      const upsertLatestAssistant = (
        sessionForNewMessage: string,
        updater: (payload: FrontendPayload) => FrontendPayload,
      ) => {
        setMessages(prev => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].role === 'assistant' && updated[i].payload) {
              const currentPayload = updated[i].payload!;
              updated[i] = {
                ...updated[i],
                payload: updater(currentPayload),
              };
              return updated;
            }
          }

          return [
            ...updated,
            {
              role: 'assistant',
              text: '',
              payload: updater(createAssistantPayload(sessionForNewMessage)),
            },
          ];
        });
      };

      await sendChatStream(
        text,
        sessionId,
        data => {
          setSessionId(data.session_id);
          setLiveThinking('');
          if (!assistantAdded) {
            assistantAdded = true;
            setMessages(prev => [...prev, {
              role: 'assistant',
              text: '',
              payload: createAssistantPayload(data.session_id || sessionId || ''),
            }]);
          }
        },
        data => {
          setLiveThinking(data.public_status || data.thinking || '');
          const thinkingItem = thinkingEventToTimelineItem(data);
          if (!assistantAdded) {
            assistantAdded = true;
          }
          upsertLatestAssistant(sessionId || '', payload => ({
            ...payload,
            timeline: mergeTimelineItems(payload.timeline, [thinkingItem]),
          }));
        },
        data => {
          if (!assistantAdded) {
            assistantAdded = true;
          }
          upsertLatestAssistant(sessionId || data.session_id || '', payload => ({
            ...payload,
            timeline: mergeTimelineItems(payload.timeline, data.timeline),
            reasoning_steps: data.reasoning_steps || payload.reasoning_steps || [],
          }));
        },
        data => {
          setLiveThinking('');
          const p = data.payload;
          setSessionId(p.session_id || null);
          setMessages(prev => {
            const updated = [...prev];
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].role === 'assistant') {
                const existingPayload = updated[i].payload;
                updated[i] = {
                  role: 'assistant',
                  text: p.message || '',
                  payload: {
                    ...p,
                    timeline: mergeTimelineItems(existingPayload?.timeline, p.timeline),
                    reasoning_steps: p.reasoning_steps || existingPayload?.reasoning_steps || [],
                  },
                };
                return updated;
              }
            }

            updated.push({
              role: 'assistant',
              text: p.message || '',
              payload: {
                ...p,
                timeline: mergeTimelineItems([], p.timeline),
              },
            });
            return updated;
          });
          setLoading(false);
        },
        data => {
          setMessages(prev => {
            const updated = [...prev];
            let existingPayload: FrontendPayload | undefined;
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].role === 'assistant' && updated[i].payload) {
                existingPayload = updated[i].payload;
                break;
              }
            }

            const errorPayload: FrontendPayload = {
              session_id: data.session_id || sessionId || '',
              type: 'error',
              message: data.message || '服务暂时不可用，请稍后重试。',
              timeline: existingPayload?.timeline || [],
              quick_replies: [],
              card: null,
              actions: [],
              sources: [],
              reasoning_steps: existingPayload?.reasoning_steps || [],
            };

            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].role === 'assistant') {
                updated[i] = { role: 'assistant', text: errorPayload.message, payload: errorPayload };
                return updated;
              }
            }

            updated.push({ role: 'assistant', text: errorPayload.message, payload: errorPayload });
            return updated;
          });
          setLoading(false);
        },
      );
    } catch {
      setMessages(prev => {
        const updated = [...prev];
        let existingPayload: FrontendPayload | undefined;
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].role === 'assistant' && updated[i].payload) {
            existingPayload = updated[i].payload;
            break;
          }
        }

        const errorPayload: FrontendPayload = {
          session_id: sessionId || '',
          type: 'error',
          message: '服务暂时不可用，请稍后重试。',
          timeline: existingPayload?.timeline || [],
          quick_replies: [],
          card: null,
          actions: [],
          sources: [],
          reasoning_steps: existingPayload?.reasoning_steps || [],
        };
        updated.push({ role: 'assistant', text: errorPayload.message, payload: errorPayload });
        return updated;
      });
    } finally {
      setTimeout(() => setLoading(false), 100);
      inputRef.current?.focus();
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    sendMessage(text);
  }

  function handleQuickReply(reply: { label: string; value: string }) {
    sendMessage(reply.label || reply.value);
  }

  function handleRestart() {
    setInput('');
    setLiveThinking('');
    setLoading(false);
    setSessionId(null);
    setMessages([]);
    window.setTimeout(() => (searchInputRef?.current || inputRef.current)?.focus(), 0);
  }

  const isLatest = (idx: number) => idx === lastAssistantIdx && latestPayload;

  return (
    <div className="chat-area">
      {hasMessages && (
        <button className="conversation-reset" type="button" onClick={handleRestart} disabled={loading}>
          重新提问
        </button>
      )}

      <div className="messages" ref={scrollRef}>
        {messages.map((m, idx) => {
          const isLast = isLatest(idx);
          const payload = m.payload;

          const showText = isLast ? displayed : m.text;
          const showReplies = isLast ? textDone : false;
          const showSources = isLast ? textDone : true;
          const hasAssistantContent = !!payload && (
            !!payload.timeline?.length ||
            !!payload.card ||
            !!showText ||
            (showReplies && (payload.type === 'clarification_required' || !!payload.quick_replies?.length)) ||
            (showSources && payload.sources?.length > 0)
          );

          return (
            <article key={idx} className={`message-row ${m.role === 'user' ? 'user-row' : 'assistant-row'}`}>
              {m.role === 'user' ? (
                <div className="question-line">
                  <span>你</span>
                  <p>{m.text}</p>
                </div>
              ) : payload && hasAssistantContent ? (
                <div className="assistant-result">
                  {payload.card && <ServiceCard card={payload.card} actions={payload.actions} />}

                  {payload.timeline?.length > 0 && <TimelinePanel items={payload.timeline} />}

                  {showText && (
                    <section className={`final-answer ${payload.type === 'error' ? 'error-answer' : ''}`}>
                      <p>{showText}{isLast && !textDone && <span className="cursor-blink">|</span>}</p>
                    </section>
                  )}

                  {showReplies && (payload.type === 'clarification_required' || !!payload.quick_replies?.length) && (
                    <QuickReplies
                      replies={payload.quick_replies}
                      onSelect={handleQuickReply}
                      disabled={loading}
                      allowCustom={payload.type !== 'service_card' && payload.type !== 'error'}
                    />
                  )}

                  {showSources && payload.sources?.length > 0 && <Sources sources={payload.sources} />}
                </div>
              ) : payload ? null : (
                <div className="final-answer"><p>{m.text}</p></div>
              )}
            </article>
          );
        })}
        {showGlobalLoading && (
          <div className="loading">
            <span className="loading-dots">{loadingStatusText}</span>
          </div>
        )}
      </div>
    </div>
  );
}
