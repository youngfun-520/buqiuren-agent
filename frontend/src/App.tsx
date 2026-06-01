import { useState, useRef } from 'react';
import ChatWindow from './components/ChatWindow';
import type { Message } from './types';

const FAQ_ITEMS = [
  { label: '深圳居住证怎么办？', value: '深圳居住证怎么办' },
  { label: '公积金怎么提取？', value: '公积金怎么提取' },
  { label: '公司拖欠工资怎么办？', value: '公司拖欠工资怎么办' },
];

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [pendingSubmit, setPendingSubmit] = useState<string | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const hasMessages = messages.length > 0;

  function handleFAQClick(value: string) {
    handleSend(value);
  }

  function handleSend(value: string) {
    const trimmed = value.trim();
    if (!trimmed) return;
    setPendingSubmit(trimmed);
    setInputValue('');
  }

  return (
    <main className="page-shell" aria-label="不求人办事流程查询">
      {/* ── Fixed decorative layer (outside hero to avoid height confusion) ── */}
      <div className="decor-layer" aria-hidden="true">
        <div className="decor decor--dots" />
        <div className="decor decor--orb" />
        <div className="decor decor--arc-one" />
        <div className="decor decor--arc-two" />
      </div>

      <section className="hero" aria-labelledby="main-title">
        {/* ── Brand ── */}
        <header className="brand">
          <span className="brand__logo-frame" aria-hidden="true">
            <span className="brand__logo-bamboo" />
            <img className="brand__logo" src="/assets/buqiuren-monk-logo.png" alt="" draggable="false" />
          </span>
          <div className="brand__text">
            <h1 className="brand__name">不求人</h1>
            <div className="brand__meta">
              <p className="brand__slogan">办&nbsp;&nbsp;事&nbsp;&nbsp;有&nbsp;&nbsp;路&nbsp;·&nbsp;问&nbsp;&nbsp;之&nbsp;&nbsp;不&nbsp;&nbsp;难</p>
              <span className="brand__seal" aria-hidden="true">
                <svg viewBox="0 0 56 96" focusable="false">
                  <rect className="brand__seal-outer" x="6" y="6" width="44" height="84" rx="17" />
                  <rect className="brand__seal-inner" x="17" y="30" width="22" height="36" rx="8" />
                  <path className="brand__seal-mark" d="M28 28v49M18 45h21M28 55 18 72M28 55l10 17" />
                </svg>
              </span>
            </div>
          </div>
        </header>

        {/* ── Headline ── */}
        {!hasMessages && (
          <>
            <div className="headline-wrap">
              <h2 id="main-title" className="headline">
                办事流程，一问就清楚
              </h2>
              <span className="headline-underline" aria-hidden="true" />
              <p className="intro">优先查找官方来源，帮你梳理条件、材料和办理入口。</p>
            </div>

            {/* ── Search Card ── */}
            <form
              className="search-card"
              onSubmit={e => { e.preventDefault(); handleSend(inputValue); }}
              autoComplete="off"
            >
              <label className="sr-only" htmlFor="matterInput">请输入你想办理的事项</label>
              <svg className="search-icon" viewBox="0 0 44 44" aria-hidden="true">
                <path d="M19.4 33.6c7.84 0 14.2-6.36 14.2-14.2S27.24 5.2 19.4 5.2 5.2 11.56 5.2 19.4s6.36 14.2 14.2 14.2Z" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round"/>
                <path d="m30.2 30.2 8.6 8.6" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round"/>
              </svg>
              <input
                ref={searchInputRef}
                id="matterInput"
                className="search-input"
                type="search"
                placeholder="请输入你想办理的事项"
                value={inputValue}
                onChange={e => setInputValue(e.target.value)}
              />
              <button className="send-button" type="submit" aria-label="提交查询">
                <svg viewBox="0 0 52 52" aria-hidden="true">
                  <path d="M43.6 10.2 7.7 25.8c-2.5 1.1-2.4 4.7.2 5.5l10.6 3.1 4.1 11.3c.9 2.5 4.3 2.8 5.7.5l17.6-31.9c1.3-2.3-.1-5.2-2.3-4.1Z" fill="currentColor" opacity=".98"/>
                  <path d="m19 34 10.9-10.6" fill="none" stroke="white" strokeWidth="3.1" strokeLinecap="round"/>
                </svg>
              </button>
            </form>

            {/* ── FAQ ── */}
            <section className="faq-section" aria-labelledby="faq-title">
              <h3 id="faq-title" className="section-title">
                <span />
                常问事项
              </h3>
              <div className="faq-list">
                {FAQ_ITEMS.map((item, i) => (
                  <button
                    key={i}
                    className="faq-item"
                    type="button"
                    onClick={() => handleFAQClick(item.value)}
                  >
                    <span>{item.label}</span>
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="m9 5 7 7-7 7" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  </button>
                ))}
              </div>
            </section>
          </>
        )}
      </section>

      {/* ── Chat ── */}
      <ChatWindow
        sessionId={sessionId}
        setSessionId={setSessionId}
        messages={messages}
        setMessages={setMessages}
        searchInputRef={searchInputRef}
        pendingSubmit={pendingSubmit}
        onSent={() => setPendingSubmit(null)}
      />
    </main>
  );
}
