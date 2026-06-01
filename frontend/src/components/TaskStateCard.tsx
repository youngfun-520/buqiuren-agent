import type { TaskState } from '../types';

const STAGE_LABELS: Record<string, string> = {
  confirm_goal: '确认办理类型',
  confirm_identity: '确认身份状态',
  confirm_city: '确认办理城市',
  confirm_subitem: '确认具体事项',
  search_official: '查找官方依据',
  ready_guidance: '方向性推进',
  done: '完成',
  unsupported: '暂不支持',
};

const GOAL_LABELS: Record<string, string> = {
  query: '查询账户',
  claim: '领取企业年金',
  transfer: '企业年金转移',
  complaint: '投诉或咨询',
  consult: '咨询',
  apply: '申请办理',
  renew: '续签或续期',
  withdraw: '提取',
  unknown: '待确认',
};

const SLOT_LABELS: Record<string, string> = {
  goal: '办理类型',
  identity_status: '身份状态',
  city: '办理城市',
  subitem: '具体事项',
};

function readableGoal(task: TaskState) {
  if (!task.goal || task.goal === 'unknown') return '待确认';
  return GOAL_LABELS[task.goal] || task.goal;
}

function readableConfirmed(task: TaskState) {
  const pairs = [
    task.goal && task.goal !== 'unknown' ? ['办理类型', readableGoal(task)] : null,
    task.identity_status ? ['身份状态', task.identity_status] : null,
    task.city ? ['办理城市', task.city] : null,
    task.subitem ? ['具体事项', task.subitem] : null,
  ].filter(Boolean) as string[][];
  return pairs.length ? pairs : [];
}

export default function TaskStateCard({ task }: { task: TaskState }) {
  const missing = task.missing_slots?.length
    ? task.missing_slots.map(slot => SLOT_LABELS[slot] || slot).join('、')
    : '暂无';

  return (
    <section className="task-card">
      <div className="task-card-head">
        <span>当前办事任务</span>
        <strong>{STAGE_LABELS[task.stage] || task.stage || '推进中'}</strong>
      </div>
      <dl className="task-grid">
        <div>
          <dt>当前事项</dt>
          <dd>{task.topic || '待确认'}</dd>
        </div>
        <div>
          <dt>办理类型</dt>
          <dd>{readableGoal(task)}</dd>
        </div>
        <div>
          <dt>待补充</dt>
          <dd>{missing}</dd>
        </div>
      </dl>
      {readableConfirmed(task).length > 0 && (
        <div className="confirmed-strip">
          {readableConfirmed(task).map(([label, value]) => (
            <span key={`${label}-${value}`}>
              <b>{label}</b>{value}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
