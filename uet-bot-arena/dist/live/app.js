import {readPracticeFile} from './quick-practice.js';
import {competitionRoute} from './competition-ui.js';
import {participantData,privatePage} from './page-data.js';
import {visiblePoll,githubPollMs} from './polling.js';
import {tr,html as l10n,language,setLanguage} from './i18n.js';
import {startAnnouncements} from './announcements.js';
import {readTournamentOptions,updateLadderPreview} from './tournament-options.js';
import {timeField,timeBody,competitionProfile} from './time-control.js';
import {ArenaAPI,saveBlob,uuid,copyText} from './client.js';
import {page,jobDetail} from './pages.js';
import {esc,icon,button,field,table,note,errors,submit,shell,badge,short,select,date} from './view.js';
import {MatchBoard,boardPage} from './board.js';
import {CompetitionFollow} from './follow.js';
import {patchChildren} from './patch.js';
import {adminTeamsBody,operationsBody} from './admin-ui.js';
import {graderTokenModal,graderMessage,validPackage,controlsMessage} from './graders-ui.js';
import {loadBotGuide} from './bot-guide.js';
import {publicRoute} from './access.js';
import {readRuleForm,ruleEditor,rulePreviewMarkup,botRulesetField,botRulesetSelection,ruleFingerprint} from './experimental-ui.js';

const api=new ArenaAPI(),app=document.getElementById('app'),modalRoot=document.getElementById('modal-root');
const defaults=()=>({recentMatch:null,initialHistory:null,initialClock:null,announcements:[],adminAnnouncements:[],duplicates:[],queue:null,rankingLookup:null,historyTotal:null,teamElo:null,teamCompetitions:null,competitionSummary:null,selectedCompetition:'',github:null,personal:null,people:[],matching:null,teamPolicy:null,teamMerges:null,teamChoices:[],team:null,competitions:[],competition:null,fixtures:[],standings:null,submissions:[],jobs:[],job:null,dashboard:null,registrations:[],accounts:[],recoveries:[],audit:[],jobProgress:null,metrics:null,adminJobs:[],adminSubmissions:[],rulesetCatalog:null,ruleEditing:null,ruleDraft:null,ruleNotes:null,ruleDetail:null,rulePlay:null,rulePlayActions:[],rulePreview:null,rulePreviewDefinition:null,rulePreviewActions:[]});
const s={...defaults(),user:null,emailRequest:null,emailAddress:'',route:'home',ids:[],loading:false,adminTab:'accounts',registrationStatus:'pending',adminVisibility:'active',adminSearch:'',adminPage:1,opsTab:'jobs',opsPage:1,opsSearch:'',opsStatus:'',opsKind:'',opsTeam:''};
let emailPoll,githubPoll;
let generation=0,board,competitionFeed,jobFeed,toastTimer,modalFocus,returnTo='home',issued=null,issuedGrader=null,refreshingGeneration=null;
const refreshNotices=startAnnouncements(api);
const authChannel=typeof BroadcastChannel==='function'?new BroadcastChannel('uet-arena-session'):null;
authChannel?.addEventListener('message',async()=>{try{const session=await api.request('/auth/me',{poll:true});Object.assign(s,defaults(),{user:session.user,canManageGraders:session.can_manage_graders});openRoute();}catch(e){if(e.status===401)expired(false);else handleError(e);}});
const authChanged=()=>authChannel?.postMessage({type:'changed'});
const terminal=status=>['completed','failed','aborted'].includes(status);
const enc=encodeURIComponent;
function toast(message){const el=document.getElementById('toast');el.className='toast show';el.textContent=message;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),6000);}
async function copyMessage(text,message){toast(await copyText(text)?message:tr('Hãy bôi đen rồi nhấn Ctrl+C'));}
function clearView(){eloPoll?.close();githubPoll?.close();emailPoll?.close();competitionFeed?.close();competitionFeed=null;board?.close();board=null;jobFeed?.close();jobFeed=null;closeModal();}
function render(){app.innerHTML=page(s);const title={home:'UET Bot Arena',login:tr('Đăng nhập'),register:tr('Đăng ký đội'),recover:tr('Cấp lại mật khẩu'),'change-password':tr('Đổi mật khẩu')}[s.route]||document.querySelector('#main h1')?.textContent||'UET Bot Arena';document.title=title==='UET Bot Arena'?title:title+' · UET Bot Arena';mobileSidebar();updateLadderPreview(document.getElementById('competition-form'));}
function mobileSidebar(){const sidebar=document.querySelector('.sidebar');if(sidebar)sidebar.inert=matchMedia('(max-width:760px)').matches&&!s.menuOpen;}
matchMedia('(max-width:760px)').addEventListener('change',mobileSidebar);
function go(route){if(location.hash==='#/'+route)openRoute();else location.hash='/'+route;}
function clearAccount(){Object.assign(s,defaults(),{user:null,emailRequest:null,emailAddress:'',emailPurpose:'verify',passwordFinished:false,inviteCode:''});api.csrf=null;api.admin=false;issued=null;}
function expired(notify=true){clearAccount();if(notify)authChanged();toast(tr('Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.'));go('login');}
async function refreshPerson(){
  const session=await api.request('/auth/me');s.user=session.user;s.canManageGraders=session.can_manage_graders;authChanged();go('team');
}
function handleError(e){if(e.status===401&&s.user)expired();else if(e.code==='password_change_required')go('change-password');else toast(e.message);}
function parseRoute(){const raw=location.hash.replace(/^#\/?/,'')||'home',[path,query='']=raw.split('?'),parts=path.split('/');return{route:parts[0],ids:parts.slice(1),query:new URLSearchParams(query),raw};}
async function loadData(route,ids,q){
  api.admin=s.user?.role==='admin';
  if(!api.admin)return participantData(api,s.user,route,ids,q);
  const data={selectedCompetition:q.get('competition')||''};
  const scope=data.selectedCompetition?'?competition='+enc(data.selectedCompetition):'';
  if(s.user&&s.user.role!=='admin'&&['home','team','submissions','competitions'].includes(route))data.personal=await api.request('/users/me');
  if(route==='team'&&s.user?.team_id)data.teamElo=await api.request('/team/elo');
  if(route==='matching')data.matching=await api.request('/matching');
  if(route==='users'){
    [data.people,data.teamPolicy,data.teamMerges,data.teamChoices]=await Promise.all([api.all('/admin/users'+scope),api.request('/admin/team-policy'),api.request('/admin/team-merges'),api.all('/admin/teams'+scope)]);
  }
  if(['practice','operations'].includes(route))data.sampleBots=(await api.request(s.user?.role==='admin'?'/admin/sample-bots':'/sample-bots')).items;
  if(route==='guide'){
    const [rules,botGuide]=await Promise.all([api.request('/rules?lang='+language()),loadBotGuide()]);
    return {rules,botGuide};
  }
  if(['login','register','recover','change-password'].includes(route))return data;
  const admin=s.user?.role==='admin';
  const needsTeams=s.user?.team_id&&['home','team','submissions','benchmark','practice','competitions','jobs','arena','replay'].includes(route)&&!admin;
  const work=[];
  if(route==='announcements'){work.push(api.snapshot('announcements.json').then(x=>data.announcements=x.items));if(admin)work.push(api.request('/admin/announcements').then(x=>data.adminAnnouncements=x.items));}
  if(route==='queue')work.push(api.snapshot('queue.json').then(x=>data.queue=x));
  if(s.user&&['rulesets','benchmark','practice','competitions','submissions'].includes(route))work.push(api.request(admin?'/admin/rulesets':'/rulesets').then(x=>data.rulesetCatalog=x));
  if(route==='rulesets'&&ids[0])work.push(api.request('/rulesets/'+enc(ids[0])).then(x=>data.ruleDetail=x));
  if(s.user?.team_id&&!admin&&['team','submissions'].includes(route))work.push(api.request('/teams/me/github').then(x=>data.github=x));
  if(needsTeams)work.push(api.request('/teams/me').then(x=>data.team=x));
  if(s.user?.team_id&&['home','submissions','benchmark','practice','competitions','jobs','arena','replay'].includes(route)&&!admin){work.push(api.all('/submissions').then(x=>data.submissions=x));work.push(api.all('/jobs').then(x=>data.jobs=x));}
  if(['home','admin','users','competitions','schedule','live','standings','arena','replay'].includes(route))work.push(api.all('/competitions').then(x=>data.competitions=x));
  if(route==='home'&&data.selectedCompetition)work.push(api.request('/admin/competitions/'+enc(data.selectedCompetition)+'/summary').then(x=>data.competitionSummary=x));
  if(route==='home'&&s.user&&(admin||s.user.team_id))work.push(api.request(admin?'/admin/metrics':'/dashboard').then(x=>{if(admin)data.metrics=x;else data.dashboard=x;}));
  if(route==='jobs')work.push(api.request('/jobs/'+enc(ids[0])).then(x=>data.job=x));
  if(route==='admin'){
    work.push(api.all('/admin/users'+scope).then(x=>data.people=x));
    work.push(api.all('/admin/registrations?status='+(s.registrationStatus||'pending')).then(x=>data.registrations=x));
    work.push(api.all('/admin/accounts'+scope).then(x=>data.accounts=x));
    work.push(api.all('/admin/recoveries').then(x=>data.recoveries=x));
  }
  if(route==='competitions'&&ids[0]&&admin)work.push(api.all('/admin/accounts').then(x=>data.entryTeams=[...new Map(x.filter(a=>a.team&&!a.team.archived_ms).map(a=>[a.team.id,a.team])).values()]));
  if(route==='operations'){
    work.push(api.request('/admin/github/duplicates').then(x=>data.duplicates=x.items));
    work.push(api.all('/admin/jobs').then(x=>data.adminJobs=x));
    work.push(api.all('/admin/submissions').then(x=>data.adminSubmissions=x));
    work.push(api.all('/admin/accounts').then(x=>data.accounts=x));
  }
  if(route==='audit')work.push(api.all('/admin/audit').then(x=>data.audit=x));
  if(route==='email')work.push(api.request('/admin/email').then(x=>data.mail=x));
  if(route==='graders'){work.push(api.request('/admin/graders').then(x=>data.graders=x));
    // A local hub may not publish a grader package.
    if(s.canManageGraders!==false)work.push(api.request('/admin/grader-package').catch(()=>null).then(x=>data.graderPackage=validPackage(x)));}
  const settled=await Promise.allSettled(work);for(const item of settled)if(item.status==='rejected')throw item.reason;
  const cid=route==='competitions'?ids[0]:['arena','replay'].includes(route)?ids[0]==='practice'?null:ids[0]:route==='benchmark'?q.get('competition'):['schedule','live','standings'].includes(route)?q.get('competition')||data.competitions?.find(c=>!c.archived_ms)?.id:null;
  if(cid){
    const [competition,fixtures,standings]=await Promise.all([api.request('/competitions/'+enc(cid)),api.all('/competitions/'+enc(cid)+'/fixtures'),api.request('/competitions/'+enc(cid)+'/standings')]);Object.assign(data,{competition,fixtures,standings});
    if(!s.user){const summary=await api.snapshot('fixtures/'+enc(cid)+'.json');data.historyTotal=summary.history_total; if(['arena','replay'].includes(route)&&ids[1]&&!fixtures.some(f=>f.id===ids[1]))fixtures.push((await api.snapshot('fixtures/'+enc(cid)+'/'+enc(ids[1])+'.json')).fixture);}
  }
  if(['arena','replay'].includes(route)&&ids[0]==='practice')data.job=await api.request('/jobs/'+enc(ids[1]));
  data.benchmarkCompetition=route==='benchmark'?q.get('competition'):null;
  data.selectedRuleset=q.get('ruleset')||'';data.selectedTurn=q.get('turn')||'';data.selectedMatch=q.get('match')||'';
  data.selectedSubmission=q.get('submission')||data.submissions?.[0]?.id;
  return data;
}
async function openRoute(){
  const parsed=parseRoute(),id=++generation;clearView();
  if(parsed.route==='experimental'){history.replaceState(null,'','#/rulesets');openRoute();return;}
  const supported=['home','login','verify-email','set-password','guest-register','guest-recover','email','register','recover','change-password','guide','team','matching','users','submissions','benchmark','practice','competitions','jobs','admin','operations','graders','rulesets','audit','schedule','live','standings','queue','announcements','limits','arena','replay'];
  if(!supported.includes(parsed.route)){go('home');return;}
  if(!s.user&&!publicRoute(parsed.route,parsed.ids)){returnTo=parsed.raw;go('login');return;}
  if(s.user?.must_change_password&&parsed.route!=='change-password'){go('change-password');return;}
  if(s.user&&['login','register'].includes(parsed.route)){go('home');return;}
  if(s.user?.role!=='admin'&&['admin','audit','operations','graders','email','users'].includes(parsed.route)){go('home');return;}
  if(s.user?.role==='admin'&&['team','submissions','benchmark','practice','jobs'].includes(parsed.route)){go('home');return;}
  if(s.user&&!s.user.team_id&&s.user.role!=='admin'&&['submissions','benchmark','practice','jobs'].includes(parsed.route)){go('team');return;}
  if(['register','recover','verify-email'].includes(parsed.route)){
    const purpose=parsed.route==='recover'?'reset':'verify';
    if(purpose!==s.emailPurpose){s.emailRequest=null;s.emailAddress='';s.passwordFinished=false;}
    s.emailPurpose=purpose;
  }
  if(parsed.query.has('invite'))s.inviteCode=parsed.query.get('invite');
  Object.assign(s,defaults(),parsed,{loading:!['login','register','recover','change-password'].includes(parsed.route),error:null,menuOpen:false});render();
  if(parsed.route==='admin'&&['accounts','registrations','recoveries'].includes(parsed.query.get('tab')))s.adminTab=parsed.query.get('tab');
  if(parsed.route==='operations'&&parsed.query.has('status'))s.opsStatus=parsed.query.get('status');
  try{
    const data=await loadData(parsed.route,parsed.ids,parsed.query);if(id!==generation)return;
    Object.assign(s,data,{loading:false,offline:false});
    if(['arena','replay'].includes(s.route)){
      const practice=s.ids[0]==='practice',fixture=s.fixtures.find(f=>f.id===s.ids[1]);
      if(practice&&s.job?.kind!=='match')throw new Error(tr('Tác vụ này không phải trận đấu.'));
      if(!practice&&!fixture)throw new Error(tr('Không tìm thấy trận trong giải này.'));
      const teamName=tid=>s.competition.entries.find(e=>e.team_id===tid)?.name||short(tid);
      const name=sid=>s.submissions.find(x=>x.id===sid)?.version||short(sid);
      const options={title:practice?tr('Phòng luyện tập · ')+short(s.job.id):s.competition.title+tr(' · Trận ')+fixture.ordinal,red:practice?name(s.job.submission_ids[0]):teamName(fixture.red_team_id),blue:practice?name(s.job.submission_ids[1]):teamName(fixture.blue_team_id),back:practice?'practice':'competitions/'+s.competition.id,admin:!practice&&s.user?.role==='admin',officialLive:!practice&&s.route==='arena'};
      options.format=practice?'practice':s.competition.config.format;
      options.profile=practice?s.job.profile:competitionProfile(s.competition);
      options.basicRules=(practice?s.job.basic_rules:s.competition.config.basic_rules)?.definition;
      app.innerHTML=shell(s,boardPage(options));mobileSidebar();document.title=options.title+' · UET Bot Arena';
      const mountBoard=(current,reset=false)=>{
        board?.close();
        board=new MatchBoard(api,{path:practice?'/jobs/'+s.job.id+'/events':'/competitions/'+s.competition.id+'/fixtures/'+current.id+'/events',official:!practice,review:s.route==='replay',initialHistory:practice?s.initialHistory:null,initialClock:practice?s.initialClock:null,profile:options.profile,terminal:practice?terminal(s.job.status):['completed','attention'].includes(current.status),onExpired:expired,onError:handleError,onReview:()=>competitionFeed?.pause()});
        if(reset)board.reset(undefined);
        board.start(reset);
      };
      if(options.officialLive){
        competitionFeed=new CompetitionFollow(api,{competitionId:s.competition.id,fixtureId:fixture.id,currentFixture:fixture,onExpired:expired,onError:handleError,
          onState:state=>{if(id===generation){s.fixtures=state.fixtures;s.competition.status=state.status;}},
          onSwitch:next=>{
            if(id!==generation)return;
            s.ids[1]=next.id;
            history.replaceState(null,'','#/arena/'+s.competition.id+'/'+next.id);
            const title=s.competition.title+tr(' · Trận ')+next.ordinal;
            document.querySelector('#main h1').textContent=title;document.title=title+' · UET Bot Arena';
            for(const side of ['red','blue'])document.querySelector('#player-'+side+' strong').textContent=teamName(next[side+'_team_id']);
            mountBoard(next,true);toast(tr('Đang theo dõi trận #')+next.ordinal);
          }});
        competitionFeed.start();
      }
      mountBoard(fixture);
    }else{render();watchElo(id);if(['team','submissions'].includes(s.route))watchGitHub(id);if(s.route==='jobs')watchJob(id);if(['register','recover','verify-email'].includes(s.route))watchEmail(id);}
  }catch(e){if(id!==generation)return;if(e.status===401){expired();return;}Object.assign(s,{loading:false,error:e.message,offline:!e.status});render();}
}
let eloPoll;
function watchElo(id){
  eloPoll?.close();
  if(id!==generation)return;
  const cid=s.competition?.id,common=['home','competitions','schedule','live','standings','queue','announcements'].includes(s.route);
  if(!common)return;
  eloPoll=visiblePoll(async()=>{
    if(id!==generation)return;
    const data={};
    if(s.route==='queue')data.queue=await api.snapshot('queue.json');
    else if(s.route==='announcements')data.announcements=(await api.snapshot('announcements.json')).items;
    else{
      data.competitions=await api.all('/competitions',{poll:true});
      if(cid){
        const [competition,standings,fixtures]=await Promise.all([api.request('/competitions/'+cid,{poll:true}),api.request('/competitions/'+cid+'/standings',{poll:true}),api.request('/competitions/'+cid+'/fixtures',{poll:true})]);
        Object.assign(data,{competition,standings,fixtures:fixtures.items,historyTotal:fixtures.history_total});
      }
    }
    if(id===generation){Object.assign(s,data);patchPage();}
  },10000,{onError:handleError});
}
function watchGitHub(id){
  githubPoll?.close();
  if(!s.user?.team_id||!['team','submissions'].includes(s.route)||id!==generation)return;
  githubPoll=visiblePoll(async()=>{
    const next=await privatePage(api,s.route,true);
    if(id===generation){Object.assign(s,next);patchPage();}
  },()=>s.platform?.team_zip_upload&&s.jobs.some(j=>j.kind==='benchmark'&&!['completed','failed','aborted'].includes(j.status))?3000:githubPollMs(s.github),{onError:handleError});
}
function watchEmail(id){
  emailPoll?.close();
  const r=s.emailRequest;
  if(id!==generation||!['register','recover','verify-email'].includes(s.route)||!r||r.verified_ms||r.status==='dropped'||r.status==='sent'&&!r.resend_after_seconds)return;
  emailPoll=visiblePoll(async()=>{
    const next=await api.request('/auth/email/requests/'+r.id,{poll:true});
    if(id===generation&&s.emailRequest?.id===r.id&&!s.emailRequest.verified_ms){s.emailRequest=next;patchPage();if(next.verified_ms)emailPoll.close();}
  },3000,{onError:handleError});
}
function connectionStatus(offline){const el=document.querySelector('.connection-label'),text=offline?tr('Đang nối lại máy chủ'):tr('Kết nối máy chủ');if(el&&el.textContent!==text){el.innerHTML='<i></i>'+text;el.classList.toggle('offline',offline);}}
function patchPage(){
  const template=document.createElement('template');template.innerHTML=page(s);
  const current=document.getElementById('main'),next=template.content.querySelector('#main');
  const focused=document.activeElement;
  if(current&&next)patchChildren(current,next);
  if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
  updateLadderPreview(document.getElementById('competition-form'));
  connectionStatus(false);
}
async function refreshData(){
  const id=generation;
  if(refreshingGeneration===id)return;
  refreshingGeneration=id;
  try{const data=await loadData(s.route,s.ids,s.query);if(id===generation){Object.assign(s,data,{loading:false,error:null,offline:false});patchPage();toast(tr('Đã cập nhật dữ liệu.'));}}
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
    if(terminal(event.type)&&!finishing){finishing=true;jobFeed.close();try{const job=await api.request('/jobs/'+s.job.id,{poll:true});if(id===generation){s.job=job;patchPage();}}catch(error){handleError(error);}return;}
    const node=document.getElementById('job-status');if(node)node.innerHTML=jobDetail(s);
  });
}
function draftRegistration(){const f=document.getElementById('registration-form'),d=new FormData(f);return{team:d.get('team'),email:d.get('email'),phone:d.get('phone'),members:[...f.querySelectorAll('[name^="name-"]')].map((x,i)=>({name:x.value,student_id:d.get('student-'+i)}))};}
const pendingKeys=new WeakMap();
function keyFor(form){if(!pendingKeys.has(form))pendingKeys.set(form,uuid());return pendingKeys.get(form);}
document.addEventListener('input',e=>{if(e.target.form)pendingKeys.delete(e.target.form);});
// Wrong login shows one plain sentence; the support ID is only useful for server-side failures.
function showFormError(form,e){const el=form.querySelector('.form-error');const message=form.id==='login-form'&&e.status===401?tr('Sai tài khoản hoặc mật khẩu.'):e.message;if(el){el.classList.add('visible');el.textContent=message+(e.requestId&&(!e.status||e.status>=500)?tr(' · Mã hỗ trợ ')+e.requestId:'');}else toast(message);if(e.status===401&&s.user)expired();}
async function runForm(form,task){if(form.dataset.busy)return;form.dataset.busy='1';const controls=[...form.querySelectorAll('button')],error=form.querySelector('.form-error');controls.forEach(b=>b.disabled=true);const loginButton=form.id==='login-form'?form.querySelector('[type=submit]'):null,loginLabel=loginButton?.textContent;if(loginButton)loginButton.textContent=tr('Đang đăng nhập…');if(error){error.classList.remove('visible');error.textContent='';}try{await task();pendingKeys.delete(form);}catch(e){showFormError(form,e);}finally{delete form.dataset.busy;controls.forEach(b=>b.disabled=false);if(loginButton)loginButton.textContent=loginLabel;}}
document.addEventListener('change',async e=>{if(e.target.id!=='practice-file'||!e.target.files[0])return;const form=e.target.form;try{const value=await readPracticeFile(e.target.files[0]);form.elements.code.value=value.code;form.elements.runtime.value=value.runtime;pendingKeys.delete(form);}catch(error){showFormError(form,error);}});
document.addEventListener('click',async e=>{const b=e.target.closest('[data-action=practice-example]');if(!b)return;try{const response=await fetch('/_ui/bot-example.cpp');if(!response.ok)throw new Error(tr('Không đọc được file.'));b.form.elements.code.value=await response.text();b.form.elements.runtime.value='cpp17';pendingKeys.delete(b.form);}catch(error){showFormError(b.form,error);}});
document.addEventListener('submit',e=>{
  const form=e.target;if(!form.matches('.real-form'))return;e.preventDefault();const data=new FormData(form),v=name=>String(data.get(name)||'').trim();
  runForm(form,async()=>{
    if(form.id==='announcement-form'){await api.post('/admin/announcements',{title:v('title'),body:v('body')},keyFor(form));await refreshNotices();await refreshData();
    }else if(form.id==='standings-lookup'){s.rankingLookup=await api.snapshot('standings/'+enc(s.competition.id)+'/'+enc(v('team_id'))+'.json');patchPage();
    }else if(form.id==='ruleset-form'){
      const definition=readRuleForm(form),r=s.ruleEditing;
      const result=await api.post(r?'/admin/rulesets/'+r.id+'/edit':'/admin/rulesets',{definition,release_notes:v('release-notes'),...(r?{expected_revision:r.revision}:{})},keyFor(form));
      s.ruleEditing=result;s.ruleDraft=null;s.ruleNotes=null;s.rulePreview=null;s.rulePreviewActions=[];await refreshRules();toast(tr('Đã lưu bản nháp. Chọn Kiểm tra & công bố để các đội sử dụng.'));
    }else if(form.id==='addon-import-form'){
      const file=data.get('addon-file');if(!file?.size||file.size>32768)throw new Error(tr('Chọn tệp JSON không rỗng, tối đa 32 KiB.'));
      let pack;try{pack=JSON.parse(await file.text());}catch{throw new Error(tr('Tệp không phải JSON hợp lệ. Kiểm tra dấu ngoặc, dấu phẩy và encoding UTF-8.'));}
      const result=await api.post('/admin/rulesets/import',{package:pack,publish:data.get('publish-import')==='on'},keyFor(form));
      s.ruleEditing=result;s.ruleDraft=null;s.ruleNotes=null;s.rulePreview=null;s.rulePreviewActions=[];await refreshRules();toast(result.status==='published'?tr('Đã nhập, kiểm tra và công bố ruleset.'):tr('Đã nhập và kiểm tra. Bạn có thể chỉnh tiếp hoặc công bố.'));
    }else if(form.id==='login-form'){
      const result=await api.post('/auth/login',{username:v('username'),password:data.get('login-password')});clearAccount();api.csrf=result.csrf_token;s.user=result.user;s.canManageGraders=result.can_manage_graders;form.reset();authChanged();go(result.user.must_change_password?'change-password':returnTo);returnTo='home';
    }else if(form.id==='registration-form'){
      const draft=draftRegistration();s.registrationDraft=draft;
      const ids=draft.members.map(m=>m.student_id.toLowerCase());if(new Set(ids).size!==ids.length)throw new Error(tr('Mã sinh viên của các thành viên phải khác nhau.'));
      s.registrationReceipt=await api.post('/registrations',{...draft,confirmed:data.get('confirmed')==='on'});s.registrationDraft=null;render();
    }else if(form.id==='email-request-form'){
      s.emailAddress=v('email');s.emailRequest=await api.post('/auth/email/request',{email:s.emailAddress,purpose:s.emailPurpose||'verify'});s.passwordFinished=false;render();watchEmail(generation);
    }else if(form.id==='email-code-form'){
      clearTimeout(emailPoll);s.emailRequest=await api.post('/auth/email/verify',{request_id:s.emailRequest.id,code:v('email-code')});render();
    }else if(form.id==='personal-password-form'){
      const pw=data.get('new-password');if(pw!==data.get('confirm-password'))throw new Error(tr('Hai mật khẩu chưa khớp.'));
      await api.post(s.emailPurpose==='reset'?'/auth/reset':'/auth/enroll',{request_id:s.emailRequest.id,verification_token:s.emailRequest.verification_token,password:pw,display_name:v('display-name')});
      if(s.emailPurpose==='reset'){clearAccount();s.emailPurpose='reset';authChanged();}
      s.emailRequest=null;s.passwordFinished=true;form.reset();render();
    }else if(form.id==='personal-profile-form'){
      await api.post('/users/me',{display_name:v('display-name'),class_name:v('class-name'),language:v('language'),introduction:v('introduction'),contact:v('contact'),looking:data.get('looking')==='on'});await refreshPerson();
    }else if(form.id==='personal-team-create'){
      await api.post('/teams',{name:v('team-name'),guest:data.get('guest')==='on'});await refreshPerson();
    }else if(form.id==='personal-team-join'){
      await api.post('/teams/join',{code:v('invite-code')});s.inviteCode='';await refreshPerson();
    }else if(form.id==='personal-team-name'){
      await api.post('/teams/name',{name:v('team-name')});await refreshPerson();
    }else if(form.id==='team-deadline-form'){
      await api.post('/admin/team-policy',{deadline_ms:v('team-deadline')?new Date(v('team-deadline')).getTime():null,expected_revision:s.teamPolicy.revision});openRoute();
    }else if(form.dataset.proposal){
      await api.post('/admin/team-merges/confirm',{proposal_id:form.dataset.proposal,name:v('team-name')});openRoute();
    }else if(form.id==='admin-membership-form'){
      await api.post('/admin/teams/'+enc(v('team-id'))+'/membership',{user_id:v('user-id'),action:v('member-action')});openRoute();
    }else if(form.id==='recovery-form'){await api.post('/auth/recovery',{username:v('username'),email:v('email')});s.recoverySent=true;render();
    }else if(form.id==='password-form'){
      const pw=data.get('new-password');if(pw.length<8)throw new Error(tr('Mật khẩu mới phải có ít nhất 8 ký tự.'));if(pw!==data.get('confirm-password'))throw new Error(tr('Hai lần nhập mật khẩu mới chưa khớp.'));
      const result=await api.post('/auth/password',{current_password:data.get('current-password'),new_password:pw});s.user=result.user;s.canManageGraders=result.can_manage_graders;form.reset();authChanged();toast(tr('Đã đổi mật khẩu và thu hồi các phiên cũ.'));go('home');
    }else if(form.id==='quick-practice-form'){
      await api.post('/practice-submissions',{runtime:v('runtime'),code:data.get('code')},keyFor(form));toast(tr('Đã nạp bot và xếp hàng kiểm 72 mẫu.'));openRoute();
    }else if(form.id==='zip-form'){
      await api.request('/submissions',{method:'POST',body:data.get('source'),headers:{'Content-Type':'application/zip','X-Submission-Version':v('version'),'X-Arena-Runtime':v('runtime'),'Idempotency-Key':keyFor(form)}});toast(tr('Đã nạp bot và xếp hàng kiểm 72 mẫu.'));openRoute();
    }else if(form.id==='admin-entry-form'){
      await api.post('/admin/competitions/'+s.competition.id+'/entries/'+encodeURIComponent(v('team_id')),{},keyFor(form));toast(tr('Đã thêm bản Đạt mới nhất của đội.'));openRoute();
    }else if(form.id==='github-attach-form'){
      s.github=await api.post('/teams/me/github',{url:v('repository')});toast(tr('Đã gắn repo; push lên main để kiểm tra.'));render();
    }else if(form.id==='benchmark-form'){
      const job=await api.post('/benchmarks',{submission_id:v('submission_id'),...(s.benchmarkCompetition?{competition_id:s.benchmarkCompetition}:{}),...botRulesetSelection(s,[v('submission_id')],v('ruleset_id'))},keyFor(form));go('jobs/'+job.id);
    }else if(form.id==='practice-form'){
      const mine=v('red_submission_id'),opponent=v('blue_submission_id'),teamRed=(v('team_side')||'red')==='red';const sides=opponent.startsWith('sample:')?{red_submission_id:mine,sample_bot_id:opponent.slice(7),sample_side:teamRed?'blue':'red'}:teamRed?{red_submission_id:mine,blue_submission_id:opponent}:{red_submission_id:opponent,blue_submission_id:mine};const job=await api.post('/matches',{...sides,...timeBody(data),delegate_timeout_claims:data.get('delegate')==='on',...botRulesetSelection(s,[mine,opponent],v('ruleset_id'))},keyFor(form));go('arena/practice/'+job.id);
    }else if(form.id==='competition-form'){
      if(s.selectedRuleset&&(!s.rulesetCatalog?.enabled||!s.rulesetCatalog.items.some(r=>r.id===s.selectedRuleset&&r.status==='published')))throw new Error(tr('Ruleset trong liên kết đang tắt hoặc chưa công bố. Bật add-on/chọn luật khả dụng trước; mở lại trang Giải đấu nếu muốn tạo giải tiêu chuẩn.'));
      const c=await api.post('/admin/competitions',{title:v('title'),...timeBody(data),max_teams:Number(v('max_teams')),...readTournamentOptions(data),...(data.get('use-experimental')==='on'?{ruleset_id:v('ruleset_id')}: {})},keyFor(form));go('competitions/'+c.id);
    }else if(form.id==='elo-exclude-form'){
      await api.post('/admin/competitions/'+form.dataset.competition+'/teams/'+form.dataset.team+'/exclude',{excluded:form.dataset.excluded==='true',reason:v('reason')},keyFor(form));closeModal();toast(tr('Đã cập nhật xếp hạng và ghi lý do.'));openRoute();
    }else if(form.id==='competition-edit-form'){
      await api.post('/admin/competitions/'+form.dataset.id+'/edit',{title:v('title'),...timeBody(data),max_teams:Number(v('max_teams')),...(v('submission_deadline')?{submission_deadline:new Date(v('submission_deadline')).getTime()}:{}),expected_revision:Number(form.dataset.revision)},keyFor(form));closeModal();toast(tr('Đã sửa giải.'));openRoute();
    }else if(form.id==='admin-team-form'){
      const members=Array.from({length:4},(_,i)=>({name:v('name-'+i),student_id:v('student-'+i)})).filter((m,i)=>i<3||m.name||m.student_id);
      const profile={team:v('team'),email:v('email'),phone:v('phone'),members,confirmed:true};
      if(form.dataset.id){await api.post('/admin/teams/'+form.dataset.id+'/edit',{...profile,expected_revision:Number(form.dataset.revision)});closeModal();toast(tr('Đã sửa thông tin đội.'));}
      else{const body={profile,username:v('username')};if(data.get('temporary-password'))body.temporary_password=data.get('temporary-password');credentialModal(await api.post('/admin/teams',body));}
      await refreshAdmin();
    }else if(form.id==='grader-form'){
      const item=await api.post('/admin/graders',{name:v('name')});form.reset();issuedGrader=item;modal(tr('Mã máy chấm'),graderTokenModal(item,s.graderPackage,location.origin));
    }else if(form.id==='entry-form'){
      const [submission_id,benchmark_id]=v('qualified').split(':');await api.post('/competitions/'+s.competition.id+'/entry',{submission_id,benchmark_id,delegate_timeout_claims:data.get('delegate')==='on'},keyFor(form));toast(tr('Đã chọn bot dự giải.'));openRoute();
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
  if(['ladder-hours','submission_deadline'].includes(e.target.name))updateLadderPreview(e.target.form);
  if(e.target.name==='admin-search'){s.adminSearch=e.target.value;s.adminPage=1;refreshAdminBody();}
  if(e.target.name==='people-search'){s.peopleSearch=e.target.value;const pos=e.target.selectionStart;render();const input=document.querySelector('[name=people-search]');input.focus();input.setSelectionRange(pos,pos);}
  if(e.target.name==='ops-search'){s.opsSearch=e.target.value;s.opsPage=1;refreshOperationsBody();}
});
document.addEventListener('change',async e=>{
  if(e.target.name==='format'&&e.target.form?.id==='competition-form'){
    const elo=e.target.value==='elo',form=e.target.form;
    form.querySelector('[data-competition-time]').hidden=elo;
    form.querySelector('[data-elo-time]').hidden=!elo;
  }
  const name=e.target.name,value=e.target.value;
  if(name==='competition-scope'){go(competitionRoute(parseRoute().raw,value));return;}
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
    if(action==='rule-release'&&s.ruleEditing?.id===r.id&&editor&&(ruleFingerprint(readRuleForm(editor))!==ruleFingerprint(r.definition)||editor.elements.namedItem('release-notes').value.trim()!==(r.release_notes||''))){b.disabled=false;throw new Error(tr('Có thay đổi chưa lưu. Lưu bản nháp trước khi kiểm tra & công bố.'));}
    try{const result=await api.post('/admin/rulesets/'+r.id+'/'+action.slice(5),{expected_revision:r.revision},keyFor(b));
    s.ruleEditing=result;s.ruleDraft=null;s.ruleNotes=null;s.rulePreview=null;s.rulePreviewActions=[];await refreshRules();toast(result.status==='published'?tr('Đã kiểm tra & công bố. Các đội có thể xem luật trong thư viện.'):tr('Đã cập nhật ruleset.'));}finally{b.disabled=false;}return;
  }
  const form=document.getElementById('ruleset-form');
  if(action==='rule-zone'){
    const next={'':'energy',energy:'control',control:''}[b.dataset.kind];b.dataset.kind=next;b.className='rule-map-cell'+(next?' zone-'+next:'');b.setAttribute('aria-label',b.dataset.square+': '+(next==='energy'?tr('năng lượng'):next==='control'?tr('kiểm soát'):tr('thường')));b.querySelector('span').innerHTML=next?icon(next==='energy'?'bolt':'target'):'';pendingKeys.delete(form);return;
  }
  if(['rule-add-modifier','rule-remove-modifier'].includes(action)){
    const d=readRuleForm(form);if(action==='rule-add-modifier'){if(d.modifiers.length>=4)throw new Error(tr('Tối đa 4 mẫu đi bổ sung.'));d.modifiers.push({id:'skill'+(d.modifiers.length+1),filter:{kinds:['rock','paper','scissors']},pattern:'orthogonal',range:1,cost:0,cooldown:0});}else d.modifiers.splice(Number(b.dataset.index),1);
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
  modalRoot.innerHTML=l10n`<div class="modal-overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-head"><h2 id="modal-title">${esc(title)}</h2><button class="icon-btn" data-action="close-modal" aria-label="Đóng">${icon('x')}</button></div><div class="modal-body">${body}</div></section></div>`;
  document.body.classList.add('modal-open');modalRoot.querySelector('input,button,a')?.focus();
}
function closeModal(){modalRoot.replaceChildren();document.body.classList.remove('modal-open');issued=null;issuedGrader=null;if(modalFocus?.isConnected)modalFocus.focus();modalFocus=null;}
function confirmAction(title,text,task){modal(title,`<p>${esc(text)}</p><div class="form-error" role="alert"></div><div class="modal-actions">${button(tr('Hủy'),'close-modal')}${button(tr('Xác nhận'),'confirm-action','','primary')}</div>`);const b=modalRoot.querySelector('[data-action="confirm-action"]');b.addEventListener('click',async()=>{b.disabled=true;try{await task();closeModal();toast(tr('Đã cập nhật.'));openRoute();}catch(e){const el=modalRoot.querySelector('.form-error');el.textContent=e.message;el.classList.add('visible');b.disabled=false;if(e.status===401)expired();}});}
function credentialModal(credential){issued=credential;modal(tr('Tài khoản đã được cấp'),l10n`<p>Mật khẩu chỉ được trả ở lần cấp này. Hãy sao chép và phát qua kênh của ban tổ chức. Đội sẽ phải đổi khi đăng nhập.</p><label class="field"><span>Tên đăng nhập</span><div class="credential-value">${esc(credential.account.username)}</div></label><label class="field"><span>Mật khẩu tạm</span><div class="credential-value" id="issued-password">${esc(credential.temporary_password)}</div></label><div class="modal-actions">${button(tr('Sao chép thông tin'),'copy-credential','','primary')}${button(tr('Đã phát cho đội'),'credential-delivered')}${button(tr('Đóng'),'close-modal')}</div>`);}
function teamForm(account){
  const t=account?.team||{name:'',email:'',phone:'',members:[]};
  modal(account?tr('Sửa đội · ')+t.name:tr('Thêm đội & cấp tài khoản'),`<form class="real-form" id="admin-team-form" ${account?`data-id="${t.id}" data-revision="${t.revision}"`:''}>${field(tr('Tên đội'),'team','required minlength="2" maxlength="40"','text',t.name)}<div class="form-grid">${field(tr('Email đội trưởng'),'email','required maxlength="120"','email',t.email)}${field(tr('Số điện thoại'),'phone','maxlength="20"','tel',t.phone)}</div>${Array.from({length:4},(_,i)=>`<fieldset class="member-field"><legend>${i===0?tr('Đội trưởng'):i===3?tr('Thành viên 4 · Tùy chọn'):tr('Thành viên ')+(i+1)}</legend><div class="form-grid">${field(tr('Họ và tên'),'name-'+i,`${i<3?'required ':''}minlength="2" maxlength="60"`,'text',t.members[i]?.name||'')}${field(tr('Mã sinh viên'),'student-'+i,`${i<3?'required ':''}pattern="[A-Za-z0-9]{6,15}"`,'text',t.members[i]?.student_id||'')}</div></fieldset>`).join('')}${account?note(tr('Mã đăng nhập giữ nguyên. Kết quả thi đấu vẫn gắn với mã đội khi đổi tên.')):field(tr('Tên đăng nhập'),'username','required pattern="[A-Za-z0-9_-]{4,30}" autocomplete="off"')+field(tr('Mật khẩu tạm (để trống cho server tạo)'),'temporary-password','minlength="8" maxlength="128" autocomplete="new-password"','password')+note(tr('Ban tổ chức xác nhận thông tin đội. Tài khoản mới phải đổi mật khẩu khi đăng nhập lần đầu.'))}${errors()}${submit(account?tr('Lưu thông tin'):tr('Tạo đội & cấp tài khoản'))}</form>`);
}
function exportCSV(filename,rows){
  const cell=value=>'"'+String(value??'').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
  saveBlob(filename,new Blob(['\uFEFF'+rows.map(r=>r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
}
async function diagnostic(fixtureId){
  const cid=s.competition.id,fid=fixtureId||s.ids[1];const {items}=await api.request(`/admin/competitions/${cid}/fixtures/${fid}/attempts`);
  const jobs=await Promise.all(items.map(a=>api.request(`/admin/competitions/${cid}/fixtures/${fid}/attempts/${a.number}`)));
  const f=s.fixtures.find(x=>x.id===fid);
  modal(tr('Lịch sử lần chạy · ')+(f?.ordinal||short(fid)),(items.length?items.map((a,i)=>l10n`<h3>Lần chạy ${a.number} ${badge(jobs[i].status)}</h3><pre class="report-json">${esc(JSON.stringify({error:jobs[i].error,report:jobs[i].report},null,2))}</pre>`).join(''):tr('<p>Chưa có lần chạy nào.</p>'))+(f?.status==='attention'?button(tr('Chạy lại trận'),'retry-fixture',`data-fixture="${fid}"`,'primary'):'')+note(tr('Chỉ lần chạy hoàn tất hợp lệ được tính điểm. Lịch sử lỗi được giữ khi chạy lại.')));
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-action]');if(!b||b.disabled)return;const action=b.dataset.action;
  try{
    if(action==='language'){setLanguage(language()==='vi'?'en':'vi');location.reload();return;}
    if(action.startsWith('rule-')||action==='experimental-toggle'||action==='experimental-starter'){await ruleAction(action,b);return;}
    if(action.startsWith('board-')){board?.action(action.slice(6),b.dataset.index);return;}
    if(action==='email-change'){emailPoll?.close();s.emailRequest=null;render();document.querySelector('#people-auth input[name=email]')?.focus();return;}
    if(action==='password'){const input=document.getElementById(b.dataset.target),visible=input.type==='password';input.type=visible?'text':'password';b.textContent=visible?tr('Ẩn'):tr('Hiện');b.setAttribute('aria-label',visible?tr('Ẩn mật khẩu'):tr('Hiện mật khẩu'));return;}
    if(action==='menu'){s.menuOpen=!s.menuOpen;document.querySelector('.app-shell')?.classList.toggle('menu-open',s.menuOpen);document.querySelector('.mobile-menu')?.setAttribute('aria-expanded',String(s.menuOpen));mobileSidebar();return;}
    if(action==='skip-main'){e.preventDefault();document.querySelector('#main')?.focus();return;}
    if(action==='close-modal'){closeModal();return;}
    if(action==='refresh'){await refreshData();return;}
    if(['team-rotate','team-recruiting','team-leave','team-captain','team-remove'].includes(action)){
      const paths={'team-rotate':'/teams/invite/rotate','team-recruiting':'/teams/recruiting','team-leave':'/teams/leave','team-captain':'/teams/captain','team-remove':'/teams/member/remove'};
      const body=action==='team-recruiting'?{enabled:!s.personal?.team?.recruiting}:b.dataset.user?{user_id:b.dataset.user}:{};
      if(['team-leave','team-remove','team-captain'].includes(action)){
        confirmAction(tr('Xác nhận thay đổi đội'),tr('Thao tác có hiệu lực ngay và được ghi nhật ký.'),async()=>{await api.post(paths[action],body);try{await refreshPerson();}catch(error){if(error.status===401)expired();else throw error;}});
      }else{await api.post(paths[action],body);await refreshPerson();}
      return;
    }
    if(action==='person-lock'||action==='person-judge'){
      await api.post('/admin/users/'+enc(b.dataset.user)+(action==='person-lock'?'/lock':'/judge'),action==='person-lock'?{locked:b.dataset.locked==='true'}:{enabled:b.dataset.enabled==='true'});openRoute();return;
    }
    if(action==='logout'){b.disabled=true;await api.post('/auth/logout');clearAccount();authChanged();go('login');return;}
    if(action==='add-member'||action==='remove-member'){s.registrationDraft=draftRegistration();if(action==='add-member')s.registrationDraft.members.push({name:'',student_id:''});else s.registrationDraft.members.pop();render();return;}
    if(action==='sample-bot-toggle'){await api.post('/admin/sample-bots/'+b.dataset.id+'/enabled',{enabled:b.dataset.enabled==='true'});openRoute();return;}
    if(action==='starter'){const runtime=document.querySelector('[name=runtime]')?.value||'cpp17';saveBlob('ottv2-'+runtime+'-v2.zip',await api.blob('/starter.zip?runtime='+enc(runtime)));return;}
    if(action==='download-report'){saveBlob('benchmark-'+short(s.job.id)+'.json',await api.blob('/jobs/'+s.job.id+'/report'));return;}
    if(action==='admin-tab'){s.adminTab=b.dataset.tab;s.adminPage=1;s.adminSearch='';render();return;}
    if(action==='ops-tab'){s.opsTab=b.dataset.tab;s.opsPage=1;render();return;}
    if(action==='admin-page'){if(b.dataset.scope==='teams'){s.adminPage=Number(b.dataset.page);refreshAdminBody();}else{s.opsPage=Number(b.dataset.page);refreshOperationsBody();}return;}
    if(action==='team-create'){teamForm();return;}
    if(['team-detail','team-edit','team-archive','team-restore'].includes(action)){
      const a=s.accounts.find(x=>x.id===b.dataset.id),t=a.team;
      if(action==='team-edit')teamForm(a);
      else if(action==='team-detail')modal(tr('Hồ sơ đội · ')+t.name,l10n`<dl class="detail-list"><div><dt>Tài khoản</dt><dd>${esc(a.username)}</dd></div><div><dt>Email</dt><dd>${esc(t.email)}</dd></div><div><dt>Điện thoại</dt><dd>${esc(t.phone||'—')}</dd></div><div><dt>Ngày tạo</dt><dd>${date(t.created_ms)}</dd></div><div><dt>Trạng thái</dt><dd>${t.archived_ms?tr('Đã lưu trữ'):a.locked?tr('Đang khóa'):tr('Đang quản lý')}</dd></div></dl>${table([tr('Thành viên'),tr('Mã sinh viên')],t.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.student_id)}</td></tr>`))}`);
      else{const restore=action==='team-restore';confirmAction((restore?tr('Khôi phục'):tr('Xóa'))+tr(' đội ')+t.name+'?',restore?tr('Đội được đưa lại vào danh sách. Tài khoản vẫn khóa; hãy mở khóa sau khi kiểm tra.'):tr('Tài khoản sẽ bị khóa và mọi phiên đăng nhập bị thu hồi. Lịch sử bot và thi đấu được giữ; có thể khôi phục.'),()=>api.post('/admin/teams/'+t.id+'/'+(restore?'restore':'archive'),{expected_revision:t.revision}));}return;
    }
    if(action==='export-teams'){exportCSV('teams.csv',[[tr('Đội'),tr('Tài khoản'),'Email',tr('Điện thoại'),tr('Trạng thái'),tr('Thành viên')],...s.accounts.map(a=>[a.team.name,a.username,a.team.email,a.team.phone,a.team.archived_ms?tr('Đã lưu trữ'):a.locked?tr('Khóa'):tr('Hoạt động'),a.team.members.map(m=>m.name+' ('+m.student_id+')').join('; ')])]);return;}
    if(action==='resolve-recovery'){confirmAction(tr('Đánh dấu đã xử lý?'),tr('Đóng yêu cầu cấp lại này. Thao tác này không đổi mật khẩu.'),()=>api.post('/admin/recoveries/'+b.dataset.id+'/resolve'));return;}
    if(action==='admin-job'){
      b.disabled=true;const j=await api.request('/admin/jobs/'+enc(b.dataset.id));b.disabled=false;
      modal(tr('Tác vụ · ')+short(j.id),`${badge(j.status)}<p>${esc(j.kind)} · ${date(j.created_unix_ms)}</p><pre class="report-json">${esc(JSON.stringify(j,null,2))}</pre>${button(tr('Tải JSON'),'admin-job-download',`data-id="${j.id}"`)}${j.official?note(tr('Mở giải đấu tương ứng để xem lịch sử lần chạy hoặc chạy lại trận lỗi.')):''}`);return;
    }
    if(action==='notice-visibility'){b.disabled=true;await api.post('/admin/announcements/'+enc(b.dataset.id)+'/visibility',{expected_revision:Number(b.dataset.revision),archived:b.dataset.archived!=='true'});await refreshNotices();await refreshData();return;}
    if(action==='retention-run'){b.disabled=true;await api.post('/admin/retention/run');toast(tr('Đã dọn dữ liệu theo chính sách.'));await openRoute();return;}
    if(action==='admin-job-download'){saveBlob('job-'+short(b.dataset.id)+'.json',new Blob([JSON.stringify(await api.request('/admin/jobs/'+enc(b.dataset.id)),null,2)],{type:'application/json'}));return;}
    if(action==='submission-manifest'){const item=s.adminSubmissions.find(x=>x.id===b.dataset.id);modal('Source · '+item.version,`<pre class="report-json">${esc(JSON.stringify(item,null,2))}</pre>`);return;}
    if(action==='elo-exclude'){
      const excluded=b.dataset.excluded!=='true';
      modal(excluded?tr('Loại đội khỏi xếp hạng'):tr('Khôi phục đội'),`<form class="real-form" id="elo-exclude-form" data-competition="${esc(s.competition.id)}" data-team="${esc(b.dataset.team)}" data-excluded="${excluded}">${note(tr('Giữ nguyên các ván đã đấu; cập nhật hạng và suất chung kết. Lý do được lưu riêng cho BTC và ghi nhật ký.'))}${field(tr('Lý do'),'reason','required minlength="3" maxlength="300"')}${errors()}${submit(tr('Áp dụng và ghi nhật ký'))}</form>`);
    }
    if(['competition-edit','competition-archive','competition-restore'].includes(action)){
      const c=s.competitions.find(x=>x.id===b.dataset.id)||s.competition;
      if(action==='competition-edit')modal(tr('Sửa giải · ')+c.title,`<form class="real-form" id="competition-edit-form" data-id="${c.id}" data-revision="${c.revision}">${field(tr('Tên giải'),'title','required minlength="3" maxlength="100"','text',c.title)}${timeField(c.config.profile)}${c.config.format==='elo'?field(tr('Hạn nộp GitHub (giờ trên máy BTC)'),'submission_deadline','required','datetime-local',new Date(c.submission_deadline-new Date(c.submission_deadline).getTimezoneOffset()*60000).toISOString().slice(0,16))+note(tr('Hạn nộp sửa được tới trước khi qua hạn.')):''}${field(tr('Số đội tối đa'),'max_teams','required min="2" max="4096"','number',c.config.max_teams)}${note(tr('Chỉ đổi thời gian ở bản nháp chưa có đội. Khi mở đăng ký, có thể sửa tên và giới hạn đội trước khi chốt lịch.'))}${errors()}${submit(tr('Lưu cấu hình'))}</form>`);
      else{const restore=action==='competition-restore';confirmAction((restore?tr('Khôi phục'):tr('Xóa'))+tr(' giải ')+c.title+'?',restore?tr('Giải trở lại danh sách, giữ nguyên trạng thái trước khi xóa.'):tr('Chỉ xóa bản nháp / giải mở chưa có đội chọn bot, hoặc giải đã hoàn tất. Toàn bộ lịch sử và kết quả được giữ.'),async()=>{await api.post('/admin/competitions/'+c.id+'/'+(restore?'restore':'archive'),{expected_revision:c.revision},keyFor(b));});}return;
    }
    if(action==='review-registration'){
      const r=s.registrations.find(x=>x.id===b.dataset.id);
      if(r.status!=='pending'){modal(tr('Hồ sơ · ')+r.team,`${badge(r.status)}<p>${esc(r.email)} · ${esc(r.phone||'—')}</p>${table([tr('Thành viên'),tr('Mã sinh viên')],r.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.student_id)}</td></tr>`))}`);return;}
      modal(tr('Duyệt đội · ')+r.team,`<p>${esc(r.email)} · ${esc(r.phone||tr('Không có số điện thoại'))}</p>${table([tr('Thành viên'),tr('Mã sinh viên')],r.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.student_id)}</td></tr>`))}<form class="real-form" id="approve-form" data-id="${r.id}">${field(tr('Tên đăng nhập sẽ cấp'),'username','required pattern="[A-Za-z0-9_-]{4,30}"','text','uet-'+r.id.slice(0,8))}${field(tr('Mật khẩu tạm (để trống cho server tạo)'),'temporary-password','minlength="8" maxlength="128" autocomplete="new-password"','password')}${errors()}<div class="modal-actions">${button(tr('Từ chối hồ sơ'),'reject-registration',`data-id="${r.id}"`)}${submit(tr('Duyệt & cấp tài khoản'))}</div></form>`);return;
    }
    if(action==='reject-registration'){confirmAction(tr('Từ chối hồ sơ'),tr('Hồ sơ này sẽ được đánh dấu từ chối; chưa tạo tài khoản đội.'),()=>api.post('/admin/registrations/'+b.dataset.id+'/reject'));return;}
    if(action==='reset-password'){
      const a=s.accounts.find(x=>x.id===b.dataset.id);
      modal(tr('Cấp lại mật khẩu · ')+(a?.username||tr('đội')),l10n`<p>Các phiên hiện tại sẽ bị thu hồi. Tài khoản giữ nguyên trạng thái khóa.</p><form class="real-form" id="reset-form" data-id="${b.dataset.id}">${field(tr('Mật khẩu tạm (để trống cho server tạo)'),'temporary-password','minlength="8" maxlength="128" autocomplete="new-password"','password')}${errors()}${submit(tr('Cấp lại mật khẩu'))}</form>`);return;
    }
    if(action==='account-lock'){const locked=b.dataset.locked!=='true';confirmAction(locked?tr('Khóa tài khoản?'):tr('Mở khóa tài khoản?'),locked?tr('Đội sẽ bị đăng xuất và không thể đăng nhập cho tới khi mở khóa.'):tr('Đội có thể đăng nhập lại với mật khẩu hiện tại.'),()=>api.post('/admin/accounts/'+b.dataset.id+'/lock',{locked}));return;}
    if(action==='delivered'){b.disabled=true;await api.post('/admin/accounts/'+b.dataset.id+'/delivered');await refreshAdmin();return;}
    if(action==='copy-grader-controls'){await copyMessage(controlsMessage(b.dataset.platform==='linux'?'linux':'windows'),tr('Đã sao chép lệnh dừng/gỡ.'));return;}
    if(action==='copy-grader-message'){if(issuedGrader&&s.graderPackage){await copyMessage(graderMessage(issuedGrader,s.graderPackage,location.origin,b.dataset.platform==='linux'?'linux':'windows'),tr('Đã sao chép tin nhắn cài máy chấm.'));}return;}
    if(action==='copy-grader-token'){if(issuedGrader){await copyMessage(issuedGrader.token,tr('Đã sao chép mã máy chấm.'));}return;}
    if(action==='delete-grader'){if(!confirm(tr('Xóa máy chấm "')+b.dataset.name+tr('" khỏi danh sách? Mã đã thu hồi nên máy không nối lại được; lịch sử ván vẫn ghi tên máy.')))return;b.disabled=true;await api.post('/admin/graders/'+b.dataset.id+'/delete');toast(tr('Đã xóa máy chấm khỏi danh sách.'));openRoute();return;}
    if(action==='revoke-grader'){if(!confirm(tr('Thu hồi mã của máy chấm "')+b.dataset.name+tr('"? Máy này sẽ mất kết nối, trận đang chạy được giao lại máy khác.')))return;b.disabled=true;await api.post('/admin/graders/'+b.dataset.id+'/revoke');toast(tr('Đã thu hồi mã máy chấm.'));openRoute();return;}
    if(action==='copy-credential'){if(issued){await copyMessage(tr('Tài khoản: ')+issued.account.username+tr('\nMật khẩu tạm: ')+issued.temporary_password,tr('Đã sao chép. Hãy phát qua kênh của ban tổ chức.'));}return;}
    if(action==='credential-delivered'){if(issued){b.disabled=true;await api.post('/admin/accounts/'+issued.account.id+'/delivered');toast(tr('Đã đánh dấu phát tài khoản.'));closeModal();await refreshAdmin();}return;}
    if(action==='competition-action'){
      const command=b.dataset.command,c=s.competition;
      if(command==='lock'){confirmAction(tr('Chốt bot và tạo lịch?'),l10n`Giải “${c.title}” hiện có ${c.entries.length} đội: ${c.entries.map(x=>x.name+' ('+x.version+')').join(', ')}. Sau khi chốt, các đội không thể thay bot.`,()=>api.post('/admin/competitions/'+c.id+'/lock',{},keyFor(b)));}
      else{b.disabled=true;await api.post('/admin/competitions/'+c.id+'/'+command,{},keyFor(b));openRoute();}return;
    }
    if(action==='withdraw-entry'||action==='remove-entry'){const path=action==='withdraw-entry'?'/competitions/'+s.competition.id+'/withdraw':`/admin/competitions/${s.competition.id}/entries/${b.dataset.team}/remove`;confirmAction(tr('Rút bot khỏi giải?'),tr('Bot này được rút trước khi chốt lịch. Đội có thể chọn bot lại khi giải còn mở đăng ký.'),()=>api.post(path,{},keyFor(b)));return;}
    if(action==='diagnostic'){await diagnostic(b.dataset.fixture);return;}
    if(action==='retry-fixture'){const fid=b.dataset.fixture;confirmAction(tr('Chạy lại trận?'),tr('Tạo lần chạy mới cho trận chưa tính điểm. Lịch sử cũ được giữ lại.'),()=>api.post(`/admin/competitions/${s.competition.id}/fixtures/${fid}/retry`,{},keyFor(b)));return;}
    if(action==='export-standings'){
      if(s.user?.role!=='admin'){const a=document.createElement('a');a.href='/snapshots/standings/'+enc(s.competition.id)+'.csv';a.click();return;}
      const csvCell=value=>'"'+String(value??'').replace(/^[=+@\-\t\r]/,"'$&").replaceAll('"','""')+'"';
      const rows=[[tr('Hạng'),tr('Đội'),tr('Phiên bản'),tr('Trận'),tr('Thua'),tr('Hòa'),tr('Thắng'),tr('Điểm')],...s.standings.items.map(x=>[x.rank,x.name,x.version,x.played,x.losses,x.draws||0,x.wins,x.points])];saveBlob('standings.csv',new Blob(['\uFEFF'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
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
  try{s.platform=await api.request('/platform');try{const session=await api.request('/auth/me');s.user=session.user;s.canManageGraders=session.can_manage_graders;}catch(e){if(e.status!==401)throw e;}await openRoute();}
  catch(e){s.loading=false;s.error=e.message;render();}
}
boot();
