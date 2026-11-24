import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppLayout } from '@/components/layout/AppLayout';
import { ExecutiveOverview } from '@/pages/ExecutiveOverview';
import { TrafficMapPage } from '@/pages/TrafficMap';
import { TrafficPrediction } from '@/pages/TrafficPrediction';
import { ForecastingAnalytics } from '@/pages/ForecastingAnalytics';
import { SmartRoutePlanner } from '@/pages/SmartRoutePlanner';
import { IncidentManagement } from '@/pages/IncidentManagement';
import { WeatherIntelligence } from '@/pages/WeatherIntelligence';
import { AITrafficCopilot } from '@/pages/AITrafficCopilot';
import { ModelEvaluation } from '@/pages/ModelEvaluation';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<ExecutiveOverview />} />
          <Route path="/traffic-map" element={<TrafficMapPage />} />
          <Route path="/prediction" element={<TrafficPrediction />} />
          <Route path="/forecasting" element={<ForecastingAnalytics />} />
          <Route path="/route-planner" element={<SmartRoutePlanner />} />
          <Route path="/incidents" element={<IncidentManagement />} />
          <Route path="/weather" element={<WeatherIntelligence />} />
          <Route path="/copilot" element={<AITrafficCopilot />} />
          <Route path="/model-evaluation" element={<ModelEvaluation />} />
          <Route path="*" element={<ExecutiveOverview />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
