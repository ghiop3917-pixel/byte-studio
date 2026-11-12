(() => {
 'use strict';
 const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
 const reduce=matchMedia('(prefers-reduced-motion:reduce)');
 const hero=$('.hero'),about=$('#about'),services=$('#services'),works=$('#works');
 // Сохраняем секции и их функции, меняя порядок под последовательность референса.
 services.before(about);
 const sectionLabels={about:'01 / О СТУДИИ',services:'02 / ЧТО МЫ ДЕЛАЕМ',works:'03 / ДЕМОНСТРАЦИОННЫЕ ПРОЕКТЫ',process:'04 / КАК МЫ РАБОТАЕМ'};
 Object.entries(sectionLabels).forEach(([id,text])=>{const el=$(`#${id} .section-meta .eyebrow`);if(el)el.textContent=text});
 const navNumbers={'#services':'02','#works':'03','#about':'01'};
 $$('nav a').forEach(a=>{$('sup',a).textContent=navNumbers[a.getAttribute('href')]});
 const title=$('#hero-heading');title.setAttribute('aria-label','Привет, мы Byte Studio. Создаём digital, который работает на ваш бизнес.');
 title.innerHTML='<span class="ref-title-line">HI, WE’RE BYTE<span class="ref-period">✦</span></span>';
 $$('.logo').forEach(link=>{link.setAttribute('aria-label','Byte Studio — на главную');link.innerHTML='<img src="assets/byte-logo-dark.png" alt="BS" width="48" height="48">'});
 $('.hero-top .eyebrow').innerHTML='<i class="status-dot"></i> OPEN FOR NEW IDEAS';
 $('.hero-top .small-note').textContent='DESIGN / DEVELOPMENT / AUTOMATION';
 $('.hero-description').innerHTML='Создаём digital,<br>который работает<br>на ваш бизнес.';
 $('.hero-caption').innerHTML='<span>МАЛЕНЬКАЯ СТУДИЯ.</span><span>БОЛЬШИЕ ИДЕИ.</span>';
 const scene=document.createElement('div');scene.className='ref-hero-scene';
 scene.innerHTML='<div class="ref-avatar-wrap"><img class="ref-avatar" src="assets/byte-logo-dark.png" alt="Объёмный логотип BS — Byte Studio" width="640" height="640" fetchpriority="high"><span class="ref-avatar-label">BYTE STUDIO / DIGITAL WITH CHARACTER</span></div><div class="ref-object ref-pill" aria-hidden="true"><span>#</span></div><div class="ref-object ref-star hero-star" aria-hidden="true">✳</div><div class="ref-orbit-label">GOOD DESIGN.<br>SMART CODE.<br>A LITTLE MAGIC.</div><a class="ref-round-link" href="#contact" aria-label="Обсудить идею">LET’S<br>TALK <span>↗</span></a>';
 hero.append(scene);
 const tape=document.createElement('div');tape.className='ref-tools';tape.setAttribute('aria-label','Направления и технологии');
 tape.innerHTML='<span>✦</span><b>WEB</b><span>⌘</span><b>TELEGRAM</b><span>✳</span><b>INTEGRATIONS</b><span>◈</span><b>SOFTWARE</b><span>↗</span><b>DESIGN</b>';
 hero.append(tape);
 const mosaic=document.createElement('section');mosaic.className='ref-mosaic';mosaic.setAttribute('aria-label','Визуальные концепции студии');
 mosaic.innerHTML='<div class="ref-mosaic-track">'+Array.from({length:2},()=>'<div class="ref-tile tile-lime"><span>DEMO / CONCEPT</span><b>make<br>some<br><em>NOISE.</em></b><i>✳</i></div><div class="ref-tile tile-pink"><span>DESIGN EXPLORATION</span><b>PLAY<br>IS THE<br>PROCESS</b><i>◉</i></div><div class="ref-tile tile-blue"><span>WEB / CONCEPT</span><b>byte<br><em>the future.</em></b><i>↗</i></div><div class="ref-tile tile-orange"><span>WORKFLOW / CONCEPT</span><b>THINK<br>OUTSIDE<br>THE <em>BOX.</em></b><i>✦</i></div><div class="ref-tile tile-silver"><span>CREATIVE CODE</span><b>Less.<br><em>But better.</em></b><i>∞</i></div>').join('')+'</div>';
 about.before(mosaic);
 $('.about-copy h2').innerHTML='<span class="ref-outline">ABOUT US</span><span class="ref-filled" aria-hidden="true">ABOUT US</span>';
 $('.about-copy>p').textContent='Небольшая digital-студия для больших идей. Соединяем дизайн, код и автоматизацию, чтобы создавать продукты с характером.';
 const aboutObjects=document.createElement('div');aboutObjects.className='ref-about-objects';aboutObjects.setAttribute('aria-hidden','true');aboutObjects.innerHTML='<span class="ref-object ref-star about-star">✳</span><span class="ref-object ref-diamond">◈</span><span class="ref-object ref-flower">✿</span><span class="ref-object ref-red">✦</span>';about.append(aboutObjects);
 $('.services .section-heading h2').innerHTML='SERVICES';
 $('.services .section-heading>p').innerHTML='Ваши амбиции.<br>Наши инструменты.';
 $$('.service-row').forEach((row,i)=>{row.style.setProperty('--index',i);row.setAttribute('aria-label',`${$('.service-title h3',row).textContent.replace('↗','')}. Обсудить проект`)});
 $('.works .section-heading h2').innerHTML='PROJECTS<span class="tiny-count">(04)</span>';
 $$('.project-card').forEach((card,i)=>{card.style.setProperty('--card',i);const index=document.createElement('span');index.className='ref-project-index';index.textContent=String(i+1).padStart(2,'0');$('.project-info',card).prepend(index);card.prepend($('.project-info',card));});
 const originalPreviewCards=$$('.project-card');
 // CSS sticky создаёт реальную стопку, не вмешиваясь в фильтры и диалоги.
 $('.filters').addEventListener('click',()=>{requestAnimationFrame(()=>{let i=0;originalPreviewCards.forEach(card=>{if(!card.hidden){card.style.setProperty('--card',i++);card.style.removeProperty('transform')}});window.ScrollTrigger?.refresh()})});
 $('.contact-copy h2').innerHTML='LET’S<br>GET IN<br><em>TOUCH<span>↗</span></em>';
 $('.contact-copy>p').innerHTML='Есть идея? Давайте превратим её<br>в работающий продукт.';
 $('.footer-word').innerHTML='GOOD THINGS<span>START WITH A BYTE.</span><i>↗</i>';
 // Контекст автоматически восстанавливает стили при смене reduced motion.
 if(window.gsap&&window.ScrollTrigger&&document.documentElement.dataset.motion!=='film'){
  gsap.registerPlugin(ScrollTrigger);
  const mm=gsap.matchMedia();
  mm.add('(prefers-reduced-motion: no-preference)',()=>{
   gsap.from('.ref-title-line',{yPercent:110,rotate:3,duration:1.05,ease:'power4.out',delay:.12});
   gsap.from('.ref-avatar-wrap',{y:130,scale:.55,rotate:-16,opacity:0,duration:1.6,ease:'expo.out',delay:.25});
   gsap.from('.hero-description,.ref-orbit-label,.ref-round-link,.hero-actions',{opacity:0,y:22,stagger:.1,duration:.8,delay:.65,clearProps:'opacity,transform'});
   gsap.from('.ref-hero-scene .ref-object',{scale:0,rotate:-80,stagger:.15,duration:1.4,ease:'elastic.out(1,.7)',delay:.6});
   gsap.to('.ref-avatar-wrap',{y:-110,rotate:8,scale:.78,ease:'none',scrollTrigger:{trigger:hero,start:'top top',end:'bottom top',scrub:1.1}});
   gsap.to('.ref-title-line',{yPercent:-35,opacity:.2,ease:'none',scrollTrigger:{trigger:hero,start:'top top',end:'bottom 20%',scrub:1}});
   gsap.fromTo('.ref-filled',{clipPath:'inset(0 100% 0 0)'},{clipPath:'inset(0 0% 0 0)',ease:'none',scrollTrigger:{trigger:about,start:'top 65%',end:'center 48%',scrub:.7}});
   $$('.ref-about-objects .ref-object').forEach((el,i)=>gsap.fromTo(el,{y:100+i*20,rotate:-45,scale:.65},{y:-90,rotate:45+i*15,scale:1,ease:'none',scrollTrigger:{trigger:about,start:'top bottom',end:'bottom top',scrub:1.4}}));
   gsap.from('.about-copy>p,.about-copy .values',{y:40,opacity:0,stagger:.15,duration:.9,scrollTrigger:{trigger:'.about-copy',start:'top 65%'},clearProps:'all'});
   gsap.from('.services .section-heading h2',{yPercent:100,scrollTrigger:{trigger:services,start:'top 80%',end:'top 25%',scrub:.7},ease:'none'});
   $$('.service-row').forEach((el,i)=>{gsap.from(el,{opacity:.15,x:35,scrollTrigger:{trigger:el,start:'top 85%',end:'top 48%',scrub:.6},ease:'none'});gsap.to($('.service-art',el),{rotate:i%2?10:-10,y:-12,scrollTrigger:{trigger:el,start:'top bottom',end:'bottom top',scrub:1},ease:'none'})});
   $$('.process-steps li').forEach(el=>gsap.from(el,{y:35,opacity:.2,duration:.8,scrollTrigger:{trigger:el,start:'top 86%'},clearProps:'all'}));
   gsap.from('.contact-copy h2',{y:70,rotate:3,scrollTrigger:{trigger:'#contact',start:'top 85%',end:'top 25%',scrub:.8},ease:'none'});
   ScrollTrigger.refresh();
  });
 }
 const wrap=$('.ref-avatar-wrap');
 $('.ref-avatar').addEventListener('load',()=>window.ScrollTrigger?.refresh());
 document.fonts?.ready.then(()=>window.ScrollTrigger?.refresh());
 if(matchMedia('(pointer:fine)').matches){hero.addEventListener('pointermove',e=>{if(reduce.matches)return;const r=hero.getBoundingClientRect();wrap.style.setProperty('--head-x',`${(e.clientX/r.width-.5)*14}deg`);wrap.style.setProperty('--head-y',`${-(e.clientY-r.top-r.height*.5)/r.height*10}deg`)});hero.addEventListener('pointerleave',()=>{wrap.style.setProperty('--head-x','0deg');wrap.style.setProperty('--head-y','0deg')})}
})();
