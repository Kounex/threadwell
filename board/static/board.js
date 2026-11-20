const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const route = location.pathname;
const params = new URLSearchParams(location.search);
const q = params.get('q') || '';
const project = route.startsWith('/project/') ? decodeURIComponent(route.slice(9)) : '';
const when = stamp => new Date(stamp*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
const link = p => '/project/'+encodeURIComponent(p);
const provider = p => ({codex:'Codex',claude:'Claude Code',launcher:'Launcher',owner:'You'}[p] || p);
// "claude-opus-5-5" + "high" -> "Opus 5.5 High"; "gpt-6.1-sol" -> "GPT-6.1 Sol". The raw id stays in the tooltip.
const efforts = {low:'Low',minimal:'Minimal',medium:'Medium',high:'High',xhigh:'Extra High',max:'Max'};
function modelName(id) {
  const words = id.replace(/\[.*\]$/,'').replace(/-\d{8}$/,'').split('-');
  const title = w => w.charAt(0).toUpperCase()+w.slice(1);
  if(words[0]==='claude') {
    const name = words.slice(1).filter(w=>!/^\d+$/.test(w)).map(title).join(' ');
    const version = words.slice(1).filter(w=>/^\d+$/.test(w)).join('.');
    return (name+' '+version).trim();
  }
  if(words[0]==='gpt' && words.length>1) return 'GPT-'+words[1]+(words.length>2?' '+words.slice(2).map(title).join(' '):'');
  return id;
}
const model = (m, e) => m ? '<small class="model" title="'+esc(m+(e?' · effort '+e:''))+'">'+esc(modelName(m)+(efforts[e]?' '+efforts[e]:''))+'</small>' : '';
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
// Masthead digital rain. Glyphs are rain.png's 5x7 pixel font (plus mirror images), 2px per pixel on its
// 16px column / 18px row grid. One canvas lives across re-renders so the rain never restarts.
const rain = (() => {
  const font = '0e11131519110e,1f010204080808,1f010102041800,04041f04040810,01010204081000,041f041f040202,040c040404040e,1f010506040810,0102040c140404,081f0909091102,0e11010e10101f,041f1101020408,101f111210100f,1e01010e01011e,1c04040404041f,1f0102040a1100,0404040a0a1100,041f0404081000';
  const flip = r => [0,1,2,3,4].reduce((m,b)=>m|((r>>b&1)<<(4-b)),0);
  const glyphs = font.split(',').flatMap(hex => {
    const rows = hex.match(/../g).map(h=>parseInt(h,16)), mirror = rows.map(flip);
    return rows.join()===mirror.join() ? [rows] : [rows, mirror];
  });
  // Lead glyph, then the PNG's three greens from bright to dim.
  const tones = ['#c4ffc6','#91f69a','#70f28a','#53ba72'];
  const W = 16, H = 18, still = matchMedia('(prefers-reduced-motion: reduce)');
  const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
  canvas.className = 'rain'; canvas.setAttribute('aria-hidden','true');
  const atlas = document.createElement('canvas'), a = atlas.getContext('2d');
  atlas.width = glyphs.length*W; atlas.height = tones.length*H;
  tones.forEach((tone,t) => { a.fillStyle = tone; glyphs.forEach((rows,g) => rows.forEach((bits,y) => {
    for(let x=0;x<5;x++) if(bits>>(4-x)&1) a.fillRect(g*W+x*2, t*H+y*2, 2, 2);
  })); });
  const pick = () => Math.random()*glyphs.length|0;
  let columns = [], rowCount = 0, frame = 0, last = 0;
  // A drop: its lead falls one cell at a time, slowly, and the short trail it leaves fades out behind it.
  // About 40% of columns are falling at any moment; the rest wait a random while before the next drop.
  function drop(column, fresh) {
    column.speed = 0.6+Math.random()*0.9;   // cells per second
    column.trail = 4+Math.random()*4|0;
    const span = rowCount+column.trail;
    column.head = fresh ? span-Math.random()*span*2.5 : -Math.random()*span*3;
    column.lead = Math.floor(column.head);
    column.cells = Array.from({length:rowCount}, pick);
  }
  function resize() {
    const w = Math.round(canvas.clientWidth), h = Math.round(canvas.clientHeight);
    if(!w || !h || (w===canvas.width && h===canvas.height)) return;
    canvas.width = w; canvas.height = h; rowCount = Math.ceil(h/H);
    columns = Array.from({length:Math.ceil(w/W)}, () => { const c = {}; drop(c, true); return c; });
    draw();
  }
  function draw() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    columns.forEach((c,i) => {
      const lead = Math.floor(c.head), x = canvas.width-(i+1)*W+3;   // columns hug the right edge, like rain.png
      for(let k=0;k<c.trail;k++) {
        const row = lead-k;
        if(row<0 || row>=rowCount) continue;
        const fade = k/c.trail, tone = k ? 1+Math.min(2,fade*3|0) : 0;
        ctx.globalAlpha = k ? 0.6*(1-fade)+0.12 : 0.72;
        ctx.drawImage(atlas, c.cells[row]*W, tone*H, W, H, x, row*H+2, W, H);
      }
    });
    ctx.globalAlpha = 1;
  }
  function tick(now) {
    if(!canvas.isConnected || still.matches) { frame = 0; return; }
    frame = requestAnimationFrame(tick);
    if(now-last < 80) return;
    const dt = Math.min(0.25,(now-last)/1000||0); last = now;
    for(const c of columns) {
      c.head += c.speed*dt;
      // Glyphs change only where the drop is: a new one at each step, now and then the one just behind it.
      if(Math.floor(c.head)!==c.lead) {
        c.lead = Math.floor(c.head);
        if(c.lead>=0 && c.lead<rowCount) c.cells[c.lead] = pick();
        if(c.lead>0 && c.lead<=rowCount && Math.random()<0.2) c.cells[c.lead-1] = pick();
      }
      if(c.head-c.trail > rowCount) drop(c, false);
    }
    draw();
  }
  new ResizeObserver(resize).observe(canvas);
  still.addEventListener('change', () => attach($('.masthead')));
  document.addEventListener('visibilitychange', () => { last = performance.now(); });
  // Called after every render: move the one canvas into the new masthead and keep it falling.
  function attach(host) {
    if(!host || still.matches) { canvas.remove(); return; }
    if(canvas.parentNode!==host) host.prepend(canvas);
    resize();
    if(!frame) { last = performance.now(); frame = requestAnimationFrame(tick); }
  }
  return {attach};
})();
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
  const messages = p.replies.map(r=>'<article class="message '+(['claude','codex','owner'].includes(r.author)?r.author:'launcher')+(r.kind==='decision'?' earlier':'')+'"><b>'+esc(provider(r.author))+'</b>'+(r.kind==='decision'?'<span class="tag">Earlier decision</span>':'')+model(r.model,r.effort)+'<span class="time">'+when(r.stamp)+'</span><div class="body">'+prose(r.body)+'</div></article>').join('');
  $('#main').innerHTML='<div class="reading"><span class="crumb"><a href="/projects">Projects</a> / <a href="'+link(p.project)+'">'+esc(p.project)+'</a></span><h1>'+esc(p.title)+'</h1>'+badge(p)+'<p class="phase">'+esc(state(p,p.online))+' · '+esc(p.host)+' · '+when(p.updated)+'</p>'+(p.decision?'<section class="decision"><h2>Decision</h2><p class="by">'+esc(provider(p.starter))+model(p.decision_model,p.decision_effort)+'</p><div class="body">'+prose(p.decision)+'</div></section>':'')+'<h2>Discussion</h2>'+messages+composer(p)+'</div>';
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
      rain.attach($('.masthead'));
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
