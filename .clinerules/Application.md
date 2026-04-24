TrainBuilder is an application that is used by a Workshop supervisor.

# Description
A workshop supervisor models a train consist : he creates wagons and their bogies, attaches sensors (temperature, vibration, speed), and connects wagons with couplers.
The application triggers an Ada calculation (e.g., nominal braking distance and coupler tension using mass, speed, friction coefficient) and ingests a sensor file through a C/C++ JNI module to summarize metrics (min/max/average, spikes). 
Results are persisted and exposed by the central Java service. Everything runs via Docker Compose.


# Architecture Components
A front-end Web Javascript / Angular / React application to control the use case and visualize the results
A main Java REST API “TrainManager.”
An Ada calculation service accessible via HTTPS or CLI.
A C/C++ module interfaced through JNI that parses a sensor .csv file.
A docker‑compose stack that launches all services + PostgreSQL / In-memory database / CSV files-based data management.
If possible, one end‑to‑end integration test validating the above scenario, by gathering together different components.

## Names
trainbuilder-ui (Angular/React): SPA which exclusively consumes the train-manager API, serves as a test and visualization interface
train-manager (Java Spring Boot): REST + orchestration (central app)
compute-service (GNAT Ada): HTTPS or CLI microservice exposing /compute/brake & /compute/coupler
sensor-bridge (C++ JNI): native library + Java wrapper for sensor ingestion/aggregation
db (PostgreSQL or in-memory) for persistence

# Model
Train { id, reference, wagons: [Wagon] } 
Wagon { id, reference, massKg, maxSpeedKmh, bogies: [Bogie] }
Bogie { id, axleCount, hasDiscBrakes }
Coupler { id, type, maxTensionKn }
SensorReading { timestamp, sensorType, value }
BrakeReport { wagonId, speedKmh, frictionCoef, brakeDistanceM }
CouplerReport { leadingWagonId, trailingWagonId, tensionKn }

# Flows
REST contracts (Java) :
POST /wagons ; GET /wagons/{id}
POST /compute/brake → delegates to Ada
POST /compute/coupler → delegates to Ada
POST /sensors/import (multipart .csv) → goes through JNI C++ for aggregation, then persists

Flow (simplified sequence) :
Create wagon + bogies → store in DB.
Import sensors (CSV) → C++ JNI aggregates metrics → Java persists.
Java calls Ada for braking/coupler calculations → receives reports → stores/exposes.
Java returns a train summary (wagons + sensor metrics + calculation reports).
