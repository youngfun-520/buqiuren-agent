import type { Action, ServiceCard as Card } from '../types';

function List({ items, ordered = false, empty = '以官方页面为准' }: { items?: string[]; ordered?: boolean; empty?: string }) {
  if (!items?.length) return <p className="muted">{empty}</p>;
  const Tag = ordered ? 'ol' : 'ul';
  return <Tag className="list">{items.map((x, i) => <li key={i}>{x}</li>)}</Tag>;
}

export default function ServiceCard({ card, actions }: { card: Card; actions: Action[] }) {
  const copyAction = actions.find(a => a.type === 'copy_materials');
  const openAction = actions.find(a => a.type === 'open_official');
  return (
    <section className="panel service-card">
      <h2>{card.title || '办事指南'}</h2>
      <p className="summary">{card.summary || '以官方页面为准'}</p>
      <div className="card-grid">
        <section><h3>适用条件</h3><List items={card.conditions} /></section>
        <section><h3>所需材料</h3><List items={card.materials} /></section>
        <section><h3>办理方式</h3><List items={card.methods} /></section>
        <section><h3>办理流程</h3><List items={card.steps} ordered /></section>
        <section>
          <h3>费用 / 时限</h3>
          <p className="muted"><strong>费用：</strong>{card.fees || '以官方页面为准'}</p>
          <p className="muted"><strong>时限：</strong>{card.processing_time || '以官方页面为准'}</p>
        </section>
        <section><h3>办理地点</h3><List items={card.offline_locations} /></section>
        <section style={{ gridColumn: '1 / -1' }}><h3>注意事项</h3><List items={card.tips} /></section>
      </div>
      {card.freshness_warning && <p className="warning">{card.freshness_warning}</p>}
      <p className="disclaimer">{card.disclaimer || '办事政策可能调整，请以官方最新页面为准。'}</p>
      {(copyAction?.text || openAction?.url) && (
        <div className="action-row">
          {copyAction?.text && <button onClick={() => navigator.clipboard?.writeText(copyAction.text || '')}>{copyAction.label}</button>}
          {openAction?.url && <a className="action-link" href={openAction.url} target="_blank" rel="noreferrer">{openAction.label}</a>}
        </div>
      )}
    </section>
  );
}
