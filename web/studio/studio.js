/* Заявка: контакт + задача; бюджет по желанию. Отправка только по действию посетителя. */
'use strict';
(() => {
  const form=document.getElementById('brief-form');
  if(!form)return;
  const get=id=>document.getElementById(id)?.value.trim()||'';
  const status=document.getElementById('intake-status');
  const button=document.getElementById('send-intake');
  let requestId=crypto.randomUUID(), previousPayload='';
  let sending=false;
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(sending)return;
    if(!form.reportValidity())return;
    const payload={contact:get('contact'),description:get('task'),budget:get('budget'),website:get('website'),consent:document.getElementById('consent').checked};
    const fingerprint=JSON.stringify(payload);
    if(previousPayload&&fingerprint!==previousPayload)requestId=crypto.randomUUID();
    previousPayload=fingerprint;
    sending=true;button.disabled=true;button.textContent='Отправляем…';status.textContent='';
    try{
      const response=await fetch('/public/intake',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,request_id:requestId})});
      if(!response.headers.get('Content-Type')?.includes('application/json'))throw Error('Форма пока недоступна. Напишите в Telegram — контакт указан ниже.');
      const result=await response.json();
      if(!response.ok)throw Error(result.error||'Не получилось отправить. Попробуйте ещё раз или напишите в Telegram.');
      if(result.accepted!==true)throw Error('Сервер не подтвердил получение заявки. Напишите в Telegram.');
      status.textContent='Заявка №'+result.reference+' сохранена. Свяжемся с вами по указанному контакту.';
      button.textContent='Заявка отправлена ✓';
      form.classList.add('is-sent');
    }catch(error){status.textContent=error.message;button.disabled=false;button.innerHTML='Отправить заявку <span>↗</span>';}
    finally{sending=false;}
  });
  document.getElementById('prepare-message')?.addEventListener('click',()=>{
    const lines=['Здравствуйте! Хочу обсудить проект с Byte Studio.'];
    if(get('contact'))lines.push('Контакт: '+get('contact'));
    if(get('task'))lines.push('Задача: '+get('task'));
    if(get('budget'))lines.push('Бюджет: '+get('budget'));
    document.getElementById('message').value=lines.join('\n\n');
    document.getElementById('brief-result').hidden=false;
  });
  document.getElementById('copy')?.addEventListener('click',async()=>{
    const message=document.getElementById('message'),state=document.getElementById('copy-state');
    try{await navigator.clipboard.writeText(message.value);state.textContent='Скопировано. Вставьте текст в чат @bytestudio_dev.';}
    catch{message.select();state.textContent='Скопируйте выделенный текст вручную.';}
  });
})();
