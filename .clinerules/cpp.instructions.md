---
description: 'C++ coding standards for native code (sensor-bridge)'
applyTo: '**/*.cpp, **/*.cc, **/*.cxx, **/*.c, **/*.hpp, **/*.hh, **/*.hxx, **/*.h'
---

# C++ Development Instructions

Instructions for generating high-quality, modern C++ code for this repository (notably `sensor-bridge/`).

## Project Context
- Primary use case: native CSV sensor ingestion + aggregation, built with CMake.
- Prefer C++20 (or the highest standard already configured by the project).
- Portability: should build on Windows + Linux.
- Interop: code is used from Java via JNI; pay attention to ABI, memory ownership, and error boundaries.

## Coding Standards

### Language & Style
- Prefer modern C++ (RAII, standard library algorithms, `std::string_view`, `std::optional`, `std::span` where appropriate).
- Do not use `new`/`delete` directly; prefer smart pointers and value types.
- Avoid macros except for include guards / compile-time feature toggles.
- Prefer `enum class` over unscoped enums.
- Prefer `constexpr`/`constinit` for compile-time constants.

### Error Handling
- Use exceptions only inside the native boundary; never let exceptions cross the JNI boundary.
- Convert failures to explicit error return types (e.g., `bool` + out parameter, or a small `Result<T>` struct) at module boundaries.
- Validate inputs (paths, CSV fields, numeric parsing) and provide actionable error messages.

### Memory / Ownership
- Make ownership explicit in APIs.
- Prefer passing by `const&` for non-trivial types, by value for small trivially copyable types.
- Avoid returning references to internal buffers.

### Performance
- Prefer streaming/iterative parsing for CSV (do not load entire files unless necessary).
- Avoid unnecessary heap allocations; reuse buffers when feasible.
- Keep hot paths simple and branch-predictable.

### Thread Safety
- Assume JNI entrypoints may be called concurrently.
- If shared state exists, protect it (mutex) or make it immutable.

## Build & Tooling
- Keep CMake targets explicit and minimal; prefer target properties (`target_include_directories`, `target_compile_features`).
- Enable warnings and treat them as errors for CI when possible (e.g., `-Wall -Wextra -Wpedantic` / `/W4`).
- Prefer sanitizers (ASan/UBSan) for Linux builds when available.

## Testing
- Add unit tests for parsing and aggregation logic in `sensor-bridge/tests/`.
- Tests should cover:
  - empty input / headers only
  - invalid numeric values
  - out-of-order timestamps
  - large files (performance/regression)

## JNI Guidance (when applicable)
- Never store `JNIEnv*` beyond the scope of the call.
- Check for and clear Java exceptions when calling back into the JVM.
- Release JNI resources (`GetStringUTFChars`, `GetByteArrayElements`, etc.) in all paths (use RAII wrappers).
- Prefer passing data as byte arrays or direct buffers if large.
