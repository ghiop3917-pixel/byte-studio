(() => {
 'use strict';
 const $ = (s,root=document)=>root.querySelector(s), $$=(s,root=document)=>[...root.querySelectorAll(s)];
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const motionOff=()=>reduced.matches;
 const previews={
 nova:`<div class="nova preview-inner"><div class="dashboard"><div class="dash-top"><strong>n°va.</strong><span>Workspace</span><span>Overview</span><span>↗</span></div><div class="dash-body"><div class="dash-side"><b>▦ Overview</b><span>↗ Analytics</span><span>◈ Portfolio</span><span>⇄ Transactions</span><span>⚙ Settings</span></div><div class="dash-main"><small>YOUR FINANCES, IN FOCUS.</small><h4>Welcome back, Alex ↗</h4><div class="dash-stats"><span><small>Total balance</small>$ 42,860<em>↗ 12.8%</em></span><span><small>Income</small>$ 8,240<em>↗ 8.2%</em></span><span><small>Savings</small>$ 16,500</span></div><div class="chart"><svg viewBox="0 0 300 100" preserveAspectRatio="none"><defs><linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#6865ff" stop-opacity=".35"/><stop offset="1" stop-color="#6865ff" stop-opacity="0"/></linearGradient></defs><path d="M0 85 Q15 80 25 82 T50 55 T75 64 T100 47 T130 60 T155 32 T180 41 T205 18 T230 29 T260 12 T300 3 V100 H0Z" fill="url(#chartFill)"/><path d="M0 85 Q15 80 25 82 T50 55 T75 64 T100 47 T130 60 T155 32 T180 41 T205 18 T230 29 T260 12 T300 3" fill="none" stroke="#9289ff" stroke-width="2"/></svg></div></div></div></div></div>`,
 aether:`<div class="aether preview-inner"><div class="aether-page"><div class="aether-nav"><b>AETHER</b><span>Spaces &nbsp; Philosophy &nbsp; Contact ↗</span></div><h4>Spaces<br>for a <em>slower</em><br>kind of life.</h4><div class="arch"></div><small>ARCHITECTURE THAT BREATHES.<br>DESIGNED AROUND YOU.</small></div></div>`,
 pulse:`<div class="pulse preview-inner"><div class="phone"><div class="phone-top">‹ &nbsp; Pulse <span>···</span></div><small>ВАШИ ФИНАНСЫ, БЕЗ ЛИШНЕГО.</small><h4>Всё под<br>контролем ✦</h4><div class="phone-card">Общий баланс<b>128 450 ₽</b><span>↗ В этом месяце +12,4%</span></div><div class="phone-row"><i>↗</i>Пополнение<b>+ 25 000 ₽</b></div><div class="phone-row"><i>☕</i>Кофе и встречи<b>− 480 ₽</b></div><div class="phone-row"><i>◈</i>Подписки<b>− 1 290 ₽</b></div></div><div class="pulse-floating">✓ &nbsp; Всё синхронизировано<span>Меньше забот. Больше жизни.</span></div></div>`,
 flow:`<div class="flow preview-inner"><div class="flow-canvas"><div class="flow-node">↗ Новый запрос<small>WEBHOOK · TRIGGER</small></div><div class="flow-node">✳ Routing<small>PROCESS & UNDERSTAND</small></div><div class="flow-node">◈ CRM<small>CREATE NEW LEAD</small></div><div class="flow-node">➤ Telegram<small>SEND NOTIFICATION</small></div><svg viewBox="0 0 330 240"><path d="M85 118H165V55H285M165 118V190H285" fill="none" stroke="#78708e" stroke-width="1"/><path d="M85 118H165V55H285" fill="none" stroke="#a69af6" stroke-width="1" stroke-dasharray="3 5"/></svg><span class="flow-dot"></span></div><span class="flow-label">CONNECT. AUTOMATE. EVOLVE.</span></div>`
 };
 Object.assign(previews,window.BYTE_CASE_PREVIEWS||{});
 const grid=$('#project-grid');
 function renderProjects(){
  grid.innerHTML=window.BYTE_PROJECTS.map(p=>`<article class="project-card" data-category="${p.category}"><button class="project-preview" data-project="${p.id}" aria-label="Открыть концепцию ${p.name}"><span class="demo-badge">DEMO / CONCEPT</span><span class="preview-corner">↗</span>${previews[p.visual]}</button><div class="project-info"><div><h3>${p.name}</h3><p>${p.subtitle}</p></div><button class="project-open" data-project="${p.id}">СМОТРЕТЬ ↗</button></div><div class="tech-tags">${p.tech.map(t=>`<span>${t}</span>`).join('')}</div></article>`).join('');
 }
 renderProjects();
 $$('.filters button').forEach(button=>button.addEventListener('click',()=>{
  $$('.filters button').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button))});
  const filter=button.dataset.filter;
  grid.classList.toggle('filtered',filter!=='all');
  $$('.project-card').forEach(card=>{card.hidden=filter!=='all'&&card.dataset.category!==filter;if(!card.hidden&&!motionOff())card.animate([{opacity:0,transform:'translateY(22px)'},{opacity:1,transform:'translateY(0)'}],{duration:500,easing:'cubic-bezier(.2,.8,.2,1)'})});
  window.ScrollTrigger?.refresh();
 }));
 const dialog=$('#project-dialog');let opener;
 grid.addEventListener('click',event=>{const button=event.target.closest('[data-project]');if(!button)return;const project=window.BYTE_PROJECTS.find(p=>p.id===button.dataset.project);if(!project)return;opener=button;$('#dialog-title').textContent=project.name;$('#dialog-description').textContent=project.description;$('#dialog-tags').replaceChildren(...project.tech.map(t=>{const span=document.createElement('span');span.textContent=t;return span}));dialog.showModal()});
 $('.dialog-close').addEventListener('click',()=>dialog.close());
 dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close()}});
 dialog.addEventListener('close',()=>opener?.focus({preventScroll:true}));
 $('#dialog-cta').addEventListener('click',()=>{dialog.close();setTimeout(()=>$('#contact-form input').focus({preventScroll:true}),500)});
 const toggle=$('.menu-toggle');toggle.addEventListener('click',()=>{const open=toggle.getAttribute('aria-expanded')!=='true';toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-label',open?'Закрыть меню':'Открыть меню');$('nav').classList.toggle('is-open',open)});
 $$('nav a').forEach(a=>a.addEventListener('click',()=>{toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Открыть меню');$('nav').classList.remove('is-open')}));
 $$('[data-service]').forEach(a=>a.addEventListener('click',()=>{document.getElementById('lead-task').focus({preventScroll:true})}));
 const form=$('#contact-form');
 document.getElementById('prepare-brief').addEventListener('click',()=>{const data=new FormData(form);$('#brief-text').value=window.ByteLocale?window.ByteLocale.formatBrief(data):`Контакт: ${data.get('contact')||''}\nЗадача: ${data.get('description')||''}\nБюджет: ${data.get('budget')||'Обсудим'}`;$('#brief-result').hidden=false;$('.form-status').textContent=window.ByteLocale?.language==='en'?'Brief prepared locally. It has not been sent by this action.':'Описание подготовлено в этой вкладке. Это действие не отправляет заявку.';});
 $('#copy-brief').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('#brief-text').value);$('.form-status').textContent='Описание проекта скопировано. Можно отправить его в Telegram.'}catch{$('#brief-text').focus();$('#brief-text').select();$('.form-status').textContent='Текст выделен. Скопируйте его вручную.'}});
 // Движение не скрывает содержимое, если библиотека недоступна.
 function setupMotion(){
  if(document.documentElement.dataset.reference==='true')return;
  if(motionOff())return;
  const gsap=window.gsap;
  if(gsap&&window.ScrollTrigger){
   gsap.registerPlugin(ScrollTrigger);
   $$('.word-line').forEach(line=>{const walker=document.createTreeWalker(line,NodeFilter.SHOW_TEXT);const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);nodes.forEach(node=>{if(!node.textContent.trim())return;const fragment=document.createDocumentFragment();node.textContent.split(/(\s+)/).forEach(word=>{if(!word.trim())fragment.append(document.createTextNode(word));else{const span=document.createElement('span');span.className='hero-word';span.textContent=word;fragment.append(span)}});node.replaceWith(fragment)})});
   gsap.from('.hero-word',{y:60,opacity:0,rotate:2,stagger:.06,duration:1.15,ease:'power3.out',delay:.1,clearProps:'all'});
   gsap.from('.hero-description,.hero-actions',{y:25,opacity:0,stagger:.12,duration:1,delay:.6,clearProps:'all'});
   gsap.from('.hero-art',{scale:.7,opacity:0,rotate:-15,duration:2,ease:'power3.out',clearProps:'all'});
   $$('.reveal').forEach(el=>gsap.from(el,{y:35,opacity:0,duration:.85,ease:'power2.out',scrollTrigger:{trigger:el,start:'top 91%',once:true},clearProps:'all'}));
   if(matchMedia('(min-width: 761px)').matches){
    $$('.project-card').forEach((el,i)=>gsap.fromTo(el,{y:i%2?45:0},{y:i%2?-45:-25,ease:'none',scrollTrigger:{trigger:el,start:'top bottom',end:'bottom top',scrub:1.4}}));
    gsap.to('.process-symbol',{rotate:190,ease:'none',scrollTrigger:{trigger:'.process',start:'top bottom',end:'bottom top',scrub:1.5}});
   }
   gsap.from('.footer-word',{y:60,opacity:.15,scrollTrigger:{trigger:'footer',start:'top 94%',end:'top 40%',scrub:1},ease:'none'});
  }
 }
 setupMotion();
 const process=$('.process-steps');let scrollQueued=false;
 function updateScroll(){scrollQueued=false;const range=document.documentElement.scrollHeight-innerHeight;$('.scroll-progress').style.transform=`scaleX(${range>0?scrollY/range:0})`;const rect=process.getBoundingClientRect();const progress=Math.max(0,Math.min(1,(innerHeight*.65-rect.top)/(rect.height-80)));process.style.setProperty('--process',progress);$$('.process-steps li').forEach(el=>el.classList.toggle('is-active',el.getBoundingClientRect().top<innerHeight*.68))}
 addEventListener('scroll',()=>{if(!scrollQueued){scrollQueued=true;requestAnimationFrame(updateScroll)}},{passive:true});updateScroll();
 if(matchMedia('(pointer:fine)').matches){$$('.magnetic').forEach(el=>{el.addEventListener('pointermove',e=>{if(motionOff())return;const r=el.getBoundingClientRect();el.style.transform=`translate(${(e.clientX-r.left-r.width/2)*.1}px,${(e.clientY-r.top-r.height/2)*.15}px)`});el.addEventListener('pointerleave',()=>{el.style.transform='translate(0,0)'})})}
 // Параметрическая 3D-скульптура: локальный Canvas без загрузки тяжёлого движка.
 if(document.documentElement.dataset.reference==='true')return;
 const canvas=$('#sculpture'),ctx=canvas.getContext('2d');
 if(!ctx)return;
 let width=0,height=0,visible=true,frame=0,lastTime=0,angle=0,pointerX=0,pointerY=0,targetX=0,targetY=0;
 const mobile=matchMedia('(max-width:760px)').matches;
 const lowPower=mobile||(navigator.hardwareConcurrency&&navigator.hardwareConcurrency<5);
 const segments=lowPower?88:144,sides=lowPower?9:13,mesh=[],faces=[];
 const point=t=>[(2+Math.cos(3*t)) * Math.cos(2*t),(2+Math.cos(3*t))*Math.sin(2*t),Math.sin(3*t)*1.15];
 function norm(v){const d=Math.hypot(...v)||1;return v.map(n=>n/d)}
 function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}
 for(let i=0;i<segments;i++){const t=i/segments*Math.PI*2,p=point(t),next=point(t+.001),tangent=norm(next.map((v,j)=>v-p[j])),normal=norm(cross(tangent,[0,0,1])),binormal=cross(tangent,normal);for(let j=0;j<sides;j++){const a=j/sides*Math.PI*2;mesh.push(p.map((v,k)=>v+.43*(Math.cos(a)*normal[k]+Math.sin(a)*binormal[k])))}}
 for(let i=0;i<segments;i++)for(let j=0;j<sides;j++)faces.push([i*sides+j,((i+1)%segments)*sides+j,((i+1)%segments)*sides+(j+1)%sides,i*sides+(j+1)%sides]);
 function resize(){const r=canvas.getBoundingClientRect();width=r.width;height=r.height;const dpr=Math.min(devicePixelRatio||1,lowPower?1.25:1.6);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);if(motionOff())draw(0)}
 function rotate(p,ax,ay,az){let[x,y,z]=p;let c=Math.cos(ax),s=Math.sin(ax);[y,z]=[y*c-z*s,y*s+z*c];c=Math.cos(ay);s=Math.sin(ay);[x,z]=[x*c+z*s,-x*s+z*c];c=Math.cos(az);s=Math.sin(az);return[x*c-y*s,x*s+y*c,z]}
 function draw(time){
  ctx.clearRect(0,0,width,height);pointerX+=(targetX-pointerX)*.045;pointerY+=(targetY-pointerY)*.045;
  const scale=Math.min(width,height)*.115;const verts=mesh.map(p=>rotate(p,.6+pointerY*.4,angle+pointerX*.5,-.55));
  const projected=verts.map(([x,y,z])=>{const d=8/(8-z);return[width*.52+x*scale*d,height*.49+y*scale*d,z]});
  const sorted=faces.map(f=>({f,z:f.reduce((a,i)=>a+verts[i][2],0)/4})).sort((a,b)=>a.z-b.z);
  const light=norm([-1,-1.8,3]);
  for(const{f,z}of sorted){const a=verts[f[0]],b=verts[f[1]],c=verts[f[2]],n=norm(cross(b.map((v,i)=>v-a[i]),c.map((v,i)=>v-a[i])));const diffuse=Math.max(0,n.reduce((v,x,i)=>v+x*light[i],0));const edge=Math.pow(Math.max(0,1-Math.abs(n[2])),2);const spec=Math.pow(Math.max(0,n[0]*-.35+n[1]*-.55+n[2]*.85),15);const hue=246+13*(z/3)-31*edge;const lum=20+diffuse*38+edge*22+spec*42;ctx.fillStyle=`hsl(${hue} ${69+spec*12}% ${Math.min(90,lum)}%)`;ctx.strokeStyle=ctx.fillStyle;ctx.lineWidth=.65;ctx.beginPath();f.forEach((idx,i)=>{const p=projected[idx];i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1])});ctx.closePath();ctx.fill();ctx.stroke()}
  // Орбитальная пунктирная линия подчёркивает глубину, оставаясь за объектом.
  ctx.save();ctx.globalCompositeOperation='destination-over';ctx.translate(width*.52,height*.49);ctx.rotate(-.38);ctx.strokeStyle='#7068ba25';ctx.lineWidth=1;ctx.setLineDash([2,7]);ctx.beginPath();ctx.ellipse(0,0,scale*4.3,scale*1.45,0,0,Math.PI*2);ctx.stroke();ctx.restore();
 }
 function loop(time){frame=0;if(!visible||document.hidden||motionOff())return;if(time-lastTime>1000/(lowPower?24:40)){angle+=.0035;draw(time);lastTime=time}frame=requestAnimationFrame(loop)}
 function start(){if(!frame&&visible&&!document.hidden&&!motionOff())frame=requestAnimationFrame(loop)}
 new ResizeObserver(resize).observe(canvas);new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)start();else{cancelAnimationFrame(frame);frame=0}},{rootMargin:'100px'}).observe(canvas);
 $('.hero').addEventListener('pointermove',e=>{if(lowPower)return;const r=canvas.getBoundingClientRect();targetX=(e.clientX-r.left-r.width/2)/r.width;targetY=(e.clientY-r.top-r.height/2)/r.height},{passive:true});
 $('.hero').addEventListener('pointerleave',()=>{targetX=targetY=0});
 document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(frame);frame=0}else start()});
 reduced.addEventListener('change',()=>{if(motionOff()){cancelAnimationFrame(frame);frame=0;window.gsap?.globalTimeline?.clear();$$('.reveal,.word-line,.hero-art,.hero-actions,.hero-description,.project-card,.footer-word').forEach(el=>{el.style.opacity='1';el.style.transform='none'});draw(0)}else start()});
 resize();draw(0);start();
})();
