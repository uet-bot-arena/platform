import {recentMatch} from './quick-practice.js';
import {language} from './i18n.js';
import {loadBotGuide} from './bot-guide.js';

export async function privatePage(api,view,poll=false){
  const [personal,data]=await Promise.all([api.request('/ui/personal?view='+view+(poll?'&poll=1':''),{poll}),api.request('/ui/read?view='+view,{poll})]);
  return {...personal,...data};
}

export async function participantData(api,user,route,ids,q){
  const data={},enc=encodeURIComponent;
  if(route==='guide')return {rules:await api.request('/rules?lang='+language()),botGuide:await loadBotGuide()};
  let view=['home','team','submissions','benchmark','practice','jobs'].includes(route)?route:null;
  if(['arena','replay'].includes(route)&&ids[0]==='practice')view='arena';
  if(user?.team_id&&view){
    const a=performance.now(),value=await (['home','team','submissions'].includes(view)?privatePage(api,view):api.request('/ui/page?view='+view+(['jobs','arena'].includes(view)?'&job_id='+enc(view==='jobs'?ids[0]:ids[1]):''))),b=performance.now();
    Object.assign(data,value);
    data.initialClock={rtt:b-a,anchor:(a+b)/2,unix:(value.clock.server_received_unix_ms+value.clock.server_sent_unix_ms)/2,clockId:value.clock.clock_id};
  }else if(user&&!user.team_id&&['home','team'].includes(route))data.personal=await api.request('/users/me');
  if(route==='matching')data.matching=await api.request('/matching');
  if(route==='rulesets'){
    data.rulesetCatalog=await api.request('/rulesets');
    if(ids[0])data.ruleDetail=await api.request('/rulesets/'+enc(ids[0]));
  }
  if(route==='announcements')data.announcements=(await api.snapshot('announcements.json')).items;
  if(route==='queue')data.queue=await api.snapshot('queue.json');
  if(['home','competitions','schedule','live','standings','arena','replay'].includes(route)||route==='team'&&user&&!user.team_id)data.competitions=(await api.snapshot('competitions.json')).items;
  if(route==='home'&&!user)data.recentMatch=await recentMatch(api,data.competitions);
  const cid=route==='competitions'?ids[0]:['arena','replay'].includes(route)?ids[0]==='practice'?null:ids[0]:route==='benchmark'?q.get('competition'):['schedule','live','standings'].includes(route)?q.get('competition')||data.competitions?.find(c=>!c.archived_ms)?.id:null;
  if(cid){
    const [competition,fixtures,standings]=await Promise.all([api.snapshot('competitions/'+enc(cid)+'.json'),api.snapshot('fixtures/'+enc(cid)+'.json'),api.snapshot('standings/'+enc(cid)+'.json')]);
    Object.assign(data,{competition,fixtures:fixtures.items,standings,historyTotal:fixtures.history_total});
    if(route==='competitions'&&user?.team_id&&competition.config.format!=='elo')Object.assign(data,await privatePage(api,'competitions'));
    if(['arena','replay'].includes(route)&&ids[1]&&!data.fixtures.some(f=>f.id===ids[1]))data.fixtures.push((await api.snapshot('fixtures/'+enc(cid)+'/'+enc(ids[1])+'.json')).fixture);
  }
  data.benchmarkCompetition=route==='benchmark'?q.get('competition'):null;
  data.selectedRuleset=q.get('ruleset')||'';data.selectedTurn=q.get('turn')||'';data.selectedMatch=q.get('match')||'';
  data.selectedSubmission=q.get('submission')||data.submissions?.[0]?.id;
  return data;
}
