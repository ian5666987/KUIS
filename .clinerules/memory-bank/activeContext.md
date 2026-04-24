# Active Context – TrainBuilder

## Current Objective

Deliver a fully working end-to-end scenario demonstrating the complete TrainBuilder workflow.

## Current Scenario

1. Define wagons and bogies
2. Import a CSV file with sensor readings
3. Aggregate sensor metrics via JNI
4. Compute braking distance and coupler tension via Ada
5. Persist and expose a consolidated train summary

## Active Design Decisions

- Synchronous calls are preferred for clarity
- Fail-fast behavior on integration errors
- Minimal but explicit domain model

## Risks & Attention Points

- JNI memory ownership and lifecycle
- Data format alignment between Java and Ada
- Error propagation across service boundaries
