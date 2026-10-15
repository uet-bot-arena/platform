#pragma once
// rules-v1 reference simulator. Keep mechanics in sync with arena/experimental.py.
// All methods are deterministic and have no I/O or clock side effects.
#include "json.hpp"
#include <algorithm>
#include <string>
#include <utility>
#include <vector>
#include <stdexcept>
namespace arena_experimental {
using json=nlohmann::json;
using Dirs=std::vector<std::pair<int,int>>;
inline std::string other(const std::string& s){return s=="red"?"blue":"red";}
inline std::pair<int,int> pos(const std::string& s){return {9-(s.at(1)-'0'),s.at(0)-'a'};}
inline std::string square(int r,int c){return std::string(1,char('a'+c))+std::to_string(9-r);}
inline std::string beats(const std::string& k){return k=="rock"?"scissors":k=="scissors"?"paper":"rock";}
inline Dirs dirs(const std::string& p){
 if(p=="orthogonal")return {{-1,0},{0,-1},{0,1},{1,0}};
 if(p=="diagonal")return {{-1,-1},{-1,1},{1,-1},{1,1}};
 if(p=="knight")return {{-2,-1},{-2,1},{-1,-2},{-1,2},{1,-2},{1,2},{2,-1},{2,1}};
 if(p=="king")return {{-1,-1},{-1,0},{-1,1},{0,-1},{0,1},{1,-1},{1,0},{1,1}};
 throw std::runtime_error("Unsupported movement primitive");
}
class Rules {
public:
 json definition;
 explicit Rules(json d):definition(std::move(d)){
   if(definition.at("schema_version")!=1)throw std::runtime_error("Unsupported rules schema");
   for(const auto& m:definition.at("modifiers"))dirs(m.at("pattern"));
 }
 bool matches(const json& p,const json& f) const {
   for(const auto& key:{"kinds","piece_ids"})if(f.contains(key)&&!f.at(key).empty()){
     const auto& values=f.at(key);const auto& v=p.at(std::string(key)=="kinds"?"kind":"id");
     if(std::find(values.begin(),values.end(),v)==values.end())return false;
   }return true;
 }
 json legal_actions(const json& s) const {
   json actions=json::array();if(!s.at("result").is_null())return actions;
   const std::string side=s.at("to_move");int turns=s.at("turns").at(side),energy=s.at("energy").at(side);
   auto add=[&](json a){a["id"]="a"+std::to_string(actions.size());actions.push_back(std::move(a));};
   for(int r=0;r<9;r++)for(int c=0;c<9;c++){
     const auto& p=s.at("board").at(r).at(c);if(p.is_null()||p.at("side")!=side)continue;
     json patterns=json::array({{{"id",""},{"pattern","king"},{"range",1},{"cost",0},{"cooldown",0}}});
     for(const auto& m:definition.at("modifiers"))if(matches(p,m.at("filter"))&&p.at("cooldowns").value(std::string(m.at("id")),0)<=turns&&int(m.at("cost"))<=energy)patterns.push_back(m);
     for(const auto& m:patterns)for(auto [dr,dc]:dirs(m.at("pattern")))for(int step=1;step<=int(m.at("range"));step++){
       int nr=r+dr*step,nc=c+dc*step;if(nr<0||nr>=9||nc<0||nc>=9)break;
       const auto& target=s.at("board").at(nr).at(nc);
       if(target.is_null()||(target.at("side")!=side&&beats(p.at("kind"))==target.at("kind")))
         add({{"type","move"},{"piece_id",p.at("id")},{"from",square(r,c)},{"to",square(nr,nc)},{"ability",m.at("id")},{"energy_cost",m.at("cost")}});
       if(!target.is_null())break;
     }
     const auto& sh=definition.at("shield");
     if(bool(sh.at("enabled"))&&energy>=int(sh.at("cost"))&&int(p.at("shield_until"))<=turns)
       add({{"type","shield"},{"piece_id",p.at("id")},{"from",square(r,c)},{"energy_cost",sh.at("cost")}});
   }return actions;
 }
 json counts(const json& s,const std::string& side) const {
   json out={{"rock",0},{"paper",0},{"scissors",0}};
   for(const auto& row:s.at("board"))for(const auto& p:row)if(!p.is_null()&&p.at("side")==side){std::string k=p.at("kind");out[k]=int(out[k])+1;}
   return out;
 }
 json apply(const json& state,const json& action) const {
   if(!state.at("result").is_null())throw std::runtime_error("Match already finished");
   json s=state;std::string side=s.at("to_move"),enemy=other(side);
   auto finish=[&](std::string reason){s["result"]={{"winner",side},{"reason",reason}};};
   if(action==json({{"resign",true}})){s["result"]={{"winner",enemy},{"reason","resignation"}};return s;}
   if(!action.is_object()||action.size()!=1||!action.contains("action_id")||!action.at("action_id").is_string())throw std::runtime_error("Experimental action requires exactly {action_id: string}");
   json a;for(const auto& candidate:legal_actions(state))if(candidate.at("id")==action.at("action_id")){a=candidate;break;}
   if(a.is_null())throw std::runtime_error("Unknown action_id");
   auto [r,c]=pos(a.at("from"));json& p=s["board"][r][c];int turn=s["turns"][side];
   s["energy"][side]=int(s["energy"][side])-int(a.at("energy_cost"));
   if(a.at("type")=="shield")p["shield_until"]=turn+int(definition.at("shield").at("duration"))+1;
   else {
     auto [tr,tc]=pos(a.at("to"));auto& target=s["board"][tr][tc];
     bool blocked=!target.is_null()&&int(target.at("shield_until"))>int(s["turns"][enemy]);
     std::string ability=a.at("ability");
     // Set cooldown on the moving piece before copying it into the destination.
     if(!ability.empty())for(const auto& m:definition.at("modifiers"))if(m.at("id")==ability)p["cooldowns"][ability]=turn+int(m.at("cooldown"))+1;
     if(blocked)target["shield_until"]=0;
     else {target=p;p=nullptr;}
     if(!blocked&&bool(definition.at("victory").at("base_capture"))&&a.at("to")==json(enemy=="red"?"a1":"i9"))finish("base_capture");
   }
   s["ply"]=int(s["ply"])+1;s["turns"][side]=turn+1;s["to_move"]=enemy;
   for(auto& row:s["board"])for(auto& piece:row)if(!piece.is_null()&&piece.at("side")==side){
     json cd=json::object();for(auto it=piece["cooldowns"].begin();it!=piece["cooldowns"].end();++it)if(int(it.value())>turn+1)cd[it.key()]=it.value();piece["cooldowns"]=cd;
     if(int(piece["shield_until"])<=turn+1)piece["shield_until"]=0;
   }
   auto remaining=counts(s,enemy);int total=0;bool missing=false;for(auto it=remaining.begin();it!=remaining.end();++it){total+=int(it.value());missing|=it.value()==0;}
   if(s["result"].is_null()&&!total)finish("all_pieces_eliminated");
   if(s["result"].is_null()&&bool(definition.at("victory").at("type_eliminated"))&&missing)finish("type_eliminated");
   if(s["result"].is_null()){
     for(const auto& z:definition.at("zones")){auto [zr,zc]=pos(z.at("square"));const auto& occupant=s["board"][zr][zc];
       if(!occupant.is_null()&&occupant.at("side")==side){std::string resource=z.at("kind")=="energy"?"energy":"control";s[resource][side]=std::min(resource=="energy"?int(definition.at("energy").at("cap")):10000,int(s[resource][side])+int(z.at("gain")));}
     }
     int goal=definition.at("victory").at("control_target");if(goal&&int(s["control"][side])>=goal)finish("control_target");
     if(s["result"].is_null()&&bool(definition.at("victory").at("no_legal_moves"))&&legal_actions(s).empty())finish("no_legal_moves");
   }return s;
 }
 // A baseline heuristic, not a promise of strategic strength. Teams can change
 // weights or replace it while retaining the exact rules simulator above.
 double evaluate_objectives(const json& s,const std::string& side) const {
   if(!s.at("result").is_null()){const auto& w=s.at("result").at("winner");return w.is_null()?0:w==side?1e6:-1e6;}
   double score=0;for(const auto& who:{side,other(side)}){
     double sign=who==side?1:-1;score+=sign*(8*int(s.at("energy").at(who))+25*int(s.at("control").at(who)));
     for(int r=0;r<9;r++)for(int c=0;c<9;c++){const auto& p=s.at("board").at(r).at(c);if(p.is_null()||p.at("side")!=who)continue;
       score+=sign*(100+(who=="red"?8-r+c:r+8-c)+8*(int(p.at("shield_until"))>int(s.at("turns").at(who))));
       for(const auto& z:definition.at("zones"))if(z.at("square")==square(r,c))score+=sign*15;
     }
   }return score;
 }
};
}
