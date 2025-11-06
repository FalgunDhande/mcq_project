document.addEventListener('DOMContentLoaded',()=>{
const el=document.getElementById('countdown'); if(!el) return;
const deadline=new Date(el.dataset.deadline).getTime();
const tick=()=>{const now=Date.now(); const diff=Math.max(0,deadline-now);
const s=Math.floor(diff/1000), m=Math.floor(s/60), h=Math.floor(m/60);
const mm=m%60, ss=s%60; el.textContent=`${String(h).padStart(2,'0')}:${String(mm).padStart(2,'0')}:${String(ss).padStart(2,'0')}`;
if(diff<=0){document.getElementById('quiz-form')?.submit(); return;} setTimeout(tick,500);}; tick();});