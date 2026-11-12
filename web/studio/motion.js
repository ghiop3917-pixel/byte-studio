/* Содержимое остаётся доступным при отключённых или не загрузившихся эффектах. */
'use strict';
(function () {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const small = window.matchMedia('(max-width: 760px)');
  const menu = document.getElementById('main-nav');
  const toggle = document.getElementById('menu-toggle');
  const year = document.getElementById('current-year');
  if (year) year.textContent = new Date().getFullYear();
  if (toggle && menu) {
    function closeMenu() {menu.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Открыть меню');}
    toggle.addEventListener('click',()=>{const open=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'Закрыть меню':'Открыть меню');menu.classList.toggle('open',open);});
    menu.querySelectorAll('a').forEach(a=>a.addEventListener('click',closeMenu));
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeMenu();if(document.activeElement&&menu.contains(document.activeElement))toggle.focus();}});
    small.addEventListener('change',closeMenu);
  }
  document.querySelectorAll('.service-link').forEach(link=>link.addEventListener('click',()=>{
    const field=document.getElementById('service');
    if(field)field.value=link.closest('[data-service]').dataset.service;
  }));
  const floating=document.createElement('a');floating.href='#brief';floating.className='floating-request';floating.textContent='Оставить заявку';
  const floatingArrow=document.createElement('span');floatingArrow.textContent='↗';floating.append(floatingArrow);document.body.append(floating);
  const requestObserver=new IntersectionObserver(()=>{
    const hero=document.querySelector('.hero').getBoundingClientRect(),contact=document.getElementById('brief').getBoundingClientRect();
    floating.classList.toggle('visible',hero.bottom<80&&(contact.top>innerHeight||contact.bottom<0));
  },{threshold:[0,.1,.5,1]});
  requestObserver.observe(document.querySelector('.hero'));requestObserver.observe(document.getElementById('brief'));
  const steps=[...document.querySelectorAll('.process-step')];
  const processCurrent=document.getElementById('process-current');
  const progress=document.getElementById('process-progress');
  function activate(index){
    steps.forEach((step,i)=>step.classList.toggle('active',i<=index));
    if(processCurrent)processCurrent.textContent=String(index+1).padStart(2,'0');
    if(progress)progress.style.width=((index+1)/Math.max(steps.length,1)*100)+'%';
  }
  const stepObserver=new IntersectionObserver(entries=>{entries.forEach(entry=>{if(entry.isIntersecting)activate(steps.indexOf(entry.target));});},{rootMargin:'-20% 0px -40% 0px',threshold:0});
  steps.forEach(step=>stepObserver.observe(step));
  function loadScript(src){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=src;s.onload=resolve;s.onerror=reject;document.head.append(s);});}
  let stopScene=null;
  async function scene(){
    if(stopScene){stopScene();stopScene=null;}
    if(reduced.matches||small.matches||navigator.connection?.saveData||navigator.hardwareConcurrency<4)return;
    try{if(!window.startByteScene)await loadScript('/hero-3d.js');if(!reduced.matches&&!small.matches)stopScene=window.startByteScene();}catch{/* Остаётся лёгкая CSS-композиция. */}
  }
  reduced.addEventListener('change',scene);small.addEventListener('change',scene);
  scene();
  if(reduced.matches)return;
  Promise.resolve().then(()=>loadScript('/vendor/gsap.min.js')).then(()=>loadScript('/vendor/ScrollTrigger.min.js')).then(()=>{
    const gsap=window.gsap,ScrollTrigger=window.ScrollTrigger;
    gsap.registerPlugin(ScrollTrigger);
    const media=gsap.matchMedia();
    media.add('(prefers-reduced-motion: no-preference)',()=>{
      const entrance=gsap.timeline({defaults:{ease:'power4.out'}});
      document.querySelectorAll('.hero .title-line').forEach(line=>line.classList.add('word-mask'));
      if(window.scrollY<100){
        entrance.from('.hero-label',{y:12,opacity:0,duration:.7})
          .from('.hero .word',{yPercent:115,rotate:3,duration:1.25,stagger:.075},.15)
          .from('.hero-description,.hero-actions',{y:30,opacity:0,duration:.9,stagger:.12},.85)
          .from('.hero-art',{scale:.65,rotate:-20,opacity:0,duration:1.8,ease:'expo.out'},.05)
          .from('.hero-footer',{opacity:0,duration:.7},1.1);
      }
      gsap.utils.toArray('.section-heading,.about-bottom,.contact-heading').forEach(node=>gsap.from(node,{y:45,opacity:0,duration:1,ease:'power3.out',scrollTrigger:{trigger:node,start:'top 90%',once:true}}));
      gsap.utils.toArray('.service-row').forEach(node=>{
        gsap.from(node.querySelector('.service-copy'),{y:35,opacity:0,duration:.8,scrollTrigger:{trigger:node,start:'top 88%',once:true}});
        gsap.from(node.querySelector('.service-art'),{x:45,rotate:-7,opacity:0,duration:1,ease:'power3.out',scrollTrigger:{trigger:node,start:'top 85%',once:true}});
      });
      gsap.utils.toArray('.work-visual').forEach(node=>{
        gsap.from(node,{clipPath:'inset(14% 0 14% 0 round 20px)',duration:1.2,ease:'power3.out',scrollTrigger:{trigger:node,start:'top 90%',once:true}});
        const preview=node.querySelector('.project-browser');
        if(preview)gsap.fromTo(preview,{y:35,rotate:node.classList.contains('forma-work')?-8:8,scale:.92},{y:-15,rotate:0,scale:1,ease:'none',scrollTrigger:{trigger:node,start:'top bottom',end:'bottom top',scrub:1.2}});
      });
      gsap.utils.toArray('.process-step').forEach((node,index)=>gsap.from(node,{x:30,opacity:.4,duration:.8,scrollTrigger:{trigger:node,start:'top 80%',once:true}}));
      gsap.fromTo('.about-wordmark',{xPercent:5},{xPercent:-2,ease:'none',scrollTrigger:{trigger:'.about-section',start:'top bottom',end:'bottom top',scrub:1.5}});
      gsap.from('.contact-form-wrap',{y:60,opacity:0,duration:1,ease:'power3.out',scrollTrigger:{trigger:'.contact-section',start:'top 75%',once:true}});
      gsap.from('.footer-big',{yPercent:30,rotate:-3,opacity:.1,duration:1.3,ease:'power3.out',scrollTrigger:{trigger:'.site-footer',start:'top 90%',once:true}});
      if(small.matches)gsap.to('.orb-fallback',{rotate:55,scale:1.08,ease:'none',scrollTrigger:{trigger:'.hero',start:'top top',end:'bottom top',scrub:1.2}});
      return ()=>entrance.kill();
    });
    media.add('(min-width: 1000px) and (min-height: 700px) and (prefers-reduced-motion: no-preference)',()=>{
      const state={progress:0};
      const scene=gsap.timeline({scrollTrigger:{trigger:'.hero',start:'top top',end:'+=430',pin:true,scrub:1,anticipatePin:1}});
      scene.to('.hero h1 .title-line',{x:-100,opacity:0,stagger:.07,ease:'power1.in',duration:.75},0)
        .to('.hero-description,.hero-actions,.hero-label',{y:-25,opacity:0,duration:.4},0)
        .to('.hero-art',{xPercent:-22,scale:1.32,rotate:12,duration:1,ease:'none'},0)
        .to(state,{progress:1,duration:1,ease:'none',onUpdate:()=>{window.byteSceneProgress=state.progress;}},0);
      return ()=>{window.byteSceneProgress=0;scene.kill();};
    });
    media.add('(pointer:fine) and (prefers-reduced-motion: no-preference)',()=>{
      const cleanups=[];
      document.querySelectorAll('.magnetic,.service-link').forEach(button=>{
        const move=e=>{const r=button.getBoundingClientRect();gsap.to(button,{x:(e.clientX-r.left-r.width/2)*.22,y:(e.clientY-r.top-r.height/2)*.25,duration:.4,ease:'power2.out'});};
        const leave=()=>gsap.to(button,{x:0,y:0,duration:.8,ease:'elastic.out(1,.35)'});
        button.addEventListener('pointermove',move);button.addEventListener('pointerleave',leave);
        cleanups.push(()=>{button.removeEventListener('pointermove',move);button.removeEventListener('pointerleave',leave);});
      });
      document.querySelectorAll('.work-visual').forEach(work=>{
        const cursor=work.querySelector('.project-hover-cursor');if(!cursor)return;
        const move=e=>{const r=work.getBoundingClientRect();gsap.to(cursor,{x:e.clientX-r.left,y:e.clientY-r.top,duration:.32,ease:'power3.out'});};
        work.addEventListener('pointermove',move);cleanups.push(()=>work.removeEventListener('pointermove',move));
      });
      return ()=>cleanups.forEach(fn=>fn());
    });
    document.addEventListener('byte:filter',()=>ScrollTrigger.refresh());
    document.fonts.ready.then(()=>ScrollTrigger.refresh());
  }).catch(()=>{/* Без GSAP секции видны и доступны. */});
})();
