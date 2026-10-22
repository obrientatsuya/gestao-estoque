const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const saved = {id:'one',items:[{id:1,counted:10},{id:2,counted:20}]};
  const calls = [];
  const context = vm.createContext({document:{getElementById:()=>({value:'Anderson'})},setTimeout,clearTimeout,console});
  const source = fs.readFileSync(path.join(__dirname,'../HL_Conferencia_NFe/static/app.js'),'utf8');
  vm.runInContext(source.slice(0,source.indexOf('$("skuSearch").oninput')),context);
  context.save = async (url, options) => {
    const payload = JSON.parse(options.body);
    calls.push({url,...payload});
    const id = Number(url.split('/').at(-1));
    saved.items.find(item=>item.id===id).counted = payload.value;
    return structuredClone(saved);
  };
  context.initial = structuredClone(saved);
  vm.runInContext('state.note=initial;api=save;renderItems=renderProgress=updateHighlights=renderNote=()=>{};',context);
  return {context,saved,calls,run:code=>vm.runInContext(code,context)};
}

test('rapid edits on different items are both persisted',async()=>{
  const f = fixture();
  f.run('setCount(1,7);setCount(2,18);');
  await f.run('Promise.all([flushCount(1),flushCount(2)])');
  assert.deepEqual(f.saved.items.map(i=>i.counted),[7,18]);
  assert.equal(f.calls.length,2);
});

test('rapid edits on the same item coalesce to the latest quantity',async()=>{
  const f = fixture();
  f.run('setCount(1,7);setCount(1,6);setCount(1,5)');
  await f.run('flushCount(1)');
  assert.equal(f.saved.items[0].counted,5);
  assert.equal(f.calls.length,1);
  assert.equal(f.run('state.pendingCounts.size'),0);
});

test('a second edit during an in-flight save is queued',async()=>{
  const f = fixture();
  f.run('setCount(1,7)');
  const first = f.run('flushCount(1)');
  f.run('setCount(1,5)');
  await f.run('flushCount(1)');
  await first;
  assert.equal(f.saved.items[0].counted,5);
  assert.equal(f.run('state.note.items[0].counted'),5);
});

test('failed saves retain a retryable pending edit',async()=>{
  const f = fixture();
  f.run('api=async()=>{throw new Error("offline")};setCount(1,3)');
  await assert.rejects(f.run('flushCount(1)'),/offline/);
  f.run('api=save');
  await f.run('flushCount(1)');
  assert.equal(f.saved.items[0].counted,3);
});

test('whole-note responses cannot restore an older quantity from another item',async()=>{
  const f = fixture();
  const save = f.context.save;
  f.context.save = async (...args) => {
    const result = await save(...args);
    if (args[0].endsWith('/1')) await new Promise(resolve=>setTimeout(resolve,20));
    return result;
  };
  f.run('api=save;setCount(1,7);setCount(2,18)');
  await f.run('flushCounts()');
  assert.deepEqual(Array.from(f.run('state.note.items.map(item=>item.counted)')),[7,18]);
});
