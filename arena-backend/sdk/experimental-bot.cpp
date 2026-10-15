#include <iostream>
#include <memory>
#include "experimental.hpp"
using json=nlohmann::json;
int main(){
 std::ios::sync_with_stdio(false);std::cin.tie(nullptr);
 std::string line,match;int version=2;std::unique_ptr<arena_experimental::Rules> rules;
 while(std::getline(std::cin,line))try{
   auto m=json::parse(line);std::string type=m.at("type");
   if(type=="init"){
     version=m.at("protocol_version");match=m.at("match_id");
     json ready={{"protocol_version",version},{"type","ready"},{"match_id",match}};
     if(version==3){const auto& pack=m.at("payload").at("ruleset");rules=std::make_unique<arena_experimental::Rules>(pack.at("definition"));ready["ruleset_hash"]=pack.at("hash");ready["capabilities"]=json::array({"rules-v1"});}
     else if(version!=2)return 2;
     std::cout<<ready.dump()<<'\n'<<std::flush;
   }else if(type=="observation"&&m.at("match_id")==match&&m.at("protocol_version")==version){
     const auto& payload=m.at("payload");json action={{"resign",true}};
     if(version==3){
       const auto& state=payload.at("state");auto legal=rules->legal_actions(state);
       if(legal!=payload.at("legal_actions"))throw std::runtime_error("Server/SDK legal action mismatch");
       double best=-1e100;std::string side=state.at("to_move");
       for(const auto& a:legal){json candidate={{"action_id",a.at("id")}};double score=rules->evaluate_objectives(rules->apply(state,candidate),side)-0.01*int(a.at("energy_cost"));if(score>best){best=score;action=candidate;}}
     }else if(!payload.at("legal_moves").empty())action=payload.at("legal_moves").at(0);
     std::cout<<json({{"protocol_version",version},{"type","action"},{"match_id",match},{"turn_id",m.at("turn_id")},{"state_version",m.at("state_version")},{"payload",action}}).dump()<<'\n'<<std::flush;
   }
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n'<<std::flush;return 1;}
}
