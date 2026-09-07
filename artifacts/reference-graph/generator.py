"""Standalone visualization of workspace entity relationships."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re

from engine.artifacts.generate import ArtifactGenerationContext
from engine.artifacts.definition import GeneratedArtifact, GeneratedArtifactSet
from engine.reference import Reference
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class _Node:
    id: str
    domain: str
    reference: Reference[object]
    kind: str
    display: str
    display_style: str
    source: str


@dataclass(frozen=True, slots=True)
class _Occurrence:
    source: str
    target: str
    kind: str


def _inputs(definition, context):
    def selected():
        nodes = _nodes(definition, context)
        return MappingProxyType(nodes), tuple(_occurrences(definition, context, nodes))
    return context.shared_result((_inputs, id(definition), id(context.workspace)), selected)


def validate(definition, context):
    if set(definition.outputs) != {"view", "data"}:
        raise ValueError(f"{definition.source}: reference graph requires view and data outputs")
    _inputs(definition, context)


def generate(definition, context: ArtifactGenerationContext) -> GeneratedArtifactSet:
    outputs = definition.outputs
    nodes, occurrences = _inputs(definition, context)
    graph_data = _render_graph(nodes, occurrences)
    graph_json = _json(graph_data)
    return GeneratedArtifactSet(
        (
            GeneratedArtifact(outputs["view"], _render_view(graph_data)),
            GeneratedArtifact(outputs["data"], graph_json),
        ),
        artifact_id=definition.id,
    )


def _nodes(definition, context: ArtifactGenerationContext) -> dict[str, _Node]:
    nodes: dict[str, _Node] = {}
    for domain in definition.inputs:
        provider = context.workspace.require_provider(domain)
        for entity in provider.entities.references.values():
            presentation = provider.entities.presentation(entity.reference)
            add_node(
                nodes,
                context,
                domain=domain,
                reference=entity.reference,
                kind=_entity_type_name(entity),
                display=presentation.display,
                display_style=presentation.display_style.value,
                source=entity.source,
            )
    return nodes


def add_node(
    nodes: dict[str, _Node],
    context: ArtifactGenerationContext,
    *,
    domain: str,
    reference: Reference[object],
    kind: str,
    display: str,
    display_style: str,
    source: Path,
) -> None:
    if any(
        (
            node.domain == domain and node.reference == reference
            for node in nodes.values()
        )
    ):
        raise ValueError("duplicate reference-graph node")
    local = ".".join((reference.owner, *reference.path, reference.element))
    node_id = f"{domain}:{local}"
    nodes[node_id] = _Node(
        id=node_id,
        domain=domain,
        reference=reference,
        kind=kind,
        display=display,
        display_style=display_style,
        source=_relative(source, context.workspace.root),
    )


def _occurrences(
    definition, context: ArtifactGenerationContext, nodes: Mapping[str, _Node]
) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    for domain in definition.inputs:
        provider = context.workspace.require_provider(domain)
        for dependency in provider.entity_dependencies():
            source = _node_id(nodes, domain, dependency.source)
            target = _node_id(nodes, dependency.target.domain, dependency.target.local)
            _require_known_edge(nodes, source, target)
            occurrences.append(_Occurrence(source, target, dependency.kind))
    return occurrences


def _render_graph(
    nodes: Mapping[str, _Node], occurrences: Sequence[_Occurrence]
) -> dict[str, object]:
    grouped: dict[tuple[str, str, str], int] = defaultdict(int)
    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    for occurrence in occurrences:
        grouped[(occurrence.source, occurrence.target, occurrence.kind)] += 1
        incoming[occurrence.target] += 1
        outgoing[occurrence.source] += 1

    links: list[dict[str, object]] = []
    for (source, target, kind), weight in sorted(grouped.items()):
        links.append(
            {
                "source": source,
                "target": target,
                "kind": kind,
                "weight": weight,
            }
        )

    rendered_nodes = []
    for node_id in sorted(nodes):
        node = nodes[node_id]
        rendered = {
            "id": node.id,
            "domain": node.domain,
            "kind": node.kind,
            "label": node.display,
            "display_style": node.display_style,
            "group": f"{node.domain}:{node.kind}",
            "incoming": incoming[node_id],
            "outgoing": outgoing[node_id],
            "degree": incoming[node_id] + outgoing[node_id],
        }
        rendered["source"] = node.source
        rendered_nodes.append(rendered)

    return {
        "node_count": len(rendered_nodes),
        "link_count": len(links),
        "occurrence_count": len(occurrences),
        "nodes": rendered_nodes,
        "links": links,
    }


def _node_id(
    nodes: Mapping[str, _Node], domain: str, reference: Reference[object]
) -> str:
    for node_id, node in nodes.items():
        if node.domain == domain and node.reference == reference:
            return node_id
    raise ValueError("reference graph node is not registered")


def _require_known_edge(nodes: Mapping[str, _Node], source: str, target: str) -> None:
    if source not in nodes:
        raise ValueError(f"reference graph has unknown source node {source}")
    if target not in nodes:
        raise ValueError(f"reference graph has unknown target node {target}")


def _relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _entity_type_name(entity: object) -> str:
    """Project a diagnostic graph grouping from the concrete entity type."""

    return re.sub(r"(?<!^)(?=[A-Z])", "-", type(entity).__name__).lower()


def _render_view(graph: Mapping[str, object]) -> str:
    embedded = json.dumps(graph, separators=(",", ":")).replace("<", "\\u003c")
    return _VIEW_HTML.replace("__GRAPH_DATA__", embedded)


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


_VIEW_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bedrock Reference Graph</title>
<style>
:root{color-scheme:dark;font:13px/1.45 ui-sans-serif,system-ui,sans-serif;background:#121318;color:#e8eaf0}
*{box-sizing:border-box}html,body{height:100%;margin:0;overflow:hidden}canvas{position:fixed;inset:0;width:100%;height:100%;cursor:grab}canvas.dragging{cursor:grabbing}
.toolbar,.details{position:fixed;z-index:2;background:rgba(25,27,34,.9);border:1px solid #393d49;box-shadow:0 8px 30px #0008;backdrop-filter:blur(12px)}
.toolbar{left:18px;top:18px;width:min(720px,calc(100vw - 36px));display:flex;gap:8px;padding:10px;border-radius:10px}.toolbar input,.toolbar select{min-width:0;border:1px solid #444957;border-radius:6px;background:#191b22;color:#f4f5f8;padding:7px 9px}.toolbar input{flex:1}.toolbar select{max-width:180px}.stats{white-space:nowrap;color:#aeb4c2;padding:7px 4px}
.details{right:18px;bottom:18px;width:min(360px,calc(100vw - 36px));padding:14px 16px;border-radius:10px;pointer-events:none}.details.empty{display:none}.details h2{font-size:15px;margin:0 0 8px;color:#fff}.details dl{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;margin:0}.details dt{color:#8f96a6}.details dd{margin:0;overflow-wrap:anywhere}.hint{position:fixed;left:20px;bottom:18px;color:#777f91;pointer-events:none}
@media(max-width:700px){.stats{display:none}.toolbar select{max-width:115px}.details{left:18px;right:auto}}
</style>
</head>
<body>
<canvas id="graph"></canvas>
<div class="toolbar">
  <input id="search" type="search" placeholder="Search nodes..." autocomplete="off">
  <select id="domain"><option value="">All domains</option></select>
  <select id="kind"><option value="">All kinds</option></select>
  <span class="stats" id="stats"></span>
</div>
<aside class="details empty" id="details"></aside>
<div class="hint">Drag to pan | Scroll to zoom | Hover a node for details</div>
<script type="application/json" id="graph-data">__GRAPH_DATA__</script>
<script>
const data=JSON.parse(document.getElementById('graph-data').textContent);
const canvas=document.getElementById('graph'),ctx=canvas.getContext('2d');
const search=document.getElementById('search'),domain=document.getElementById('domain'),kind=document.getElementById('kind');
const stats=document.getElementById('stats'),details=document.getElementById('details');
const nodes=data.nodes.map((n,i)=>({...n,i,x:0,y:0,vx:0,vy:0,visible:true}));
const byId=new Map(nodes.map(n=>[n.id,n]));
const links=data.links.map(l=>({...l,a:byId.get(l.source),b:byId.get(l.target),enabled:false}));
let width=0,height=0,dpr=1,zoom=1,panX=0,panY=0,drag=null,hover=null,stable=false,quietFrames=0;
function hash(s){let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0}
function color(group,alpha=1){return `hsla(${hash(group)%360},68%,64%,${alpha})`}
function seed(nodesToSeed){for(const n of nodesToSeed){const h=hash(n.id),a=(h%6283)/1000,r=80+((h>>>8)%520);n.x=Math.cos(a)*r;n.y=Math.sin(a)*r;n.vx=0;n.vy=0}}
seed(nodes);
for(const value of [...new Set(nodes.map(n=>n.domain))].sort())domain.add(new Option(value,value));
for(const value of [...new Set(nodes.map(n=>n.kind))].sort())kind.add(new Option(value,value));
function resize(){dpr=Math.min(devicePixelRatio||1,2);width=innerWidth;height=innerHeight;canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.width=width+'px';canvas.style.height=height+'px'}
addEventListener('resize',resize);resize();
function applyFilter(){const q=search.value.trim().toLowerCase();let count=0;for(const n of nodes){n.visible=(!q||n.id.toLowerCase().includes(q)||n.label.toLowerCase().includes(q))&&(!domain.value||n.domain===domain.value)&&(!kind.value||n.kind===kind.value);n.vx=0;n.vy=0;if(n.visible)count++}let linkCount=0;for(const l of links){l.enabled=l.a.visible&&l.b.visible;if(l.enabled)linkCount++}stable=false;quietFrames=0;stats.textContent=`${count.toLocaleString()} nodes | ${linkCount.toLocaleString()} links`}
search.addEventListener('input',applyFilter);domain.addEventListener('change',applyFilter);kind.addEventListener('change',applyFilter);applyFilter();
function simulate(){if(stable)return;const active=nodes.filter(n=>n.visible);for(const l of links){if(!l.enabled)continue;const dx=l.b.x-l.a.x,dy=l.b.y-l.a.y,d=Math.hypot(dx,dy)||1,strength=Math.min(l.weight,4),f=(d-70)*.0009*strength;l.a.vx+=dx/d*f;l.a.vy+=dy/d*f;l.b.vx-=dx/d*f;l.b.vy-=dy/d*f}let energy=0;for(const n of active){n.vx-=n.x*.00008;n.vy-=n.y*.00008;for(let k=1;k<=6;k++){const other=nodes[(n.i+k*173)%nodes.length];if(!other.visible||other===n)continue;const dx=n.x-other.x,dy=n.y-other.y,d2=dx*dx+dy*dy+20,f=15/d2;n.vx+=dx*f;n.vy+=dy*f}n.vx*=.91;n.vy*=.91;n.x+=n.vx;n.y+=n.vy;energy+=n.vx*n.vx+n.vy*n.vy}if(energy/Math.max(1,active.length)<.0008)quietFrames++;else quietFrames=0;if(quietFrames>=45){stable=true;for(const n of active){n.vx=0;n.vy=0}}}
function screen(n){return{x:width/2+panX+n.x*zoom,y:height/2+panY+n.y*zoom}}
function draw(){simulate();ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);for(const l of links){if(!l.enabled)continue;const a=screen(l.a),b=screen(l.b);ctx.lineWidth=.55;ctx.strokeStyle='#72798b24';ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()}for(const n of nodes){if(!n.visible)continue;const p=screen(n),degree=n.degree,r=Math.max(2,Math.min(9,2+Math.sqrt(degree)*.45))*Math.min(zoom,1.5);ctx.fillStyle=color(n.group,n===hover?1:.82);ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fill();if(n===hover||zoom>1.7&&degree>8){ctx.fillStyle='#eef0f5';ctx.font='11px system-ui';ctx.fillText(n.label,p.x+r+4,p.y+4)}}requestAnimationFrame(draw)}
function nearest(x,y){let best=null,dist=14;for(const n of nodes){if(!n.visible)continue;const p=screen(n),d=Math.hypot(p.x-x,p.y-y);if(d<dist){best=n;dist=d}}return best}
function show(n){hover=n;if(!n){details.classList.add('empty');return}details.classList.remove('empty');details.innerHTML=`<h2>${escapeHtml(n.label)}</h2><dl><dt>Node</dt><dd>${escapeHtml(n.id)}</dd><dt>Kind</dt><dd>${escapeHtml(n.kind)}</dd><dt>Connections</dt><dd>${n.degree.toLocaleString()}</dd>${n.source?`<dt>Source</dt><dd>${escapeHtml(n.source)}</dd>`:''}</dl>`}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
canvas.addEventListener('pointerdown',e=>{canvas.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,panX,panY,node:nearest(e.clientX,e.clientY)};if(drag.node){stable=false;quietFrames=0}canvas.classList.add('dragging')});
canvas.addEventListener('pointermove',e=>{if(drag){if(drag.node){drag.node.x+=(e.clientX-drag.x)/zoom;drag.node.y+=(e.clientY-drag.y)/zoom;drag.x=e.clientX;drag.y=e.clientY}else{panX=drag.panX+e.clientX-drag.x;panY=drag.panY+e.clientY-drag.y}}else show(nearest(e.clientX,e.clientY))});
canvas.addEventListener('pointerup',()=>{drag=null;canvas.classList.remove('dragging')});canvas.addEventListener('pointerleave',()=>{if(!drag)show(null)});
canvas.addEventListener('wheel',e=>{e.preventDefault();const beforeX=(e.clientX-width/2-panX)/zoom,beforeY=(e.clientY-height/2-panY)/zoom;zoom=Math.max(.12,Math.min(5,zoom*Math.exp(-e.deltaY*.001)));panX=e.clientX-width/2-beforeX*zoom;panY=e.clientY-height/2-beforeY*zoom},{passive:false});
requestAnimationFrame(draw);
</script>
</body>
</html>
"""
