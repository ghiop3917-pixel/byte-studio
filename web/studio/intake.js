/* Подключение короткой заявки к существующей CRM. Остальной сайт — из архива владельца. */
(() => {
  'use strict';
  const form=document.getElementById('contact-form');
  if(!form)return;
  const button=document.getElementById('send-request');
  const label=button.querySelector('span');
  const status=form.querySelector('.form-status');
  const en=()=>window.ByteLocale?.language==='en';
  const copy=(ru,eng)=>en()?eng:ru;
  let requestId=crypto.randomUUID(),signature='',sending=false,savedReference=null;
  addEventListener('byte:language',()=>{
    if(savedReference!==null){
      label.textContent=copy('Заявка отправлена','Request sent');
      status.textContent=copy('Заявка №'+savedReference+' сохранена. Ответим по указанному контакту.','Request #'+savedReference+' saved. We will reply using your contact details.');
    }
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(sending||button.dataset.sent==='true'||!form.reportValidity())return;
    const data=new FormData(form);
    const payload={contact:String(data.get('contact')||'').trim(),description:String(data.get('description')||'').trim(),budget:String(data.get('budget')||'').trim(),website:String(data.get('website')||''),consent:data.get('consent')==='on'};
    const current=JSON.stringify(payload);
    if(signature&&signature!==current)requestId=crypto.randomUUID();
    signature=current;sending=true;button.disabled=true;button.setAttribute('aria-busy','true');
    label.textContent=copy('Отправляем…','Sending…');status.textContent='';status.classList.remove('is-error','is-success');
    try{
      const response=await fetch('/public/intake',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,request_id:requestId})});
      if(!response.headers.get('Content-Type')?.includes('application/json'))throw Error(copy('Приём заявок пока недоступен. Напишите нам в Telegram ниже.','Submission is unavailable. Please use the Telegram link below.'));
      const result=await response.json();
      if(!response.ok)throw Error(copy(result.error||'Не удалось отправить заявку. Попробуйте ещё раз.','The request was not saved. Please try again or contact us on Telegram.'));
      if(result.accepted!==true)throw Error(copy('Сервер не подтвердил сохранение заявки.','The server did not confirm your request.'));
      savedReference=result.reference;
      button.dataset.sent='true';label.textContent=copy('Заявка отправлена','Request sent');
      status.textContent=copy('Заявка №'+result.reference+' сохранена. Ответим по указанному контакту.','Request #'+result.reference+' saved. We will reply using your contact details.');status.classList.add('is-success');
    }catch(error){
      status.textContent=error instanceof TypeError?copy('Нет соединения. Попробуйте ещё раз или напишите в Telegram.','Connection failed. Please retry or use Telegram.'):error.message;
      status.classList.add('is-error');button.disabled=false;label.textContent=copy('Отправить заявку','Send request');
    }finally{sending=false;button.removeAttribute('aria-busy');}
  });
})();
