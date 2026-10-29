const text=values=>values.filter(Boolean).join(' ').toLocaleLowerCase('vi');
export function teamSearchText(team,people=[]){
 return text([team.name,...(team.roster||[]).flatMap(p=>[p.display_name,p.email,p.username]),...people.filter(p=>p.team_id===team.id).flatMap(p=>[p.display_name,p.email,p.username])]);
}
export function matchingPeople(s){
 const query=text([s.peopleSearch]),teams=new Map((s.teamChoices||[]).map(t=>[t.id,t.name]));
 return (s.people||[]).filter(p=>(s.peopleTeamFilter!=='without'||!p.team_id)&&(s.peopleTeamFilter!=='with'||!!p.team_id)&&text([p.display_name,p.email,p.username,teams.get(p.team_id)]).includes(query));
}
export function availableTeams(s,query=''){
 return (s.teamChoices||[]).filter(t=>!t.archived_ms&&(t.member_count??t.roster?.length??0)<(t.team_size??4)&&teamSearchText(t,s.people).includes(text([query]))).slice(0,8);
}
