function statusClass(s: string) {
  if (['done', 'passed', 'success'].includes(s)) return 'is-done';
  if (s === 'warning') return 'is-warning';
  return 'is-running';
}

function statusIcon(s: string) {
  if (['done', 'passed', 'success'].includes(s)) return '✓';
  if (s === 'warning') return '⚠';
  return '';
}

export default function Timeline({ items }: { items: { label: string; status: string; message: string }[] }) {
  if (!items?.length) return null;
  return (
    <section className="panel timeline-panel">
      <h3>处理步骤</h3>
      <ul className="timeline">
        {items.map((item, idx) => (
          <li key={idx} className={statusClass(item.status)}>
            <span className="tick" aria-hidden="true">{statusIcon(item.status)}</span>
            <span><strong>{item.label}</strong>{item.message ? `：${item.message}` : ''}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
