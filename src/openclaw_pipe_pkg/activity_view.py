# Browser code is fixed. Gateway values are serialized data and rendered as text.
_ACTIVITY_VIEW_JS = r"""
(function() {
  const D = __DATA__;
  const S = window.__openclawStatus;
  const root = document.getElementById('openclaw-status-modal-root');
  if (!S || !root || S.identity.dialogId !== D.dialogId) return;
  let section = document.getElementById('openclaw-status-section-activity');
  if (!section) {
    section = document.createElement('section');
    section.id = 'openclaw-status-section-activity';
    document.getElementById('openclaw-status-sections').prepend(section);
  }
  Object.assign(section.style,{fontSize:'13px',lineHeight:'1.5',overflowWrap:'anywhere',marginBottom:'16px',paddingBottom:'12px'});
  const opened = new Set([...section.querySelectorAll('details[open]')].map(e => e.dataset.call));
  const focused = document.activeElement && document.activeElement.dataset.activityFocus;
  section.replaceChildren();
  const add = (parent,tag,text) => {
    const el = document.createElement(tag); el.textContent = text || ''; parent.append(el); return el;
  };
  const heading=add(section,'h3','Activity');
  Object.assign(heading.style,{fontSize:'11px',fontWeight:'600',textTransform:'uppercase',letterSpacing:'.06em',marginBottom:'8px',opacity:'.7'});
  heading.title='Bridge '+D.release;
  if (D.error) add(section,'p',D.error).setAttribute('role','status');
  for (const [label,card] of [["Run plan",D.plan],["Task progress",D.card]]) {
  if (card) {
    const steps = card.steps || [];
    const done = steps.filter(s => s.status === 'completed').length;
    const title=add(section,'h4',label + (steps.length ? ' · '+done+'/'+steps.length+' complete' : ''));
    Object.assign(title.style,{fontWeight:'600',marginTop:'8px'});
    for (const step of steps) {
      add(section,'p',(step.status === 'completed' ? '✓ ' : step.status === 'in_progress' ? '● ' : '○ ') + step.step);
    }
    if (card.markdown) { const note=add(section,'p',card.markdown); note.style.whiteSpace='pre-wrap'; }
    if (card.updatedAt) add(section,'small','Last update: '+new Date(card.updatedAt).toLocaleString());
    if (D.status !== 'running' && done < steps.length) add(section,'p','Work remains unfinished.');
  }
  }
  if(!D.card && !D.plan) add(section,'p','No saved task progress.');
  if (D.cardError) add(section,'p','Progress could not be refreshed.');
  if (D.historyError) add(section,'p','History is unavailable; showing received activity.');
  if (D.historyLimited) add(section,'small','Showing recent activity; older calls are outside this view.');
  S.activityOutput = S.activityOutput || {};
  for (const call of D.calls || []) {
    const detail=add(section,'details','');detail.dataset.call=call.id;detail.open=opened.has(call.id);detail.style.marginTop='10px';
    const summary=add(detail,'summary',call.status[0].toUpperCase()+call.status.slice(1)+' · '+call.title+(call.durationMs!=null ? ' · '+(call.durationMs/1000).toFixed(1)+'s' : call.startedAt>0 ? ' · '+Math.max(0,Math.floor((Date.now()-call.startedAt)/1000))+'s' : ''));summary.style.cursor='pointer';
    const pre=add(detail,'pre',S.activityOutput[call.id] || call.output || 'No output received.');
    Object.assign(pre.style,{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'320px',overflow:'auto',fontSize:'12px'});
    if(call.clipped && !S.activityOutput[call.id]) add(detail,'small','Preview clipped to the latest 8,000 characters.').dataset.activityClip='1';
    if(call.messageId) {
      const load=add(detail,'button','Load retained output'); load.dataset.activityFocus='load-'+call.id;
      load.onclick=async function(){
        load.disabled=true;
        try {
          await S.askAction({mode:'activity-output',callId:call.id,messageId:call.messageId});
        } catch (_) { pre.textContent='Could not load retained output. Try again.'; }
        finally {load.disabled=false;}
      };
    }
    const copy=add(detail,'button','Copy'); copy.style.marginLeft='12px';
    copy.dataset.activityFocus='copy-'+call.id;
    copy.onclick=()=>navigator.clipboard.writeText(pre.textContent).catch(()=>{copy.textContent='Copy failed';});
    const save=add(detail,'button','Download'); save.style.marginLeft='12px';
    save.dataset.activityFocus='save-'+call.id;
    for(const b of detail.querySelectorAll('button')) Object.assign(b.style,{border:'1px solid #9ca3af',borderRadius:'6px',padding:'3px 7px',fontSize:'12px',marginTop:'6px'});
    save.onclick=function(){
      const url=URL.createObjectURL(new Blob([pre.textContent],{type:'text/plain;charset=utf-8'}));
      const a=document.createElement('a');a.href=url;a.download='command-output.txt';a.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
  }
  if(focused) [...section.querySelectorAll('[data-activity-focus]')].find(e=>e.dataset.activityFocus===focused)?.focus();
  if (!S.activityTimer) {
    const identity=S.identity.dialogId;
    const again=()=>{
      if(!document.getElementById('openclaw-status-modal-root') || window.__openclawStatus!==S || S.identity.dialogId!==identity) return;
      if(document.hidden) {S.activityTimer=setTimeout(again,2000);return;}
      S.askAction({mode:'activity'}).catch(()=>{}).finally(()=>{
        if(window.__openclawStatus===S) S.activityTimer=setTimeout(again,2000);
      });
    };
    S.activityTimer=setTimeout(again,2000);
  }
})();
"""


def _render_activity_view(data):
    return _ACTIVITY_VIEW_JS.replace("__DATA__", _json_for_js(data))


def _render_activity_output(data):
    return r"""(function(){
      const D=__DATA__,S=window.__openclawStatus;
      if(!S || S.identity.dialogId!==D.dialogId || !document.getElementById('openclaw-status-modal-root')) return;
      S.activityOutput=S.activityOutput||{};
      S.activityOutput[D.callId]=D.available ? D.text+(D.truncated ? '\n\n[Gateway retained output is truncated.]' : '') : ('Output unavailable: '+D.reason);
      const e=[...document.querySelectorAll('#openclaw-status-section-activity details')].find(e=>e.dataset.call===D.callId);
      if(e) {e.querySelector('pre').textContent=S.activityOutput[D.callId];e.querySelector('[data-activity-clip]')?.remove();}
    })();""".replace("__DATA__", _json_for_js(data))
