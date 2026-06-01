import { FormEvent, useState } from 'react';
import type { QuickReply } from '../types';

interface Props {
  replies: QuickReply[];
  onSelect: (reply: QuickReply) => void;
  disabled?: boolean;
  allowCustom?: boolean;
}

export default function QuickReplies({ replies, onSelect, disabled, allowCustom = false }: Props) {
  const [customValue, setCustomValue] = useState('');

  if (!replies?.length && !allowCustom) return null;

  function handleCustomSubmit(event: FormEvent) {
    event.preventDefault();
    const value = customValue.trim();
    if (!value || disabled) return;
    setCustomValue('');
    onSelect({ label: value, value });
  }

  return (
    <section className="choice-panel" aria-label="选择下一步">
      {!!replies?.length && (
        <div className="quick-replies">
          {replies.map((reply, idx) => (
            <button key={idx} disabled={disabled} onClick={() => onSelect(reply)}>{reply.label}</button>
          ))}
        </div>
      )}
      {allowCustom && (
        <form className="custom-reply-form" onSubmit={handleCustomSubmit}>
          <input
            value={customValue}
            onChange={event => setCustomValue(event.target.value)}
            placeholder="补充说明，也可以直接写你的情况"
            disabled={disabled}
          />
          <button type="submit" disabled={disabled || !customValue.trim()} aria-label="提交补充">
            补充
          </button>
        </form>
      )}
    </section>
  );
}
