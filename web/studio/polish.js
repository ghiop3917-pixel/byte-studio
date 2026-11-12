(() => {
 'use strict';
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const sphere='<svg viewBox="0 0 240 240" fill="none" aria-hidden="true"><circle cx="120" cy="120" r="110"/><ellipse cx="120" cy="120" rx="110" ry="38"/><ellipse cx="120" cy="120" rx="110" ry="76"/><ellipse cx="120" cy="120" rx="38" ry="110"/><ellipse cx="120" cy="120" rx="76" ry="110"/><path d="M10 120H230M120 10V230"/></svg>';
 for(const [selector,kind] of [['#home','hero'],['#about','about'],['#contact','contact']]){
  const host=document.querySelector(selector),art=document.createElement('div');art.className='polish-orbit polish-orbit-'+kind;art.setAttribute('aria-hidden','true');art.innerHTML=sphere;host.append(art);
 }
 const paths=[
  '<circle cx="20" cy="20" r="12"/><path d="M29 29L41 41M14 20H26M20 14V26"/>',
  '<path d="M9 38L12 27 32 7 41 16 21 36 9 38ZM27 12L36 21M12 27L21 36"/><path d="M29 39H41"/>',
  '<path d="M16 13L5 24 16 35M32 13L43 24 32 35M28 8L20 40"/>',
  '<path d="M9 25L19 35 40 12"/><circle cx="24" cy="24" r="20"/>',
  '<path d="M13 32L8 40 18 36M17 30L11 24 24 10 40 7 37 23 23 36 17 30Z"/><circle cx="30" cy="17" r="4"/>',
  '<path d="M12 28V21a12 12 0 0 1 24 0V28M12 23H8v13h7V23h-3ZM36 23h4v13h-7V23h3ZM36 36c0 5-7 6-12 6"/>'
 ];
 document.querySelectorAll('.process-steps>li').forEach((card,i)=>{const icon=document.createElement('span');icon.className='polish-step-icon';icon.setAttribute('aria-hidden','true');icon.innerHTML='<svg viewBox="0 0 48 48" fill="none">'+paths[i%paths.length]+'</svg>';card.append(icon)});
 const orbits=document.querySelectorAll('.polish-orbit');const observer=new IntersectionObserver(entries=>entries.forEach(e=>e.target.classList.toggle('orbit-visible',e.isIntersecting)),{rootMargin:'100px'});orbits.forEach(el=>observer.observe(el));
 if(matchMedia('(pointer:fine)').matches&&!reduced.matches){
  const targets=document.querySelectorAll('#project-grid .project-card,.process-steps>li,.header-contact,.film-about-link');
  targets.forEach(el=>{let pending=false;el.addEventListener('pointermove',e=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;const r=el.getBoundingClientRect();el.style.setProperty('--mx',((e.clientX-r.left)/r.width*100)+'%');el.style.setProperty('--my',((e.clientY-r.top)/r.height*100)+'%')})},{passive:true})});
 }
})();
