import type { Source } from '../types';

function sourceHost(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

export default function Sources({ sources }: { sources: Source[] }) {
  if (!sources?.length) return null;
  return (
    <section className="panel sources-panel">
      <h3>引用来源</h3>
      <ul>
        {sources.map((src, idx) => (
          <li key={idx}>
            {src.url ? <a href={src.url} target="_blank" rel="noreferrer">{src.title || src.name || '官方来源'}</a> : <span>{src.title || src.name || '官方来源'}</span>}
            {src.url && <div className="source-url">{sourceHost(src.url)}</div>}
          </li>
        ))}
      </ul>
    </section>
  );
}
