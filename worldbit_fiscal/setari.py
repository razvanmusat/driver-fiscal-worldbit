"""Pagina /setari (HTML simplu, server-side): doar câmpurile driverului ales la instalare."""
import copy
import html
import re
from decimal import Decimal, InvalidOperation

from . import drawer as drawer_mod
from .contract import percent_key
from .version import __version__
from .pos.base import POS_DRIVERS

_ORIGIN = re.compile(r"^https?://[^/\s]+$")

_CSS = """
:root{--bg:#f4f6fa;--card:#fff;--text:#1b2333;--muted:#667085;--line:#e3e7ee;--primary:#2557d6;--primary-ink:#fff;--primary-soft:#eaf0fe;
--ok:#067647;--ok-bg:#e8f8ef;--warn:#9a5b00;--warn-bg:#fff6e0;--err:#b42318;--err-bg:#fdecea;--field:#fff;--radius:14px;
--shadow:0 1px 2px rgba(16,24,40,.05),0 2px 8px rgba(16,24,40,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#0e131d;--card:#161d2b;--text:#e7eaf2;--muted:#98a2b8;--line:#263045;--primary:#7b9bff;--primary-ink:#0b1020;
--primary-soft:#1c2a4d;--ok:#5fe0a0;--ok-bg:#0f2a20;--warn:#ffc266;--warn-bg:#2d2310;--err:#ff8f85;--err-bg:#33161a;--field:#0f1521;--shadow:none}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif}
.top{position:sticky;top:0;z-index:20;background:var(--card);border-bottom:1px solid var(--line)}
.top-in{max-width:960px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{font-weight:700;font-size:16px;letter-spacing:.2px;margin-right:auto}
.brand small{display:block;font-weight:500;font-size:12px;color:var(--muted);letter-spacing:0}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:var(--primary-soft);color:var(--text);font-size:12.5px;font-weight:500;border:1px solid transparent}
.chip::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--muted)}
.chip.ok{background:var(--ok-bg);color:var(--ok)}.chip.ok::before{background:var(--ok)}
.chip.warn{background:var(--warn-bg);color:var(--warn)}.chip.warn::before{background:var(--warn)}
.tabs{background:var(--card);border-bottom:1px solid var(--line)}
.tabs-in{max-width:960px;margin:0 auto;padding:0 20px;display:flex;gap:4px;overflow-x:auto}
.tab{appearance:none;background:none;border:0;border-bottom:3px solid transparent;color:var(--muted);font:inherit;font-weight:600;padding:12px 14px;cursor:pointer;white-space:nowrap}
.tab:hover{color:var(--text)}
.tab[aria-selected=true]{color:var(--primary);border-bottom-color:var(--primary)}
.js .panel{display:none}.js .panel.on{display:block}
.js .savebar{display:none}.js .savebar.on{display:block}
main{max-width:960px;margin:0 auto;padding:22px 20px 120px}
.notice{border-radius:12px;padding:12px 16px;margin:0 0 16px;font-weight:500;border:1px solid transparent}
.notice.ok{background:var(--ok-bg);color:var(--ok);border-color:var(--ok)}
.notice.err{background:var(--err-bg);color:var(--err);border-color:var(--err)}
pre.out{margin:0 0 16px;padding:14px 16px;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:auto;white-space:pre-wrap;font:13px/1.5 ui-monospace,Consolas,monospace}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:22px 24px;margin:0 0 18px}
.card>h2{margin:0;font-size:17px;font-weight:650}
.card>.sub{margin:4px 0 18px;color:var(--muted);font-size:13.5px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px 20px}
.field{min-width:0}.field.wide{grid-column:1/-1}
label{display:block;font-weight:600;font-size:13.5px;margin:0 0 6px}
.help{display:block;margin-top:6px;color:var(--muted);font-size:12.5px;line-height:1.45}
input[type=text],input[type=password],input[type=number],select,textarea{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--field);color:var(--text);font:inherit;transition:border-color .15s,box-shadow .15s}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-soft)}
textarea{min-height:92px;font-family:ui-monospace,Consolas,monospace;font-size:13.5px;resize:vertical}
.mini{display:flex;gap:10px;align-items:center}.mini input{width:130px;flex:none}
.check{display:flex;gap:10px;align-items:flex-start;font-weight:500;cursor:pointer}.check input{margin-top:4px;accent-color:var(--primary)}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}
.btn{appearance:none;border:1px solid var(--line);background:var(--card);color:var(--text);padding:9px 16px;border-radius:10px;font:inherit;font-weight:600;cursor:pointer;transition:background .15s,border-color .15s}
.btn:hover{border-color:var(--primary)}
.btn.primary{background:var(--primary);color:var(--primary-ink);border-color:var(--primary)}.btn.primary:hover{filter:brightness(1.06)}
.btn.ghost{background:transparent}.btn.small{padding:6px 12px;font-size:13px}
.savebar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);z-index:15}
.savebar-in{max-width:960px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:14px}
.savebar .hint{margin-right:auto;color:var(--muted);font-size:13px}
.port{display:flex;gap:12px;align-items:flex-start;width:100%;text-align:left;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--field);color:var(--text);cursor:pointer;margin:0;font-weight:400}
.port:hover{border-color:var(--primary)}.port.sel{border-color:var(--primary);background:var(--primary-soft)}
.port input{margin-top:4px;accent-color:var(--primary);flex:none}
.port b{display:block;font-weight:600}.port span{display:block;color:var(--muted);font-size:12.5px;word-break:break-all}
.port em{font-style:normal;font-size:11.5px;font-weight:600;padding:1px 8px;border-radius:999px;margin-left:8px;background:var(--ok-bg);color:var(--ok);white-space:nowrap}
.port em.no{background:var(--warn-bg);color:var(--warn)}
.port code{display:block;margin-top:6px;padding:6px 8px;background:var(--bg);border-radius:8px;font-size:12px;word-break:break-all}
.port .nota{color:var(--warn);margin-top:4px}
dialog{border:none;border-radius:16px;padding:0;width:calc(100% - 32px);max-width:640px;background:var(--card);color:var(--text);box-shadow:0 24px 70px rgba(0,0,0,.35)}
dialog::backdrop{background:rgba(12,17,28,.6)}
.modal-head{padding:22px 24px 6px}.modal-head h3{margin:0;font-size:18px}.modal-head p{margin:4px 0 0;color:var(--muted);font-size:13.5px}
.modal-body{padding:12px 24px;max-height:55vh;overflow:auto;display:grid;gap:10px}
.modal-foot{padding:14px 24px 20px;display:flex;justify-content:flex-end;gap:10px;border-top:1px solid var(--line)}
.btn:disabled{opacity:.5;cursor:not-allowed}
[hidden]{display:none!important}
.bar{height:8px;border-radius:999px;background:var(--primary-soft);overflow:hidden;margin:4px 0 0}
.bar i{display:block;height:100%;width:38%;border-radius:999px;background:var(--primary);animation:slide 1.25s ease-in-out infinite}
.bar.done i,.bar.ok i{animation:none;width:100%;background:var(--ok)}.bar.err i{animation:none;width:100%;background:var(--err)}.bar.warn i{animation:none;width:100%;background:var(--warn)}
@keyframes slide{0%{margin-left:-38%}100%{margin-left:100%}}
@keyframes spin{to{transform:rotate(360deg)}}
ol.steps{list-style:none;margin:18px 0 6px;padding:0;display:grid;gap:10px}
ol.steps li{position:relative;padding-left:30px;color:var(--muted)}
ol.steps li::before{content:"";position:absolute;left:0;top:2px;width:18px;height:18px;border-radius:50%;border:2px solid var(--line);box-sizing:border-box}
ol.steps li.active{color:var(--text);font-weight:600}
ol.steps li.active::before{border-color:var(--primary);border-top-color:transparent;animation:spin .8s linear infinite}
ol.steps li.done{color:var(--text)}
ol.steps li.done::before{content:"✓";background:var(--ok);border-color:var(--ok);color:#fff;font-size:12px;line-height:14px;text-align:center}
ol.steps li.fail{color:var(--err);font-weight:600}
ol.steps li.fail::before{content:"!";background:var(--err);border-color:var(--err);color:#fff;font-size:12px;line-height:14px;text-align:center}
.empty{color:var(--muted);font-size:13.5px}
.login{min-height:100vh;display:grid;place-items:center;padding:20px}
.login .card{width:100%;max-width:400px;margin:0}
.login .card>h2{font-size:20px}
.foot{display:flex;justify-content:flex-end;margin-top:6px}
@media (max-width:720px){.grid{grid-template-columns:1fr}.card{padding:18px}.top-in,.savebar-in,main{padding-left:14px;padding-right:14px}}
"""


def _e(value):
    return html.escape(str(value), quote=True)


def _page(title, body):
    return (f"<!doctype html><html lang='ro'><head><meta charset='utf-8'><title>{_e(title)}</title>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='light dark'>"
            f"<style>{_CSS}</style></head><body>{body}</body></html>")


def login_page(error=None, configured=True):
    if not configured:
        corp = ("<div class='notice err'>Parola de admin nu e setată. Rulează <code>python -m worldbit_fiscal init</code>.</div>")
        return _page("Setări driver fiscal", f"<div class='login'><section class='card'><h2>Agent fiscal Worldbit</h2><p class='sub'>Setări</p>{corp}</section></div>")
    msg = f"<div class='notice err'>{_e(error)}</div>" if error else ""
    return _page("Setări driver fiscal", f"""<div class='login'><section class='card'>
<h2>Agent fiscal Worldbit</h2><p class='sub'>Autentifică-te ca administrator ca să vezi setările.</p>{msg}
<form method='post' action='/setari/login'>
<div class='field'><label for='parola'>Parola de admin</label><input type='password' name='password' id='parola' autofocus required></div>
<div class='actions'><button class='btn primary' type='submit'>Autentificare</button></div></form></section></div>""")


def _map_text(value):
    return "\n".join(f"{k}={v}" for k, v in value.items())


def _detect(target):
    """Butonul „Detectează porturi": deschide fereastra de alegere a echipamentului pentru câmpul `target`."""
    return (f"<div class='actions' style='margin-top:10px'><button type='button' class='btn ghost small detect' data-target='{_e(target)}'>Detectează porturi</button></div>")


def _vat_button():
    """Butonul „Interoghează cotele TVA" de lângă câmpul de înlocuire TVA: deschide fereastra cu grupele citite de pe aparat."""
    return "<button type='button' class='btn ghost small' id='vat-btn'>Interoghează cotele TVA din casă</button>"


def _field_html(field, value, prefix="d_", extra=""):
    name = prefix + field.key
    mini = field.kind == "map" and bool(extra)
    help_ = f"<small class='help'>{_e(field.help)}</small>" if field.help else ""
    if field.kind == "map" and extra:   # câmp mic (câteva caractere pe linie), cu butonul alături
        control = f"<div class='mini'><input type='text' name='{name}' id='f_{name}' value='{_e(', '.join(f'{k}={v}' for k, v in value.items()))}' placeholder='21=A'>{extra}</div>"
        extra = ""
    elif field.kind == "map":
        control = f"<textarea name='{name}' id='f_{name}'>{_e(_map_text(value))}</textarea>"
    else:
        control = f"<input type='text' name='{name}' value='{_e(value)}' id='f_{name}'>"
    return f"<div class='field{' wide' if (field.kind == 'map' and not mini) or extra else ''}'><label for='f_{name}'>{_e(field.label)}</label>{control}{help_}{extra}</div>"


_JS = """
(function(){
  document.documentElement.className+=' js';
  var q=function(s,r){return (r||document).querySelector(s)},qa=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};
  var DOAR_SETARI=['agent','casa','pos','sertar'];
  function tab(nume){
    if(!q('[data-panel="'+nume+'"]'))nume='agent';
    qa('.panel').forEach(function(p){p.classList.toggle('on',p.getAttribute('data-panel')===nume)});
    qa('.tab').forEach(function(t){t.setAttribute('aria-selected',t.getAttribute('data-tab')===nume?'true':'false')});
    var b=q('.savebar');if(b)b.classList.toggle('on',DOAR_SETARI.indexOf(nume)>=0);
    try{sessionStorage.setItem('tab',nume)}catch(e){}
    if(location.hash!=='#'+nume)history.replaceState(null,'','#'+nume);
  }
  qa('.tab').forEach(function(t){t.addEventListener('click',function(){tab(t.getAttribute('data-tab'))})});
  var inceput=location.hash.slice(1);
  if(!inceput){try{inceput=sessionStorage.getItem('tab')||''}catch(e){}}
  tab(inceput||'agent');
  var sel=q('#pos_model');
  function modele(){qa('.pos-model').forEach(function(d){d.style.display=d.getAttribute('data-model')===sel.value?'grid':'none'})}
  sel.addEventListener('change',modele);modele();
  function chip(id,text,cls){var c=q('#'+id);c.textContent=text;c.className='chip '+cls}
  fetch('/health').then(function(r){return r.json()}).then(function(h){
    chip('chip-casa','Casa de marcat: '+(h.fiscal.ready?'pregătită':'nepregătită'),h.fiscal.ready?'ok':'warn');
    chip('chip-pos','POS: '+(h.pos.configured?'configurat':'neconfigurat'),h.pos.configured?'ok':'');
  }).catch(function(){chip('chip-casa','Casa de marcat: necunoscută','warn')});
  var dlg=q('#port-modal'),lista=q('#modal-list'),ok=q('#modal-ok'),tinta=null;
  function mesaj(text){lista.textContent='';var e=document.createElement('div');e.className='empty';e.textContent=text;lista.appendChild(e)}
  function randeaza(ports){
    lista.textContent='';ok.disabled=true;
    if(!ports.length){mesaj('Niciun echipament serial găsit. Conectează echipamentul, apoi închide și deschide din nou fereastra.');return}
    ports.forEach(function(p){
      var l=document.createElement('label');l.className='port';
      var r=document.createElement('input');r.type='radio';r.name='port-choice';r.value=p.path;
      var c=document.createElement('div');
      var t=document.createElement('b');t.textContent=p.label;var em=document.createElement('em');em.textContent=p.stable?'cale fixă':'se poate schimba';if(!p.stable)em.className='no';t.appendChild(em);
      var d=document.createElement('span');d.textContent=p.path;var d2=document.createElement('span');d2.textContent=p.detail;
      c.appendChild(t);c.appendChild(d);c.appendChild(d2);
      if(p.note){var n=document.createElement('span');n.className='nota';n.textContent=p.note;c.appendChild(n)}
      if(p.udev_rule){var u=document.createElement('code');u.textContent=p.udev_rule;c.appendChild(u)}
      l.appendChild(r);l.appendChild(c);lista.appendChild(l);
      if(tinta&&tinta.value===p.path){r.checked=true;l.classList.add('sel');ok.disabled=false}
      r.addEventListener('change',function(){ok.disabled=false;qa('.port',lista).forEach(function(x){x.classList.toggle('sel',x.querySelector('input').checked)})});
    });
  }
  qa('.detect').forEach(function(btn){btn.addEventListener('click',function(){
    tinta=q("[name='"+btn.getAttribute('data-target')+"']");ok.disabled=true;mesaj('Se caută echipamente…');dlg.showModal();
    fetch('/setari/ports',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){randeaza(j.ports)})
      .catch(function(){mesaj('Nu s-a putut citi lista de porturi.')});
  })});
  ok.addEventListener('click',function(){var r=q("input[name='port-choice']:checked",lista);if(r&&tinta){tinta.value=r.value}dlg.close()});
  q('#modal-cancel').addEventListener('click',function(){dlg.close()});
  dlg.addEventListener('click',function(e){if(e.target===dlg)dlg.close()});
  var vb=q('#vat-btn');
  if(vb){
    var vd=q('#vat-modal'),vl=q('#vat-list'),vok=q('#vat-ok');
    function vmesaj(text){vl.textContent='';var e=document.createElement('div');e.className='empty';e.textContent=text;vl.appendChild(e)}
    vb.addEventListener('click',function(){
      vok.disabled=true;vmesaj('Se interoghează casa de marcat…');vd.showModal();
      fetch('/setari/vat-rates',{credentials:'same-origin'}).then(function(r){return r.json().then(function(j){return {ok:r.ok,j:j}})}).then(function(x){
        if(!x.ok){vmesaj((x.j.error&&x.j.error.message)||'Nu s-au putut citi cotele de TVA.');return}
        vl.textContent='';
        x.j.groups.forEach(function(g){
          var l=document.createElement('label');l.className='port';
          var r=document.createElement('input');r.type='radio';r.name='vat-choice';r.value=g.percent+'='+g.letter;
          var c=document.createElement('div');var t=document.createElement('b');t.textContent='Grupa '+g.letter+' · '+g.percent+'%';c.appendChild(t);
          l.appendChild(r);l.appendChild(c);vl.appendChild(l);
          r.addEventListener('change',function(){vok.disabled=false;qa('.port',vl).forEach(function(y){y.classList.toggle('sel',y.querySelector('input').checked)})});
        });
      }).catch(function(){vmesaj('Nu s-au putut citi cotele de TVA.')});
    });
    vok.addEventListener('click',function(){
      var r=q("input[name='vat-choice']:checked",vl);
      if(r){
        var camp=q('#f_d_vat_map'),nou=r.value,cota=Number(nou.split('=')[0]),linii=camp.value.split(/[\\n,]/).filter(function(l){return l.trim()!==''}),gasit=false;
        linii=linii.map(function(l){var k=Number(l.split('=')[0]);if(k===cota){gasit=true;return nou}return l});
        if(!gasit)linii.push(nou);
        camp.value=linii.join(', ');
      }
      vd.close();
    });
    q('#vat-cancel').addEventListener('click',function(){vd.close()});
    vd.addEventListener('click',function(e){if(e.target===vd)vd.close()});
  }
  var uf=q('#update-form');
  if(uf){
    var um=q('#update-modal'),ub=q('#u-bar'),umsg=q('#u-msg'),utitle=q('#u-title'),uclose=q('#u-close'),ufoot=q('#u-foot'),udet=q('#u-det'),udetail=q('#u-detail'),terminat=true;
    var pasi=[q('#u1'),q('#u2'),q('#u3')];
    function seteaza(activ,esec){pasi.forEach(function(li,i){li.className=esec&&i===activ?'fail':(i<activ?'done':(i===activ?'active':''))})}
    function final(stare,titlu,text,reincarca,pasEsec,detaliu){
      terminat=true;ub.className='bar '+stare;utitle.textContent=titlu;umsg.textContent=text;
      if(detaliu){udetail.textContent=detaliu;udet.hidden=false}
      if(stare==='done'){seteaza(3,false)}else if(stare==='ok'){pasi[0].className='done';pasi[1].className='';pasi[2].className=''}else if(pasEsec!==undefined){seteaza(pasEsec,true)}
      if(reincarca){setTimeout(function(){location.reload()},1600)}
      else{ufoot.hidden=false;uclose.onclick=function(){um.close();if(stare!=='ok'&&stare!=='warn'){location.reload()}}}
    }
    um.addEventListener('cancel',function(e){if(!terminat)e.preventDefault()});
    function asteapta(tinta,vechi){
      var start=Date.now(),cazut=false,limita=parseInt(um.getAttribute('data-timeout'),10);
      (function tick(){
        function urmator(){if(Date.now()-start>limita){final('warn','Durează mai mult decât de obicei','Agentul nu a revenit încă. Reîncarcă pagina peste câteva minute: rezultatul actualizării apare în secțiunea „Versiune agent”.',false,1)}else{setTimeout(tick,1500)}}
        fetch('/health',{cache:'no-store',signal:AbortSignal.timeout(3000)}).then(function(r){return r.json()}).then(function(h){
          if(h.version===tinta){final('done','Actualizat la versiunea '+tinta,'Agentul a repornit cu versiunea nouă. Pagina se reîncarcă singură…',true)}
          else if(cazut&&h.version===vechi){
            fetch('/setari/update-status',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(st){
              var motiv=(st.result&&st.result.reason)||'';
              final('err','Versiunea nouă nu a pornit','Agentul a revenit singur la versiunea '+vechi+'. Configurația și joburile au rămas neschimbate.',false,1,
                motiv||('Versiunea nouă nu a scris nimic la pornire. Jurnalul actualizării:\\n'+(st.log||'(gol)')));
            }).catch(function(){final('err','Versiunea nouă nu a pornit','Agentul a revenit singur la versiunea '+vechi+'.',false,1)});
          }
          else{urmator()}
        }).catch(function(){cazut=true;urmator()});
      })();
    }
    uf.addEventListener('submit',function(e){
      e.preventDefault();var vechi=uf.getAttribute('data-version');
      terminat=false;ufoot.hidden=true;udet.hidden=true;ub.className='bar';utitle.textContent='Actualizare agent';umsg.textContent='Se caută și se descarcă ultima versiune…';seteaza(0,false);um.showModal();
      fetch('/setari/update',{method:'POST',credentials:'same-origin',headers:{'Accept':'application/json'}}).then(function(r){return r.json()}).then(function(j){
        if(j.state==='started'){seteaza(1,false);utitle.textContent='Se instalează versiunea '+j.target;umsg.textContent='Agentul se oprește și repornește singur (aproximativ 30 de secunde). Pagina se reîncarcă singură.';asteapta(j.target,vechi)}
        else if(j.state==='current'||j.state==='irrelevant'){final('ok','Nimic de actualizat',j.message,false)}
        else{final('warn',j.state==='busy'?'Nu acum':(j.state==='unavailable'?'Nu se poate instala de aici':'Actualizarea nu a fost instalată'),j.message,false,0,j.detail)}
      }).catch(function(){final('err','Agentul nu răspunde','Nu s-a putut contacta agentul. Încearcă din nou peste puțin timp.',false,0)});
    });
  }
})();
"""


def settings_page(cfg, driver_cls, driver_values, message=None, error=None, output=None, last_update=None):
    avize = ""
    if message:
        avize += f"<div class='notice ok'>{_e(message)}</div>"
    if error:
        avize += f"<div class='notice err'>{_e(error)}</div>"
    if output:
        avize += f"<pre class='out'>{_e(output)}</pre>"

    def _option(m, label):
        return f"<option value='{_e(m)}'{' selected' if m == cfg['pos']['model'] else ''}>{_e(label)}</option>"
    banks = {}
    for m, cls in POS_DRIVERS.items():
        banks.setdefault(cls.bank or "Altele", []).append(_option(m, cls.label or m))
    pos_options = _option("none", "none (fără POS)") + "".join(
        f"<optgroup label='{_e(b)}'>{''.join(opts)}</optgroup>" for b, opts in sorted(banks.items()))
    pos_fields = ""
    for m, cls in POS_DRIVERS.items():
        valori = {**{f.key: f.default for f in cls.fields}, **((cfg["pos"].get("settings") or {}) if m == cfg["pos"]["model"] else {})}
        campuri = ""
        for f in cls.fields:
            campuri += _field_html(f, valori[f.key], f"p_{m}_", _detect(f"p_{m}_{f.key}") if f.key == "port" else "")
            if f.key == "port" and len(cls.fields) > 1:
                campuri += ("<div class='field wide'><div class='notice' style='margin:6px 0 0;background:var(--primary-soft)'>"
                            "Parametrii de mai jos sunt cei din documentația protocolului și sunt completați corect. "
                            "Nu-i schimba decât dacă banca sau testul pe terminal cere asta.</div></div>")
        pos_fields += f"<div class='pos-model grid' data-model='{_e(m)}' style='margin-top:16px'>{campuri}</div>"
    driver_fields = "".join(_field_html(f, driver_values[f.key], extra=_vat_button() if f.key == "vat_map" and hasattr(driver_cls, "vat_groups") else "")
                            for f in driver_cls.fields)
    discover = ("<button type='submit' class='btn' formaction='/setari/devices'>Detectează dispozitive</button>"
                if hasattr(driver_cls, "discover") else "")
    sertar = drawer_mod.settings(cfg)
    corp = f"""
<header class='top'><div class='top-in'>
<div class='brand'>Agent fiscal Worldbit<small>Setări · versiunea {_e(__version__)}</small></div>
<span class='chip' id='chip-casa'>Casa de marcat: se verifică…</span><span class='chip' id='chip-pos'>POS: …</span>
<form method='post' action='/setari/logout' style='margin:0'><button class='btn small ghost' type='submit'>Ieșire</button></form>
</div>
<nav class='tabs'><div class='tabs-in' role='tablist'><button type='button' class='tab' role='tab' data-tab='agent'>Agent</button><button type='button' class='tab' role='tab' data-tab='casa'>Casa de marcat</button><button type='button' class='tab' role='tab' data-tab='pos'>POS</button><button type='button' class='tab' role='tab' data-tab='sertar'>Sertar de bani</button><button type='button' class='tab' role='tab' data-tab='actualizare'>Actualizare</button><button type='button' class='tab' role='tab' data-tab='parola'>Schimbă parola</button></div></nav></header>
<main>{avize}
<form method='post' action='/setari/save'>
<div class='panel' data-panel='agent'>
<section class='card'><h2>Agent</h2><p class='sub'>Cum vorbește aplicația web cu agentul de pe acest calculator.</p>
<div class='grid'>
<div class='field'><label for='f_port'>Port agent</label><input type='text' name='port' value='{_e(cfg['port'])}' id='f_port'>
<small class='help'>Se aplică după repornirea serviciului.</small></div>
<div class='field wide'><label for='f_origins'>Origini permise (aplicațiile web)</label>
<textarea name='allowed_origins' id='f_origins'>{_e(chr(10).join(cfg['allowed_origins']))}</textarea>
<small class='help'>Câte una pe linie, ex. https://dressbit.exemplu.ro (fără / la final).</small></div>
</div></section>
</div>
<div class='panel' data-panel='casa'>
<section class='card'><h2>Casa de marcat · {_e(driver_cls.name)}</h2>
<p class='sub'>Driverul se schimbă doar la reinstalare (<code>python -m worldbit_fiscal init</code>).</p>
<div class='grid'>{driver_fields}</div>
<div class='actions'><button type='submit' class='btn' formaction='/setari/test'>Testează conexiunea</button>{discover}</div></section>
</div>
<div class='panel' data-panel='pos'>
<section class='card'><h2>POS bancar</h2><p class='sub'>Terminalul pentru plata cu cardul, legat la acest calculator.</p>
<div class='grid'><div class='field wide'><label for='pos_model'>Model POS</label><select name='pos_model' id='pos_model'>{pos_options}</select>
<small class='help'>„none” = agentul nu apelează POS-ul; plata cu cardul se face în afara agentului.</small></div></div>
{pos_fields}
<div class='actions'><button type='submit' class='btn' formaction='/setari/pos-test'>Verifică portul POS</button></div>
<small class='help'>Deschide portul salvat și îl închide, fără să trimită nimic către POS. Salvează întâi setările.</small></section>
</div>
<div class='panel' data-panel='sertar'>
<section class='card'><h2>Sertar de bani</h2><p class='sub'>Se deschide singur la încasările cu numerar. Gol la port = fără sertar.</p>
<input type='hidden' name='drawer_present' value='1'>
<div class='grid'>
<div class='field'><label for='f_drawer_port'>Port sertar</label><input type='text' name='drawer_port' value='{_e(sertar['port'])}' id='f_drawer_port'>
<small class='help'>Portul modulului USB al sertarului (Windows: COM3; Linux: /dev/ttyUSB0).</small>{_detect('drawer_port')}</div>
<div class='field'><label for='f_drawer_cmd'>Comandă sertar</label><input type='text' name='drawer_command' value='{_e(sertar['command'])}' id='f_drawer_cmd'>
<small class='help'>Textul scris pe port; după el se adaugă automat sfârșit de linie.</small></div>
<div class='field wide'><label class='check'><input type='checkbox' name='drawer_auto'{' checked' if sertar['auto_open'] else ''}><span>Deschide automat la încasările cu numerar (inclusiv plata mixtă)</span></label></div>
</div>
<div class='actions'><button type='submit' class='btn' formaction='/setari/drawer-test'>Deschide sertarul (test)</button></div></section>
</div>
<div class='savebar'><div class='savebar-in'><span class='hint'>Modificările se aplică la salvare.</span><button type='submit' class='btn primary'>Salvează setările</button></div></div>
</form>
<div class='panel' data-panel='actualizare'>{_update_box(last_update)}</div>
<div class='panel' data-panel='parola'>
<section class='card'><h2>Schimbă parola</h2><p class='sub'>Parola paginii de setări (minim 8 caractere).</p>
<form method='post' action='/setari/password'><div class='grid'><div class='field'><label for='f_newpass'>Parola nouă</label>
<input type='password' name='new_password' minlength='8' id='f_newpass'></div></div>
<div class='actions'><button type='submit' class='btn'>Schimbă parola</button></div></form></section>
</div>
</main>
<dialog id='port-modal'>
<div class='modal-head'><h3>Alege echipamentul</h3><p>Echipamentele seriale (USB) găsite pe acest calculator. Alege-l pe cel dorit și apasă „Selectează”.</p></div>
<div class='modal-body' id='modal-list'></div>
<div class='modal-foot'><button type='button' class='btn' id='modal-cancel'>Anulează</button><button type='button' class='btn primary' id='modal-ok' disabled>Selectează</button></div>
</dialog>
<dialog id='vat-modal'>
<div class='modal-head'><h3>Cote TVA din casa de marcat</h3><p>Grupele de TVA citite de pe aparat (prin driver). Alege grupa pe care o folosești și apasă „Selectează”: se completează în câmpul „Înlocuire TVA” (cota=litera).</p></div>
<div class='modal-body' id='vat-list'></div>
<div class='modal-foot'><button type='button' class='btn' id='vat-cancel'>Anulează</button><button type='button' class='btn primary' id='vat-ok' disabled>Selectează</button></div>
</dialog>
{_UPDATE_MODAL}
<script>{_JS}</script>"""
    return _page("Setări driver fiscal", corp)


def _update_box(last_update):
    log = f"<pre class='out' style='margin:14px 0 0'>{_e(last_update)}</pre>" if last_update else ""
    return (f"<section class='card'><h2>Versiune agent: {_e(__version__)}</h2>"
            "<p class='sub'>Descarcă ultima versiune de pe GitHub și o instalează doar dacă are modificări pentru driverul fiscal și POS-ul de pe acest "
            "calculator. Altfel spune că nu e nimic de actualizat. Nu se face niciodată singur.</p>"
            f"<form method='post' action='/setari/update' id='update-form' data-version='{_e(__version__)}'>"
            f"<div class='actions' style='margin-top:0'><button type='submit' class='btn'>Actualizează</button></div></form>{log}</section>")


_UPDATE_MODAL = """<dialog id='update-modal' data-timeout='240000'>
<div class='modal-head'><h3 id='u-title'>Actualizare agent</h3><p id='u-msg'></p></div>
<div class='modal-body'><div class='bar' id='u-bar'><i></i></div>
<ol class='steps'><li id='u1'>Verificare și descărcare</li><li id='u2'>Instalare și repornire agent</li><li id='u3'>Gata</li></ol>
<div id='u-det' hidden><b style='font-size:13px'>Motivul</b><pre class='out' id='u-detail' style='margin:6px 0 0;max-height:220px'></pre></div></div>
<div class='modal-foot' id='u-foot' hidden><button type='button' class='btn primary' id='u-close'>Închide</button></div>
</dialog>"""


def parse_settings_form(form, cfg, driver_cls):
    """form: dict nume -> valoare (text). Întoarce o configurație nouă sau ridică ValueError cu mesajul pentru admin."""
    new = copy.deepcopy(cfg)

    try:
        port = int(form.get("port", "").strip())
    except ValueError:
        raise ValueError("Port invalid")
    if not 1024 <= port <= 65535:
        raise ValueError("Portul trebuie să fie între 1024 și 65535")
    new["port"] = port

    origins = []
    for line in form.get("allowed_origins", "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not _ORIGIN.match(line):
            raise ValueError(f"Origine invalidă: {line} (formă: https://gazda[:port], fără cale)")
        origins.append(line)
    new["allowed_origins"] = origins

    if form.get("drawer_present"):   # câmpurile sertarului au fost trimise (un formular fără ele nu atinge setarea)
        port, command = drawer_mod.valideaza(form.get("drawer_port", ""), form.get("drawer_command", ""))
        new["drawer"] = {"port": port, "command": command, "auto_open": form.get("drawer_auto") is not None}

    model = form.get("pos_model", "none")
    if model != "none" and model not in POS_DRIVERS:
        raise ValueError("Model POS necunoscut")
    if model != new["pos"]["model"]:
        new["pos"]["settings"] = {}          # setările unui alt model nu se păstrează
    new["pos"]["model"] = model
    if model != "none":
        cls = POS_DRIVERS[model]
        pos_values = dict(new["pos"].get("settings") or {})
        for f in cls.fields:
            raw = form.get(f"p_{model}_{f.key}")
            if raw is None:
                continue                      # câmp netrimis: rămâne valoarea de până acum / cea implicită
            pos_values[f.key] = _parse_field(f, raw)
        full = {**{f.key: f.default for f in cls.fields}, **pos_values}
        cls.validate_settings(full)
        new["pos"]["settings"] = pos_values

    values = {f.key: _parse_field(f, form.get("d_" + f.key, "")) for f in driver_cls.fields}
    new.setdefault("drivers", {})[driver_cls.name] = values
    return new


def _parse_field(f, raw):
    """O valoare de câmp din formular, după tipul câmpului. Ridică ValueError cu eticheta câmpului."""
    try:
        if f.kind == "int":
            return int(raw.strip())
        if f.kind == "float":
            return float(raw.strip().replace(",", "."))
        if f.kind == "map":
            return _parse_map(raw)
        return raw.strip()
    except ValueError as e:
        raise ValueError(f"{f.label}: {e}" if str(e) else f"{f.label}: valoare invalidă")


def _parse_map(raw):
    result = {}
    for line in raw.replace(",", "\n").splitlines():   # perechi pe linii sau separate prin virgulă
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"linie invalidă „{line}” (formă: 21=1)")
        key, value = (p.strip() for p in line.split("=", 1))
        try:
            key = percent_key(Decimal(key))
        except InvalidOperation:
            raise ValueError(f"procent invalid „{key}”")
        if not value:
            raise ValueError(f"valoare lipsă pentru {key}")
        result[key] = value
    return result
