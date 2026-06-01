import { FormEvent, useEffect, useRef, useState } from 'react';
import { sendChatStream } from '../api';
import type { FrontendPayload, Message } from '../types';
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

// ── Typewriter hook: progressively reveals text ──
function useTypewriter(fullText: string, speed = 35) {
  const [displayed, setDisplayed] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!fullText) { setDisplayed(''); setDone(true); return; }
    setDisplayed('');
    setDone(false);
    let i = 0;
    // Handle CJK: each char IS a token, so 35ms per char gives ~28 chars/s
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

export default function ChatWindow({ sessionId, setSessionId, messages, setMessages, searchInputRef, pendingSubmit, onSent }: Props) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [liveThinking, setLiveThinking] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // When parent passes a pendingSubmit, auto-send it via SSE
  useEffect(() => {
    if (pendingSubmit) {
      sendMessage(pendingSubmit);
      onSent?.();
    }
  }, [pendingSubmit]);

  // Get the latest assistant message index for streaming
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
    30
  );

  // Auto-scroll to bottom
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
      // All messages go through SSE streaming
      let assistantAdded = false;

      await sendChatStream(
        text,
        sessionId,
        // onStart
        (data) => {
          setSessionId(data.session_id);
          setLiveThinking(''); // reset thinking on new query
          if (!assistantAdded) {
            assistantAdded = true;
            setMessages(prev => [...prev, {
              role: 'assistant',
              text: '',
              payload: {
                session_id: data.session_id || sessionId || '',
                type: 'unknown',
                message: '',
                timeline: [],
                quick_replies: [],
                card: null,
                actions: [],
                sources: [],
                reasoning_steps: [],
              },
            }]);
          }
        },
        // onThinking (实时 thinking 步骤)
        (data) => {
          setLiveThinking(data.thinking || '');
          // 确保 assistant 消息已添加
          if (!assistantAdded) {
            assistantAdded = true;
            setMessages(prev => [...prev, {
              role: 'assistant',
              text: '',
              payload: {
                session_id: sessionId || '',
                type: 'unknown',
                message: '',
                timeline: [],
                quick_replies: [],
                card: null,
                actions: [],
                sources: [],
                reasoning_steps: [],
              },
            }]);
          }
        },
        // onNodeUpdate
        (data) => {
          if (!assistantAdded) {
            assistantAdded = true;
            // Add placeholder assistant message
            setMessages(prev => [...prev, {
              role: 'assistant',
              text: '',
              payload: {
                session_id: sessionId || data.session_id || '',
                type: 'unknown',
                message: '',
                timeline: data.timeline,
                quick_replies: [],
                card: null,
                actions: [],
                sources: [],
              },
            }]);
          } else {
            // Update timeline on the latest assistant message
            setMessages(prev => {
              const updated = [...prev];
              for (let i = updated.length - 1; i >= 0; i--) {
                if (updated[i].role === 'assistant' && updated[i].payload) {
                  updated[i] = {
                    ...updated[i],
                    payload: {
                      ...updated[i].payload!,
                      timeline: data.timeline,
                      reasoning_steps: data.reasoning_steps || updated[i].payload?.reasoning_steps || [],
                    },
                  };
                  break;
                }
              }
              return updated;
            });
          }
        },
        // onComplete
        (data) => {
          setLiveThinking(''); // 清除实时 thinking
          const p = data.payload;
          setSessionId(p.session_id || null);
          setMessages(prev => {
            const updated = [...prev];
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].role === 'assistant') {
                const existingPayload = updated[i].payload;
                // Preserve timeline accumulated from node_update,
                // merge in new fields from final payload
                const mergedTimeline = (existingPayload?.timeline?.length ?? 0) > 0
                  ? existingPayload!.timeline
                  : p.timeline;
                updated[i] = {
                  role: 'assistant',
                  text: p.message || '',
                  payload: {
                    ...p,
                    timeline: mergedTimeline,
                    reasoning_steps: p.reasoning_steps || existingPayload?.reasoning_steps || [],
                  },
                };
                return updated;
              }
            }
            // No assistant message found, add one
            updated.push({ role: 'assistant', text: p.message || '', payload: p });
            return updated;
          });
          setLoading(false);
        },
        // onError
        (data) => {
          const errorPayload: FrontendPayload = {
            session_id: data.session_id || sessionId || '',
            type: 'error',
            message: data.message || '服务暂时不可用，请稍后重试。',
            timeline: [],
            quick_replies: [],
            card: null,
            actions: [],
            sources: [],
            reasoning_steps: [],
          };
          setMessages(prev => {
            const updated = [...prev];
            if (assistantAdded) {
              for (let i = updated.length - 1; i >= 0; i--) {
                if (updated[i].role === 'assistant') {
                  updated[i] = { role: 'assistant', text: errorPayload.message, payload: errorPayload };
                  return updated;
                }
              }
            }
            updated.push({ role: 'assistant', text: errorPayload.message, payload: errorPayload });
            return updated;
          });
          setLoading(false);
        },
      );
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant',
        text: '服务暂时不可用，请稍后重试。',
        payload: {
          session_id: sessionId || '',
          type: 'error',
          message: '服务暂时不可用，请稍后重试。',
          timeline: [],
          quick_replies: [],
          card: null,
          actions: [],
          sources: [],
          reasoning_steps: [],
        },
      }]);
    } finally {
      // onComplete/onError already set loading=false, but guard against edge cases
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
          ← 重新提问
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
            !!payload.card ||
            !!showText ||
            (showReplies && (payload.type === 'clarification_required' || !!payload.quick_replies?.length)) ||
            (showSources && payload.sources?.length > 0)
          );

          return (
            <article key={idx} className={`message-row ${m.role === 'user' ? 'user-row' : 'assistant-row'}`}>
              {m.role === 'user' ? (
                <div className="question-line">
                  <span>您</span>
                  <p>{m.text}</p>
                </div>
              ) : payload && hasAssistantContent ? (
                <div className="assistant-result">
                  {/* 办事卡片优先展示 */}
                  {payload.card && <ServiceCard card={payload.card} actions={payload.actions} />}

                  {/* LLM 回复 */}
                  {showText && (
                    <section className={`final-answer ${payload.type === 'error' ? 'error-answer' : ''}`}>
                      <p>{showText}{isLast && !textDone && <span className="cursor-blink">|</span>}</p>
                    </section>
                  )}

                  {/* 追问选项（clarification_required 时，选项即为问题，不再显示额外文本） */}
                  {showReplies && (payload.type === 'clarification_required' || !!payload.quick_replies?.length) && (
                    <QuickReplies
                      replies={payload.quick_replies}
                      onSelect={handleQuickReply}
                      disabled={loading}
                      allowCustom={payload.type !== 'service_card' && payload.type !== 'error'}
                    />
                  )}

                  {/* 来源信息 */}
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
