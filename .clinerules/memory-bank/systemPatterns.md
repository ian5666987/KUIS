# System Patterns – TrainBuilder

## Architectural Style

TrainBuilder follows a service-oriented, polyglot architecture with a strong emphasis on separation of concerns and explicit integration boundaries.

## Core Components

- trainbuilder-ui: Web SPA used for control and visualization
- train-manager: Central Java Spring Boot application acting as orchestrator
- compute-service: Ada service responsible for deterministic engineering calculations
- sensor-bridge: C++ native module accessed through JNI for high-performance CSV processing
- database: Relational or in-memory persistence

## Key Patterns

- Orchestrator Pattern: all workflows are coordinated by train-manager
- Anti-Corruption Layer: Java adapters isolate Ada and C++ specifics
- Synchronous Service Calls: calculations are blocking and deterministic
- Explicit Data Contracts: DTOs define all cross-boundary exchanges

## Data Flow Overview

1. UI triggers actions via REST
2. Java persists domain data
3. Java delegates calculations to Ada
4. Java delegates sensor processing to JNI
5. Results are stored and exposed back to the UI
