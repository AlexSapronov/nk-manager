'use strict';
/* Account provisioning UI: a setup code is returned only once and never cached. */
window.AccountsUI=(()=>{
 function html(users){
  return `<section class="section"><h2>Сотрудники и пароли</h2>
  <p class="muted">У каждого сотрудника собственный пароль. Новый сотрудник получает одноразовый код, затем создаёт пароль при первом входе.</p>
  <div class="content-scroll"><table><thead><tr><th>Username</th><th>Доступ</th><th>Пароль</th><th>Действия</th></tr></thead>
  <tbody>${users.map(u=>`<tr><td>${esc(u.username)}</td><td>${u.is_admin?'Администратор':'Сотрудник'}</td>
  <td>${u.needs_password?'Ожидает первого входа':'Установлен'}</td>
  <td><button data-reset-user="${esc(u.username)}">Сбросить пароль</button></td></tr>`).join('')}</tbody></table></div>
  <div class="actions"><input id="new-user" aria-label="Username нового сотрудника" placeholder="Новый username" maxlength="64">
  <button id="add-user">Создать сотрудника</button></div>
  </section>`;
 }
 function showCode(data){
  modal('Одноразовый код первого входа',`<p>Пользователь <b>${esc(data.username)}</b> должен ввести этот код вместо пароля, затем задать личный пароль дважды.</p>
  <p class="muted">Код показывается только сейчас. Передайте его сотруднику приватно. Повторный сброс отменяет прежний код.</p>
  <label class="field">Код первого входа<input id="user-setup-code" type="text" class="mono" readonly></label>`);
  $('#user-setup-code').value=data.setup_code;
  $('#user-setup-code').onclick=()=>$('#user-setup-code').select();
  $('#user-setup-code').select();
 }
 function bindControls(){
  bind('#add-user',async()=>{
   const username=$('#new-user').value.trim();
   if(!username)throw new Error('Введите username');
   const result=await api('/api/admin/users',json('POST',{username}));
   await admin();showCode(result);
  });
  $$('[data-reset-user]').forEach(button=>{
   button.onclick=async()=>{
    const username=button.dataset.resetUser;
    if(!window.confirm(`Сбросить пароль ${username}? Все его действующие сессии будут закрыты.`))return;
    try{
     const result=await api('/api/admin/users/'+encodeURIComponent(username)+'/reset-password',json('POST',{}));
     if(username!==state.user.username)await admin();
     showCode(result);
     if(username===state.user.username){
      toast('Ваши сессии отозваны. Сохраните код и войдите заново.');
     }
    }catch(error){showError(error);}
   };
  });
 }
 return {html,bind:bindControls};
})();
