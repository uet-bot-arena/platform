import {timeField,timeBody} from './time-control.js';
import {ArenaAPI,saveBlob} from './client.js';
import {page,jobDetail} from './pages.js';
import {esc,icon,button,field,table,note,errors,submit,shell,badge,short,select,date} from './view.js';
import {MatchBoard,boardPage} from './board.js';
import {CompetitionFollow} from './follow.js';
import {patchChildren} from './patch.js';
import {adminTeamsBody,operationsBody} from './admin-ui.js';
import {loadBotGuide} from './bot-guide.js';
import {publicRoute} from './access.js';
import {readRuleForm,ruleEditor,rulePreviewMarkup,botRulesetField,botRulesetSelection,ruleFingerprint} from './experimental-ui.js';

const api=new ArenaAPI(),app=document.getElementById('app'),modalRoot=document.getElementById('modal-root');
const defaults=()=>({team:null,competitions:[],competition:null,fixtures:[],standings:null,submissions:[],jobs:[],job:null,dashboard:null,registrations:[],accounts:[],recoveries:[],audit:[],jobProgress:null,metrics:null,adminJobs:[],adminSubmissions:[],rulesetCatalog:null,ruleEditing:null,ruleDraft:null,ruleNotes:null,ruleDetail:null,rulePlay:null,rulePlayActions:[],rulePreview:null,rulePreviewDefinition:null,rulePreviewActions:[]});
const s={...defaults(),user:null,route:'home',ids:[],loading:false,adminTab:'accounts',registrationStatus:'pending',adminVisibility:'active',adminSearch:'',adminPage:1,opsTab:'jobs',opsPage:1,opsSearch:'',opsStatus:'',opsKind:'',opsTeam:''};
let generation=0,board,competitionFeed,jobFeed,toastTimer,modalFocus,returnTo='home',issued=null,refreshingGeneration=null;
const authChannel=typeof BroadcastChannel==='function'?new BroadcastChannel('uet-arena-session'):null;
authChannel?.addEventListener('message',async()=>{try{const session=await api.request('/auth/me');Object.assign(s,defaults(),{user:session.user});openRoute();}catch(e){if(e.status===401){clearAccount();go('login');}else handleError(e);}});
const authChanged=()=>authChannel?.postMessage({type:'changed'});
const terminal=status=>['completed','failed','aborted'].includes(status);
const uuid=()=>crypto.randomUUID();
const enc=encodeURIComponent;
function toast(message){const el=document.getElementById('toast');el.className='toast show';el.textContent=message;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),6000);}
function clearView(){competitionFeed?.close();competitionFeed=null;board?.close();board=null;jobFeed?.close();jobFeed=null;closeModal();}
function render(){app.innerHTML=page(s);const title={home:'UET Bot Arena',login:'Đăng nhập',register:'Đăng ký đội',recover:'Cấp lại mật khẩu','change-password':'Đổi mật khẩu'}[s.route]||document.querySelector('#main h1')?.textContent||'UET Bot Arena';document.title=title==='UET Bot Arena'?title:title+' · UET Bot Arena';mobileSidebar();}
function mobileSidebar(){const sidebar=document.querySelector('.sidebar');if(sidebar)sidebar.inert=matchMedia('(max-width:760px)').matches&&!s.menuOpen;}
matchMedia('(max-width:760px)').addEventListener('change',mobileSidebar);
function go(route){if(location.hash==='#/'+route)openRoute();else location.hash='/'+route;}
function clearAccount(){Object.assign(s,defaults(),{user:null});api.csrf=null;issued=null;}
function expired(){clearAccount();toast('Phiên đã hết hạn. Hãy đăng nhập lại.');go('login');}
function handleError(e){if(e.status===401&&s.user)expired();else if(e.code==='password_change_required')go('change-password');else toast(e.message);}
function parseRoute(){const raw=location.hash.replace(/^#\/?/,'')||'home',[path,query='']=raw.split('?'),parts=path.split('/');return{route:parts[0],ids:parts.slice(1),query:new URLSearchParams(query),raw};}
async function loadData(route,ids,q){
  const data={};
  if(route==='guide'){
    const [rules,botGuide]=await Promise.all([api.request('/rules'),loadBotGuide()]);
    return {rules,botGuide};
  }
  if(['login','register','recover','change-password'].includes(route))return data;
  const admin=s.user?.role==='admin';
  const needsTeams=s.user&&['home','team','submissions','benchmark','practice','competitions','jobs','arena','replay'].includes(route)&&!admin;
  const work=[];
  if(s.user&&['rulesets','benchmark','practice','competitions','submissions'].includes(route))work.push(api.request(admin?'/admin/rulesets':'/rulesets').then(x=>data.rulesetCatalog=x));
  if(route==='rulesets'&&ids[0])work.push(api.request('/rulesets/'+enc(ids[0])).then(x=>data.ruleDetail=x));
  if(needsTeams)work.push(api.request('/teams/me').then(x=>data.team=x));
  if(s.user&&['home','submissions','benchmark','practice','competitions','jobs','arena','replay'].includes(route)&&!admin){work.push(api.all('/submissions').then(x=>data.submissions=x));work.push(api.all('/jobs').then(x=>data.jobs=x));}
  if(['home','competitions','schedule','live','standings','arena','replay'].includes(route))work.push(api.all('/competitions').then(x=>data.competitions=x));
  if(route==='home'&&s.user)work.push(api.request(admin?'/admin/metrics':'/dashboard').then(x=>{if(admin)data.metrics=x;else data.dashboard=x;}));
  if(route==='jobs')work.push(api.request('/jobs/'+enc(ids[0])).then(x=>data.job=x));
  if(route==='admin'){
    work.push(api.all('/admin/registrations?status='+(s.registrationStatus||'pending')).then(x=>data.registrations=x));
    work.push(api.all('/admin/accounts').then(x=>data.accounts=x));
    work.push(api.all('/admin/recoveries').then(x=>data.recoveries=x));
  }
  if(route==='operations'){
    work.push(api.all('/admin/jobs').then(x=>data.adminJobs=x));
    work.push(api.all('/admin/submissions').then(x=>data.adminSubmissions=x));
    work.push(api.all('/admin/accounts').then(x=>data.accounts=x));
  }
  if(route==='audit')work.push(api.all('/admin/audit').then(x=>data.audit=x));
  const settled=await Promise.allSettled(work);for(const item of settled)if(item.status==='rejected')throw item.reason;
  const cid=route==='competitions'?ids[0]:['arena','replay'].includes(route)?ids[0]==='practice'?null:ids[0]:['schedule','live','standings'].includes(route)?q.get('competition')||data.competitions?.find(c=>!c.archived_ms)?.id:null;
  if(cid){
    const [competition,fixtures,standings]=await Promise.all([api.request('/competitions/'+enc(cid)),api.all('/competitions/'+enc(cid)+'/fixtures'),api.request('/competitions/'+enc(cid)+'/standings')]);Object.assign(data,{competition,fixtures,standings});
  }
  if(['arena','replay'].includes(route)&&ids[0]==='practice')data.job=await api.request('/jobs/'+enc(ids[1]));
  data.selectedRuleset=q.get('ruleset')||'';data.selectedTurn=q.get('turn')||'';data.selectedMatch=q.get('match')||'';
  data.selectedSubmission=q.get('submission')||data.submissions?.[0]?.id;
  return data;
}
async function openRoute(){
  const parsed=parseRoute(),id=++generation;clearView();
  if(parsed.route==='experimental'){history.replaceState(null,'','#/rulesets');openRoute();return;}
  const supported=['home','login','register','recover','change-password','guide','team','submissions','benchmark','practice','competitions','jobs','admin','operations','rulesets','audit','schedule','live','standings','arena','replay'];
  if(!supported.includes(parsed.route)){go('home');return;}
  if(!s.user&&!publicRoute(parsed.route,parsed.ids)){returnTo=parsed.raw;go('login');return;}
  if(s.user?.must_change_password&&parsed.route!=='change-password'){go('change-password');return;}
  if(s.user&&['login','register'].includes(parsed.route)){go('home');return;}
  if(s.user?.role!=='admin'&&['admin','audit','operations'].includes(parsed.route)){go('home');return;}
  if(s.user?.role==='admin'&&['team','submissions','benchmark','practice','jobs'].includes(parsed.route)){go('home');return;}
  Object.assign(s,defaults(),parsed,{loading:!['login','register','recover','change-password'].includes(parsed.route),error:null,menuOpen:false});render();
  if(parsed.route==='admin'&&['accounts','registrations','recoveries'].includes(parsed.query.get('tab')))s.adminTab=parsed.query.get('tab');
  if(parsed.route==='operations'&&parsed.query.has('status'))s.opsStatus=parsed.query.get('status');
  try{
    const data=await loadData(parsed.route,parsed.ids,parsed.query);if(id!==generation)return;
    Object.assign(s,data,{loading:false,offline:false});
    if(['arena','replay'].includes(s.route)){
      const practice=s.ids[0]==='practice',fixture=s.fixtures.find(f=>f.id===s.ids[1]);
      if(practice&&s.job?.kind!=='match')throw new Error('Tác vụ này không phải trận đấu.');
      if(!practice&&!fixture)throw new Error('Không tìm thấy trận trong giải này.');
      const teamName=tid=>s.competition.entries.find(e=>e.team_id===tid)?.name||short(tid);
      const name=sid=>s.submissions.find(x=>x.id===sid)?.version||short(sid);
      const options={title:practice?'Phòng luyện tập · '+short(s.job.id):s.competition.title+' · Trận '+fixture.ordinal,red:practice?name(s.job.submission_ids[0]):teamName(fixture.red_team_id),blue:practice?name(s.job.submission_ids[1]):teamName(fixture.blue_team_id),back:practice?'practice':'competitions/'+s.competition.id,admin:!practice&&s.user?.role==='admin',officialLive:!practice&&s.route==='arena'};
      options.profile=practice?s.job.profile:s.competition.config.profile;
      app.innerHTML=shell(s,boardPage(options));mobileSidebar();document.title=options.title+' · UET Bot Arena';
      const mountBoard=(current,reset=false)=>{
        board?.close();
        board=new MatchBoard(api,{path:practice?'/jobs/'+s.job.id+'/events':'/competitions/'+s.competition.id+'/fixtures/'+current.id+'/events',official:!practice,terminal:practice?terminal(s.job.status):['completed','attention'].includes(current.status),onExpired:expired,onError:handleError,onReview:()=>competitionFeed?.pause()});
        if(reset)board.reset(undefined);
        board.start();
      };
      if(options.officialLive){
        competitionFeed=new CompetitionFollow(api,{competitionId:s.competition.id,fixtureId:fixture.id,onExpired:expired,onError:handleError,
          onState:state=>{if(id===generation){s.fixtures=state.fixtures;s.competition.status=state.status;}},
          onSwitch:next=>{
            if(id!==generation)return;
            s.ids[1]=next.id;
            history.replaceState(null,'','#/arena/'+s.competition.id+'/'+next.id);
            const title=s.competition.title+' · Trận '+next.ordinal;
            document.querySelector('#main h1').textContent=title;document.title=title+' · UET Bot Arena';
            for(const side of ['red','blue'])document.querySelector('#player-'+side+' strong').textContent=teamName(next[side+'_team_id']);
            mountBoard(next,true);toast('Đang theo dõi trận #'+next.ordinal);
          }});
        competitionFeed.start();
      }
      mountBoard(fixture);
    }else{render();if(s.route==='jobs')watchJob(id);}
  }catch(e){if(id!==generation)return;if(e.status===401){expired();return;}Object.assign(s,{loading:false,error:e.message,offline:!e.status});render();}
}
function connectionStatus(offline){const el=document.querySelector('.connection-label'),text=offline?'Đang nối lại máy chủ':'Kết nối máy chủ';if(el&&el.textContent!==text){el.innerHTML='<i></i>'+text;el.classList.toggle('offline',offline);}}
function patchPage(){
  const template=document.createElement('template');template.innerHTML=page(s);
  const current=document.getElementById('main'),next=template.content.querySelector('#main');
  const focused=document.activeElement;
  if(current&&next)patchChildren(current,next);
  if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
  connectionStatus(false);
}
async function refreshData(){
  const id=generation;
  if(refreshingGeneration===id)return;
  refreshingGeneration=id;
  try{const data=await loadData(s.route,s.ids,s.query);if(id===generation){Object.assign(s,data,{loading:false,error:null,offline:false});patchPage();toast('Đã cập nhật dữ liệu.');}}
  catch(e){if(id===generation){if(!e.status){s.offline=true;connectionStatus(true);}handleError(e);}}
  finally{if(refreshingGeneration===id)refreshingGeneration=null;}
}
function watchJob(id){
  if(!s.job||terminal(s.job.status))return;let seq=0,finishing=false;
  jobFeed=api.stream('/jobs/'+s.job.id+'/events');
  jobFeed.addEventListener('auth_expired',expired);
  jobFeed.addEventListener('arena',async e=>{
    if(id!==generation)return;const event=JSON.parse(e.data);if(event.seq<=seq)return;seq=event.seq;
    if(event.type==='phase')s.job.status=event.data.phase;
    if(event.type==='progress')s.jobProgress=event.data;
    if(terminal(event.type)&&!finishing){finishing=true;jobFeed.close();try{const job=await api.request('/jobs/'+s.job.id);if(id===generation){s.job=job;patchPage();}}catch(error){handleError(error);}return;}
    const node=document.getElementById('job-status');if(node)node.innerHTML=jobDetail(s);
  });
}
function draftRegistration(){const f=document.getElementById('registration-form'),d=new FormData(f);return{team:d.get('team'),email:d.get('email'),phone:d.get('phone'),members:[...f.querySelectorAll('[name^="name-"]')].map((x,i)=>({name:x.value,student_id:d.get('student-'+i)}))};}
const pendingKeys=new WeakMap();
function keyFor(form){if(!pendingKeys.has(form))pendingKeys.set(form,uuid());return pendingKeys.get(form);}
document.addEventListener('input',e=>{if(e.target.form)pendingKeys.delete(e.target.form);});
function showFormError(form,e){const el=form.querySelector('.form-error');if(el){el.classList.add('visible');el.textContent=e.message+(e.requestId?' · Mã hỗ trợ '+e.requestId:'');}else toast(e.message);if(e.status===401&&s.user)expired();}
async function runForm(form,task){if(form.dataset.busy)return;form.dataset.busy='1';const controls=[...form.querySelectorAll('button')],error=form.querySelector('.form-error');controls.forEach(b=>b.disabled=true);if(error){error.classList.remove('visible');error.textContent='';}try{await task();pendingKeys.delete(form);}catch(e){showFormError(form,e);}finally{delete form.dataset.busy;controls.forEach(b=>b.disabled=false);}}
document.addEventListener('submit',e=>{
  const form=e.target;if(!form.matches('.real-form'))return;e.preventDefault();const data=new FormData(form),v=name=>String(data.get(name)||'').trim();
  runForm(form,async()=>{
    if(form.id==='ruleset-form'){
      const definition=readRuleForm(form),r=s.ruleEditing;
      const result=await api.post(r?'/admin/rulesets/'+r.id+'/edit':'/admin/rulesets',{definition,release_notes:v('release-notes'),...(r?{expected_revision:r.revision}:{})},keyFor(form));
      s.ruleEditing=result;s.ruleDraft=null;s.ruleNotes=null;s.rulePreview=null;s.rulePreviewActions=[];await refreshRules();toast('Đã lưu bản nháp. Chọn Kiểm tra & công bố để các đội sử dụng.');
    }else if(form.id==='addon-import-form'){
      const file=data.get('addon-file');if(!file?.size||file.size>32768)throw new Error('Chọn tệp JSON không rỗng, tối đa 32 KiB.');
      let pack;try{pack=JSON.parse(await file.text());}catch{throw new Error('Tệp không phải JSON hợp lệ. Kiểm tra dấu ngoặc, dấu phẩy và encoding UTF-8.');}
      const result=await api.post('/admin/rulesets/import',{package:pack,publish:data.get('publish-import')==='on'},keyFor(form));
      s.ruleEditing=result;s.ruleDraft=null;s.ruleNotes=null;s.rulePreview=null;s.rulePreviewActions=[];await refreshRules();toast(result.status==='published'?'Đã nhập, kiểm tra và công bố ruleset.':'Đã nhập và kiểm tra. Bạn có thể chỉnh tiếp hoặc công bố.');
    }else if(form.id==='login-form'){
      const result=await api.post('/auth/login',{username:v('username'),password:data.get('login-password')});clearAccount();api.csrf=result.csrf_token;s.user=result.user;form.reset();authChanged();go(result.user.must_change_password?'change-password':returnTo);returnTo='home';
    }else if(form.id==='registration-form'){
      const draft=draftRegistration();s.registrationDraft=draft;
      const ids=draft.members.map(m=>m.student_id.toLowerCase());if(new Set(ids).size!==ids.length)throw new Error('Mã sinh viên của các thành viên phải khác nhau.');
      s.registrationReceipt=await api.post('/registrations',{...draft,confirmed:data.get('confirmed')==='on'});s.registrationDraft=null;render();
    }else if(form.id==='recovery-form'){await api.post('/auth/recovery',{username:v('username'),email:v('email')});s.recoverySent=true;render();
    }else if(form.id==='password-form'){
      const pw=data.get('new-password');if(pw.length<8)throw new Error('Mật khẩu mới phải có ít nhất 8 ký tự.');if(pw!==data.get('confirm-password'))throw new Error('Hai lần nhập mật khẩu mới chưa khớp.');
      const result=await api.post('/auth/password',{current_password:data.get('current-password'),new_password:pw});s.user=result.user;form.reset();authChanged();toast('Đã đổi mật khẩu và thu hồi các phiên cũ.');go('home');
    }else if(form.id==='submission-form'){
      const zip=data.get('source');if(!zip.size||zip.size>10*1024*1024)throw new Error('Chọn source ZIP dung lượng từ 1 byte đến 10 MiB.');
      const magic=new Uint8Array(await zip.slice(0,4).arrayBuffer());if(magic[0]!==80||magic[1]!==75)throw new Error('Tệp chưa đúng định dạng ZIP.');
      const result=await api.request('/submissions',{method:'POST',body:zip,headers:{'Content-Type':'application/zip','X-Submission-Version':v('version'),'X-Arena-Runtime':'cpp17','X-Arena-Protocol':v('protocol_version')||'2','Idempotency-Key':keyFor(form)}});toast('Đã lưu phiên bản '+result.version);go('benchmark?submission='+result.id);
    }else if(form.id==='benchmark-form'){
      const job=await api.post('/benchmarks',{submission_id:v('submission_id'),...timeBody(data),...botRulesetSelection(s,[v('submission_id')],v('ruleset_id'))},keyFor(form));go('jobs/'+job.id);
    }else if(form.id==='practice-form'){
      const job=await api.post('/matches',{red_submission_id:v('red_submission_id'),blue_submission_id:v('blue_submission_id'),...timeBody(data),delegate_timeout_claims:data.get('delegate')==='on',...botRulesetSelection(s,[v('red_submission_id'),v('blue_submission_id')],v('ruleset_id'))},keyFor(form));go('arena/practice/'+job.id);
    }else if(form.id==='competition-form'){
      if(s.selectedRuleset&&(!s.rulesetCatalog?.enabled||!s.rulesetCatalog.items.some(r=>r.id===s.selectedRuleset&&r.status==='published')))throw new Error('Ruleset trong liên kết đang tắt hoặc chưa công bố. Bật add-on/chọn luật khả dụng trước; mở lại trang Giải đấu nếu muốn tạo giải tiêu chuẩn.');
      const c=await api.post('/admin/competitions',{title:v('title'),...timeBody(data),max_teams:Number(v('max_teams')),...(data.get('use-experimental')==='on'?{ruleset_id:v('ruleset_id')}: {})},keyFor(form));go('competitions/'+c.id);
    }else if(form.id==='competition-edit-form'){
      await api.post('/admin/competitions/'+form.dataset.id+'/edit',{title:v('title'),...timeBody(data),max_teams:Number(v('max_teams')),expected_revision:Number(form.dataset.revision)},keyFor(form));closeModal();toast('Đã sửa giải.');openRoute();
    }else if(form.id==='admin-team-form'){
      const members=Array.from({length:4},(_,i)=>({name:v('name-'+i),student_id:v('student-'+i)})).filter((m,i)=>i<3||m.name||m.student_id);
      const profile={team:v('team'),email:v('email'),phone:v('phone'),members,confirmed:true};
      if(form.dataset.id){await api.post('/admin/teams/'+form.dataset.id+'/edit',{...profile,expected_revision:Number(form.dataset.revision)});closeModal();toast('Đã sửa thông tin đội.');}
      else{const body={profile,username:v('username')};if(data.get('temporary-password'))body.temporary_password=data.get('temporary-password');credentialModal(await api.post('/admin/teams',body));}
      await refreshAdmin();
    }else if(form.id==='entry-form'){
      const [submission_id,benchmark_id]=v('qualified').split(':');await api.post('/competitions/'+s.competition.id+'/entry',{submission_id,benchmark_id,delegate_timeout_claims:data.get('delegate')==='on'},keyFor(form));toast('Đã chọn bot dự giải.');openRoute();
    }else if(form.id==='approve-form'){
      const body={username:v('username')};if(data.get('temporary-password'))body.temporary_password=data.get('temporary-password');
      const cred=await api.post('/admin/registrations/'+form.dataset.id+'/approve',body);credentialModal(cred);await refreshAdmin();
    }else if(form.id==='reset-form'){
      const body={};if(data.get('temporary-password'))body.temporary_password=data.get('temporary-password');
      const cred=await api.post('/admin/accounts/'+form.dataset.id+'/password',body);credentialModal(cred);await refreshAdmin();
    }
  });
});
function refreshAdminBody(){const el=document.getElementById('admin-table');if(el)el.innerHTML=adminTeamsBody(s);}
function refreshOperationsBody(){const el=document.getElementById('operations-table');if(el)el.innerHTML=operationsBody(s);}
document.addEventListener('input',e=>{
  if(e.target.name==='admin-search'){s.adminSearch=e.target.value;s.adminPage=1;refreshAdminBody();}
  if(e.target.name==='ops-search'){s.opsSearch=e.target.value;s.opsPage=1;refreshOperationsBody();}
});
document.addEventListener('change',async e=>{
  const name=e.target.name,value=e.target.value;
  if(name==='use-experimental'){const label=e.target.form.querySelector('[data-experimental-select]'),select=label.querySelector('select');label.hidden=!e.target.checked;select.required=e.target.checked;if(!e.target.checked)select.value='';}

  if(['submission_id','red_submission_id','blue_submission_id'].includes(name)){
    const form=e.target.form,choice=form?.querySelector('[data-bot-ruleset]');
    if(choice){const ids=form.id==='benchmark-form'?[form.elements.submission_id.value]:[form.elements.red_submission_id.value,form.elements.blue_submission_id.value];const old=choice.querySelector('select').value;choice.outerHTML=botRulesetField(s,ids,old==='classic'?'':old);pendingKeys.delete(form);}
  }
  if(name==='ruleset_id'&&e.target.form)pendingKeys.delete(e.target.form);
  if(name==='competition-select')go(s.route+(value?'?competition='+enc(value):''));
  if(name==='admin-visibility'){s.adminVisibility=value;s.adminPage=1;refreshAdminBody();}
  if(name==='competition-archived'){s.competitionArchived=e.target.checked;render();}
  if(name==='registration-status'){s.registrationStatus=value;s.adminPage=1;try{await refreshAdmin();}catch(error){handleError(error);}}
  const ops={'ops-status':'opsStatus','ops-kind':'opsKind','ops-team':'opsTeam'};
  if(ops[name]){s[ops[name]]=value;s.opsPage=1;refreshOperationsBody();}
});
async function refreshRules(){s.rulesetCatalog=await api.request(s.user?.role==='admin'?'/admin/rulesets':'/rulesets');render();}
async function ruleAction(action,b){
  if(action==='experimental-starter'){saveBlob('ottv2-experimental-v3.zip',await api.blob('/experimental/starter.zip'));return;}
  if(['rule-package','rule-kit','rule-starter'].includes(action)){
    const suffix={'rule-package':'package.json','rule-kit':'kit.zip','rule-starter':'starter.zip'}[action];
    b.disabled=true;try{saveBlob('ruleset-'+b.dataset.id.slice(0,8)+'-'+suffix,await api.blob('/rulesets/'+b.dataset.id+'/'+suffix));}finally{b.disabled=false;}return;
  }
  if(['rule-play-step','rule-play-reset'].includes(action)){
    const id=generation,actions=action==='rule-play-step'?[...s.rulePlayActions,document.querySelector('[name="preview-action"]').value]:[];
    b.disabled=true;try{const result=await api.post('/rulesets/'+s.ruleDetail.id+'/preview',{actions});if(id!==generation)return;s.rulePlay=result;s.rulePlayActions=actions;document.getElementById('team-rule-preview').innerHTML=rulePreviewMarkup(result,actions.length,'rule-play');}finally{b.disabled=false;}return;
  }
  if(action==='experimental-toggle'){
    const form=document.getElementById('ruleset-form');if(form){s.ruleDraft=readRuleForm(form);s.ruleNotes=form.elements.namedItem('release-notes').value;}
    b.disabled=true;try{await api.post('/admin/experimental',{enabled:b.checked,expected_revision:s.rulesetCatalog.revision},keyFor(b));await refreshRules();}catch(e){b.checked=!b.checked;throw e;}finally{b.disabled=false;}return;
  }
  if(action==='rule-new'||action==='rule-preset'||action==='rule-open'){
    s.ruleEditing=action==='rule-open'?s.rulesetCatalog.items.find(r=>r.id===b.dataset.id):null;
    s.ruleDraft=action==='rule-preset'?structuredClone(s.rulesetCatalog.presets[b.dataset.preset]):null;s.ruleNotes=null;s.rulePreview=null;s.rulePreviewActions=[];render();document.getElementById('rule-editor')?.scrollIntoView({block:'start'});return;
  }
  if(['rule-validate','rule-publish','rule-release','rule-clone','rule-archive'].includes(action)){
    const r=s.rulesetCatalog.items.find(r=>r.id===b.dataset.id);b.disabled=true;
    const editor=document.getElementById('ruleset-form');
    if(action==='rule-release'&&s.ruleEditing?.id===r.id&&editor&&(ruleFingerprint(readRuleForm(editor))!==ruleFingerprint(r.definition)||editor.elements.namedItem('release-notes').value.trim()!==(r.release_notes||''))){b.disabled=false;throw new Error('Có thay đổi chưa lưu. Lưu bản nháp trước khi kiểm tra & công bố.');}
    try{const result=await api.post('/admin/rulesets/'+r.id+'/'+action.slice(5),{expected_revision:r.revision},keyFor(b));
    s.ruleEditing=result;s.ruleDraft=null;s.ruleNotes=null;s.rulePreview=null;s.rulePreviewActions=[];await refreshRules();toast(result.status==='published'?'Đã kiểm tra & công bố. Các đội có thể xem luật trong thư viện.':'Đã cập nhật ruleset.');}finally{b.disabled=false;}return;
  }
  const form=document.getElementById('ruleset-form');
  if(action==='rule-zone'){
    const next={'':'energy',energy:'control',control:''}[b.dataset.kind];b.dataset.kind=next;b.className='rule-map-cell'+(next?' zone-'+next:'');b.setAttribute('aria-label',b.dataset.square+': '+(next==='energy'?'năng lượng':next==='control'?'kiểm soát':'thường'));b.querySelector('span').innerHTML=next?icon(next==='energy'?'bolt':'target'):'';pendingKeys.delete(form);return;
  }
  if(['rule-add-modifier','rule-remove-modifier'].includes(action)){
    const d=readRuleForm(form);if(action==='rule-add-modifier'){if(d.modifiers.length>=4)throw new Error('Tối đa 4 mẫu đi bổ sung.');d.modifiers.push({id:'skill'+(d.modifiers.length+1),filter:{kinds:['rock','paper','scissors']},pattern:'orthogonal',range:1,cost:0,cooldown:0});}else d.modifiers.splice(Number(b.dataset.index),1);
    document.getElementById('rule-editor').innerHTML=ruleEditor(d,s.ruleEditing,form.elements.namedItem('release-notes').value);return;
  }
  if(action==='rule-export-editor'){document.getElementById('rule-json').value=JSON.stringify(readRuleForm(form),null,2);document.querySelector('.rule-json').open=true;return;}
  if(action==='rule-import-editor'){
    const definition=JSON.parse(document.getElementById('rule-json').value);await api.post('/admin/rulesets/preview',{definition});document.getElementById('rule-editor').innerHTML=ruleEditor(definition,s.ruleEditing,form.elements.namedItem('release-notes').value);return;
  }
  if(['rule-preview','rule-preview-reset','rule-preview-step'].includes(action)){
    const definition=readRuleForm(form),changed=JSON.stringify(definition)!==JSON.stringify(s.rulePreviewDefinition);
    const actions=action==='rule-preview-step'&&!changed?[...s.rulePreviewActions,document.querySelector('[name="preview-action"]').value]:[];
    b.disabled=true;try{s.rulePreview=await api.post('/admin/rulesets/preview',{definition,actions});s.rulePreviewActions=actions;s.rulePreviewDefinition=definition;document.getElementById('rule-preview').innerHTML=rulePreviewMarkup(s.rulePreview,actions.length);}finally{b.disabled=false;}return;
  }
}
async function refreshAdmin(){const id=generation;const data=await loadData('admin',[],new URLSearchParams());if(id===generation){Object.assign(s,data);render();}}
function modal(title,body){
  if(!modalRoot.childElementCount)modalFocus=document.activeElement;
  modalRoot.innerHTML=`<div class="modal-overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-head"><h2 id="modal-title">${esc(title)}</h2><button class="icon-btn" data-action="close-modal" aria-label="Đóng">${icon('x')}</button></div><div class="modal-body">${body}</div></section></div>`;
  document.body.classList.add('modal-open');modalRoot.querySelector('input,button,a')?.focus();
}
function closeModal(){modalRoot.replaceChildren();document.body.classList.remove('modal-open');issued=null;if(modalFocus?.isConnected)modalFocus.focus();modalFocus=null;}
function confirmAction(title,text,task){modal(title,`<p>${esc(text)}</p><div class="form-error" role="alert"></div><div class="modal-actions">${button('Hủy','close-modal')}${button('Xác nhận','confirm-action','','primary')}</div>`);const b=modalRoot.querySelector('[data-action="confirm-action"]');b.addEventListener('click',async()=>{b.disabled=true;try{await task();closeModal();toast('Đã cập nhật.');openRoute();}catch(e){const el=modalRoot.querySelector('.form-error');el.textContent=e.message;el.classList.add('visible');b.disabled=false;if(e.status===401)expired();}});}
function credentialModal(credential){issued=credential;modal('Tài khoản đã được cấp',`<p>Mật khẩu chỉ được trả ở lần cấp này. Hãy sao chép và phát qua kênh của ban tổ chức. Đội sẽ phải đổi khi đăng nhập.</p><label class="field"><span>Tên đăng nhập</span><div class="credential-value">${esc(credential.account.username)}</div></label><label class="field"><span>Mật khẩu tạm</span><div class="credential-value" id="issued-password">${esc(credential.temporary_password)}</div></label><div class="modal-actions">${button('Sao chép thông tin','copy-credential','','primary')}${button('Đã phát cho đội','credential-delivered')}${button('Đóng','close-modal')}</div>`);}
function teamForm(account){
  const t=account?.team||{name:'',email:'',phone:'',members:[]};
  modal(account?'Sửa đội · '+t.name:'Thêm đội & cấp tài khoản',`<form class="real-form" id="admin-team-form" ${account?`data-id="${t.id}" data-revision="${t.revision}"`:''}>${field('Tên đội','team','required minlength="2" maxlength="40"','text',t.name)}<div class="form-grid">${field('Email đội trưởng','email','required maxlength="120"','email',t.email)}${field('Số điện thoại','phone','maxlength="20"','tel',t.phone)}</div>${Array.from({length:4},(_,i)=>`<fieldset class="member-field"><legend>${i===0?'Đội trưởng':i===3?'Thành viên 4 · Tùy chọn':'Thành viên '+(i+1)}</legend><div class="form-grid">${field('Họ và tên','name-'+i,`${i<3?'required ':''}minlength="2" maxlength="60"`,'text',t.members[i]?.name||'')}${field('Mã sinh viên','student-'+i,`${i<3?'required ':''}pattern="[A-Za-z0-9]{6,15}"`,'text',t.members[i]?.student_id||'')}</div></fieldset>`).join('')}${account?note('Mã đăng nhập giữ nguyên. Kết quả thi đấu vẫn gắn với mã đội khi đổi tên.'):field('Tên đăng nhập','username','required pattern="[A-Za-z0-9_-]{4,30}" autocomplete="off"')+field('Mật khẩu tạm (để trống cho server tạo)','temporary-password','minlength="8" maxlength="128" autocomplete="new-password"','password')+note('Ban tổ chức xác nhận thông tin đội. Tài khoản mới phải đổi mật khẩu khi đăng nhập lần đầu.')}${errors()}${submit(account?'Lưu thông tin':'Tạo đội & cấp tài khoản')}</form>`);
}
function exportCSV(filename,rows){
  const cell=value=>'"'+String(value??'').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
  saveBlob(filename,new Blob(['\uFEFF'+rows.map(r=>r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
}
async function diagnostic(fixtureId){
  const cid=s.competition.id,fid=fixtureId||s.ids[1];const {items}=await api.request(`/admin/competitions/${cid}/fixtures/${fid}/attempts`);
  const jobs=await Promise.all(items.map(a=>api.request(`/admin/competitions/${cid}/fixtures/${fid}/attempts/${a.number}`)));
  const f=s.fixtures.find(x=>x.id===fid);
  modal('Lịch sử lần chạy · '+(f?.ordinal||short(fid)),(items.length?items.map((a,i)=>`<h3>Lần chạy ${a.number} ${badge(jobs[i].status)}</h3><pre class="report-json">${esc(JSON.stringify({error:jobs[i].error,report:jobs[i].report},null,2))}</pre>`).join(''):'<p>Chưa có lần chạy nào.</p>')+(f?.status==='attention'?button('Chạy lại trận','retry-fixture',`data-fixture="${fid}"`,'primary'):'')+note('Chỉ lần chạy hoàn tất hợp lệ được tính điểm. Lịch sử lỗi được giữ khi chạy lại.'));
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-action]');if(!b||b.disabled)return;const action=b.dataset.action;
  try{
    if(action.startsWith('rule-')||action==='experimental-toggle'||action==='experimental-starter'){await ruleAction(action,b);return;}
    if(action.startsWith('board-')){board?.action(action.slice(6),b.dataset.index);return;}
    if(action==='password'){const input=document.getElementById(b.dataset.target),visible=input.type==='password';input.type=visible?'text':'password';b.textContent=visible?'Ẩn':'Hiện';b.setAttribute('aria-label',visible?'Ẩn mật khẩu':'Hiện mật khẩu');return;}
    if(action==='menu'){s.menuOpen=!s.menuOpen;document.querySelector('.app-shell')?.classList.toggle('menu-open',s.menuOpen);document.querySelector('.mobile-menu')?.setAttribute('aria-expanded',String(s.menuOpen));mobileSidebar();return;}
    if(action==='skip-main'){e.preventDefault();document.querySelector('#main')?.focus();return;}
    if(action==='close-modal'){closeModal();return;}
    if(action==='refresh'){await refreshData();return;}
    if(action==='logout'){b.disabled=true;await api.post('/auth/logout');clearAccount();authChanged();go('login');return;}
    if(action==='add-member'||action==='remove-member'){s.registrationDraft=draftRegistration();if(action==='add-member')s.registrationDraft.members.push({name:'',student_id:''});else s.registrationDraft.members.pop();render();return;}
    if(action==='starter'){saveBlob('ottv2-cpp-v2.zip',await api.blob('/starter.zip'));return;}
    if(action==='download-report'){saveBlob('benchmark-'+short(s.job.id)+'.json',await api.blob('/jobs/'+s.job.id+'/report'));return;}
    if(action==='admin-tab'){s.adminTab=b.dataset.tab;s.adminPage=1;s.adminSearch='';render();return;}
    if(action==='ops-tab'){s.opsTab=b.dataset.tab;s.opsPage=1;render();return;}
    if(action==='admin-page'){if(b.dataset.scope==='teams'){s.adminPage=Number(b.dataset.page);refreshAdminBody();}else{s.opsPage=Number(b.dataset.page);refreshOperationsBody();}return;}
    if(action==='team-create'){teamForm();return;}
    if(['team-detail','team-edit','team-archive','team-restore'].includes(action)){
      const a=s.accounts.find(x=>x.id===b.dataset.id),t=a.team;
      if(action==='team-edit')teamForm(a);
      else if(action==='team-detail')modal('Hồ sơ đội · '+t.name,`<dl class="detail-list"><div><dt>Tài khoản</dt><dd>${esc(a.username)}</dd></div><div><dt>Email</dt><dd>${esc(t.email)}</dd></div><div><dt>Điện thoại</dt><dd>${esc(t.phone||'—')}</dd></div><div><dt>Ngày tạo</dt><dd>${date(t.created_ms)}</dd></div><div><dt>Trạng thái</dt><dd>${t.archived_ms?'Đã lưu trữ':a.locked?'Đang khóa':'Đang quản lý'}</dd></div></dl>${table(['Thành viên','Mã sinh viên'],t.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.student_id)}</td></tr>`))}`);
      else{const restore=action==='team-restore';confirmAction((restore?'Khôi phục':'Xóa')+' đội '+t.name+'?',restore?'Đội được đưa lại vào danh sách. Tài khoản vẫn khóa; hãy mở khóa sau khi kiểm tra.':'Tài khoản sẽ bị khóa và mọi phiên đăng nhập bị thu hồi. Lịch sử bot và thi đấu được giữ; có thể khôi phục.',()=>api.post('/admin/teams/'+t.id+'/'+(restore?'restore':'archive'),{expected_revision:t.revision}));}return;
    }
    if(action==='export-teams'){exportCSV('teams.csv',[['Đội','Tài khoản','Email','Điện thoại','Trạng thái','Thành viên'],...s.accounts.map(a=>[a.team.name,a.username,a.team.email,a.team.phone,a.team.archived_ms?'Đã lưu trữ':a.locked?'Khóa':'Hoạt động',a.team.members.map(m=>m.name+' ('+m.student_id+')').join('; ')])]);return;}
    if(action==='resolve-recovery'){confirmAction('Đánh dấu đã xử lý?','Đóng yêu cầu cấp lại này. Thao tác này không đổi mật khẩu.',()=>api.post('/admin/recoveries/'+b.dataset.id+'/resolve'));return;}
    if(action==='admin-job'){
      b.disabled=true;const j=await api.request('/admin/jobs/'+enc(b.dataset.id));b.disabled=false;
      modal('Tác vụ · '+short(j.id),`${badge(j.status)}<p>${esc(j.kind)} · ${date(j.created_unix_ms)}</p><pre class="report-json">${esc(JSON.stringify(j,null,2))}</pre>${button('Tải JSON','admin-job-download',`data-id="${j.id}"`)}${j.official?note('Mở giải đấu tương ứng để xem lịch sử lần chạy hoặc chạy lại trận lỗi.'):''}`);return;
    }
    if(action==='admin-job-download'){saveBlob('job-'+short(b.dataset.id)+'.json',new Blob([JSON.stringify(await api.request('/admin/jobs/'+enc(b.dataset.id)),null,2)],{type:'application/json'}));return;}
    if(action==='submission-manifest'){const item=s.adminSubmissions.find(x=>x.id===b.dataset.id);modal('Source · '+item.version,`<pre class="report-json">${esc(JSON.stringify(item,null,2))}</pre>`);return;}
    if(['competition-edit','competition-archive','competition-restore'].includes(action)){
      const c=s.competitions.find(x=>x.id===b.dataset.id)||s.competition;
      if(action==='competition-edit')modal('Sửa giải · '+c.title,`<form class="real-form" id="competition-edit-form" data-id="${c.id}" data-revision="${c.revision}">${field('Tên giải','title','required minlength="3" maxlength="100"','text',c.title)}${timeField(c.config.profile)}${field('Số đội tối đa','max_teams','required min="2" max="32"','number',c.config.max_teams)}${note('Chỉ đổi thời gian ở bản nháp chưa có đội. Khi mở đăng ký, có thể sửa tên và giới hạn đội trước khi chốt lịch.')}${errors()}${submit('Lưu cấu hình')}</form>`);
      else{const restore=action==='competition-restore';confirmAction((restore?'Khôi phục':'Xóa')+' giải '+c.title+'?',restore?'Giải trở lại danh sách, giữ nguyên trạng thái trước khi xóa.':'Chỉ xóa bản nháp / giải mở chưa có đội chọn bot, hoặc giải đã hoàn tất. Toàn bộ lịch sử và kết quả được giữ.',async()=>{await api.post('/admin/competitions/'+c.id+'/'+(restore?'restore':'archive'),{expected_revision:c.revision},keyFor(b));});}return;
    }
    if(action==='review-registration'){
      const r=s.registrations.find(x=>x.id===b.dataset.id);
      if(r.status!=='pending'){modal('Hồ sơ · '+r.team,`${badge(r.status)}<p>${esc(r.email)} · ${esc(r.phone||'—')}</p>${table(['Thành viên','Mã sinh viên'],r.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.student_id)}</td></tr>`))}`);return;}
      modal('Duyệt đội · '+r.team,`<p>${esc(r.email)} · ${esc(r.phone||'Không có số điện thoại')}</p>${table(['Thành viên','Mã sinh viên'],r.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.student_id)}</td></tr>`))}<form class="real-form" id="approve-form" data-id="${r.id}">${field('Tên đăng nhập sẽ cấp','username','required pattern="[A-Za-z0-9_-]{4,30}"','text','uet-'+r.id.slice(0,8))}${field('Mật khẩu tạm (để trống cho server tạo)','temporary-password','minlength="8" maxlength="128" autocomplete="new-password"','password')}${errors()}<div class="modal-actions">${button('Từ chối hồ sơ','reject-registration',`data-id="${r.id}"`)}${submit('Duyệt & cấp tài khoản')}</div></form>`);return;
    }
    if(action==='reject-registration'){confirmAction('Từ chối hồ sơ','Hồ sơ này sẽ được đánh dấu từ chối; chưa tạo tài khoản đội.',()=>api.post('/admin/registrations/'+b.dataset.id+'/reject'));return;}
    if(action==='reset-password'){
      const a=s.accounts.find(x=>x.id===b.dataset.id);
      modal('Cấp lại mật khẩu · '+(a?.username||'đội'),`<p>Các phiên hiện tại sẽ bị thu hồi. Tài khoản giữ nguyên trạng thái khóa.</p><form class="real-form" id="reset-form" data-id="${b.dataset.id}">${field('Mật khẩu tạm (để trống cho server tạo)','temporary-password','minlength="8" maxlength="128" autocomplete="new-password"','password')}${errors()}${submit('Cấp lại mật khẩu')}</form>`);return;
    }
    if(action==='account-lock'){const locked=b.dataset.locked!=='true';confirmAction(locked?'Khóa tài khoản?':'Mở khóa tài khoản?',locked?'Đội sẽ bị đăng xuất và không thể đăng nhập cho tới khi mở khóa.':'Đội có thể đăng nhập lại với mật khẩu hiện tại.',()=>api.post('/admin/accounts/'+b.dataset.id+'/lock',{locked}));return;}
    if(action==='delivered'){b.disabled=true;await api.post('/admin/accounts/'+b.dataset.id+'/delivered');await refreshAdmin();return;}
    if(action==='copy-credential'){if(issued){await navigator.clipboard.writeText('Tài khoản: '+issued.account.username+'\nMật khẩu tạm: '+issued.temporary_password);toast('Đã sao chép. Hãy phát qua kênh của ban tổ chức.');}return;}
    if(action==='credential-delivered'){if(issued){b.disabled=true;await api.post('/admin/accounts/'+issued.account.id+'/delivered');toast('Đã đánh dấu phát tài khoản.');closeModal();await refreshAdmin();}return;}
    if(action==='competition-action'){
      const command=b.dataset.command,c=s.competition;
      if(command==='lock'){confirmAction('Chốt bot và tạo lịch?',`Giải “${c.title}” hiện có ${c.entries.length} đội: ${c.entries.map(x=>x.name+' ('+x.version+')').join(', ')}. Sau khi chốt, các đội không thể thay bot.`,()=>api.post('/admin/competitions/'+c.id+'/lock',{},keyFor(b)));}
      else{b.disabled=true;await api.post('/admin/competitions/'+c.id+'/'+command,{},keyFor(b));openRoute();}return;
    }
    if(action==='withdraw-entry'||action==='remove-entry'){const path=action==='withdraw-entry'?'/competitions/'+s.competition.id+'/withdraw':`/admin/competitions/${s.competition.id}/entries/${b.dataset.team}/remove`;confirmAction('Rút bot khỏi giải?','Bot này được rút trước khi chốt lịch. Đội có thể chọn bot lại khi giải còn mở đăng ký.',()=>api.post(path,{},keyFor(b)));return;}
    if(action==='diagnostic'){await diagnostic(b.dataset.fixture);return;}
    if(action==='retry-fixture'){const fid=b.dataset.fixture;confirmAction('Chạy lại trận?','Tạo lần chạy mới cho trận chưa tính điểm. Lịch sử cũ được giữ lại.',()=>api.post(`/admin/competitions/${s.competition.id}/fixtures/${fid}/retry`,{},keyFor(b)));return;}
    if(action==='export-standings'){
      const csvCell=value=>'"'+String(value??'').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
      const rows=[['Hạng','Đội','Phiên bản','Trận','Thua','Hòa','Thắng','Điểm'],...s.standings.items.map(x=>[x.rank,x.name,x.version,x.played,x.losses,x.draws||0,x.wins,x.points])];saveBlob('standings.csv',new Blob(['\uFEFF'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    }
  }catch(error){b.disabled=false;handleError(error);}
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&s.menuOpen&&!modalRoot.childElementCount){s.menuOpen=false;document.querySelector('.app-shell')?.classList.remove('menu-open');document.querySelector('.mobile-menu')?.setAttribute('aria-expanded','false');mobileSidebar();document.querySelector('.mobile-menu')?.focus();return;}
  if(!modalRoot.childElementCount)return;if(e.key==='Escape'){closeModal();return;}
  if(e.key==='Tab'){const focusable=[...modalRoot.querySelectorAll('button:not(:disabled),input:not(:disabled),a[href],select')],first=focusable[0],last=focusable.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
});
window.addEventListener('hashchange',openRoute);window.addEventListener('beforeunload',clearView);
async function boot(){
  try{s.platform=await api.request('/platform');try{const session=await api.request('/auth/me');s.user=session.user;}catch(e){if(e.status!==401)throw e;}await openRoute();}
  catch(e){s.loading=false;s.error=e.message;render();}
}
boot();
