# Project Brief – TrainBuilder

## Vision

TrainBuilder is a workshop-oriented application that enables a Workshop Supervisor to design, validate, and analyze a train consist before operation.

The supervisor models wagons and their bogies, attaches sensors, connects wagons using couplers, and then executes engineering calculations and sensor data analysis to assess operational safety and performance.

The project intentionally combines multiple programming languages and runtimes (Web, Java, Ada, C++) to reflect realistic industrial constraints and integration challenges.

## Business Goals

- Reduce the risk of unsafe train configurations before deployment
- Provide early insight into braking distance and mechanical stress
- Transform raw sensor measurements into actionable indicators
- Serve as a reference use case for multi-language system integration

## Technical Goals

- Showcase orchestration through a central Java service
- Demonstrate interoperability between Java, Ada, and C++
- Provide a reproducible, containerized environment
- Enable end-to-end automated validation

## Scope

Included capabilities:
- Train modeling (wagons, bogies, couplers)
- Sensor data ingestion from CSV files
- Aggregation of sensor metrics (min, max, average, spikes)
- Physical calculations (braking distance, coupler tension)
- Persistence and retrieval of computed results

## Out of Scope

- User management and security
- High-fidelity physics models
- Real-time streaming sensors
- Production-grade monitoring
