// No npm dependencies. Run: node --test tests/web/*.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.events = {}; this.dataset = {}; }
  setAttribute(k,v) { this.attrs[k] = v; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(k, fn) { this.events[k] = fn; }
}
function setup(file, extra = {}) {
  const window = {location: {origin: 'http://localhost'}, ...extra};
  const context = {window, URL, Node: Element, document: {
    createElement: tag => new Element(tag), createTextNode: text => ({text})
  }, ...extra};
  vm.runInNewContext(fs.readFileSync('app/web/templates/' + file, 'utf8'), context);
  return window;
}
const nodes = n => [n, ...(n.children || []).flatMap(nodes)];
const text = n => nodes(n).map(n=>n.text || '').join(' ');
test('enabled G1 action and disabled unsupported synthetic event use boolean DOM semantics', () => {
  const {AlfaUI:u} = setup('ui.js');
  const feed = u.renderFeed([{event:{id:'case-rate-20260911',domain:'case',title:'G1'}}], ()=>{});
  const btn = nodes(feed).find(n=>n.tag==='button');
  assert.equal(btn.attrs.disabled, undefined);
  assert.equal(u.h('button', {disabled:true}).attrs.disabled, '');
});
test('untrusted text stays text and executable links are rejected', () => {
  const {AlfaUI:u} = setup('ui.js');
  assert.equal(u.h('a',{href:'javascript:alert(1)'},'<img onerror=x>').attrs.href, undefined);
  assert.equal(u.h('a',{href:'data:text/html,x'}).attrs.href, undefined);
  assert.equal(text(u.h('p',{},'<script>x</script>')).trim(), '<script>x</script>');
});
test('replay discloses controlled transport and preserves assumptions, missing fields and orphan OCR evidence', () => {
  const {AlfaUI:u} = setup('ui.js');
  const result = u.renderAnalysisCard({id:'a', company_id:'U_15728', event_id:'e', status:'partial',
    claims:[], sections:{}, delivery:{mode:'saved_replay',original_mode:'controlled'},
    missing_fields:['contract_rate_type'], assumptions:[{name:'rate',value:'fixed'}],
    evidence:[{id:'ev',value_kind:'forecast',quality:'unverified',page:1}]},()=>{},()=>{});
  assert.match(text(result), /ответ AI имитируется/);
  assert.match(text(result), /Сообщено вами[\s\S]*rate: fixed/);
  assert.match(text(result), /тип ставки в договоре/);
  assert.match(text(result), /требует сверки/);
  const evidence = u.renderEvidenceDrawerContent({text:'x'}, [{id:'ev',document_version_id:'doc', page:3}],()=>{});
  assert.ok(nodes(evidence).some(n=>n.attrs?.href==='http://localhost/api/v1/sources/doc/asset#page=3'));
});
test('network-uncertain POST retries retain request key only for the identical payload', async () => {
  const calls=[];
  let fail=true;
  const {AlfaApi:a} = setup('api.js', {fetch:async (url, opts)=>{
    calls.push(opts.headers['Idempotency-Key']);
    if(fail) {fail=false; throw Error('offline');}
    return {ok:true, headers:{get:()=> 'application/json'}, json:async()=>({id:'job'})};
  }});
  await assert.rejects(a.createAnalysis({event_id:'g1'}));
  await a.createAnalysis({event_id:'g1'});
  await a.createAnalysis({event_id:'other'});
  assert.equal(calls[0],calls[1]); assert.notEqual(calls[1],calls[2]);
});
function controller(api) {
  const elements = new Map();
  class Control extends Element {
    constructor(id) {
      super('div'); this.id = id; this.value=''; this.style={};
      const flags = new Set(['hidden']);
      this.classList = {add:k=>flags.add(k), remove:k=>flags.delete(k), contains:k=>flags.has(k), toggle:()=>{}};
    }
    replaceChildren(...c) {this.children=c;}
    set innerHTML(v) {this.children=[];}
  }
  const el = id => { if(!elements.has(id)) elements.set(id,new Control(id)); return elements.get(id); };
  const window = {AlfaApi:api, AlfaFixtures:{SCENARIOS:{}},
    AlfaUI:{h:setup('ui.js').AlfaUI.h, renderFeed:(items, onAnalyze)=>{el('trigger-analysis').events.click=()=>onAnalyze({id:'real',domain:'case'});return new Element('feed');},
      renderNewsList:()=>new Element('news'), renderNewsSourcesTable:()=>new Element('table'),
      renderAnalysisCard:(a,open,q,docs,feature)=>{el('trigger-feature').events.click=feature;el('trigger-evidence').events.click=open;return {analysis:a};}, renderEvidenceDrawerContent:(claim,evidence)=>({claim,evidence}), renderFeatureDrawer:feature=>({feature})}, location:{search:''}, addEventListener:()=>{}};
  vm.runInNewContext(fs.readFileSync('app/web/templates/app.js','utf8'), {
    window, document:{readyState:'complete',getElementById:el,querySelectorAll:()=>[]},
    Option: function(text,value){this.text=text;this.value=value;}, URLSearchParams,
    setTimeout:fn=>setImmediate(fn)
  });
  return el;
}
function deferred() {let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
const baseApi = () => ({
  login:async()=>({context:{company_id:'A',version:1,role:'cfo'}}),
  getCompanies:async()=>[{company_id:'A',name:'A'},{company_id:'B',name:'B'}],
  getFeed:async()=>[],getNews:async()=>[],getNewsSources:async()=>[],
  patchContext:async p=>({company_id:p.company_id,role:p.role,version:2}),
});
test('late create response after company switch cannot begin polling or display old result', async()=>{
  const post=deferred(); let polls=0;
  const el=controller({...baseApi(),createAnalysis:()=>post.promise,getJob:async()=>{polls++;}});
  await el('btn-login').events.click();
  const running=el('trigger-analysis').events.click();
  el('assumption-toggle').checked=true; el('assumption-value').value='private A condition';
  el('company-select').value='B'; el('role-select').value='cfo';
  await el('btn-apply-context').events.click();
  post.resolve({job:{id:'old',poll_url:'/api/v1/jobs/old'}});
  await running;
  assert.equal(el('analysis-container').children.length,0);
  assert.equal(polls,0);
  assert.equal(el('assumption-value').value,'');
  assert.equal(el('assumption-toggle').checked,false);
});
test('in-flight poll response after context change cannot restore old progress or claims', async()=>{
  const poll=deferred(), started=deferred(); let reads=0;
  const el=controller({...baseApi(),createAnalysis:async()=>({job:{id:'old'}}),
    getJob:()=>{started.resolve(); return poll.promise;},getAnalysis:()=>{reads++;}});
  await el('btn-login').events.click();
  const running=el('trigger-analysis').events.click();
  await started.promise;
  el('company-select').value='B'; await el('btn-apply-context').events.click();
  poll.resolve({id:'old',status:'partial',result_id:'old'}); await running;
  assert.equal(reads,0);
  assert.equal(el('analysis-container').children.length,0);
  assert.ok(el('job-progress-container').classList.contains('hidden'));
});
test('partial is terminal and loads a saved analysis through actual controller', async()=>{
  const result={id:'result',company_id:'A'};
  const el=controller({...baseApi(),createAnalysis:async()=>({job:{id:'j'}}),
    getJob:async()=>({id:'j',status:'partial',result_id:'result'}),getAnalysis:async()=>result});
  await el('btn-login').events.click();
  await el('trigger-analysis').events.click();
  assert.equal(el('analysis-container').children[0].analysis.id,'result');
  assert.equal(el('saved-analysis-id').value,'result');
});
test('live job can outlast the former polling window without a second POST', async()=>{
  let polls=0, posts=0;
  const result={id:'slow-result',company_id:'A'};
  const el=controller({...baseApi(),createAnalysis:async()=>{posts++;return {job:{id:'slow'}};},
    getJob:async()=> ++polls < 130 ? {id:'slow',status:'running'} : {id:'slow',status:'partial',result_id:result.id},
    getAnalysis:async()=>result});
  await el('btn-login').events.click();
  await el('trigger-analysis').events.click();
  assert.equal(posts,1); assert.equal(polls,130);
  assert.equal(el('analysis-container').children[0].analysis.id,result.id);
});
test('denied polling terminates without retrying or revealing old analysis', async()=>{
  let polls=0;
  const el=controller({...baseApi(),createAnalysis:async()=>({job:{id:'j'}}),
    getJob:async()=>{polls++;throw Object.assign(Error('Resource not found'),{status:404,code:'not_found'});}});
  await el('btn-login').events.click(); await el('trigger-analysis').events.click();
  assert.equal(polls,1);
  assert.match(text(el('global-error-alert')),/not_found/);
  assert.equal(el('analysis-container').children.length,0);
});
test('saved-result response after a role-only change is fenced too', async()=>{
  const read=deferred();
  const el=controller({...baseApi(),getAnalysis:()=>read.promise});
  await el('btn-login').events.click();
  const opening=el('btn-open-saved').events.click();
  el('company-select').value='A';el('role-select').value='ceo';
  await el('btn-apply-context').events.click();
  read.resolve({id:'old',company_id:'A'}); await opening;
  assert.equal(el('analysis-container').children.length,0);
});
test('company feature opens within UI instead of raw JSON navigation',()=>{
  const {AlfaUI:u}=setup('ui.js');let chosen;
  const claim=u.renderClaim({id:'c',text:'conditional',feature_ids:['feature/1']},()=>{},id=>{chosen=id;});
  const button=nodes(claim).find(n=>n.tag==='button');button.events.click();
  assert.equal(chosen,'feature/1'); assert.equal(nodes(claim).filter(n=>n.tag==='a').length,0);
  const drawer=u.renderFeatureDrawer({id:'f',name:'CL_PRODUCT_LOA_MARK',value:true,raw_value:'1.0',provenance:[{original_filename:'input.csv',row:3972,sha256:'hash',source_version_id:'source'}]});
  assert.match(text(drawer),/Кредитные продукты[\s\S]*Есть[\s\S]*3972/);
});

test('late evidence fetch cannot replace a newer claim in the drawer', async()=>{
  const a=deferred(),b=deferred();
  const el=controller({...baseApi(),getAnalysis:async()=>({id:'r',company_id:'A'}),
    getEvidence:id=>id==='a'?a.promise:b.promise});
  await el('btn-login').events.click(); await el('btn-open-saved').events.click();
  el('drawer-body').replaceChildren({claim:{id:'previous-claim'}});
  const openingA=el('trigger-evidence').events.click({id:'claim-a',evidence_ids:['a']});
  assert.equal(el('drawer-body').children.some(n=>n.claim),false);
  const openingB=el('trigger-evidence').events.click({id:'claim-b',evidence_ids:['b']});
  b.resolve({id:'b'});await openingB;
  a.resolve({id:'a'});await openingA;
  assert.equal(el('drawer-body').children[0].claim.id,'claim-b');
});

test('feature response after company switch cannot leak the previous company data',async()=>{
  const read=deferred();
  const el=controller({...baseApi(),getAnalysis:async()=>({id:'r',company_id:'A'}),getFeature:()=>read.promise});
  await el('btn-login').events.click();await el('btn-open-saved').events.click();
  const opening=el('trigger-feature').events.click('private-feature');
  el('company-select').value='B';await el('btn-apply-context').events.click();
  read.resolve({id:'private-feature',raw_value:'private'});await opening;
  assert.equal(el('drawer-body').children.length,0);
});
const visibleText=n=>n.tag==='details'?visibleText(n.children[0]):[n.text||'',...(n.children||[]).map(visibleText)].join(' ');
test('business surface hides internal IDs and gaps codes while retaining details and bank quote',()=>{
  const {AlfaUI:u}=setup('ui.js');
  const a={id:'internal-id',event_id:'event-internal',event_title:'Событие',company_id:'U_PRIVATE',status:'partial',context:{role:'cfo'},
    claims:[{id:'r',kind:'hypothesis',origin:'model',text:'Риск при условии',impact_kind:'risk',conditions:['Если потребуется закупка']},{id:'o',kind:'hypothesis',origin:'model',text:'Возможность при условии',impact_kind:'opportunity'}],
    sections:{consequences:['r','o'],basis:['r','o']},missing_fields:['fuel_cost_share'],delivery:{mode:'controlled'},
    bank_context:{theses:[{id:'t',text:'Точный прогноз банка',issued_at:'2026-09-11T00:00:00Z',status:'unverified',evidence_ids:['e']}],gaps:['bank_ocr_unverified']},
    evidence:[{id:'e',document_version_id:'doc',quality:'unverified',page:1}]};
  const card=u.renderAnalysisCard(a,()=>{},()=>{});
  assert.doesNotMatch(visibleText(card),/internal-id|event-internal|fuel_cost_share|U_PRIVATE|page_chars/);
  assert.match(visibleText(card),/доля топлива в расходах/);
  assert.match(text(card),/Точный прогноз банка/);
  assert.equal(nodes(card).filter(n=>n.dataset?.claimId==='r').length,1);
  const cfo=text(card),ceo=text(u.renderAnalysisCard({...a,context:{role:'ceo'}},()=>{},()=>{}));
  assert.ok(cfo.indexOf('Возможные риски')<cfo.indexOf('Возможности при'));
  assert.ok(ceo.indexOf('Возможности при')<ceo.indexOf('Возможные риски'));
});
