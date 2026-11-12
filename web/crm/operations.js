/* Панели качества и обслуживания. Все изменения выполняются через защищённый API. */
'use strict';
window.renderStage7 = async function renderStage7(view,box,ctx,days=30) {
  const {api,el,button,openLead}=ctx;
  box.replaceChildren();
  const state=value=>({pending:'запланировано',completed:'выполнено',running:'выполняется',retry_wait:'ожидает повтора',failed:'остановлено после ошибок',interrupted:'прервано'})[value]||value;
  const date=value=>value?new Date(value*1000).toLocaleString():'ещё нет';
  const metric=(title,value)=>{const tile=el('article',undefined,'tile');tile.append(el('div',String(value),'count'),el('p',title));return tile;};
  const judged=item=>item.judged<5?`Недостаточно оценок (${item.judged})`:`Одобрено ${Math.round(item.observed_approval_rate*100)}% из ${item.judged} оценённых`;
  const reload=()=>window.renderStage7(view,box,ctx,days);
  if(view==='quality'){
    const report=await api('insights?days='+days);
    const controls=el('div',undefined,'actions'),period=el('select');period.setAttribute('aria-label','Период статистики');
    for(const n of [7,30,90]){const option=el('option',n+' дней');option.value=n;period.append(option);}period.value=days;
    period.onchange=()=>ctx.guard(()=>window.renderStage7(view,box,ctx,Number(period.value)));controls.append(period);box.append(controls);
    const grid=el('div',undefined,'grid');grid.append(metric('Источников с сообщениями',report.sources.length),metric('Заявок в CRM за период',report.feedback.leads),metric('Оценено владельцем',report.feedback.judged),metric('Подтверждённых полезных',report.feedback.approved));box.append(grid);
    box.append(el('p',report.note,'operations-note'),el('h2','Качество источников'));
    if(!report.sources.length)box.append(el('div','За этот период сообщений нет.','empty'));
    for(const source of report.sources){const row=el('article',undefined,'tile quality-source');row.append(el('h3',source.name),el('p',`${source.messages} сообщений · ${source.candidates} кандидатов по правилам · ${source.leads} карточек CRM`),el('p',`Подходит: ${source.approved} · Мусор: ${source.rejected} · Без решения: ${source.unjudged}`),el('p',judged(source),'tag'),el('p',`По текущим правилам ${report.version} обработано ${source.current_version_messages} из ${source.messages} сообщений`,'meta'));const actions=el('div',undefined,'actions');source.sample_lead_ids.forEach(id=>actions.append(button('Оценить #'+id,()=>openLead(id))));row.append(actions);box.append(row);}
    box.append(el('h2','Направления'));
    for(const category of report.categories){const tile=el('article',undefined,'tile');tile.append(el('h3',category.category),el('p',`${category.leads} карточек · Подходит ${category.approved} · Мусор ${category.rejected}`),el('p',judged(category)));box.append(tile);}
    box.append(el('h2','Причины отказа'));if(!Object.keys(report.rejection_reasons).length)box.append(el('p','Причины ещё не отмечены. Оцени заявку в карточке.'));
    for(const [reason,count]of Object.entries(report.rejection_reasons))box.append(el('p',`${reason}: ${count}`));
    if(report.won_prices.length){box.append(el('h2','Фактические суммы сделок'));for(const item of report.won_prices)box.append(el('p',`${item.currency}: ${item.count} сделок · ${item.min}–${item.max} · медиана ${item.median??'недостаточно данных'}`));}
    return;
  }
  if(view==='suggestions'){
    box.append(el('p','Предложения основаны на твоих оценках. Поддержка предложения записывает решение; правила, направления и источники автоматически не меняются.','operations-note'),button('Пересчитать предложения',async()=>{await api('suggestions','POST',{});await reload();}));
    const items=await api('suggestions');if(!items.length)box.append(el('div','Предложений пока нет. Они появятся, когда накопятся данные для проверки.','empty'));
    for(const item of items){const tile=el('article',undefined,'tile');tile.append(el('h3',item.title),el('p',({pending:'Ждёт решения',approved:'Поддержано; изменения не применены',rejected:'Отклонено'})[item.status]),el('p',`Карточек: ${item.evidence.leads??0} · Оценено: ${item.evidence.judged??0} · Мусор: ${item.evidence.rejected??0}`));const actions=el('div',undefined,'actions');for(const [decision,label]of [['approved','Поддержать предложение'],['rejected','Отклонить']])actions.append(button(label,async()=>{await api('suggestions/'+item.id+'/decision','POST',{decision});await reload();}));tile.append(actions);box.append(tile);}return;
  }
  const report=await api('operations'),health=report.health;
  box.append(el('h2',health.status==='healthy'?'Система работает':'Есть пункты для проверки'),el('p','Обслуживание работает внутри мониторинга. Служебных рассылок в Telegram нет. Копии и данные автоматически не удаляются.','operations-note'));
  const componentNames={monitor:'Сбор сообщений',processing:'Обработка сообщений',notifications:'Уведомления'};
  for(const h of health.heartbeats)box.append(el('p',`${componentNames[h.component]||h.component}: ${({running:'работает',stopped:'остановлен',stale:'нет свежей активности',unknown:'нет данных'})[h.state]} · ${h.age_seconds===null?'нет heartbeat':Math.round(h.age_seconds)+' сек. назад'}`));
  for(const alert of health.alerts){const labels={heartbeat:'Проверь активность компонента',queue:'В очереди есть задачи, требующие проверки',sources:'Есть недоступные источники',telegram_cooldown:'Действует ограничение Telegram — ожидаем без обхода',backup:'Нет свежей проверенной резервной копии',maintenance_failed:'Задание обслуживания остановлено после трёх ошибок',maintenance_paused:'Планировщик приостановлен после ошибок; нужен ручной запуск задания',disk_space:'Мало места для надёжной резервной копии'};box.append(el('p',labels[alert.kind]||alert.kind,'operations-alert'));}
  const jobNames={backup:'Резервная копия',health:'Контроль состояния',insights:'Статистика и предложения'};
  box.append(el('h2','Расписание обслуживания'));
  for(const job of report.jobs){const tile=el('article',undefined,'tile');tile.append(el('h3',jobNames[job.kind]),el('p',`${state(job.status)} · Последний успех: ${date(job.last_success)}`),el('p',`Следующий запуск: ${date(job.next_run)} · Неудачных попыток: ${job.attempts}/3`));if(job.error)tile.append(el('p','Ошибка: '+job.error));tile.append(button('Запустить сейчас',async()=>{const result=await api('operations/run','POST',{kind:job.kind});await reload();box.prepend(el('p',result.message,'operations-note'));}));box.append(tile);}
  if(!report.jobs.length)box.append(button('Включить обслуживание при следующем цикле мониторинга',async()=>{await api('operations/run','POST',{kind:'health'});await reload();}));
  box.append(el('h2','Проверенные копии'));if(!report.backups.length)box.append(el('p','Проверенных плановых копий пока нет. Копии перед миграциями доступны в прежнем разделе резервных копий.'));
  for(const item of report.backups){const tile=el('article',undefined,'tile');tile.append(el('h3',item.path),el('p',date(item.created_at)+' · '+Math.round(item.size/1024)+' КБ · integrity '+item.integrity),button('Проверить копию',async()=>{await api('backups/'+item.id+'/verify');tile.append(el('p','Контрольная сумма и целостность подтверждены. Рабочая база не изменялась.','tag'));}));box.append(tile);}
  box.append(el('h2','Последние выполнения'));for(const item of report.runs)box.append(el('p',`${date(item.started_at)} · ${jobNames[item.job_name.split(':')[0]]} · ${state(item.status)}${item.error?' · '+item.error:''}`));
};
