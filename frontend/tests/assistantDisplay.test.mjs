import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';

const root = path.resolve(import.meta.dirname, '..');

function loadTsModule(relativePath, extraContext = {}) {
  const sourcePath = path.join(root, relativePath);
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
    require: extraContext.require || (() => ({})),
  };
  vm.runInNewContext(outputText, context);
  return context.exports;
}

const loadingStatus = loadTsModule('src/components/loadingStatus.ts');
const { getAssistantDisplayText } = loadTsModule('src/components/assistantDisplay.ts', {
  require(specifier) {
    if (specifier === './loadingStatus') return loadingStatus;
    return {};
  },
});

const basePayload = {
  session_id: 's1',
  type: 'unknown',
  message: '',
  timeline: [],
  quick_replies: [],
  card: null,
  actions: [],
  sources: [],
  reasoning_steps: [],
};

assert.equal(
  getAssistantDisplayText(basePayload, { loading: true, liveThinking: '我先确认到你要查的是深圳的居住证办理。' }),
  '我先确认到你要查的是深圳的居住证办理。',
);

assert.equal(
  getAssistantDisplayText({
    ...basePayload,
    timeline: [{ label: '理解你的问题', status: 'done', message: '已理解您要办理居住证' }],
  }, { loading: true }),
  '',
);

assert.equal(
  getAssistantDisplayText({ ...basePayload, message: '请问您目前的户籍状态是什么？' }, { loading: false }),
  '请问您目前的户籍状态是什么？',
);

assert.notEqual(getAssistantDisplayText(basePayload, { loading: true }), '已收到。');
assert.equal(getAssistantDisplayText(basePayload, { loading: true }), '');
assert.equal(getAssistantDisplayText(basePayload, { loading: false }), '');
