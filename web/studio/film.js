(() => {
 'use strict';
 const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
 const reduced=matchMedia('(prefers-reduced-motion:reduce)');
 const hero=$('#home'),about=$('#about'),services=$('#services'),works=$('#works');
 const navNames={'#services':'Услуги ','#works':'Проекты ','#about':'О студии '};
 $$('nav a').forEach(a=>{a.firstChild.textContent=navNames[a.getAttribute('href')]});
 $('.hero-description').innerHTML='<span class="offer-brand">BYTE STUDIO — DIGITAL-СТУДИЯ</span><strong>Сайты. Telegram-продукты.<br>Интеграции и автоматизация.</strong><span>Продумываем, разрабатываем<br>и запускаем под вашу задачу.</span>';
 $('.ref-orbit-label').innerHTML='ОТ ИДЕИ<br>ДО РАБОТАЮЩЕГО<br>ПРОДУКТА.';
 $('.ref-title-line').innerHTML='<span class="film-head-word">HI,</span> <span class="film-head-word">WE’RE</span> <span class="film-head-word">BYTE</span><span class="ref-period">✦</span>';
 const logoEntry=document.createElement('div');logoEntry.className='film-logo-entry';
 $('.ref-avatar-wrap').prepend(logoEntry);logoEntry.append($('.ref-avatar'));
 $('.ref-mosaic').setAttribute('aria-label','Эксперименты с визуальным стилем — демонстрационные концепции');
 $$('.ref-mosaic-track .ref-tile').forEach((tile,i)=>{tile.setAttribute('aria-hidden','true');tile.style.setProperty('--tile-index',i);tile.style.setProperty('--art-x',`${(i%5)*25}%`);tile.style.setProperty('--art-y',i<5?'0%':'100%')});
 const artObserver=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){$('.ref-mosaic-track').style.setProperty('--tile-art','url("assets/reference-visuals.png")');artObserver.disconnect()}},{rootMargin:'500px'});artObserver.observe($('.ref-mosaic'));
 const cube=document.createElement('span');cube.className='video-cube';cube.innerHTML=['front','back','left','right','top','bottom'].map(side=>`<i class="cube-${side}"></i>`).join('');$('.ref-diamond').append(cube);
 const serviceCopy={
  web:{title:'Сайты для бизнеса',result:'Лендинг, корпоративный сайт, каталог или магазин — под ваш сценарий.',items:['Структура и дизайн ключевых страниц','Адаптация под телефон и компьютер','Формы и необходимые интеграции по согласованному объёму','Подготовка к запуску и передача проекта']},
  telegram:{title:'Telegram-продукты',result:'Бот или Mini App, который помогает пользователю выполнить задачу.',items:['Пользовательские сценарии и интерфейс','Заявки, уведомления и работа с данными','Интеграции с вашими сервисами по задаче','Проверка сценариев и подготовка к запуску']},
  automation:{title:'Интеграции и автоматизация',result:'Меньше ручных действий: от обработки запроса до связи между сервисами.',items:['Разбор текущего процесса и точек автоматизации','Логика правил и сценариев','Интеграции и обработка ошибок','Проверка на согласованных примерах']},
  custom:{title:'Приложения и ПО',result:'Индивидуальный интерфейс и логика, когда готового сервиса недостаточно.',items:['Сценарии, требования и прототип','Разработка согласованных функций','Тестирование основных пользовательских путей','Подготовка сборки и документации']}
 };
 $$('[data-service]').forEach(row=>{
  const info=serviceCopy[row.dataset.service];
  $('.service-title p',row).textContent=info.result;
  row.setAttribute('aria-label',`${info.title}. Обсудить проект`);
  const details=document.createElement('details');details.className='service-includes';
  const summary=document.createElement('summary');summary.textContent='Что может входить в проект';
  const list=document.createElement('ul');info.items.forEach(text=>{const li=document.createElement('li');li.textContent=text;list.append(li)});
  details.append(summary,list);row.after(details);
  details.addEventListener('toggle',()=>window.ScrollTrigger?.refresh());
 });
 const estimate=document.createElement('div');estimate.className='project-estimate';
 estimate.innerHTML='<span class="estimate-label">ДО СТАРТА</span><h3>Понятная задача.<br>Понятные условия.</h3><div class="estimate-items"><div><b>Стоимость</b><p>Зависит от объёма страниц, функций, интеграций и материалов. Для оценки достаточно описать задачу — готовое ТЗ необязательно.</p></div><div><b>Сроки</b><p>Обсуждаем вместе с объёмом работ и этапами согласования. Лендинг и продукт с личным кабинетом требуют разного времени.</p></div><div><b>Результат</b><p>До начала работы уточняем состав проекта, участие заказчика, порядок согласований, передачу доступов и поддержку после запуска.</p></div></div><a href="#contact" class="text-link">Описать задачу <span>↗</span></a>';
 const estimateDetails=document.createElement('details');estimateDetails.className='video-estimate';
 const estimateSummary=document.createElement('summary');estimateSummary.innerHTML='<span>05</span><b>До старта проекта</b><i>+</i>';estimateDetails.append(estimateSummary,estimate);services.append(estimateDetails);
 estimateDetails.addEventListener('toggle',()=>window.ScrollTrigger?.refresh());
 const aboutLink=document.createElement('a');aboutLink.href='#works';aboutLink.className='film-about-link';aboutLink.textContent='ПОСМОТРЕТЬ КОНЦЕПЦИИ ↗';$('.about-copy').append(aboutLink);
 const notes={
  nova:{task:'Собрать сложные финансовые данные в понятный интерфейс.',decision:'Разделить сводку, баланс и динамику; выделить основные действия и облегчить чтение показателей.',scope:'Визуальная концепция dashboard и состояния ключевых блоков. Финансовые значения демонстрационные.'},
  aether:{task:'Передать характер архитектурного бюро через digital-пространство.',decision:'Редакционная типографика, природная палитра, крупная композиция и спокойная визуальная иерархия.',scope:'Концепция главной страницы и визуального языка. Архитектурное бюро и коммерческий результат не заявляются.'},
  pulse:{task:'Сделать повседневные финансовые действия понятными на небольшом экране.',decision:'Короткий путь к сводке, заметный баланс и компактная лента операций внутри Telegram.',scope:'Концепция интерфейса Mini App. Банки, платежи и синхронизация реально не подключены.'},
  flow:{task:'Показать, как входящий запрос проходит через автоматизированный процесс.',decision:'Визуальные узлы и связи: источник, обработка, CRM и уведомление. Каждый этап имеет собственную роль.',scope:'Концепция редактора процессов. Показанные интеграции иллюстрируют сценарий и не выполняют реальные действия.'}
 };
 function clonePreview(source,suffix){
  const clone=source.cloneNode(true);clone.setAttribute('aria-hidden','true');
  $$('[id]',clone).forEach(el=>{const old=el.id;el.id=`${old}-${suffix}`;$$('*',clone).forEach(child=>{for(const attr of [...child.attributes])if(attr.value.includes(`url(#${old})`))child.setAttribute(attr.name,attr.value.replaceAll(`url(#${old})`,`url(#${el.id})`))})});
  return clone;
 }
 $$('.project-card').forEach(card=>{
  const preview=$('.project-preview',card),source=$('.preview-inner',preview),id=preview.dataset.project;
  const visual=document.createElement('div');visual.className='film-project-visual';preview.before(visual);visual.append(preview);
  const side=document.createElement('div');side.className='film-project-sides';
  ['Композиция','Детали'].forEach((label,i)=>{const button=document.createElement('button');button.type='button';button.className=`film-side-preview film-side-${i}`;button.dataset.project=id;button.dataset.caseView='detail';button.dataset.caseDetail=String(i);button.setAttribute('aria-label',`${label} концепции ${window.BYTE_PROJECTS.find(p=>p.id===id).name}`);if(window.BYTE_CASE_DETAILS?.[id]?.[i])button.insertAdjacentHTML('beforeend',window.BYTE_CASE_DETAILS[id][i]);else button.append(clonePreview(source,`${id}-side${i}`));const caption=document.createElement('span');caption.textContent=`0${i+1} / ${label}`;button.append(caption);side.append(button)});
  visual.append(side);
 });
 const dialog=$('#project-dialog');dialog.setAttribute('aria-labelledby','dialog-title');
 const gallery=document.createElement('div');gallery.className='case-gallery';
 gallery.innerHTML='<div class="case-view-controls" role="group" aria-label="Вид концепции"><button type="button" data-case-mode="overview" aria-pressed="true">Общий вид</button><button type="button" data-case-mode="detail" aria-pressed="false">Крупный план</button></div><div class="case-stage"></div><div class="case-explanation"></div>';
 $('#dialog-tags').after(gallery);
 let activeCase=null;
 function setCaseMode(mode){const stage=$('.case-stage');stage.classList.toggle('is-detail',mode==='detail');if(activeCase){if(mode==='detail'&&window.BYTE_CASE_DETAILS?.[activeCase])stage.innerHTML=window.BYTE_CASE_DETAILS[activeCase][0];else{const source=$(`.project-preview[data-project="${activeCase}"] .preview-inner`);stage.replaceChildren(clonePreview(source,`${activeCase}-dialog`))}}$$('[data-case-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.caseMode===mode)))}
 gallery.addEventListener('click',e=>{const b=e.target.closest('[data-case-mode]');if(b)setCaseMode(b.dataset.caseMode)});
 $('#project-grid').addEventListener('click',e=>{
  const button=e.target.closest('[data-project]');if(!button)return;const project=window.BYTE_PROJECTS.find(p=>p.id===button.dataset.project);if(!project)return;
  const preview=$(`.project-preview[data-project="${project.id}"] .preview-inner`);
  $('.case-stage').replaceChildren(clonePreview(preview,`${project.id}-dialog`));
  const info=notes[project.id];$('.case-explanation').replaceChildren(...[['Задача',info.task],['Дизайн-решение',info.decision],['Статус и объём',info.scope]].map(([title,text])=>{const block=document.createElement('div'),h=document.createElement('h3'),p=document.createElement('p');h.textContent=title;p.textContent=text;block.append(h,p);return block}));
  activeCase=project.id;setCaseMode(button.dataset.caseView||'overview');if(button.dataset.caseDetail!==undefined)$('.case-stage').innerHTML=window.BYTE_CASE_DETAILS[project.id][Number(button.dataset.caseDetail)];dialog.scrollTop=0;
 });
 const process=$('#process');$('.process-intro h2').innerHTML='РАБОТА В ДЕТАЛЯХ';$('.process-intro p').innerHTML='От первого разговора до запуска.<br>Что происходит на каждом этапе.';
 const support=document.createElement('li');support.innerHTML='<span>06</span><div><h3>Поддержка</h3><p>Обсуждаем обновления, поддержку и дальнейшее развитие продукта после запуска.</p><small>СЛЕДУЮЩИЙ ШАГ</small></div>';$('.process-steps').append(support);
 const processNames=['Разбор задачи','Дизайн','Разработка','Проверка','Запуск'];$$('.process-steps h3').slice(0,5).forEach((h,i)=>{h.textContent=processNames[i]});
 const contactLink=$('.telegram-link');contactLink.href='https://t.me/bytestudio_dev';contactLink.target='_blank';contactLink.rel='noopener noreferrer';contactLink.setAttribute('aria-label','Написать в Telegram');$('small',contactLink).textContent='НАПИШИТЕ НАМ В TELEGRAM';$('b',contactLink).textContent='@bytestudio_dev';
 const contactAccent=document.createElement('span');contactAccent.className='video-contact-accent';contactAccent.setAttribute('aria-hidden','true');contactAccent.textContent='✦';$('#contact').append(contactAccent);
 $('#form-notice').textContent='Укажите контакт и задачу. Бюджет можно обсудить позже.';
 $('#contact-form button[type=submit]>span:first-child').textContent='Отправить заявку';
 const oldTelegram=$('#brief-result a');if(oldTelegram){oldTelegram.hidden=false;oldTelegram.href='https://t.me/bytestudio_dev';}
 const download=document.createElement('button');download.type='button';download.className='text-link';download.id='download-brief';download.textContent='Скачать описание .txt ↗';$('.brief-actions').append(download);
 download.addEventListener('click',()=>{const blob=new Blob(['\uFEFF'+$('#brief-text').value],{type:'text/plain;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='byte-studio-project.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('.form-status').textContent='Подготовлен текстовый файл с описанием проекта.'});
 // Вход и скролл управляют разными обёртками: обратная прокрутка восстанавливает сцену.
 if(!window.gsap||!window.ScrollTrigger)return;
 gsap.registerPlugin(ScrollTrigger);
 const mm=gsap.matchMedia();
 mm.add({motion:'(prefers-reduced-motion: no-preference)',wide:'(min-width: 761px)'},context=>{
  const {motion,wide}=context.conditions;if(!motion)return;
  const intro=gsap.timeline({paused:document.body.classList.contains('language-pending'),defaults:{ease:'power3.out'}});
  const enterSite=()=>intro.play(0);addEventListener('byte:enter',enterSite,{once:true});
  intro.from('.film-head-word',{yPercent:115,rotation:3,duration:1,stagger:.085},.12)

   .from('.hero-description,.ref-orbit-label,.hero-actions,.ref-round-link',{y:18,opacity:0,duration:.8,stagger:.08,clearProps:'opacity,transform'},.55)
   .from('.ref-hero-scene .ref-object',{scale:.25,rotation:-55,opacity:0,duration:1.1,stagger:.12,clearProps:'opacity'},.58);
  gsap.fromTo('.ref-avatar-wrap',{y:0,scale:1,rotation:0},{y:wide?-95:-55,scale:.88,rotation:7,ease:'none',scrollTrigger:{trigger:hero,start:'top top',end:'bottom top',scrub:.65,invalidateOnRefresh:true}});
  gsap.fromTo('#hero-heading',{y:0},{y:wide?-75:-35,ease:'none',scrollTrigger:{trigger:hero,start:'top top',end:'bottom top',scrub:.6}});
  $$('.ref-hero-scene .ref-object').forEach((el,i)=>gsap.to(el,{y:wide?-55: -25,rotation:i?25:-10,ease:'none',scrollTrigger:{trigger:hero,start:'top top',end:'bottom top',scrub:.8}}));
  // Две непрерывные ленты управляются CSS, независимо от скролла.
  gsap.fromTo('.ref-filled',{clipPath:'inset(0 100% 0 0)'},{clipPath:'inset(0 0% 0 0)',ease:'none',scrollTrigger:{trigger:about,start:'top 60%',end:'center 43%',scrub:.45}});
  $$('.ref-about-objects .ref-object').forEach((el,i)=>gsap.fromTo(el,{y:wide?100:45,x:i%2? -20:20,rotation:-25,scale:.8},{y:wide?-80:-35,x:i%2?20:-20,rotation:25+i*7,scale:1,ease:'none',scrollTrigger:{trigger:about,start:'top bottom',end:'bottom top',scrub:.75}}));
  gsap.from('.about-copy>p,.about-copy .values,.film-about-link',{y:26,opacity:0,stagger:.12,duration:.75,scrollTrigger:{trigger:'.about-copy h2',start:'top 68%',toggleActions:'play none none reverse'}});
  gsap.fromTo(services,{clipPath:'inset(0 3% 0 3% round 12px)'},{clipPath:'inset(0 0% 0 0% round 8px)',ease:'none',scrollTrigger:{trigger:services,start:'top 95%',end:'top 40%',scrub:.5}});
  gsap.from('.services .section-heading h2',{yPercent:100,ease:'none',scrollTrigger:{trigger:'.services .section-heading',start:'top 92%',end:'top 55%',scrub:.5}});
  $$('.service-row').forEach(row=>{
   gsap.from($('.service-title',row),{y:25,opacity:.15,ease:'none',scrollTrigger:{trigger:row,start:'top 91%',end:'top 61%',scrub:.45}});
   gsap.from($('.service-num',row),{y:15,opacity:.25,ease:'none',scrollTrigger:{trigger:row,start:'top 91%',end:'top 64%',scrub:.45}});
   gsap.from($('.service-art',row),{scale:.75,rotation:-10,opacity:.4,ease:'none',scrollTrigger:{trigger:row,start:'top 90%',end:'top 55%',scrub:.55}});
  });
  gsap.from('.works .section-heading h2',{y:40,opacity:.3,ease:'none',scrollTrigger:{trigger:works,start:'top 90%',end:'top 45%',scrub:.5}});
  $$('.project-card').slice(0,-1).forEach((card,i)=>{
   const next=$$('.project-card')[i+1];
   gsap.fromTo($('.film-project-visual',card),{scale:1,opacity:1},{scale:.97,opacity:.72,ease:'none',scrollTrigger:{trigger:next,start:'top 75%',end:'top 18%',scrub:.5}});
  });
  $$('.process-steps li').forEach((card,i)=>gsap.from(card,{x:(i%3-1)*(wide?45:12),y:30,opacity:.25,duration:.8,scrollTrigger:{trigger:card,start:'top 91%',toggleActions:'play none none reverse'}}));
  gsap.fromTo('#contact',{clipPath:'inset(0 3% 0 3% round 12px)'},{clipPath:'inset(0 0% 0 0% round 8px)',ease:'none',scrollTrigger:{trigger:'#contact',start:'top 96%',end:'top 45%',scrub:.6}});
  gsap.from('.contact-copy h2',{y:55,rotation:2,ease:'none',scrollTrigger:{trigger:'#contact',start:'top 90%',end:'top 35%',scrub:.65}});
  return()=>{intro.kill();removeEventListener('byte:enter',enterSite)};
 });
 $('.filters').addEventListener('click',()=>{requestAnimationFrame(()=>{gsap.set('.film-project-visual',{clearProps:'transform,opacity'});ScrollTrigger.refresh()})});
 document.fonts?.ready.then(()=>ScrollTrigger.refresh());
 addEventListener('load',()=>ScrollTrigger.refresh(),{once:true});
})();
