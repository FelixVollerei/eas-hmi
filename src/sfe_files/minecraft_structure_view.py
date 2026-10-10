"""Self-contained offline plan view; scene JSON remains the editable authority."""

from __future__ import annotations

import json
from types import MappingProxyType

from .minecraft_structure import ResolvedScene, _runs, statistics

ROLE_COLORS = MappingProxyType({
    "terrain": "#8b9d70", "foundation": "#84888b", "floor": "#b18d65",
    "wall": "#e4d8be", "column": "#a74639", "beam": "#b37640",
    "roof": "#3e5360", "opening": "#73aabb", "stair": "#9c988b",
    "path": "#c3b69a", "landscape": "#4d815c", "water": "#4d9abc",
    "lighting": "#e8c676", "decoration": "#d7ad49",
})


def render_html(resolved: ResolvedScene, *, scene_sha256: str | None = None) -> str:
    scene = resolved.scene
    data = {
        "origin": scene.origin,
        "size": scene.size,
        "runs": _runs(resolved),
        "elements": [{"id": item.id, "parent": item.parent, "role": item.role,
                      "block": item.block} for item in scene.elements],
        "colors": dict(ROLE_COLORS),
        "stats": statistics(resolved),
        "scene_sha256": scene_sha256,
    }
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return """<!doctype html>
<html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Minecraft 语义建筑沙箱</title>
<style>
:root{font:15px system-ui,sans-serif;color:#e9e5da;background:#182025}
body{margin:0;padding:24px;max-width:1400px}h1{margin:0 0 4px;font-size:25px}
p{color:#b9c6c4}main{display:grid;grid-template-columns:minmax(360px,740px) minmax(260px,1fr);gap:24px}
@media(max-width:900px){main{grid-template-columns:1fr}}
canvas{width:100%;height:auto;image-rendering:pixelated;background:#283532;border:1px solid #53645f;cursor:crosshair}
section{background:#222d31;padding:18px;border-radius:10px}label{display:flex;gap:12px;align-items:center;margin:12px 0}
input[type=range]{flex:1}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#152025;padding:12px;border-radius:6px}
.bar{display:flex;gap:12px;flex-wrap:wrap}.bar strong{color:#eed3a1}button{background:#435960;color:white;border:0;border-radius:5px;padding:7px 12px;cursor:pointer}
</style>
<h1>Minecraft 语义建筑沙箱</h1>
<p>离线审阅场景；点击平面查看方块归属。修改请编辑源 JSON，再重新生成本页。</p>
<p>场景 SHA-256：<code id="sha"></code></p>
<main><section><div class="bar"><button id="top">俯视最高方块</button><button id="slice">查看水平剖面</button>
<strong id="mode"></strong></div><label>Y 剖面 <input id="y" type="range"><span id="yv"></span></label>
<canvas id="map"></canvas><p>北 ↑　东 →　颜色表示建筑角色；透明位置无已设计方块。</p></section>
<section><h2>选中坐标</h2><pre id="selection">点击图面查看构件</pre><h2>最终方块统计</h2>
<pre id="stats"></pre><h2>角色图例</h2><div id="legend"></div></section></main>
<script id="scene" type="application/json">""" + embedded + """</script>
<script>
const d=JSON.parse(document.getElementById('scene').textContent), [w,h]=d.size;
const c=document.getElementById('map'),ctx=c.getContext('2d');c.width=w;c.height=h;
const layers=new Map(), top=new Int32Array(w*h).fill(-1), topY=new Int16Array(w*h);
for(const [x,y,z,n,i] of d.runs){let layer=layers.get(y);if(!layer){layer=new Int32Array(w*h).fill(-1);layers.set(y,layer)}
  for(let dx=0;dx<n;dx++){const p=z*w+x+dx;layer[p]=i;if(y>=topY[p]||top[p]<0){top[p]=i;topY[p]=y}}}
const ys=[...layers.keys()].sort((a,b)=>a-b),slider=document.getElementById('y');
slider.min=ys[0];slider.max=ys.at(-1);slider.value=ys[0];let mode='top';
function draw(){const y=+slider.value, pixels=mode==='top'?top:(layers.get(y)||new Int32Array(w*h).fill(-1));
 const image=ctx.createImageData(w,h);for(let p=0;p<pixels.length;p++){const i=pixels[p];if(i<0)continue;
  const hex=d.colors[d.elements[i].role]||'#aaaaaa',v=parseInt(hex.slice(1),16),o=p*4;
  image.data[o]=(v>>16)&255;image.data[o+1]=(v>>8)&255;image.data[o+2]=v&255;image.data[o+3]=255}
 ctx.putImageData(image,0,0);document.getElementById('mode').textContent=mode==='top'?'俯视':'Y='+y;
 document.getElementById('yv').textContent=y}
document.getElementById('top').onclick=()=>{mode='top';draw()};
document.getElementById('slice').onclick=()=>{mode='slice';draw()};
slider.oninput=()=>{mode='slice';draw()};
c.onclick=e=>{const b=c.getBoundingClientRect(),x=Math.min(w-1,Math.floor((e.clientX-b.left)*w/b.width)),
 z=Math.min(h-1,Math.floor((e.clientY-b.top)*h/b.height)),p=z*w+x,
 i=mode==='top'?top[p]:(layers.get(+slider.value)||[])[p],y=mode==='top'?topY[p]:+slider.value,
 el=i>=0?d.elements[i]:null;
 document.getElementById('selection').textContent=JSON.stringify({world:[d.origin[0]+x,y,d.origin[1]+z],
  local:[x,y,z],element:el?.id??null,parent:el?.parent??null,role:el?.role??null,block:el?.block??null},null,2)};
document.getElementById('stats').textContent=JSON.stringify(d.stats,null,2);
document.getElementById('sha').textContent=d.scene_sha256??'未提供';
document.getElementById('legend').innerHTML=Object.entries(d.colors).map(([role,color])=>
 '<div><span style="display:inline-block;width:12px;height:12px;background:'+color+'"></span> '+role+'</div>').join('');draw();
</script></html>"""
