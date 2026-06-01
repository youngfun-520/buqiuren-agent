import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';

const root = path.resolve(import.meta.dirname, '..');
const sourcePath = path.join(root, 'src', 'types.ts');
const source = fs.readFileSync(sourcePath, 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
});

const context = {
  exports: {},
  module: { exports: {} },
};
vm.runInNewContext(outputText, context);

const {
  createAssistantPayload,
  mergeTimelineItems,
  normalizeTimelineItem,
  thinkingEventToTimelineItem,
} = context.exports;

const thinkingItem = thinkingEventToTimelineItem({
  node: 'public_node',
  thinking: 'checking records',
  public_status: '我先确认你要办理的事项。',
  status: 'running',
});

assert.equal(JSON.stringify(thinkingItem), JSON.stringify({
  label: 'public_node',
  status: 'running',
  message: '我先确认你要办理的事项。',
  kind: 'thinking',
  compact: true,
}));

assert.equal(JSON.stringify(thinkingEventToTimelineItem({
  node: '',
  thinking: '',
  status: '',
})), JSON.stringify({
  label: '思考中',
  status: 'unknown',
  message: '',
  kind: 'thinking',
  compact: true,
}));

assert.equal(JSON.stringify(normalizeTimelineItem({
  label: '复用上下文：成都',
  status: 'done',
  message: '已沿用上一次的城市信息',
})), JSON.stringify({
  label: '复用上下文：成都',
  status: 'done',
  message: '已沿用上一次的城市信息',
  compact: true,
}));

assert.equal(JSON.stringify(mergeTimelineItems(
  [
    { label: '理解问题', status: 'running', message: '我先确认一下', kind: 'thinking' },
  ],
  [
    { label: '理解问题', status: 'running', message: '我先确认一下' },
    { label: '查找官方依据', status: 'done', message: '已检索官方来源' },
  ],
)), JSON.stringify([
  { label: '理解问题', status: 'running', message: '我先确认一下', kind: 'thinking', compact: true },
  { label: '理解问题', status: 'running', message: '我先确认一下', kind: 'public', compact: false },
  { label: '查找官方依据', status: 'done', message: '已检索官方来源', kind: 'public', compact: false },
]));

assert.equal(JSON.stringify(mergeTimelineItems(
  [
    { label: '查找官方依据', status: 'done', message: '已检索官方来源' },
  ],
  [
    { label: '查找官方依据', status: 'done', message: '已检索官方来源' },
    { label: '整理回复', status: 'done', message: '生成办事指引' },
  ],
)), JSON.stringify([
  { label: '查找官方依据', status: 'done', message: '已检索官方来源', kind: 'public', compact: false },
  { label: '整理回复', status: 'done', message: '生成办事指引', kind: 'public', compact: false },
]));

assert.equal(createAssistantPayload('session-1').session_id, 'session-1');
assert.equal(JSON.stringify(createAssistantPayload('session-1').timeline), '[]');
