import type { ReasoningStep } from '../types';

export default function ThinkingSummary({ steps }: { steps?: ReasoningStep[] }) {
  if (!steps?.length) return null;
  return (
    <section className="panel thinking-panel">
      <h3>思考摘要</h3>
      <ul>
        {steps.map((step, idx) => (
          <li key={`${step.label}-${idx}`}>
            <strong>{step.label}</strong>
            {step.summary ? <span>{step.summary}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
