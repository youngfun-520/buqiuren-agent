import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';

const root = path.resolve(import.meta.dirname, '..');
const sourcePath = path.join(root, 'src', 'components', 'loadingStatus.ts');
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
const { getLoadingStatusText } = context.exports;

assert.equal(getLoadingStatusText('正在识别办理城市...'), '正在识别办理城市...');
assert.equal(getLoadingStatusText('  正在匹配官方指南  '), '正在匹配官方指南');
assert.equal(getLoadingStatusText(''), '');
assert.equal(getLoadingStatusText(undefined), '');
