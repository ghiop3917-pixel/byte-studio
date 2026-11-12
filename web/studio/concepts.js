/* Два самостоятельных демо. Все изменения остаются в памяти вкладки. */
'use strict';
(() => {
  const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(document.body.classList.contains('forma-page')){
    document.querySelectorAll('[data-room]').forEach(button=>button.addEventListener('click',()=>{
      document.querySelectorAll('[data-room]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});
      document.querySelectorAll('[data-room-type]').forEach(card=>{card.hidden=button.dataset.room!=='all'&&card.dataset.roomType!==button.dataset.room;});
    }));
    const spaces={living:{title:'Quiet Living',image:'/images/forma-interior.webp',description:'Концепция светлой гостиной: мягкие фактуры, естественное освещение и спокойная композиция. Фотография используется для демонстрации сайта, это не реализованный проект студии.'},quiet:{title:'Soft Balance',image:'/images/forma-living.webp',description:'Исследование тёплого минимализма: нейтральная палитра, лёгкие формы и продуманные детали. Демонстрационный проект сайта, не реальный клиентский кейс.'}};
    const dialog=document.getElementById('space-dialog');
    document.querySelectorAll('[data-space]').forEach(button=>button.addEventListener('click',()=>{
      const item=spaces[button.dataset.space];document.getElementById('space-title').textContent=item.title;document.getElementById('space-description').textContent=item.description;
      const image=document.getElementById('space-image');image.src=item.image;image.alt=item.title;dialog.showModal();
    }));
    document.getElementById('space-close').onclick=()=>dialog.close();
    dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});
    if(!reduced){
      const observer=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('in-view');observer.unobserve(entry.target);}}),{threshold:.15});
      document.querySelectorAll('.forma-philosophy,.forma-projects article,.forma-end').forEach(el=>{el.classList.add('concept-reveal');observer.observe(el);});
    }
  }
  if(!document.body.classList.contains('nexus-page'))return;
  const projects=[
    {id:1,title:'Forma — Digital experience',category:'Web development',description:'Сайт интерьерного бренда: от идеи до интерактивной коллекции.',status:'progress',color:'peach',icon:'F',progress:65},
    {id:2,title:'Telegram booking',category:'Telegram product',description:'Запись на услуги, уведомления и удобный личный кабинет.',status:'progress',color:'lavender',icon:'↗',progress:40},
    {id:3,title:'Brand platform',category:'Brand & design',description:'Визуальная система для цифрового продукта.',status:'done',color:'sage',icon:'✳',progress:100},
    {id:4,title:'Client workspace',category:'Automation',description:'Единое пространство для заявок и следующих действий.',status:'new',color:'blue',icon:'⌘',progress:0}
  ];
  const labels={new:'Новый',progress:'В работе',done:'Готово'};
  const events=['Демонстрационное пространство открыто'];
  let filter='all',view='projects';
  const el=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  function toast(text){const n=document.getElementById('nexus-toast');n.textContent=text;n.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>n.classList.remove('show'),2500);}
  function render(){
    document.getElementById('stat-total').textContent=projects.length;
    document.getElementById('stat-progress').textContent=projects.filter(p=>p.status==='progress').length;
    document.getElementById('stat-done').textContent=projects.filter(p=>p.status==='done').length;
    document.getElementById('nav-project-count').textContent=projects.length;
    document.getElementById('project-count').textContent=String(projects.length).padStart(2,'0');
    const list=document.getElementById('nexus-projects'),search=document.getElementById('nexus-search').value.toLocaleLowerCase();list.replaceChildren();
    const matches=projects.filter(p=>(filter==='all'||p.status===filter)&&(p.title+' '+p.category+' '+p.description).toLocaleLowerCase().includes(search));
    for(const project of matches){
      const card=el('article',undefined,'nexus-project');card.dataset.projectId=project.id;
      const top=el('div',undefined,'project-card-top'),icon=el('span',project.icon,'project-icon '+project.color),status=el('select');status.setAttribute('aria-label','Статус проекта '+project.title);
      for(const [key,label]of Object.entries(labels)){const o=el('option',label);o.value=key;status.append(o);}status.value=project.status;
      status.onchange=()=>{project.status=status.value;project.progress=project.status==='done'?100:project.status==='new'?0:Math.max(25,Math.min(project.progress,85));events.unshift('«'+project.title+'»: '+labels[project.status]);render();toast('Статус проекта обновлён');};
      top.append(icon,status);card.append(top,el('div',project.category.toUpperCase(),'project-card-category'),el('h3',project.title),el('p',project.description));
      const progress=el('div',undefined,'project-progress-label');progress.append(el('span','Прогресс'),el('b',project.progress+'%'));const track=el('div',undefined,'project-progress-track'),bar=el('i');bar.style.width=project.progress+'%';track.append(bar);
      const bottom=el('div',undefined,'project-card-bottom');bottom.append(el('span','DEMO PROJECT'),el('span','↗'));card.append(progress,track,bottom);list.append(card);
    }
    document.getElementById('nexus-empty').hidden=matches.length>0;
    const activity=document.getElementById('nexus-activity');activity.replaceChildren();events.forEach((event,index)=>{const row=el('div',undefined,'activity-item');row.append(el('span',String(events.length-index).padStart(2,'0')),el('p',event),el('small','Эта сессия'));activity.append(row);});
  }
  document.querySelectorAll('[data-status-filter]').forEach(button=>button.onclick=()=>{filter=button.dataset.statusFilter;document.querySelectorAll('[data-status-filter]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});render();});
  document.getElementById('nexus-search').oninput=render;
  document.querySelectorAll('[data-nexus-view]').forEach(button=>button.onclick=()=>{view=button.dataset.nexusView;document.querySelectorAll('[data-nexus-view]').forEach(b=>b.classList.toggle('active',b===button));for(const pane of ['projects','overview','activity'])document.getElementById('nexus-'+(pane==='projects'?'project':pane)+'-panel').hidden=pane!==view;document.getElementById('nexus-breadcrumb').textContent={projects:'Проекты',overview:'Обзор',activity:'Активность'}[view];});
  const dialog=document.getElementById('project-dialog');
  document.getElementById('new-project').onclick=()=>dialog.showModal();document.getElementById('project-close').onclick=()=>dialog.close();
  document.getElementById('nexus-project-form').onsubmit=event=>{event.preventDefault();const title=document.getElementById('nexus-title').value.trim();if(!title)return;projects.unshift({id:Math.max(...projects.map(p=>p.id))+1,title,category:document.getElementById('nexus-category').value,description:document.getElementById('nexus-description').value.trim()||'Описание ещё не добавлено.',status:'new',color:'lavender',icon:title.charAt(0).toUpperCase(),progress:0});events.unshift('Создан проект «'+title+'»');filter='all';document.getElementById('nexus-search').value='';document.querySelector('[data-status-filter="all"]').click();document.querySelector('[data-nexus-view="projects"]').click();dialog.close();event.target.reset();render();toast('Проект создан в демо-пространстве');};
  render();
})();
