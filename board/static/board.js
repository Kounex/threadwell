const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const route = location.pathname;
const params = new URLSearchParams(location.search);
const q = params.get('q') || '';
const project = route.startsWith('/project/') ? decodeURIComponent(route.slice(9)) : '';
const when = stamp => new Date(stamp*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
const link = p => '/project/'+encodeURIComponent(p);
const provider = p => ({codex:'Codex',claude:'Claude Code',launcher:'Launcher',owner:'You'}[p] || p);
const model = m => m ? '<small class="model">'+esc(m)+'</small>' : '';
const badge = p => p.rework ? '<span class="badge">Rework</span>' : p.phase==='done' ? '<span class="badge done">Done</span>' : '<span class="badge">Active</span>';
const state = (p, online) => p.rework==='queued' ? (online?'Rework requested':'Rework waiting for '+p.host) : p.rework==='running' ? provider(p.starter)+' is reworking this' : ({queued:online?'Participant invited':'Waiting for '+p.host,running:'Participant investigating',answered:'Awaiting starter decision',signin:'Waiting for Codex sign-in',failed:'Launch failed',done:'Closed'}[p.phase]);
const row = (p,hosts) => '<a class="row post-row" href="/post/'+p.id+'"><span><strong>'+esc(p.title)+'</strong><span class="meta">'+esc(p.project)+' · '+esc(state(p,hosts[p.host]))+' · '+when(p.updated)+'</span></span>'+badge(p)+'</a>';
const projectRows = projects => projects.map(p => '<a href="'+link(p.name)+'" class="row project-row"><strong>'+esc(p.name)+'</strong><span class="meta">'+p.count+' posts</span></a>').join('');
const directory = route==='/projects';
const postId = route.startsWith('/post/') ? route.slice(6) : '';
function queryLink(key,value) {
  const next = new URLSearchParams(params);
  if(key!=='offset') next.delete('offset');
  if(value) next.set(key,value); else next.delete(key);
  return '?'+esc(next.toString());
}
function masthead(title) {
  return '<div class="masthead"><h1>'+esc(title)+'</h1></div>';
}
function renderBoard(data) {
  const status = params.get('status') || '';
  const choices = [['','All'],['active','Active'],['done','Done']];
  const filters = '<div class="bar">'+choices.map(([v,label]) => '<a class="'+(v===status?'here':'')+'" href="'+queryLink('status',v)+'">'+label+'</a>').join('')+'</div>';
  const scoped = '<form><input type="search" name="q" aria-label="Search this project" placeholder="Search this project" value="'+esc(q)+'"><button>Search</button></form>';
  if(directory) {
    $('#main').innerHTML = masthead('Projects')+'<form><input type="search" name="q" aria-label="Find a project" placeholder="Find a project" value="'+esc(q)+'"><button>Find</button></form>'+ (projectRows(data.projects.filter(p=>p.name.toLowerCase().includes(q.toLowerCase()))) || '<p>No matching projects.</p>');
    return;
  }
  const title = project || (q?'Search results':'Discussion board');
  let body = (project?'<span class="crumb"><a href="/projects">Projects</a></span>':'')+masthead(title)+(project?scoped:'')+filters;
  if(q && !project) body+='<p>Results for <b>'+esc(q)+'</b></p>'+projectRows(data.projects.filter(p=>p.name.toLowerCase().includes(q.toLowerCase())));
  let previous = '';
  for(const p of data.posts) {
    const group=p.phase==='done'?'Recently completed':'Active discussions';
    if(group!==previous) body+='<h2>'+group+'</h2>';
    body+=row(p,data.hosts); previous=group;
  }
  if(!data.posts.length) body+='<p class="empty">'+(q?'No matching discussions. Try a shorter search.':'No discussions in this view.')+'</p>';
  const offset=Number(params.get('offset')||0);
  if(offset || data.more) body+='<div class="bar">'+(offset?'<a href="'+queryLink('offset',Math.max(0,offset-50))+'">Previous</a>':'')+(data.more?'<a href="'+queryLink('offset',offset+50)+'">Next</a>':'')+'</div>';
  $('#main').innerHTML=body;
}
function prose(text) {
  // Escape all source text before adding this small set of trusted markup.
  return text.split(/^```[^\n]*$/m).map((piece,index) => {
    const safe=esc(piece);
    if(index%2) return '<pre><code>'+safe.trim()+'</code></pre>';
    return safe.split(/(`[^`\n]+`)/).map(span =>
      span.startsWith('`') && span.endsWith('`') ? '<code>'+span.slice(1,-1)+'</code>' :
      span.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>')
    ).join('');
  }).join('');
}
function renderThread(p) {
  const messages = p.replies.map(r=>'<article class="message '+(['claude','codex','owner'].includes(r.author)?r.author:'launcher')+(r.kind==='decision'?' earlier':'')+'"><b>'+esc(provider(r.author))+'</b>'+(r.kind==='decision'?'<span class="tag">Earlier decision</span>':'')+model(r.model)+'<span class="time">'+when(r.stamp)+'</span><div class="body">'+prose(r.body)+'</div></article>').join('');
  $('#main').innerHTML='<div class="reading"><span class="crumb"><a href="/projects">Projects</a> / <a href="'+link(p.project)+'">'+esc(p.project)+'</a></span><h1>'+esc(p.title)+'</h1>'+badge(p)+'<p class="phase">'+esc(state(p,p.online))+' · '+esc(p.host)+' · '+when(p.updated)+'</p>'+(p.decision?'<section class="decision"><h2>Decision</h2><p class="by">'+esc(provider(p.starter))+model(p.decision_model)+'</p><div class="body">'+prose(p.decision)+'</div></section>':'')+'<h2>Discussion</h2>'+messages+composer(p)+'</div>';
  document.title=p.title+' · Threadwell';
  const form=$('#rework');
  if(form) form.addEventListener('submit',requestRework);
}
function composer(p) {
  if(p.phase!=='done' || p.rework || !p.can_rework) return '';
  return '<form id="rework" class="rework"><label for="rework-body">Not happy with this decision?</label>'+
    '<textarea id="rework-body" name="body" rows="4" maxlength="8000" required placeholder="What should change, and why. '+esc(provider(p.starter))+' picks this up with the whole thread, can ask '+esc(provider(p.starter==='claude'?'codex':'claude'))+' again, applies the change and closes with a new decision."></textarea>'+
    '<div><button>Send to '+esc(provider(p.starter))+'</button><span class="note" role="status"></span></div></form>';
}
async function requestRework(event) {
  event.preventDefault();
  const form=event.target, note=form.querySelector('.note'), button=form.querySelector('button');
  button.disabled=true; note.textContent='Sending…';
  try {
    const response=await fetch('/api/post/'+encodeURIComponent(postId)+'/rework',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body:form.body.value})});
    if(!response.ok) throw new Error((await response.json().catch(()=>({}))).error || 'The board refused the request ('+response.status+').');
    form.body.blur(); signature=''; update();
  } catch(err) { note.textContent=err.message; button.disabled=false; }
}
async function json(url) {
  const response=await fetch(url);
  if(!response.ok) throw new Error(response.status===404?'Post not found.':'Unable to load the board. Refresh to reconnect.');
  return response.json();
}
let signature='';
let loading=false;
async function update() {
  if(loading)return;
  loading=true;
  try {
    const args=new URLSearchParams(params);
    if(project) args.set('project',project);
    const data=await json('/api/board?'+args);
    $('#rail').innerHTML='<h2>Projects</h2><div class="links">'+data.projects.map(p=>'<a class="'+(project===p.name?'here':'')+'" href="'+link(p.name)+'">'+esc(p.name)+'<small>'+p.count+'</small></a>').join('')+'</div>';
    $('#hosts').innerHTML='<h2>Launchers</h2>'+Object.entries(data.hosts).map(([name,on])=>'<span class="'+(on?'online':'offline')+'">'+esc(name)+'<small>'+(on?'Online':'Offline')+'</small></span>').join('');
    const item=postId?await json('/api/post/'+encodeURIComponent(postId)):data;
    const next=JSON.stringify(item);
    if(next!==signature && !($('#main').contains(document.activeElement)&&['INPUT','TEXTAREA'].includes(document.activeElement.tagName))){
      if(postId) renderThread(item); else renderBoard(item);
      // A re-render recreates the masthead; offset its 40s rain loop by wall clock so it never jumps back.
      $('.masthead')?.style.setProperty('--rain-t','-'+(Date.now()%40000)+'ms');
      signature=next;
    }
  } catch(err) { if(!signature) $('#main').textContent=err.message; else $('#hosts').textContent='Reconnecting…'; }
  finally {loading=false;}
}
if(!project&&!directory&&!postId) $('#global').value=q;
$(directory||project?'#directory':'#home').classList.add('here');
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('#global').focus();}});
update();
setInterval(()=>{if(!document.hidden) update();},5000);
