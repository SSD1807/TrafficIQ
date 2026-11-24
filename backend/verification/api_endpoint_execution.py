from fastapi.testclient import TestClient
from backend.main import app
import json

client = TestClient(app)
results=[]
def record(method, path, **kwargs):
    r=getattr(client,method.lower())(path, **kwargs)
    try: body=r.json()
    except Exception: body=r.text[:300]
    results.append({"method":method.upper(),"path":path,"status":r.status_code,"body":body})
    print(f"{method.upper():6} {path:46} -> {r.status_code}")
    if r.status_code >= 400: print("  error:", body)
    return r

record('get','/')
record('get','/health')
loc=record('get','/locations').json()['locations'][0]
record('get','/traffic/current')
record('get','/traffic/predictions')
record('post',f'/traffic/predict/{loc}',json={})
record('post',f'/traffic/predict/UNKNOWN LOCATION',json={})
record('get',f'/forecasts/{loc}?horizon_minutes=30')
record('get',f'/forecasts/{loc}?horizon_minutes=60')
record('get',f'/forecasts/{loc}?horizon_minutes=45')
nodes=record('get','/routing/nodes?limit=8').json()['nodes']
# Valid Dijkstra and A* on same graph pair
origin=nodes[0]['id']; destination=nodes[-1]['id']
r=record('post','/routes/calculate',json={'origin_node':origin,'destination_node':destination,'algorithm':'dijkstra','alternatives':2,'use_simulated_congestion':False})
record('post','/routes/calculate',json={'origin_node':origin,'destination_node':destination,'algorithm':'astar','alternatives':2,'use_simulated_congestion':False})
record('post','/routes/calculate',json={'origin_node':origin,'algorithm':'dijkstra'})
record('get','/incidents')
# Try incident on a directed existing edge from route pair or first edge
edge = None
for seg in r.json()['routes'][0]['segments']:
    edge=(seg['from_node'],seg['to_node']); break
if edge:
    incident=record('post','/incidents',json={'edge_u':edge[0],'edge_v':edge[1],'kind':'road_closure','severity':1.0,'expires_in_seconds':3600}).json()
    record('get','/incidents')
    # calculate again under closure and log whether rerouted
    after=record('post','/routes/calculate',json={'origin_node':origin,'destination_node':destination,'algorithm':'dijkstra','alternatives':1,'use_simulated_congestion':False})
    record('delete',f"/incidents/{incident['id']}")
record('post','/incidents',json={'edge_u':'invalid','edge_v':'bad','kind':'accident'})
record('delete','/incidents/not-a-real-id')
record('delete','/incidents')
record('get','/weather/current')
record('get','/weather/current?allow_simulated_fallback=true&force_refresh=true')
record('get','/traffic/tomtom/flow')
record('get','/traffic/tomtom/incidents')
record('get','/traffic/tomtom/incidents?min_lon=86&max_lon=85')
record('get','/routes/osrm?origin_lat=20.2961&origin_lon=85.8245&destination_lat=20.3015&destination_lon=85.8205')
record('post','/copilot/chat',json={'message':'Show current traffic congestion predictions'})
record('post','/copilot/chat',json={'message':''})
record('get','/routing/nodes?limit=0')
with open('verification/api_results.json','w') as f: json.dump(results,f,indent=2,default=str)
print(f"\nTOTAL endpoint cases: {len(results)}")
print(f"Success statuses: {sum(x['status']<400 for x in results)}; error/validation statuses: {sum(x['status']>=400 for x in results)}")
