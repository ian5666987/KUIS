# Technical Context – TrainBuilder

## Technology Stack

- Frontend: Angular or React (REST-driven SPA)
- Backend: Java 17+, Spring Boot
- Computation: GNAT Ada
- Native Processing: Modern C++ (C++17+) via JNI
- Persistence: PostgreSQL or embedded database
- Infrastructure: Docker, Docker Compose

## Communication Mechanisms

- JSON over HTTP for REST APIs
- HTTPS or CLI invocation for Ada services
- JNI boundary for native code execution

## Development Constraints

- All services must run locally via Docker Compose
- Interfaces must remain stable for integration testing
- Simplicity and clarity are preferred over optimization

## Non-Goals

- Horizontal scalability
- Cloud-native deployment
