/* Copyright (c) 2026 ampohene. All rights reserved. */
(function(){
  var $=function(s,r){return (r||document).querySelector(s)},$$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};
  var calm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  // toasts
  $$('.toast').forEach(function(t,i){
    var close=function(){t.classList.add('out');setTimeout(function(){t.remove()},300)};
    t.addEventListener('click',close);setTimeout(close,5200+i*600);
  });
  // count-up numbers
  $$('[data-count]').forEach(function(el){
    var end=parseInt(el.dataset.count,10)||0,suf=el.dataset.suffix||'';
    if(calm||end===0){el.textContent=end+suf;return}
    var t0=null;function step(t){t0=t0||t;var p=Math.min((t-t0)/900,1);el.textContent=Math.round(end*(1-Math.pow(1-p,3)))+suf;if(p<1)requestAnimationFrame(step)}
    requestAnimationFrame(step);
  });
  // live table filter
  $$('[data-filter]').forEach(function(inp){
    inp.addEventListener('input',function(){
      var q=inp.value.toLowerCase();
      $$('#'+inp.dataset.filter+' tbody tr').forEach(function(r){r.hidden=q&&r.textContent.toLowerCase().indexOf(q)<0});
    });
  });
  // dialogs
  function openDlg(d){if(d.showModal)d.showModal();else d.setAttribute('open','')}
  $$('[data-modal]').forEach(function(b){b.addEventListener('click',function(){openDlg($('#'+b.dataset.modal))})});
  $$('dialog').forEach(function(d){
    d.addEventListener('click',function(e){if(e.target===d)d.close()});
    $$('[data-close]',d).forEach(function(b){b.addEventListener('click',function(){d.close()})});
  });
  // confirm before risky forms
  var cd=$('#confirm'),pending=null;
  $$('form[data-confirm]').forEach(function(f){
    f.addEventListener('submit',function(e){
      if(f.dataset.ok||!cd)return;e.preventDefault();pending=f;
      $('#confirm-msg').textContent=f.dataset.confirm;openDlg(cd);
    });
  });
  if(cd){$('#confirm-yes').addEventListener('click',function(){if(pending){pending.dataset.ok='1';pending.submit()}})}
  // member quick view
  var qv=$('#qv');
  $$('tr[data-member]').forEach(function(r){
    r.addEventListener('click',function(e){
      if(e.target.closest('a,button'))return;
      var d=JSON.parse(r.dataset.member);
      $('#qv-name').textContent=d.name;
      var dl=$('#qv-list');dl.innerHTML='';
      d.rows.forEach(function(p){var a=document.createElement('dt'),b=document.createElement('dd');a.textContent=p[0];b.textContent=p[1]||'-';dl.appendChild(a);dl.appendChild(b)});
      $('#qv-edit').href=d.edit;openDlg(qv);
    });
  });
  // attendance
  var att=$('#attform');
  if(att){
    var tally=function(){
      ['present','late','excused','absent'].forEach(function(s){
        var n=$$('input[value="'+s+'"]:checked',att).length;var el=$('#n-'+s);if(el)el.textContent=n;
      });
    };
    att.addEventListener('change',tally);tally();
    var all=$('#mark-all');if(all)all.addEventListener('click',function(){$$('input[value="present"]',att).forEach(function(i){i.checked=true});tally()});
  }
  // copy
  $$('[data-copy]').forEach(function(b){b.addEventListener('click',function(){
    var t=$('#'+b.dataset.copy).textContent.trim();
    (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){b.textContent='Copied'}).catch(function(){var r=document.createRange();r.selectNodeContents($('#'+b.dataset.copy));getSelection().removeAllRanges();getSelection().addRange(r);b.textContent='Press Ctrl+C'});
  })});
  // show/hide password
  $$('[data-pw]').forEach(function(b){b.addEventListener('click',function(){
    var i=$('#'+b.dataset.pw);i.type=i.type==='password'?'text':'password';b.textContent=i.type==='password'?'Show':'Hide';
  })});
  $$('dialog[data-auto]').forEach(openDlg);
  // floating action button
  var fab=$('.fab');if(fab){$('button',fab).addEventListener('click',function(){fab.classList.toggle('open')})}
})();
(function(){var p=document.querySelector('[data-print]');if(p)p.addEventListener('click',function(){window.print()})})();
(function(){
  var f=document.getElementById('unitfilter');
  if(f)f.addEventListener('change',function(){
    var v=f.value;[].forEach.call(document.querySelectorAll('#attform .person'),function(p){p.hidden=!!v&&(p.dataset.units||'').indexOf(','+v+',')<0});
  });
  var l=document.querySelector('[data-filter-list]');
  if(l)l.addEventListener('input',function(){
    var q=l.value.toLowerCase();[].forEach.call(document.querySelectorAll('#'+l.dataset.filterList+' .check'),function(r){r.hidden=!!q&&r.textContent.toLowerCase().indexOf(q)<0});
  });
})();
(function(){
  var n=document.getElementById('sidenav'),b=document.querySelector('.burger');if(!n||!b)return;
  function set(o){n.classList.toggle('open',o);document.body.classList.toggle('menu-open',o);b.setAttribute('aria-expanded',o?'true':'false')}
  b.addEventListener('click',function(e){e.stopPropagation();set(!n.classList.contains('open'))});
  document.addEventListener('click',function(e){if(!n.contains(e.target))set(false)});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')set(false)});
  n.addEventListener('click',function(e){if(e.target.closest('a.nl'))set(false)});
  window.addEventListener('resize',function(){if(window.innerWidth>820)set(false)});
})();
