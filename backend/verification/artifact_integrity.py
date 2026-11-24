from pathlib import Path
import json, joblib
from services.traffic_service import TrafficService
from forecasting.forecast_service import ForecastService

root=Path.cwd()
metadata=json.loads((root/'models/model_metadata.json').read_text())
model=joblib.load(root/'models/best_model.pkl')
print('classifier artifact:',type(model).__name__)
print('classifier steps:',[(n,type(v).__name__) for n,v in model.steps])
print('selected_model:',metadata['selected_model'])
print('feature_count:',len(metadata['features']))
print('all_artifact_features_in_metadata:',set(metadata['features'])==set(__import__('ml_pipeline').FEATURE_COLUMNS))
traffic=TrafficService(root/'dataset/bhubaneswar_synthetic_traffic.csv',root/'models/best_model.pkl')
loc=traffic.list_locations()[0]
p=traffic.predict_next(loc)
print('traffic inference:',loc,p['predicted_congestion'],p['model_name'],p['source'],p['is_live'])
forecasts=ForecastService(root/'dataset/bhubaneswar_synthetic_traffic.csv',root/'models/forecasting')
for h in (30,60):
 f=forecasts.forecast(loc,h)
 print('forecast inference:',h,f['vehicle_count'],f['avg_speed'],f['predicted_congestion'],f['source'],f['is_live'])
files=sorted((root/'models').rglob('*'))
for f in files:
 if f.is_file():
  if f.suffix in {'.pkl','.joblib'}:
   # Attempt to deserialize every saved model artifact.
   obj=joblib.load(f)
   print('artifact OK:',f.relative_to(root),type(obj).__name__)
