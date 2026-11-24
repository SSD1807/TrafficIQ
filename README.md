# TrafficIQ – Intelligent Traffic Prediction & Smart Routing System

<p align="center">
  <strong>AI-Powered Traffic Intelligence for Smarter Urban Mobility</strong>
</p>

<p align="center">
  Machine Learning | Time-Series Forecasting | Smart Routing | Geospatial Analytics
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-Backend-blue?logo=python" />
  <img src="https://img.shields.io/badge/React-Frontend-61DAFB?logo=react" />
  <img src="https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi" />
  <img src="https://img.shields.io/badge/Machine%20Learning-XGBoost-orange" />
  <img src="https://img.shields.io/badge/Forecasting-SARIMA-purple" />
  <img src="https://img.shields.io/badge/License-MIT-green" />
</p>

---

## Overview

**TrafficIQ** is an AI-powered Intelligent Traffic Management and Smart Routing Platform designed to predict traffic congestion, forecast future traffic conditions, identify road incidents, and recommend optimized travel routes.

Developed with a geographical focus on **Bhubaneswar, Odisha, India**, the platform integrates Machine Learning, time-series forecasting, graph-based pathfinding algorithms, and geospatial technologies to address urban transportation challenges.

By combining predictive analytics with intelligent route optimization, TrafficIQ aims to help users understand traffic conditions and make smarter transportation decisions.

## Key Features

### 1. Machine Learning-Based Traffic Prediction

- Predicts traffic congestion levels using trained Machine Learning models.
- Implements XGBoost and other classification models.
- Analyzes historical traffic patterns and engineered features.
- Provides congestion classification through an interactive dashboard.

### 2. Time-Series Traffic Forecasting

- Implements SARIMA-based time-series forecasting.
- Forecasts traffic conditions for the next 30 and 60 minutes.
- Analyzes historical traffic trends.
- Helps identify potential congestion before it develops.

### 3. Intelligent Route Optimization

- Uses real-world OpenStreetMap road network data.
- Implements Dijkstra's shortest-path algorithm.
- Implements A* search algorithm.
- Calculates optimized routes based on road network connectivity.
- Supports alternative route recommendations.

### 4. Incident-Aware Smart Routing

- Integrates TomTom Traffic Incident API.
- Detects traffic incidents such as accidents, road closures, and construction.
- Maps incidents to corresponding road network edges.
- Adjusts routing costs according to incident severity.
- Automatically calculates alternative routes when confirmed incidents affect the selected path.

### 5. Interactive Traffic Dashboard

- React and TypeScript-based frontend.
- FastAPI-powered backend.
- Interactive traffic visualization.
- Congestion prediction and forecasting.
- Incident monitoring and management.
- Smart route planning and navigation.

### 6. Weather and AI Integration

- Integrates Open-Meteo weather information.
- Supports AI-powered assistance through the configured OpenAI integration.
- Provides additional contextual information for traffic analysis.

---

## Technology Stack

| Category | Technologies |
|---|---|
| Programming Language | Python, TypeScript, JavaScript |
| Frontend | React, Vite |
| Backend | FastAPI |
| Machine Learning | Scikit-learn, XGBoost |
| Time-Series Forecasting | SARIMA |
| Routing Algorithms | Dijkstra's Algorithm, A* |
| Geospatial Processing | OpenStreetMap, OSMnx |
| Traffic Data | TomTom Traffic API |
| Weather Data | Open-Meteo |
| AI Integration | OpenAI API |
| Testing | Pytest, Vitest, Playwright |
| Development Tools | Git, GitHub, VS Code |

---

## System Architecture

TrafficIQ follows a modular frontend-backend architecture.

```text
              DATA SOURCES
                    |
        +-----------+-----------+
        |           |           |
   Historical   OpenStreetMap  External APIs
   Traffic      Road Network   TomTom / Weather
   Dataset          |             |
        |           |             |
        +-----------+-------------+
                    |
              DATA PROCESSING
                    |
          +---------+----------+
          |                    |
    ML Prediction        Time-Series
    (XGBoost)            Forecasting
                         (SARIMA)
          |                    |
          +---------+----------+
                    |
              ROUTING ENGINE
                    |
          +---------+----------+
          |                    |
       Dijkstra               A*
          |                    |
          +---------+----------+
                    |
           INCIDENT-AWARE
             ROUTING
                    |
              FASTAPI
               BACKEND
                    |
             REACT FRONTEND
                    |
           INTERACTIVE DASHBOARD
```

---

## Project Structure

```text
TrafficIQ/
│
├── backend/
│   ├── backend/
│   │   └── main.py
│   │
│   ├── models/
│   ├── dataset/
│   ├── outputs/
│   ├── routing/
│   ├── forecasting/
│   ├── services/
│   ├── copilot/
│   ├── cache/
│   ├── tests/
│   ├── requirements.txt
│   └── README.md
│
├── frontend/
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
│
├── docs/
│   ├── DATA_PROVENANCE.md
│   ├── LIVE_INCIDENTS.md
│   ├── INCIDENT_AWARE_ROUTING.md
│   ├── API_INTEGRATION_MAP.md
│   └── verification/
│
├── scripts/
│   ├── smoke_test_api.py
│   ├── verify_release.py
│   ├── download_osm.bat
│   └── run_tests.bat
│
├── tests/
│   └── e2e/
│
├── .env.example
├── .gitignore
├── RELEASE_MANIFEST.json
├── SHA256SUMS.txt
├── setup_windows.bat
├── start_backend.bat
├── start_frontend.bat
├── start_project.bat
│
└── README.md
```

---

## Installation and Setup

### Prerequisites

Make sure the following are installed:

- Python 3.12 or 3.14 (64-bit)
- Node.js 18 or later
- npm
- Git
- Internet connection for external integrations

### Step 1: Clone the Repository

```bash
git clone https://github.com/SSD1807/TrafficIQ.git

cd TrafficIQ
```

### Step 2: Install Dependencies

For Windows:

```bash
setup_windows.bat
```

This script configures the project environment and installs the required backend and frontend dependencies.

### Step 3: Start the Application

```bash
start_project.bat
```

This starts both the FastAPI backend and React frontend.

### Step 4: Access the Application

| Component | URL |
|---|---|
| TrafficIQ Dashboard | http://127.0.0.1:5173 |
| Backend API | http://127.0.0.1:8000 |
| API Documentation | http://127.0.0.1:8000/docs |
| Network Status | http://127.0.0.1:8000/network/status |

---

## OpenStreetMap Configuration

TrafficIQ uses OpenStreetMap road network data for geospatial routing.

To download or refresh the Bhubaneswar road network:

```bash
scripts/download_osm.bat
```

The project includes a cached road network graph.

The routing engine uses this graph to calculate paths using Dijkstra's algorithm and A* search.

---

## External API Configuration

TrafficIQ supports optional external API integrations.

Create the backend environment file using `.env.example`.

Configure the following variables when required:

```env
TOMTOM_API_KEY=your_tomtom_api_key

OPENAI_API_KEY=your_openai_api_key

CORS_ALLOW_ORIGINS=http://127.0.0.1:5173
```

**Important:** Keep API credentials in the backend environment. Never expose private API keys through frontend environment variables or commit them to GitHub.

---

## Incident-Aware Routing

The Smart Route Planner combines road network information with traffic incident data.

The routing engine:

1. Accepts source and destination locations.
2. Identifies the corresponding road network nodes.
3. Calculates the shortest path.
4. Checks available traffic incident information.
5. Evaluates the impact of incidents on road edges.
6. Recalculates routes when closures or significant incidents affect the selected path.

### Route Visualization

| Color | Meaning |
|---|---|
| Blue | Normal route |
| Green | Recommended alternative |
| Red | Road closure |
| Orange | Accident |
| Yellow | Construction |
| Dashed Amber | Unmatched incident |

---

## Data Transparency

TrafficIQ distinguishes between historical synthetic data, model-generated predictions, and externally sourced information.

- Historical traffic values are based on a synthetic dataset.
- Machine Learning predictions are generated using trained models.
- SARIMA forecasts are estimates based on historical traffic patterns.
- OpenStreetMap provides geographical road network information.
- TomTom provides external traffic incident information when configured.
- Simulated incidents are distinguished from externally sourced incidents.

**Note:** The bundled historical traffic dataset does not represent actual live traffic measurements. Forecasts and predictions should be interpreted as model outputs rather than verified real-time traffic observations.

For further details, refer to `docs/DATA_PROVENANCE.md`.

---

## Testing

TrafficIQ includes backend, frontend, and end-to-end testing.

### Run Complete Test Suite

```bash
scripts/run_tests.bat
```

### Frontend Testing

```bash
cd frontend

npm test
```

### Backend API Smoke Test

```bash
python scripts/smoke_test_api.py
```

### End-to-End Testing

```bash
cd tests/e2e

npm install

npx playwright install chromium

node e2e.mjs
```

### Release Verification

```bash
python scripts/verify_release.py
```

---

## Future Scope

- Integration of real-time traffic sensor data.
- Expansion to additional Indian cities.
- Improved traffic prediction using additional environmental and temporal features.
- Advanced congestion detection using computer vision.
- Enhanced route recommendations using continuously updated traffic information.
- Integration with smart city transportation infrastructure.
- Improved forecasting through additional machine learning and deep learning approaches.

---

## Contributors

TrafficIQ was developed through collaborative efforts involving Machine Learning, time-series forecasting, intelligent routing, geospatial analytics, and full-stack development.

We sincerely acknowledge all the team members who contributed to the research, development, implementation, and documentation of this project.

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/SidharthSatapathy04">
        <img src="https://github.com/SidharthSatapathy04.png" width="100px;" alt="Sidharth Satapathy"/>
        <br />
        <sub><b>Sidharth Satapathy</b></sub>
      </a>
      <br />
      <sub>Contributor</sub>
    </td>
    <td align="center">
      <a href="https://github.com/SSD1807">
        <img src="https://github.com/SSD1807.png" width="100px;" alt="Shreeyan Satwik Dash"/>
        <br />
        <sub><b>Shreeyan Satwik Dash</b></sub>
      </a>
      <br />
      <sub>Contributor</sub>
    </td>
    <td align="center">
      <a href="https://github.com/Biswa2006">
        <img src="https://github.com/Biswa2006.png" width="100px;" alt="Biswa Ranjan Panda"/>
        <br />
        <sub><b>Biswa Ranjan Panda</b></sub>
      </a>
      <br />
      <sub>Contributor</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <a href="https://github.com/swagatpradhan2005">
        <img src="https://github.com/swagatpradhan2005.png" width="100px;" alt="Swagat Pradhan"/>
        <br />
        <sub><b>Swagat Pradhan</b></sub>
      </a>
      <br />
      <sub>Contributor</sub>
    </td>
    <td align="center">
      <a href="https://github.com/Sanat6">
        <img src="https://github.com/Sanat6.png" width="100px;" alt="Sanat Kumar"/>
        <br />
        <sub><b>Sanat Kumar</b></sub>
      </a>
      <br />
      <sub>Contributor</sub>
    </td>
    <td align="center">
      <sub><b>Sambit Kumar Sahoo</b></sub>
      <br />
      <sub>Contributor</sub>
      <br />
      <sub>GitHub profile link pending</sub>
    </td>
  </tr>
</table>


## Acknowledgements

We would like to acknowledge the developers and organizations behind the technologies and resources used in this project:

- [OpenStreetMap](https://www.openstreetmap.org/)
- [TomTom Developer Platform](https://developer.tomtom.com/)
- [Open-Meteo](https://open-meteo.com/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [React](https://react.dev/)
- [Scikit-learn](https://scikit-learn.org/)
- [XGBoost](https://xgboost.readthedocs.io/)
- [OSMnx](https://osmnx.readthedocs.io/)

---

## Project Information

| Attribute | Details |
|---|---|
| Project Name | TrafficIQ |
| Project Domain | Artificial Intelligence & Machine Learning |
| Application Area | Intelligent Transportation Systems |
| Geographic Focus | Bhubaneswar, Odisha, India |
| Frontend | React + TypeScript |
| Backend | Python + FastAPI |
| Prediction | Machine Learning |
| Forecasting | SARIMA |
| Routing | Dijkstra's + A* |
| Repository | [GitHub - TrafficIQ](https://github.com/SSD1807/TrafficIQ) |

---

<p align="center">
  <strong>TrafficIQ – Making Urban Mobility Smarter Through Predictive Intelligence.</strong>
  <br />
  Built with innovation, collaboration, and technology.
</p>
