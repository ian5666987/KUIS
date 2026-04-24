---
description: 'Ada coding standards for compute-service (compute_ada)'
applyTo: '**/*.adb, **/*.ads'
---

# Ada Development Instructions

Instructions for generating high-quality Ada code for this repository (notably `compute_ada/`).

## Project Context
- Primary use case: compute service that exposes braking distance / coupler tension calculations.
- Build tooling: Alire + GNAT.
- The service may be used via HTTPS and/or CLI; it is called by the Java `train-manager`.

## Coding Standards

### Ada Style
- Prefer clear package structure: `spec` (`.ads`) defines the public API, `body` (`.adb`) implements it.
- Use strong typing to prevent unit errors (e.g., distinct types for `Speed_Kmh`, `Mass_Kg`, `Distance_M`).
- Favor `subtype` constraints and `pragma Assert`/preconditions for defensive programming.
- Use `Ada.Containers` rather than custom collections unless necessary.

### Contracts & Safety
- When possible, use contract aspects: `Pre`, `Post`, `Invariant` to document and validate behavior.
- Treat numeric conversions carefully; explicitly handle overflow and invalid ranges.
- Avoid global mutable state; prefer passing state through parameters.

### Error Handling
- Use exceptions for truly exceptional situations; for expected validation errors, return an error result (record with status/message) from public APIs.
- Ensure exceptions do not leak through external boundaries (HTTP handler / CLI main): convert to a stable error response.

### Performance
- Keep computations deterministic and side-effect free.
- Avoid unnecessary allocations in hot paths.

## API Design
- Keep calculation packages pure when possible (no I/O).
- Isolate transport concerns (HTTP/CLI) in dedicated packages that call calculation packages.

## Testing
- Add/extend tests under `compute_ada/tests/`.
- Tests should cover:
  - boundary values (0 speed, max speed)
  - invalid inputs (negative mass/speed, friction outside [0,1])
  - deterministic outputs for known fixtures

## Interop / Service Integration
- Keep request/response DTO parsing separate from domain calculations.
- Use clear, stable JSON field naming if you expose JSON.
- Log failures with enough context to troubleshoot (input values, error type), without leaking sensitive info.
