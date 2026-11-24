from __future__ import annotations
import importlib.util, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi.testclient import TestClient
from backend.main import app
from routing.osm_network import OSMNetworkProvider

print('RUNTIME', sys.version.split()[0])
for name in ('streamlit','streamlit_folium','osmnx','openai'):
    print('MODULE', name, 'AVAILABLE' if importlib.util.find_spec(name) else 'MISSING')
for name in ('TOMTOM_API_KEY','OPENAI_API_KEY'):
    print('CREDENTIAL', name, 'SET' if os.getenv(name) else 'NOT_SET')

client = TestClient(app)
for method, path, kwargs in [
    ('GET','/health',{}),
    ('GET','/routing/nodes',{}),
    ('GET','/weather/current?force_refresh=true',{}),
    ('GET','/traffic/tomtom/flow',{}),
    ('POST','/copilot/chat',{'json':{'message':'How many configured locations have High predicted congestion?'}}),
]:
    try:
        response = getattr(client, method.lower())(path, **kwargs)
        try: body = response.json()
        except Exception: body = response.text[:200]
        if path.startswith('/health'):
            summary = {'status': body.get('status'), 'data_sources': body.get('data_sources')}
        elif path.startswith('/routing'):
            summary = {'graph_source': body.get('graph_source'), 'is_real_road_geometry': body.get('is_real_road_geometry'), 'node_count': len(body.get('nodes', []))}
        elif path.startswith('/weather'):
            summary = {k: body.get(k) for k in ('status','source','is_live','is_simulated','error','message')}
        elif path.startswith('/traffic/tomtom'):
            summary = {k: body.get(k) for k in ('status','source','is_live','message','error')}
        else:
            summary = {k: body.get(k) for k in ('mode','answer','llm_error')}
        print('HTTP', method, path, 'STATUS', response.status_code, 'BODY', json.dumps(summary, default=str))
    except Exception as exc:
        print('HTTP', method, path, 'ERROR', type(exc).__name__, str(exc))

# Explicit provider attempt: with OSMnx absent this can only exercise the labelled fallback path.
provider = OSMNetworkProvider(cache_path='cache/deployment_probe.graphml')
graph = provider.load(force_download=True, allow_fallback=True)
print('OSM_ATTEMPT', json.dumps({'provider_source': provider.source, 'nodes': graph.number_of_nodes(), 'edges': graph.number_of_edges(), 'last_error': provider.last_error}))
