import type { PredictionError, PredictionResult } from '@/types';

export const isPrediction = (p: PredictionResult | PredictionError): p is PredictionResult =>
  'predicted_congestion' in p;
