#!/usr/bin/env python3
"""Live visualizer: watch an agent drive a savestate from a browser.

Loads the ROM once, hot-reloads the watched savestate whenever the agent
rewrites it, and serves a dashboard over plain stdlib HTTP -- no extra
dependencies, and it never touches the state file itself (read-only), so
it is safe to run against any session's working save.

    .venv/bin/python watch.py --state saves/joey.state [--port 8123]
Then open http://localhost:8123/ . The page polls ~1/s: pixel screenshot,
structured state (party/battle/money/badges), decoded text screen, and a
collision-map view of the current map with the player (@) and NPCs (N)
marked. The dropdown switches between any save in saves/; recently
written saves are marked. A live/idle dot shows whether the watched file
is still being rewritten, and the activity feed diffs consecutive
snapshots into events (map changes, battles, level-ups, captures, money,
badges, new checkpoints) so you can follow what the agent is doing
without staring at the screen. Party and enemy sprites are rendered from
the disassembly's gfx/pokemon PNGs (shiny palette and Unown letter come
from the mon's DVs, eggs show the egg pic).
"""
import argparse
import io
import json
import re
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from crystalagent import paths
from crystalagent.charmap import Charmap
from crystalagent.emu import Crystal
from crystalagent.names import Names
from crystalagent.nav import MapData, WALKABLE, WARPS, HOPS
from crystalagent.state import game_state, status_line
from crystalagent.symfile import Symbols

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>crystal watch</title>
<style>
 :root{
  --bg:#0b0d12;--panel:#141821;--panel2:#1b2030;--line:#262c3a;
  --fg:#d7dbe4;--muted:#7d8597;--dim:#4e5566;
  --accent:#7cc4ff;--gold:#f5c451;--green:#56d364;--amber:#e3b341;--red:#f47067;--cyan:#39d2c0;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
 }
 *{box-sizing:border-box}
 html,body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:14px}
 body{padding:14px 18px 28px}
 a{color:var(--accent)}

 /* header */
 header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:12px}
 .brand{font-weight:650;font-size:16px;letter-spacing:.2px;color:#fff;display:flex;align-items:center;gap:8px}
 .brand .gem{width:12px;height:12px;background:linear-gradient(135deg,#a8e4ff,#4a9ee8);transform:rotate(45deg);border-radius:2px;box-shadow:0 0 10px #4a9ee880}
 .brand small{color:var(--muted);font-weight:400;font-size:12px}
 select{background:var(--panel2);color:var(--fg);border:1px solid var(--line);border-radius:6px;
        font-family:var(--mono);font-size:12.5px;padding:5px 28px 5px 9px;appearance:none;-webkit-appearance:none;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%237d8597'/%3E%3C/svg%3E");
        background-repeat:no-repeat;background-position:right 9px center;cursor:pointer}
 select:focus{outline:1px solid var(--accent)}
 .pill{display:inline-flex;align-items:center;gap:7px;padding:4px 10px;border-radius:999px;border:1px solid var(--line);
       background:var(--panel);font-size:12px;font-family:var(--mono);color:var(--muted)}
 .dot{width:8px;height:8px;border-radius:50%;background:var(--dim)}
 .pill.live{color:var(--green);border-color:#2a4a32}
 .pill.live .dot{background:var(--green);box-shadow:0 0 0 3px #56d36430;animation:pulse 1.6s infinite}
 .pill.stale .dot{background:var(--red)}
 .pill.stale{color:var(--red);border-color:#4a2a2a}
 @keyframes pulse{0%{box-shadow:0 0 0 0 #56d36460}70%{box-shadow:0 0 0 5px #56d36400}100%{box-shadow:0 0 0 0 #56d36400}}
 .spacer{flex:1}
 .hmeta{font-family:var(--mono);font-size:12px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap}
 .hmeta b{color:var(--fg);font-weight:500}

 /* battle banner */
 #battle{display:none;align-items:center;gap:14px;margin-bottom:12px;padding:10px 14px;border-radius:8px;
         background:linear-gradient(90deg,#3b1418,#2a1214);border:1px solid #6b2a2e}
 #battle.on{display:flex;flex-wrap:wrap}
 #battle .sw{font-size:18px}
 #battle .esp{width:56px;height:56px;image-rendering:pixelated;image-rendering:crisp-edges;margin:-6px 0;
              background:radial-gradient(ellipse at 50% 70%,#5a2228,#2a1214 70%);border-radius:6px}
 #battle .who{font-weight:600;font-size:15px}
 #battle .mode{font-family:var(--mono);font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#f0a0a0;
               border:1px solid #6b2a2e;border-radius:4px;padding:2px 6px}
 #battle .ehp{flex:1;min-width:160px;display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:12px}

 /* stat strip */
 .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:12px}
 .stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 12px;min-height:58px}
 .stat .k{font-size:10.5px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:4px}
 .stat .v{font-size:15px;font-weight:600;font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .stat .s{font-size:11.5px;color:var(--muted);font-family:var(--mono);margin-top:2px}
 .badges{display:flex;gap:5px;margin-top:4px}
 .badge{width:16px;height:16px;border-radius:50%;background:#1f2532;border:1px solid var(--line);position:relative}
 .badge.on{background:radial-gradient(circle at 35% 35%,#ffe9a3,#d99a1e);border-color:#f5c451;box-shadow:0 0 6px #f5c45160}

 /* main layout: three columns of natural-height cards */
 main{display:flex;flex-direction:column;gap:12px}
 .col{display:contents}
 #c-screen{order:1}#c-party{order:2}#c-map{order:3}#c-text{order:4}#c-log{order:5}
 @media(min-width:900px){
  main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:start}
  .col{display:flex;flex-direction:column;gap:12px;min-width:0}
  .c1{grid-row:1/3}.c2{grid-column:2}.c3{grid-column:2}}
 @media(min-width:1340px){
  main{display:flex;flex-direction:row;align-items:flex-start}
  .c1{flex:0 0 512px}.c2{flex:1 1 340px}.c3{flex:1.3 1 380px}}
 .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
 .card h3{margin:0 0 10px;font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);font-weight:600;
          display:flex;align-items:center;gap:8px}
 .card h3 .cnt{margin-left:auto;font-family:var(--mono);font-weight:400;text-transform:none;letter-spacing:0}

 /* screen */
 .bezel{background:#000;border-radius:8px;padding:6px;display:inline-block;border:1px solid #2a2f3a;box-shadow:inset 0 0 0 1px #000,0 6px 20px #0008}
 canvas{display:block;image-rendering:pixelated;image-rendering:crisp-edges}
 #scr{width:480px;height:432px;max-width:100%;height:auto;aspect-ratio:10/9;border-radius:3px}
 #status{font-family:var(--mono);font-size:12px;color:var(--muted);margin-top:10px;word-break:break-all}
 #status.err{color:var(--red)}

 /* party */
 .mons{display:flex;flex-direction:column;gap:8px}
 .mon{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 11px;display:flex;gap:12px}
 .mon.dead{opacity:.55}
 .mon.dead .sp img{filter:grayscale(1)}
 .sp{flex:none;width:112px;height:112px;border-radius:8px;position:relative;overflow:hidden;
     background:radial-gradient(ellipse at 50% 70%,#27304a 0%,#131826 65%,#0d1017 100%);border:1px solid #1b2030}
 .sp img{display:block;width:112px;height:112px;image-rendering:pixelated;image-rendering:crisp-edges}
 .sp .shiny{position:absolute;top:4px;left:6px;color:var(--gold);font-size:13px;text-shadow:0 0 6px #f5c451}
 .sp .egg{position:absolute;bottom:3px;right:5px;font-size:10px;color:var(--muted);font-family:var(--mono)}
 .info{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center}
 .mon .row{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
 .mon .nm{font-weight:650;font-size:14px}
 .mon .nick{color:var(--muted);font-size:12px}
 .mon .lvl{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--accent)}
 .hp{height:7px;background:#0d1017;border-radius:4px;overflow:hidden;margin:7px 0 4px;border:1px solid #0a0c10}
 .hp i{display:block;height:100%;background:var(--green);transition:width .4s}
 .hp i.mid{background:var(--amber)}.hp i.low{background:var(--red)}
 .hpn{font-family:var(--mono);font-size:11.5px;color:var(--muted);display:flex;gap:8px;align-items:center}
 .chip{font-family:var(--mono);font-size:10.5px;border-radius:4px;padding:1px 5px;background:#2a2238;color:#d9b3ff;border:1px solid #3d3250}
 .chip.item{background:#1d2a3a;color:#9ccfff;border-color:#2a4058}
 .chip.st{background:#3a1f1f;color:#ffb3b3;border-color:#5a2a2a}
 .moves{display:grid;grid-template-columns:1fr 1fr;gap:3px 10px;margin-top:7px;font-family:var(--mono);font-size:11.5px}
 .moves span{display:flex;justify-content:space-between;color:#b8bfcc}
 .moves span i{font-style:normal;color:var(--dim)}
 .moves span.pp0 i{color:var(--red)}
 .empty{color:var(--dim);font-family:var(--mono);font-size:12px;padding:8px 0}

 /* map */
 .mapwrap{overflow:auto;max-height:72vh;background:#070910;border-radius:6px;border:1px solid #1b2030;padding:6px}
 #map{max-width:100%}
 .legend{display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:9px;font-size:11px;color:var(--muted);font-family:var(--mono)}
 .legend span{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
 .legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
 .legend i.rd{border-radius:50%}

 /* text screen */
 #txt{margin:0;font-family:var(--mono);font-size:12.5px;line-height:1.25;background:#070910;border:1px solid #1b2030;
      border-radius:6px;padding:8px 10px;color:#c9d1d9;overflow-x:auto;white-space:pre}

 /* log */
 #log{max-height:320px;overflow-y:auto;font-family:var(--mono);font-size:12px;display:flex;flex-direction:column;gap:2px}
 #log .ev{display:grid;grid-template-columns:62px 18px 1fr auto;gap:8px;align-items:center;padding:4px 8px;border-radius:5px}
 #log .ev:nth-child(odd){background:#ffffff05}
 #log .fr{color:var(--dim);text-align:right}
 #log .ic{text-align:center}
 #log .ts{color:var(--dim)}
 #log .ev.new{animation:flash 1.4s ease-out}
 @keyframes flash{from{background:#7cc4ff25}to{background:transparent}}
 #log .t-map{color:var(--cyan)}#log .t-battle{color:var(--red)}#log .t-level{color:var(--green)}
 #log .t-badge{color:var(--gold)}#log .t-money{color:var(--amber)}#log .t-save{color:var(--accent)}#log .t-party{color:#d9b3ff}
 ::-webkit-scrollbar{width:10px;height:10px}::-webkit-scrollbar-thumb{background:#2b3242;border-radius:5px}
 ::-webkit-scrollbar-track{background:transparent}
</style></head><body>
<header>
 <div class="brand"><span class="gem"></span>Crystal Watch <small>agent viewer</small></div>
 <select id="save" title="switch watched save" onchange="location='/?save='+encodeURIComponent(this.value)"></select>
 <span id="live" class="pill"><span class="dot"></span><span id="livetxt">connecting</span></span>
 <span class="spacer"></span>
 <div class="hmeta"><span>frame <b id="frame">—</b></span><span>play <b id="ptime">—</b></span><span id="file" style="color:var(--dim)"></span></div>
</header>

<div id="battle"><span class="sw">⚔</span><img class="esp" id="besp" alt=""><span class="mode" id="bmode"></span>
 <span class="who" id="bwho"></span>
 <span class="ehp"><span class="hp" style="flex:1;margin:0"><i id="bhp"></i></span><span id="bhpn"></span></span></div>

<section class="stats">
 <div class="stat"><div class="k">Location</div><div class="v" id="s-map">—</div><div class="s" id="s-pos"></div></div>
 <div class="stat"><div class="k">Trainer</div><div class="v" id="s-name">—</div><div class="s" id="s-money"></div></div>
 <div class="stat"><div class="k">Johto badges</div><div class="badges" id="s-badges"></div><div class="s" id="s-badgen"></div></div>
 <div class="stat"><div class="k">Lead</div><div class="v" id="s-lead">—</div><div class="s" id="s-leadhp"></div></div>
</section>

<main>
 <div class="col c1">
  <section class="card" id="c-screen"><h3>Screen</h3>
   <div class="bezel"><canvas id="scr" width="160" height="144"></canvas></div>
   <div id="status">loading…</div></section>
  <section class="card" id="c-text"><h3>Text screen</h3><pre id="txt"></pre></section>
 </div>
 <div class="col c2">
  <section class="card" id="c-party"><h3>Party <span class="cnt" id="pcnt"></span></h3><div class="mons" id="party"></div></section>
  <section class="card" id="c-log"><h3>Activity <span class="cnt" id="lcnt"></span></h3><div id="log"></div></section>
 </div>
 <div class="col c3">
  <section class="card" id="c-map"><h3>Map <span class="cnt" id="mapname"></span></h3>
   <div class="mapwrap"><canvas id="map"></canvas></div>
   <div class="legend" id="legend"></div></section>
 </div>
</main>

<script>
const DEFAULT=__DEFAULT_SAVE__;
const $=id=>document.getElementById(id);
const cv=$('scr').getContext('2d');
const CELL={'@':'#39e0ff','N':'#ffd84a','.':'#262b36','"':'#2e6b35','W':'#ff8c2b',
            '^':'#6f83ff','v':'#6f83ff','<':'#6f83ff','>':'#6f83ff',
            '~':'#1a7fd0','#':'#10131a'};
const LEGEND=[['@','player',1],['N','npc',1],['W','warp'],['^','ledge'],['"','grass'],['~','water'],['.','ground'],['#','wall']];
$('legend').innerHTML=LEGEND.map(([c,n,r])=>'<span><i class="'+(r?'rd':'')+'" style="background:'+CELL[c]+'"></i>'+n+'</span>').join('');
let cursor=-1,first=true,lastParty='';
function cur(){return new URLSearchParams(location.search).get('save')||DEFAULT;}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function fmt(n){return Number(n).toLocaleString();}
function age(ms){const s=Math.round(ms/1000);return s<60?s+'s':s<3600?Math.floor(s/60)+'m':Math.floor(s/3600)+'h';}
function drawShot(){const im=new Image();im.onload=()=>cv.drawImage(im,0,0);
 im.src='/shot.png?save='+encodeURIComponent(cur())+'&t='+Date.now();}
function drawMap(rows){const m=$('map'),c=m.getContext('2d'),dpr=window.devicePixelRatio||1;
 if(!rows||!rows.length){m.width=120;m.height=20;m.style.width=m.style.height='';c.fillStyle='#4e5566';
  c.font='11px monospace';c.fillText('no map data',4,14);return;}
 const h=rows.length,w=Math.max(...rows.map(r=>r.length));
 const avail=Math.max(80,(m.parentElement.clientWidth||400)-12);
 const S=Math.max(8,Math.min(28,Math.floor(avail/w)));
 m.width=w*S*dpr;m.height=h*S*dpr;m.style.width=w*S+'px';m.style.height=h*S+'px';
 c.setTransform(dpr,0,0,dpr,0,0);c.fillStyle='#070910';c.fillRect(0,0,w*S,h*S);
 for(let y=0;y<h;y++)for(let x=0;x<rows[y].length;x++){
  const ch=rows[y][x],col=CELL[ch];if(!col)continue;
  if(ch==='@'||ch==='N'){
   c.fillStyle=CELL['.'];c.fillRect(x*S,y*S,S-1,S-1);
   c.fillStyle=col;c.beginPath();c.arc(x*S+S/2-.5,y*S+S/2-.5,S/2-2,0,Math.PI*2);c.fill();
   if(ch==='@'){c.strokeStyle='#fff';c.lineWidth=Math.max(1,S/8);c.stroke();}
  }else{c.fillStyle=col;c.fillRect(x*S,y*S,S-1,S-1);}
  if(ch==='^'||ch==='v'||ch==='<'||ch==='>'){c.fillStyle='#dfe5ff';c.font='bold '+Math.round(S*.75)+'px monospace';
   c.textAlign='center';c.textBaseline='middle';c.fillText(ch,x*S+S/2-.5,y*S+S/2);}
 }}
function hpClass(p){return p>50?'':p>25?'mid':'low';}
function renderParty(party){
 $('pcnt').textContent=party.length+'/6';
 if(!party.length){$('party').innerHTML='<div class="empty">no pokémon</div>';lastParty='';return;}
 let h='';
 for(const p of party){
  const pct=p.max_hp?Math.max(0,Math.min(100,100*p.hp/p.max_hp)):0;
  const chips=[...(p.status||[]).map(s=>'<span class="chip st">'+esc(s)+'</span>'),
               p.item?'<span class="chip item">'+esc(p.item)+'</span>':''].join('');
  const mv=(p.moves||[]).map(m=>'<span class="'+(m.pp?'':'pp0')+'">'+esc(m.name)+'<i>'+m.pp+'</i></span>').join('');
  h+='<div class="mon'+(p.hp===0?' dead':'')+'">'+
   '<div class="sp"><img src="'+esc(p.sprite||'')+'" alt="">'+(p.shiny?'<span class="shiny" title="shiny">✦</span>':'')+
   (p.egg?'<span class="egg">egg</span>':'')+'</div><div class="info">'+
   '<div class="row"><span class="nm">'+esc(p.egg?'EGG':p.name)+'</span>'+
   (!p.egg&&p.nickname&&p.nickname!==p.name?'<span class="nick">“'+esc(p.nickname)+'”</span>':'')+
   '<span class="lvl">Lv '+p.level+'</span></div>'+
   '<div class="hp"><i class="'+hpClass(pct)+'" style="width:'+pct+'%"></i></div>'+
   '<div class="hpn"><span>'+p.hp+' / '+p.max_hp+' HP</span>'+chips+'</div>'+
   (mv?'<div class="moves">'+mv+'</div>':'')+'</div></div>';
 }
 if(h!==lastParty){$('party').innerHTML=h;lastParty=h;}
}
function renderBattle(b){
 const el=$('battle');
 if(!b){el.className='';return;}
 const e=b.enemy,pct=e.max_hp?Math.max(0,100*e.hp/e.max_hp):0;
 $('bmode').textContent=b.mode;
 const im=$('besp');if(im.getAttribute('src')!==e.sprite)im.src=e.sprite||'';
 $('bwho').textContent=e.name+' Lv '+e.level+(e.shiny?' ✦':'');
 const bar=$('bhp');bar.style.width=pct+'%';bar.className=hpClass(pct);
 $('bhpn').textContent=e.hp+'/'+e.max_hp;
 el.className='on';
}
async function poll(){
 const st=$('status');
 try{
  const r=await fetch('/state.json?save='+encodeURIComponent(cur()));
  const s=await r.json();
  if(s.error){st.textContent='error: '+s.error;st.className='err';return;}
  st.textContent=s.status;st.className='';
  $('frame').textContent=fmt(s.frame);$('ptime').textContent=s.play_time;
  $('file').textContent=s.save;
  const live=s.state_age_ms<8000,pill=$('live');
  pill.className='pill '+(live?'live':'stale');
  $('livetxt').textContent=live?'live':'idle '+age(s.state_age_ms);
  const loc=s.location;
  $('s-map').textContent=loc.map;$('s-map').title=loc.map;
  $('s-pos').textContent='('+loc.x+', '+loc.y+') · group '+loc.map_group+' #'+loc.map_number;
  $('mapname').textContent=loc.map;
  $('s-name').textContent=s.player.name;
  $('s-money').textContent='₽'+fmt(s.player.money)+' · rival '+s.player.rival;
  const jb=s.player.johto_badges,ALL=['ZEPHYR','HIVE','PLAIN','FOG','STORM','MINERAL','GLACIER','RISING'];
  $('s-badges').innerHTML=ALL.map(n=>'<span class="badge'+(jb.includes(n)?' on':'')+'" title="'+n+'"></span>').join('');
  $('s-badgen').textContent=jb.length+'/8'+(s.player.kanto_badges.length?' · kanto '+s.player.kanto_badges.length+'/8':'');
  const lead=s.party[0];
  if(lead){$('s-lead').textContent=lead.name+' Lv '+lead.level;
   $('s-leadhp').textContent=lead.hp+'/'+lead.max_hp+' HP'+(lead.status.length?' · '+lead.status.join(' '):'');}
  else{$('s-lead').textContent='—';$('s-leadhp').textContent='';}
  renderBattle(s.battle);
  renderParty(s.party);
  $('txt').textContent=s.screen.join('\n');
  drawMap(s.map);
  drawShot();
 }catch(e){st.textContent='offline: '+e;st.className='err';
  $('live').className='pill stale';$('livetxt').textContent='offline';}
}
function kind(m){
 if(/^battle/.test(m))return['⚔','t-battle'];
 if(/grew to/.test(m))return['↑','t-level'];
 if(/^badge/.test(m))return['★','t-badge'];
 if(/^money/.test(m))return['₽','t-money'];
 if(/^checkpoint/.test(m))return['💾','t-save'];
 if(/^entered/.test(m))return['➜','t-map'];
 if(/party/.test(m))return['●','t-party'];
 return['·',''];
}
async function pollEvents(){
 try{
  const r=await fetch('/events.json?save='+encodeURIComponent(cur())+'&since='+cursor);
  const d=await r.json();
  if(d.error)return;
  const log=$('log');
  for(const e of d.events){
   const[ic,cls]=kind(e.msg);
   const div=document.createElement('div');
   div.className='ev'+(first?'':' new');
   div.innerHTML='<span class="fr">'+fmt(e.frame)+'</span><span class="ic '+cls+'">'+ic+'</span>'+
    '<span class="'+cls+'">'+esc(e.msg)+'</span><span class="ts">'+e.t+'</span>';
   log.prepend(div);}
  while(log.children.length>200)log.lastChild.remove();
  if(d.cursor>=0)cursor=d.cursor;
  first=false;
  $('lcnt').textContent=log.children.length?log.children.length+' events':'';
  if(!log.children.length)log.innerHTML='<div class="empty">waiting for events…</div>';
  else{const em=log.querySelector('.empty');if(em)em.remove();}
 }catch(e){}
}
async function refreshSaves(){
 try{
  const r=await fetch('/saves.json');const list=await r.json();
  const sel=$('save');const c=cur();sel.textContent='';
  for(const o of list){const op=document.createElement('option');
   op.value=o.name;
   op.textContent=(o.age_s<120?'● ':'   ')+o.name+(o.age_s<120?'  ('+o.age_s+'s)':'');
   if(o.name===c)op.selected=true;sel.append(op);}
 }catch(e){}
}
refreshSaves();setInterval(refreshSaves,5000);
pollEvents();setInterval(pollEvents,1500);
poll();setInterval(poll,1000);
</script></body></html>"""


SPRITE_BOX = 56          # largest front pic is 7x7 tiles
_SPRITE_DIRS = {"NIDORAN♀": "nidoran_f", "NIDORAN♂": "nidoran_m",
                "MR.MIME": "mr__mime"}


class Sprites:
    """Front pics rendered from the disassembly's gfx/pokemon/<mon>/front.png
    (the PNG palette carries the real colours; shiny.pal the shiny ones).
    Background white is made transparent by flood-filling from the border so
    white inside the body (eyes, bellies) survives. Results are cached."""

    def __init__(self, names):
        self.names = names
        self.root = paths.REPO_ROOT / "gfx" / "pokemon"
        self._cache = {}
        self._lock = threading.Lock()

    def _dir(self, species, form):
        if species == "egg":
            return self.root / "egg"
        name = self.names.species.get(species, "")
        d = _SPRITE_DIRS.get(name) or re.sub(r"[^a-z0-9]+", "_", name.lower())
        if d == "unown":
            d = f"unown_{form if form in 'abcdefghijklmnopqrstuvwxyz' else 'a'}"
        return self.root / d

    @staticmethod
    def _pal_file(path):
        out = []
        for m in re.finditer(r"RGB\s+(\d+),\s*(\d+),\s*(\d+)", path.read_text()):
            out.append(tuple(int(v) * 255 // 31 for v in m.groups()))
        return out

    def png(self, species, shiny=False, form=None):
        key = (species, bool(shiny), form)
        with self._lock:
            if key not in self._cache:
                self._cache[key] = self._render(*key)
            return self._cache[key]

    def _render(self, species, shiny, form):
        d = self._dir(species, form)
        src = d / "front.png"
        if not src.exists():
            return None
        im = Image.open(src)
        dim = (d / "front.dimensions").read_bytes()[0]
        w, h = (dim >> 4) * 8, (dim & 0xF) * 8
        im = im.crop((0, 0, w, h))          # first frame of the animation strip
        pal = im.getpalette()
        colors = [tuple(pal[i * 3:i * 3 + 3]) for i in range(4)]
        if shiny and (d / "shiny.pal").exists():
            sp = self._pal_file(d / "shiny.pal")
            if len(sp) == 2:
                colors[1], colors[2] = sp
        px = im.load()
        # flood-fill transparent from the edges through colour-0 pixels
        outside = bytearray(w * h)
        q = deque([(x, y) for x in range(w) for y in (0, h - 1)] +
                  [(x, y) for y in range(h) for x in (0, w - 1)])
        while q:
            x, y = q.popleft()
            if not (0 <= x < w and 0 <= y < h) or outside[y * w + x] or px[x, y] != 0:
                continue
            outside[y * w + x] = 1
            q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
        out = Image.new("RGBA", (SPRITE_BOX, SPRITE_BOX), (0, 0, 0, 0))
        op = out.load()
        ox, oy = (SPRITE_BOX - w) // 2, SPRITE_BOX - h
        for y in range(h):
            for x in range(w):
                if not outside[y * w + x]:
                    op[ox + x, oy + y] = colors[px[x, y]] + (255,)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()


class Viewer:
    """One emulator instance, hot-reloaded from disk whenever the watched
    state file changes. All access is serialized through self.lock."""

    def __init__(self):
        sym = Symbols(paths.SYM)
        cm = Charmap(paths.CHARMAP)
        self.lock = threading.RLock()
        self.emu = Crystal(paths.ROM, sym, cm)          # no state loaded yet
        self.names = Names(paths.ROM, sym, cm, paths.MAP_CONSTANTS)
        self.nav = MapData(paths.REPO_ROOT)
        self.sprites = Sprites(self.names)
        self.state_path = None
        self._stamp = None
        self._ticks = 0            # emulator frames advanced since last load
        self.events = {}           # save name -> [{"i",frame,t,msg}, ...]
        self._last = {}            # save name -> previous snapshot for diffing
        self._known_saves = None   # checkpoint-watch: *.state names seen so far
        self._last_png = None      # wall clock of last /shot.png render

    def select(self, path):
        with self.lock:
            path = Path(path).resolve()
            if self.state_path != path:
                self.state_path = path
                self._stamp = None
            self._reload()

    def _reload(self, force=False):
        p = self.state_path
        if p is None or not p.exists():
            return
        st = p.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        if not force and stamp == self._stamp:
            return
        with open(p, "rb") as f:
            self.emu.py.load_state(f)
        self._stamp = stamp
        self._ticks = 0

    def npc_cells(self):
        """Live NPC walk-cells, same trick as trek.Driver.npc_cells."""
        bank, base = self.emu.sym["wObjectStructs"]
        stride = self.emu.sym.addr("wObject1Struct") - base
        cells = []
        for i in range(1, 13):
            b = self.emu.read((bank, base + i * stride), 18)
            if b[0]:
                cells.append((b[16] - 4, b[17] - 4))
        return cells

    def _map_rows(self, gs):
        loc = gs["location"]
        const = self.names.maps.get((loc["map_group"], loc["map_number"]))
        if const is None or const not in self.nav.consts:
            return None
        try:
            grid = self.nav.grid(const)
        except KeyError:
            return None
        px, py = loc["x"], loc["y"]
        npcs = set(self.npc_cells())
        def ch(x, y, c):
            if (x, y) == (px, py):
                return "@"
            if (x, y) in npcs:
                return "N"
            if c == 0x00:
                return "."
            if c in (0x14, 0x18):
                return '"'
            if c in WARPS:
                return "W"
            if c in HOPS:
                return {"R": ">", "L": "<", "U": "^", "D": "v"}[HOPS[c]]
            if c == 0x29:
                return "~"
            return "#"

        return ["".join(ch(x, y, c) for x, c in enumerate(row))
                for y, row in enumerate(grid)]

    def png(self):
        with self.lock:
            self._reload()   # the old comment promised this; never called
            # A savestate can land mid-transition with the LCD off (map
            # fade/warp: rLCDC bit 7 clear); PyBoy then renders a solid
            # fill. Advance until the PPU is live before capturing.
            settle = 0
            for _ in range(120):
                if not self.emu.read(0xFF40)[0] & 0x80:
                    settle = 8          # PPU needs frames to repaint
                elif settle:
                    settle -= 1
                if not settle:
                    break
                self.emu.py.tick(1, False)
            # Tick toward real time instead of one frame per request
            # (~1 fps playback made every battle look frozen). 240x real
            # speed, capped so a freshly opened tab can't stall a request.
            now = time.monotonic()
            elapsed = 0.0 if self._last_png is None else now - self._last_png
            self._last_png = now
            budget = min(1800, max(1, int(elapsed * 240)))
            if budget > 1:
                self.emu.py.tick(budget - 1, False)
            self.emu.py.tick(1, True)       # render exactly one frame
            self._ticks += budget
            buf = io.BytesIO()
            self.emu.py.screen.image.save(buf, format="PNG")
            return buf.getvalue()

    def snapshot(self, save_name):
        with self.lock:
            self._reload()
            if self.state_path is None or not self.state_path.exists():
                return {"error": "no state selected"}
            gs = game_state(self.emu, self.names, include_screen=True)
            meta = Path(f"{self.state_path}.meta")
            frame = json.loads(meta.read_text()).get("frames") \
                if meta.exists() else None
            out = {
                "save": save_name,
                "file": str(self.state_path),
                "status": status_line(gs),
                **gs,
            }
            out["frame"] = frame if frame is not None else gs["frame"]
            # status_line reports the emulator's per-process frame; show the
            # save's cumulative count instead.
            out["status"] = re.sub(r"frame=\d+", f"frame={out['frame']}",
                                   status_line(gs), count=1)
            out["map"] = self._map_rows(gs)
            for p in out["party"]:
                p["sprite"] = _sprite_url("egg" if p["egg"] else p["species"],
                                          p["shiny"], p["form"])
            if out["battle"]:
                e = out["battle"]["enemy"]
                e["sprite"] = _sprite_url(e["species"], e["shiny"], e["form"])
            st = self.state_path.stat()
            out["state_age_ms"] = max(0.0, (time.time_ns() - st.st_mtime_ns) / 1e6)
            self._record(save_name, out)
            return out

    def _new_checkpoints(self):
        """Names of *.state files that appeared in saves/ since last poll."""
        names = {f.name for f in paths.SAVES_DIR.glob("*.state")}
        new = [] if self._known_saves is None \
            else sorted(names - self._known_saves)
        self._known_saves = names
        return new

    def _record(self, save_name, gs):
        """Diff this snapshot against the previous one into feed events."""
        msgs = []
        prev = self._last.get(save_name)
        self._last[save_name] = gs
        if prev is not None:
            pl, cl = prev["location"]["map"], gs["location"]["map"]
            if cl != pl:
                msgs.append(f"entered {cl}")
            pb, cb = prev.get("battle"), gs.get("battle")
            if cb and not pb:
                e = cb["enemy"]
                msgs.append(f"battle start: {e['name']} L{e['level']}")
            elif pb and not cb:
                msgs.append("battle ended")
            pp, cp = prev["party"], gs["party"]
            for a, b in zip(pp, cp):
                if b["level"] > a["level"]:
                    msgs.append(f"{b['name']} grew to L{b['level']}")
            if len(cp) > len(pp) and cp:
                n = cp[-1]
                msgs.append(f"joined party: {n['name']} L{n['level']}")
            elif len(cp) < len(pp):
                msgs.append("party member left")
            pm, cm = prev["player"]["money"], gs["player"]["money"]
            if cm != pm:
                msgs.append(f"money {pm}->{cm} ({cm - pm:+d})")
            for key in ("johto_badges", "kanto_badges"):
                for b in set(gs["player"][key]) - set(prev["player"][key]):
                    msgs.append(f"badge earned: {b}")
        for name in self._new_checkpoints():
            msgs.append(f"checkpoint saved: {name}")
        if msgs:
            lst = self.events.setdefault(save_name, [])
            now = time.strftime("%H:%M:%S")
            for m in msgs:
                lst.append({"i": len(lst), "frame": gs["frame"],
                            "t": now, "msg": m})
            del lst[:-400]


def _sprite_url(species, shiny=False, form=None):
    u = f"/sprite/{species}.png"
    qs = [k for k, v in (("shiny=1", shiny), (f"form={form}", form)) if v]
    return u + ("?" + "&".join(qs) if qs else "")


def _safe_save(name):
    """Only serve files that already live in saves/ (no path escapes)."""
    p = (paths.SAVES_DIR / name).resolve()
    if p.parent != paths.SAVES_DIR.resolve() or not p.exists():
        raise ValueError(f"no such save: {name}")
    return p


def make_handler(viewer):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            name = (q.get("save") or [None])[0]
            if u.path == "/":
                self._send(200, "text/html; charset=utf-8",
                           PAGE.replace("__DEFAULT_SAVE__",
                                        json.dumps(name or paths.DEFAULT_STATE.name)) \
                               .encode())
            elif u.path == "/shot.png":
                try:
                    if name:
                        viewer.select(_safe_save(name))
                    self._send(200, "image/png", viewer.png())
                except Exception as e:
                    self._send(500, "text/plain", str(e).encode())
            elif u.path == "/state.json":
                try:
                    if name:
                        viewer.select(_safe_save(name))
                    body = json.dumps(viewer.snapshot(name or
                                      paths.DEFAULT_STATE.name),
                                      ensure_ascii=False).encode()
                    self._send(200, "application/json", body)
                except Exception as e:
                    self._send(500, "application/json",
                               json.dumps({"error": str(e)}).encode())
            elif u.path == "/events.json":
                sname = name or paths.DEFAULT_STATE.name
                try:
                    _safe_save(sname)
                    since = int((q.get("since") or ["-1"])[0])
                except Exception as e:
                    self._send(200, "application/json",
                               json.dumps({"error": str(e),
                                           "events": [], "cursor": -1}).encode())
                    return
                with viewer.lock:
                    lst = viewer.events.get(sname, [])
                    out = [e for e in lst if e["i"] > since]
                    cursor = lst[-1]["i"] if lst else -1
                self._send(200, "application/json",
                           json.dumps({"cursor": cursor, "events": out},
                                      ensure_ascii=False).encode())
            elif u.path.startswith("/sprite/"):
                m = re.fullmatch(r"/sprite/(\d+|egg)\.png", u.path)
                body = None
                if m:
                    sp = m.group(1)
                    body = viewer.sprites.png(
                        sp if sp == "egg" else int(sp),
                        shiny=(q.get("shiny") or ["0"])[0] == "1",
                        form=(q.get("form") or [None])[0])
                if body is None:
                    self._send(404, "text/plain", b"no sprite")
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(body)
            elif u.path == "/saves.json":
                now = time.time()
                lst = [{"name": f.name, "age_s": int(now - f.stat().st_mtime)}
                       for f in sorted(paths.SAVES_DIR.glob("*.state"))]
                self._send(200, "application/json", json.dumps(lst).encode())
            else:
                self._send(404, "text/plain", b"nope")

    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=str(paths.DEFAULT_STATE),
                    help="savestate file to watch (default: %(default)s)")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if not paths.ROM.exists():
        sys.exit(f"ROM not found at {paths.ROM}")
    state = Path(args.state)
    if not state.is_absolute():
        state = Path.cwd() / state
    viewer = Viewer()
    viewer.select(state)

    srv = ThreadingHTTPServer((args.host, args.port), make_handler(viewer))
    print(f"watching {state}")
    print(f"http://{args.host}:{args.port}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
