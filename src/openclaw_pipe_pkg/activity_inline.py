# Activity has its own DOM view and stored output kind. It is never answer text.
_ACTIVITY_ITEM = "openclaw:activity"
_ACTIVITY_INLINE_JS = r"""
(function(){
 const KEY='__openclawActivity';
 if(typeof requestAnimationFrame!=='function'||typeof MutationObserver!=='function'||!document.body)return;
 if(window[KEY]) return;
 const G={items:new Map(),queued:false,lastChat:null,restored:false,contexts:{}};window[KEY]=G;
 const current=()=>{const m=location.pathname.match(/^\/c\/([^/]+)/);return m?decodeURIComponent(m[1]):null;};
 const add=(p,t,text)=>{const e=document.createElement(t);e.textContent=text||'';p.append(e);return e;};
 function mount(){
  G.queued=false;const chat=current();
  if(chat!==G.lastChat){G.lastChat=chat;G.restored=false;}
  for(const D of G.items.values()){
   if(D.chatId!==chat) continue;
   const message=document.getElementById('message-'+D.messageId);
   const parent=message&&message.querySelector('.chat-assistant');if(!parent) continue;
   const id='openclaw-activity-'+D.activityId;
   let root=document.getElementById(id);
   if(root&&root.dataset.serial===D.serial) continue;
   if(!root){root=document.createElement('details');root.id=id;root.dataset.openclawActivity='1';root.open=true;parent.prepend(root);}
   root.dataset.serial=D.serial;
   Object.assign(root.style,{fontSize:'13px',lineHeight:'1.5',margin:'8px 0',padding:'10px 12px',border:'1px solid #9ca3af',borderRadius:'10px',overflowWrap:'anywhere'});
   root.replaceChildren();const V=D.activity;
   add(root,'summary',V.label).style.cursor='pointer';
   for(const [label,card] of [['Run plan',V.plan],['Task progress',V.card]]){
    if(!card)continue;
    add(root,'strong',label).style.display='block';
    for(const step of card.steps||[])add(root,'div',(step.status==='completed'?'✓ ':step.status==='in_progress'?'● ':'○ ')+step.step);
    if(card.markdown){const n=add(root,'div',card.markdown);n.style.whiteSpace='pre-wrap';}
    if(card.updatedAt)add(root,'small','Last update: '+new Date(card.updatedAt).toLocaleString());
   }
   if(V.cardError)add(root,'div','Progress refresh failed; showing the last confirmed snapshot.');
   if(V.summary)add(root,'div',V.summary);
   for(const call of V.calls||[]){
    add(root,'div',call.status[0].toUpperCase()+call.status.slice(1)+' · '+call.title).style.fontWeight='600';
    if(call.output&&['running','failed','interrupted'].includes(call.status)){
     const pre=add(root,'pre',call.output);Object.assign(pre.style,{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'220px',overflow:'auto',fontSize:'12px'});
     if(call.clipped)add(root,'small','Preview limited to the latest 8,000 characters. Open Status for retained output.');
    }
   }
   if(V.status!=='running')add(root,'small','Response ended; unfinished task steps remain unfinished.');
  }
  const C=G.contexts[chat];
  const users=document.querySelectorAll('.chat-user');
  const target=users.length&&users[users.length-1].closest('[id^="message-"]');
  if(chat&&!G.restored&&C&&C.model&&target){
   G.restored=true;
   fetch('/api/chat/actions/openclaw_status_action',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+localStorage.getItem('token')},
    body:JSON.stringify({mode:'activity-restore',chat_id:chat,id:target.id.slice(8),model:C.model,session_id:C.sessionId||'openclaw-composer'})}).catch(()=>{});
  }
 }
 const schedule=()=>{if(!G.queued){G.queued=true;requestAnimationFrame(mount);}};
 G.configure=C=>{if(C.chatId&&C.model)G.contexts[C.chatId]=C;schedule();};
 G.receive=D=>{G.items.set(D.activityId,D);while(G.items.size>30)G.items.delete(G.items.keys().next().value);schedule();};
 G.observer=new MutationObserver(schedule);G.observer.observe(document.body,{childList:true,subtree:true});schedule();
})();
"""


def _render_activity_inline(chat_id, message_id, item):
    data = {"chatId": chat_id, "messageId": message_id, "activityId": item["id"],
            "activity": item["activity"]}
    data["serial"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:20]
    return _ACTIVITY_INLINE_JS + "\nwindow.__openclawActivity.receive(" + json.dumps(data).replace("<", "\\u003c") + ");"
