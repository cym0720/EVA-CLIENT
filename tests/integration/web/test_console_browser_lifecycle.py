"""Execute browser lifecycle code with a small DOM/network harness."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_reload_preserves_real_mode_and_failed_setup_releases_post_queue():
    node = shutil.which("node") or shutil.which("nodejs")
    if node is None:
        pytest.skip("Node is required for browser lifecycle regression")
    root = Path(__file__).resolve().parents[3]
    script = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const base = 'src/core/app/console/static/js/';
const nodes = new Map();
const node = () => ({style: {}, dataset: {}, classList: {toggle(){},add(){},remove(){}},
  appendChild(){}, addEventListener(){}, querySelectorAll(){return []}});
const dom = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
class Controller {
  constructor() {
    const listeners = [];
    this.signal = {aborted:false,addEventListener:(event,fn)=>listeners.push(fn)};
    this.abort = () => { this.signal.aborted = true; listeners.forEach(fn=>fn()); };
  }
}
const sent = [];
const context = vm.createContext({
  console:{info(){},warn(){}}, document:{getElementById:dom,createElement:node},
  window:{addEventListener(){}}, sessionStorage:{getItem(){},setItem(){}},
  performance:{now:()=>Date.now()}, Date, Math, Promise, AbortController:Controller,
  setTimeout:(fn,ms)=>setTimeout(fn,Math.min(ms,25)), clearTimeout,
  fetch:(path,opts={})=> {
    if(path==='/api/client_trace') return Promise.resolve({});
    sent.push(path);
    if(path==='/api/setup') return new Promise((resolve,reject)=> {
      opts.signal.addEventListener('abort',()=>reject(new Error('aborted')));
    });
    return Promise.resolve({ok:true,status:200,json:async()=>({ok:true})});
  },
});
function moduleSource(file) {
  return fs.readFileSync(base+file,'utf8').replace(/^import .*;\n/gm,'')
    .replace(/^export \{[\s\S]*?\};/gm,'').replace(/^export /gm,'');
}
vm.runInContext(moduleSource('core.js')+'\nglobalThis.store=S;',context);
vm.runInContext(moduleSource('run.js'),context);
vm.runInContext(`
  renderGripperButtons=renderPromptButtons=renderCollectTaskButtons=renderModeButtons=
    renderReplayConfig=renderTune=renderManualTune=renderManualConn=mark=()=>{};
  evalEnabled=()=>false;
  updateGuide=()=>{globalThis.guideUpdates=(globalThis.guideUpdates||0)+1;};
  globalThis.actualRenderConfig=renderConfig;
`,context);
const cfg={robot_type:'agibot_g2',tasks:[],modes:['real','sim','step'],strategies:[]};
context.store.CFG=cfg;

// A fresh page must read REAL state before choosing any default mode.
const main=fs.readFileSync(base+'main.js','utf8');
const boot=main.slice(main.indexOf('async function boot()'),
  main.indexOf('\ndocument.querySelectorAll(".tab")'));
const boundary=new Error('stop after initial status rendering');
context.stopBoundary=boundary;
context.bootSeen=[];
context.apiGet=async path=>path==='/api/config'?cfg:
  {cli_mode:'real',setup_stage:'publishing reset trajectory'};
vm.runInContext(`
  initDashboard=installCollectKeyboardControls=()=>{};
  tpToggle=tpSeek=trialPopClose=()=>{};
  renderConfig=()=>{bootSeen.push(S.STATUS.cli_mode);actualRenderConfig();};
  applyStatus=renderRlStatus=()=>{};
  collectConfigured=()=>{throw stopBoundary;};
`,context);
vm.runInContext(boot,context);
(async()=> {
  await vm.runInContext('boot()',context).catch(e=>assert.strictEqual(e,boundary));
  assert.deepStrictEqual(context.bootSeen,['real']);
  assert(!sent.includes('/api/select_mode'),'reload must not interrupt REAL setup with SIM');

  // Unknown state must not cause a mode switch when the first GET fails.
  context.store.STATUS={};
  vm.runInContext('actualRenderConfig()',context);
  assert(!sent.includes('/api/select_mode'));

  // A lost setup acknowledgement must release the serialized queue and show retry.
  vm.runInContext('requestSetup();',context);
  const later=vm.runInContext('apiPost("/api/select_task",{task:"test"});',context);
  await later;
  assert.strictEqual(sent.filter(p=>p==='/api/setup').length,1);
  assert(sent.includes('/api/select_task'));
  assert(context.store._setupRequestError.includes('timed out'));
  assert(context.guideUpdates>0);
  console.log('REAL reload, unknown status, and stalled Setup regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
