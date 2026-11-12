const {test}=require('node:test');
const assert=require('node:assert/strict');
const {environment}=require('./audit_admin_behaviors.cjs');

test('password form reports the server rejection', async () => {
 const e = environment();
 e.get('current-master-pw').value = 'wrong-fixture-password';
 e.get('new-master-pw').value = 'replacement-fixture-key';
 e.get('confirm-master-pw').value = 'replacement-fixture-key';
 const detail = 'Password lama atau sesi tidak sesuai.';
 e.ctx.network = async () => ({ok: false, status: 400, json: async () => ({detail})});
 await e.ctx.handleChangePassword({preventDefault() {}});
 assert.equal(e.get('change-pw-alert').textContent, detail);
});

test('late table reads leave the current result and pagination visible', async () => {
 const e = environment();
 const pending = [];
 e.ctx.network = () => new Promise(resolve => pending.push(resolve));
 const first = e.run("loadTableData('actor')");
 const second = e.run("loadTableData('film')");
 const response = title => ({
  ok: true,
  json: async () => ({columns: ['title'], rows: [[title]], total_rows: 1})
 });
 pending[1](response('Latest table'));
 await second;
 pending[0](response('Old table'));
 await first;
 assert.match(e.get('admin-table-data-container').innerHTML, /Latest table/);
 assert.equal(e.get('admin-table-data-pagination').style.display, 'flex');
});

test('the question form requires a key in automatic mode and preserves policy options in manual mode', async () => {
 const e = environment();
 const fields = {'modal-q-id':'','modal-q-category':'A','modal-q-num':'1','modal-q-title':'Audit',
  'modal-q-desc':'Fixture','modal-q-weight':'10','modal-q-expected':'','modal-q-target-db':'pagila'};
 for (const [id, value] of Object.entries(fields)) e.get(id).value = value;
 let sent;
 e.ctx.network = async (_, options) => {
  sent = JSON.parse(options.body);
  return {ok: true, json: async () => ({status: 'success'})};
 };
 e.ctx.closeQuestionModal = () => {};
 e.ctx.loadQuestionsData = () => {};
 e.run("setQuestionGradingPolicy({row_order:'ignore'})");
 await e.ctx.saveQuestionForm({preventDefault() {}});
 assert.equal(e.get('modal-q-expected').required, true);
 assert.equal(sent, undefined);
 e.get('modal-q-grading-mode').value = 'manual';
 e.run('updateQuestionKeyRequirement()');
 await e.ctx.saveQuestionForm({preventDefault() {}});
 assert.equal(e.get('modal-q-expected').required, false);
 assert.deepEqual(sent.grading_policy, {row_order: 'ignore', mode: 'manual'});
});

test('untrusted names are data attributes, never inline handlers',async()=>{
 for(const name of ["O'Brien","');window.auditInjected=true;//"]){
  const e=environment();e.ctx.network=async()=>({ok:true,json:async()=>({students:[{nim:'AUDIT',name}]})});
  await e.run('loadDashboardData()');
  assert.doesNotMatch(e.get('student-table-body').innerHTML,/onclick=/);
  assert.match(e.get('student-table-body').innerHTML,/data-admin-action="unlock"/);
  assert.equal(e.ctx.auditInjected,undefined);
  let received;
  e.ctx.clearStudentSessionLock=(...args)=>{received=args};
  e.ctx.events.click({target:{closest:()=>({dataset:{adminAction:'unlock',nim:'AUDIT',name}})}});
  assert.deepEqual(received,['AUDIT',name]);
 }
});
test('failed admin reads preserve the last dashboard',async()=>{
 const e=environment();e.get('stat-total-students').textContent='24';
 await e.run('loadDashboardData()');
 assert.equal(e.get('stat-total-students').textContent,'24');
 assert.ok(e.ctx.toasts.some(t=>t[1]==='warning'));
});
test('invalid question SQL displays validation reason and keeps the form',async()=>{
 const e=environment();
 const fields={'modal-q-id':'','modal-q-category':'A','modal-q-num':'1','modal-q-title':'Test',
  'modal-q-desc':'Description','modal-q-weight':'10','modal-q-expected':'random words','modal-q-target-db':'pagila'};
 for(const [id,value] of Object.entries(fields))e.get(id).value=value;
 e.get('question-modal').style.display='flex';
 e.ctx.network=async()=>({ok:false,status:400,json:async()=>({detail:'Kunci jawaban bukan SQL yang valid.'})});
 await e.ctx.saveQuestionForm({preventDefault(){}});
 assert.deepEqual(e.ctx.toasts.at(-1),['Kunci jawaban bukan SQL yang valid.','warning']);
 assert.equal(e.get('modal-q-expected').value,'random words');
 assert.equal(e.get('question-modal').style.display,'flex');
});
test('exam start rejection displays the question reason and allows retry',async()=>{
 const e=environment();
 const reason='Acuan soal B nomor 3 (ID 2610) gagal divalidasi. Pisahkan acuan perubahan state dan SELECT.';
 e.get('admin-exam-state-badge').textContent='Menunggu';
 e.ctx.network=async()=>({ok:false,status:400,json:async()=>({detail:reason})});
 await e.run('startExam()');
 assert.deepEqual(e.ctx.toasts.at(-1),[reason,'warning']);
 assert.equal(e.get('btn-exam-start').disabled,false);
 assert.equal(e.get('admin-exam-state-badge').textContent,'Menunggu');
 assert.ok(!e.ctx.toasts.some(t=>t[1]==='success'));
});

test('exam start network failure allows retry',async()=>{
 const e=environment();
 e.ctx.network=async()=>{throw new TypeError('Failed to fetch')};
 await e.run('startExam()');
 assert.deepEqual(e.ctx.toasts.at(-1),['Failed to fetch','danger']);
 assert.equal(e.get('btn-exam-start').disabled,false);
});

test('waiting room polling cannot re-enable start during a pending request',async()=>{
 const e=environment();let finish,posts=0,state='waiting';
 e.get('btn-exam-start').textContent='Mulai Ujian';
 e.ctx.updateWaitingParticipantsList=()=>{};
 e.ctx.network=async(url,options)=>{
  if(options?.method==='POST'){
   posts++;
   return new Promise(resolve=>{finish=resolve});
  }
  return {ok:true,json:async()=>({exam_state:state,participants:[]})};
 };
 const starting=e.run('startExam()');
 await e.run('loadWaitingRoomPanel()');
 await e.run('startExam()');
 assert.equal(posts,1);
 assert.equal(e.get('btn-exam-start').disabled,true);
 assert.equal(e.get('btn-exam-start').textContent,'Memulai ujian...');
 state='active';
 finish({ok:true,json:async()=>({message:'Ujian dimulai.'})});
 await starting;
 assert.equal(e.get('admin-exam-state-badge').textContent,'Aktif');
 assert.equal(e.get('btn-exam-start').textContent,'Mulai Ujian');
 assert.equal(e.get('btn-exam-start').disabled,true);
});

test('poll JSON from before start cannot restore the waiting state',async()=>{
 const e=environment();let reads=0,finishOld,entered;
 const reading=new Promise(resolve=>{entered=resolve});
 e.ctx.updateWaitingParticipantsList=()=>{};
 e.ctx.network=async(url,options)=>{
  if(options?.method==='POST')return {ok:true,json:async()=>({message:'Ujian dimulai.'})};
  if(++reads===1)return {ok:true,json:()=>{
   entered();return new Promise(resolve=>{finishOld=resolve});
  }};
  return {ok:true,json:async()=>({exam_state:'active',participants:[]})};
 };
 const old=e.run('loadWaitingRoomPanel()');
 await reading;
 await e.run('startExam()');
 finishOld({exam_state:'waiting',participants:[]});
 await old;
 assert.equal(e.get('admin-exam-state-badge').textContent,'Aktif');
 assert.equal(e.get('btn-exam-start').disabled,true);
 assert.equal(e.get('btn-exam-stop').disabled,false);
});

test('preflight runs separately, keeps waiting and enables start only when ready',async()=>{
 const e=environment();let finish,posted=[];
 const ready={ready:true,question_count:20,message:'Siap'};
 e.ctx.updateWaitingParticipantsList=()=>{};
 e.ctx.network=async(url,options)=>{
  if(options?.method==='POST'){
   posted.push(url);
   return new Promise(resolve=>{finish=resolve});
  }
  return {ok:true,json:async()=>({exam_state:'waiting',participants:[],readiness:ready})};
 };
 const validating=e.run('validateExamReadiness()');
 await e.run('loadWaitingRoomPanel()');
 await e.run('startExam()');
 assert.equal(e.get('btn-exam-start').disabled,true);
 assert.equal(e.get('btn-exam-validate').disabled,true);
 assert.equal(e.get('btn-exam-validate').textContent,'Memvalidasi soal...');
 assert.deepEqual(posted,['/api/admin/exam/validate']);
 finish({ok:true,json:async()=>({message:'Siap dimulai',readiness:ready})});
 await validating;
 assert.equal(e.get('admin-exam-state-badge').textContent,'Menunggu');
 assert.equal(e.get('admin-exam-readiness').textContent,'Siap (20 soal).');
 assert.equal(e.get('btn-exam-start').disabled,false);
 assert.equal(e.get('btn-exam-validate').textContent,'Validasi Kesiapan Ujian');
});

test('invalidated readiness disables start until preparation is repeated',async()=>{
 const e=environment();
 e.ctx.updateWaitingParticipantsList=()=>{};
 e.ctx.network=async()=>({ok:true,json:async()=>({exam_state:'waiting',participants:[],
  readiness:{ready:false,message:'Dataset berubah. Validasi kesiapan ulang.'}})});
 await e.run('loadWaitingRoomPanel()');
 assert.equal(e.get('btn-exam-start').disabled,true);
 assert.equal(e.get('btn-exam-validate').disabled,false);
 assert.equal(e.get('admin-exam-readiness').textContent,'Dataset berubah. Validasi kesiapan ulang.');
});

test('preflight failure displays the reason and leaves exam closed',async()=>{
 const e=environment();
 e.ctx.network=async()=>({ok:false,status:400,json:async()=>({detail:'Acuan soal B nomor 3 gagal divalidasi.'})});
 await e.run('validateExamReadiness()');
 assert.deepEqual(e.ctx.toasts.at(-1),['Acuan soal B nomor 3 gagal divalidasi.','warning']);
 assert.equal(e.get('btn-exam-start').disabled,true);
 assert.equal(e.get('admin-exam-state-badge').textContent,'Menunggu');
 assert.equal(e.get('btn-exam-validate').disabled,false);
});

test('gallery navigation rejects executable URLs and removes opener access',()=>{
 const e=environment(),opened=[];
 e.ctx.open=(...args)=>opened.push(args);
 const click=url=>e.ctx.events.click({target:{closest:()=>({dataset:{adminAction:'open-image',url}})}});
 click('javascript:window.auditInjected=true');
 assert.equal(opened.length,0);
 click('https://example.com/image.png');
 assert.equal(opened[0][2],'noopener,noreferrer');
});
for(const action of ['deleteAllQuestions','confirmClearLogs','executeClearSessions']){
 test(action+' cannot display success on HTTP rejection',async()=>{
  const e=environment();e.ctx.loadQuestionsData=()=>{};e.ctx.loadDashboardData=()=>{};e.ctx.loadLogsData=()=>{};
  await e.run(action+'()');
  assert.ok(e.ctx.toasts.some(t=>t[1]==='danger'));
  assert.ok(!e.ctx.toasts.some(t=>t[1]==='success'||t[0].includes('berhasil')));
 });
}
test('old polling responses cannot replace newer data',async()=>{
 const e=environment(),pending=[];
 e.ctx.network=()=>new Promise(resolve=>pending.push(resolve));
 const a=e.run('loadDashboardData()'),b=e.run('loadDashboardData()');
 pending[1]({ok:true,json:async()=>({students:[{nim:'NEW',name:'Current'}]})});await b;
 pending[0]({ok:true,json:async()=>({students:[{nim:'OLD',name:'Stale'}]})});await a;
 assert.match(e.get('student-table-body').innerHTML,/NEW/);
 assert.doesNotMatch(e.get('student-table-body').innerHTML,/OLD/);
});
