// Frontend policy only. The production API must enforce its own pinned policy.
export const LANGUAGE_POLICY = Object.freeze({id:'cpp-first-v1',defaultRuntime:'cpp17',codonEnabled:true});
export const RUNTIME_PROFILES = Object.freeze({
  cpp17:{id:'cpp17',label:'C++17',choice:'C++17 · mặc định',entry:'bot.cpp',starter:'assets/bot-starter-cpp.zip',tree:['bot.cpp','json.hpp','LICENSE.MIT','README.md'],libs:['STL','nlohmann/json 3.11.3'],experimental:false},
  'python-codon':{id:'python-codon',label:'Python + Codon 0.20.3',choice:'Python + Codon 0.20.3 · thử nghiệm',entry:'bot.py',starter:'assets/bot-starter-codon.zip',tree:['bot.py','requirements.txt','README.md'],libs:['json','sys','heapq','collections','bisect','itertools'],experimental:true},
  python3:{id:'python3',label:'Python 3 · bản cũ',experimental:false}
});
export const availableRuntimes=()=>LANGUAGE_POLICY.codonEnabled?['cpp17','python-codon']:['cpp17'];
export const draftProfile=state=>RUNTIME_PROFILES[availableRuntimes().includes(state.draftRuntime)?state.draftRuntime:LANGUAGE_POLICY.defaultRuntime];
// Before runtime selection existed, every submission used Python 3.
export const submissionProfile=submission=>RUNTIME_PROFILES[submission?.runtime]||RUNTIME_PROFILES.python3;
