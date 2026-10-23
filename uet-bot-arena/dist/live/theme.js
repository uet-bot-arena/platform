// Run before CSS to avoid a light flash; store only the display preference.
(()=>{
  const key='uet-arena-theme',media=matchMedia('(prefers-color-scheme: dark)');
  let choice=null;
  try{const saved=localStorage.getItem(key);if(['light','dark'].includes(saved))choice=saved;}catch{}
  function apply(){
    const value=choice||(media.matches?'dark':'light');
    document.documentElement.dataset.theme=value;
    document.documentElement.style.colorScheme=value;
  }
  function save(){try{if(choice)localStorage.setItem(key,choice);else localStorage.removeItem(key);}catch{}apply();}
  window.arenaTheme={
    toggle(){choice=document.documentElement.dataset.theme==='dark'?'light':'dark';save();},
    system(){choice=null;save();}
  };
  media.addEventListener('change',()=>{if(!choice)apply();});
  document.addEventListener('click',event=>{if(event.target.closest?.('[data-action="theme"]'))window.arenaTheme.toggle();});
  apply();
})();
