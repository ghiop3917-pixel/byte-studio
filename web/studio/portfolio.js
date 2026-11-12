/* Структура данных готова к замене будущим авторизованным API кейсов. */
'use strict';
window.bytePortfolio = Object.freeze([
 {id:'forma-interiors',title:'Forma — Quiet Living',category:'web',kind:'concept',url:'/forma.html',technologies:['Editorial design','Interactive catalog','Responsive']},
 {id:'nexus-workspace',title:'Nexus — Work in Flow',category:'automation',kind:'demo',url:'/nexus.html',technologies:['Product design','Interactive workspace','State management']}
]);
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{
  const filter=button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(item=>{const active=item===button;item.classList.toggle('active',active);item.setAttribute('aria-pressed',String(active));});
  let count=0;
  document.querySelectorAll('.work[data-category]').forEach(work=>{const show=filter==='all'||work.dataset.category===filter;work.hidden=!show;if(show)count++;});
  const countLabel=document.getElementById('works-count'),empty=document.getElementById('works-empty');
  if(countLabel)countLabel.textContent='/ '+String(count).padStart(2,'0');
  if(empty)empty.hidden=count>0;
  document.dispatchEvent(new Event('byte:filter'));
}));

// Статические первые два превью остаются доступными без JavaScript.
// Последующие кейсы можно добавлять в projects.json; будущая админка заменит источник данных.
fetch('/projects.json').then(response=>{if(!response.ok)throw Error('Каталог недоступен');return response.json();}).then(projects=>{
 if(!Array.isArray(projects))return;
 const valid=projects.filter(p=>p&&typeof p.id==='string'&&/^[a-z0-9-]+$/.test(p.id)&&typeof p.title==='string'&&typeof p.description==='string'&&['web','automation'].includes(p.category)&&['demo','concept'].includes(p.kind)&&typeof p.url==='string'&&p.url.startsWith('/')&&!p.url.startsWith('//')&&Array.isArray(p.technologies)&&p.technologies.every(t=>typeof t==='string'));
 window.bytePortfolio=Object.freeze(valid);
 const grid=document.getElementById('works-grid');
 valid.forEach((project,index)=>{
  let article=grid.children[index];
  if(!article){
   article=document.createElement('article');article.className='work work-additional';article.dataset.category=project.category;
   const visual=document.createElement('a');visual.className='work-visual';visual.href=project.url;
   const label=document.createElement('span');label.className='project-label';label.textContent='DEMO / CONCEPT';visual.append(label);
   const name=document.createElement('span');name.className='concept-name';name.textContent=project.title;visual.append(name);
   if(typeof project.previewImage==='string'&&/^\/(?!\/)[a-zA-Z0-9_./-]+\.(webp|avif|png|jpg)$/.test(project.previewImage)){
    const img=document.createElement('img');img.src=project.previewImage;img.alt=project.title;img.loading='lazy';img.decoding='async';visual.append(img);
   }
   const open=document.createElement('span');open.className='project-open';open.textContent='Открыть проект ↗';visual.append(open);
   const info=document.createElement('div');info.className='work-info';const text=document.createElement('div');
   const cat=document.createElement('p');cat.className='project-category';cat.textContent=project.category.toUpperCase()+' / '+project.kind.toUpperCase();
   const title=document.createElement('h3');title.textContent=project.title;const description=document.createElement('p');description.textContent=project.description;
   text.append(cat,title,description);info.append(text);article.append(visual,info);const tech=document.createElement('p');tech.className='project-tech';article.append(tech);grid.append(article);
  }
  article.dataset.projectId=project.id;article.dataset.category=project.category;
  article.querySelector('.work-info h3').textContent=project.title;
  article.querySelector('.work-info p:not(.project-category)').textContent=project.description;
  article.querySelector('.project-tech').textContent=project.technologies.join(' · ');
  article.querySelector('.work-visual').href=project.url;
 });
 const count=document.getElementById('works-count');if(count)count.textContent='/ '+String(grid.children.length).padStart(2,'0');
 document.dispatchEvent(new Event('byte:filter'));
}).catch(()=>{/* Существующие концепции остаются доступны из HTML. */});
