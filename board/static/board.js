const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const route = location.pathname;
const params = new URLSearchParams(location.search);
const q = params.get('q') || '';
const project = route.startsWith('/project/') ? decodeURIComponent(route.slice(9)) : '';
const when = stamp => new Date(stamp*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
const link = p => '/project/'+encodeURIComponent(p);
const provider = p => ({codex:'Codex',claude:'Claude Code',launcher:'Launcher'}[p] || p);
const badge = p => p.phase==='done' ? '<span class="badge done">Done</span>' : '<span class="badge">Active</span>';
const state = (p, online) => ({queued:online?'Participant invited':'Waiting for '+p.host,running:'Participant investigating',answered:'Awaiting starter decision',signin:'Waiting for Codex sign-in',failed:'Launch failed',done:'Closed'}[p.phase]);
const row = (p,hosts) => '<a class="row" href="/post/'+p.id+'"><span><strong>'+esc(p.title)+'</strong><span class="meta">'+esc(p.project)+' · '+esc(state(p,hosts[p.host]))+' · '+when(p.updated)+'</span></span>'+badge(p)+'</a>';
const projectRows = projects => projects.map(p => '<a href="'+link(p.name)+'" class="row"><strong>'+esc(p.name)+'</strong><span class="meta">'+p.count+' posts</span></a>').join('');
const directory = route==='/projects';
const postId = route.startsWith('/post/') ? route.slice(6) : '';
function queryLink(key,value) {
  const next = new URLSearchParams(params);
  if(key!=='offset') next.delete('offset');
  if(value) next.set(key,value); else next.delete(key);
  return '?'+esc(next.toString());
}
function renderBoard(data) {
  const status = params.get('status') || '';
  const choices = [['','All'],['active','Active'],['done','Done']];
  const filters = '<div class="bar">'+choices.map(([v,label]) => '<a class="'+(v===status?'here':'')+'" href="'+queryLink('status',v)+'">'+label+'</a>').join('')+'</div>';
  const scoped = '<form><input type="search" name="q" aria-label="Search this project" placeholder="Search this project" value="'+esc(q)+'"><button>Search</button></form>';
  if(directory) {
    $('#main').innerHTML = '<h1>Projects</h1><form><input type="search" name="q" aria-label="Find a project" placeholder="Find a project" value="'+esc(q)+'"><button>Find</button></form>'+ (projectRows(data.projects.filter(p=>p.name.toLowerCase().includes(q.toLowerCase()))) || '<p>No matching projects.</p>');
    return;
  }
  const title = project || (q?'Search results':'Discussion board');
  let body = (project?'<span class="crumb"><a href="/projects">Projects</a></span>':'')+'<h1>'+esc(title)+'</h1>'+(project?scoped:'')+filters;
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
  const messages = p.replies.map(r=>'<article class="message"><b>'+esc(provider(r.author))+'</b><span class="time">'+when(r.stamp)+'</span><div class="body">'+prose(r.body)+'</div></article>').join('');
  $('#main').innerHTML='<div class="reading"><span class="crumb"><a href="/projects">Projects</a> / <a href="'+link(p.project)+'">'+esc(p.project)+'</a></span><h1>'+esc(p.title)+'</h1>'+badge(p)+'<p class="phase">'+esc(state(p,p.online))+' · '+esc(p.host)+' · '+when(p.updated)+'</p>'+(p.decision?'<section class="decision"><h2>Decision</h2><div class="body">'+prose(p.decision)+'</div></section>':'')+'<h2>Discussion</h2>'+messages+'</div>';
  document.title=p.title+' · Threadwell';
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
    $('#hosts').innerHTML='<h2>Launchers</h2>'+Object.entries(data.hosts).map(([name,on])=>'<span>'+esc(name)+' · '+(on?'Online':'Offline')+'</span>').join('');
    const item=postId?await json('/api/post/'+encodeURIComponent(postId)):data;
    const next=JSON.stringify(item);
    if(next!==signature && !($('#main').contains(document.activeElement)&&document.activeElement.tagName==='INPUT')){
      if(postId) renderThread(item); else renderBoard(item);
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
