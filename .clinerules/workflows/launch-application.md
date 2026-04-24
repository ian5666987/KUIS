## Launch TrainBuilder (local dev)

This guide describes how to build and run each TrainBuilder module locally:

- **compute_ada** (Ada compute-service) → `http://localhost:8081`
- **sensor-bridge** (C++ JNI shared library) → loaded by the JVM (no HTTP port)
- **train-manager** (Java Spring Boot REST API) → `http://localhost:8080`
- **trainbuilder-ui** (Vite + React SPA) → Vite dev server (prints URL, usually `http://localhost:5173`)

> Order matters: **DB → sensor-bridge → compute_ada → train-manager → trainbuilder-ui**.

---

## 0) Prerequisites

- **Java 21** (matches `train-manager/pom.xml`)
- **Maven** (`mvn`)
- **Node.js + npm**
- **Docker** (for PostgreSQL)
- **CMake** + a C++ toolchain (MSVC or MinGW)
- **Alire** (`alr`) + GNAT toolchain (for Ada)

---

## 1) Start PostgreSQL (required by train-manager default profile)

`train-manager/src/main/resources/application.yaml` expects:

- DB name: `trainbuilder`
- User: `trainbuilder`
- Password: `trainbuilder`
- Host/port: `localhost:5432`

If you don’t have a docker-compose stack in this repo, you can start PostgreSQL using plain Docker:

```bash
docker run --name trainbuilder-db -p 5432:5432 \
  -e POSTGRES_DB=trainbuilder \
  -e POSTGRES_USER=trainbuilder \
  -e POSTGRES_PASSWORD=trainbuilder \
  postgres:16
```

To stop/remove later:

```bash
docker rm -f trainbuilder-db
```

---

## 2) Build sensor-bridge (C++ JNI shared library)

The Spring Boot app loads the JNI library named `sensor_bridge`.
On Windows, this typically produces `sensor_bridge.dll`.

### Build (CMake)

From repo root:

```bash
cmake -S sensor-bridge -B sensor-bridge/build
cmake --build sensor-bridge/build --config Release
```

> If you are using MinGW, you may prefer:
>
> ```bash
> cmake -S sensor-bridge -B sensor-bridge/build-gcc -G "MinGW Makefiles"
> cmake --build sensor-bridge/build-gcc
> ```
>
> Note: `sensor-bridge/CMakeLists.txt` uses `find_package(JNI REQUIRED)`.
> If CMake can’t find JNI on your machine, configure with explicit `JAVA_INCLUDE_PATH` / `JAVA_JVM_LIBRARY` vars as documented in that file.

### Make the DLL discoverable by train-manager

`train-manager/src/main/resources/application.yaml` config:

```yaml
trainbuilder:
  sensors:
    jni:
      enabled: true
      library-name: sensor_bridge
```

The DLL must be on the process library path:

- easiest on Windows: copy the built `sensor_bridge.dll` into `train-manager/native/` and ensure that folder is on `%PATH%` when you run the app
- alternative: run with `-Djava.library.path=...` pointing to the directory containing `sensor_bridge.dll`

This repo already contains `train-manager/native/libsensor_bridge.dll` which may be used if it matches your architecture/toolchain.

---

## 3) Run compute_ada (Ada compute-service)

From repo root:

```bash
cd compute_ada
alr build
alr run
```

Expected:

- service listens on `http://localhost:8081`
- endpoints:
  - `POST /compute/brake`
  - `POST /compute/coupler`

Quick smoke test:

```bash
curl -s -X POST http://localhost:8081/compute/brake \
  -H "Content-Type: application/json" \
  -d "{\"massKg\":10000,\"speedKmh\":80,\"frictionCoef\":0.3}"
```

---

## 4) Run train-manager (Spring Boot API)

From repo root:

```bash
cd train-manager
mvn spring-boot:run
```

Expected:

- API listens on `http://localhost:8080`
- it calls compute-service using `trainbuilder.compute.base-url` (defaults to `http://localhost:8081`)
- it loads the JNI library if enabled

If the JNI library is not available yet, you can temporarily disable it by overriding:

```bash
mvn spring-boot:run -Dspring-boot.run.arguments="--trainbuilder.sensors.jni.enabled=false"
```

---

## 5) Run trainbuilder-ui (Vite + React)

The UI reads `trainbuilder-ui/.env`:

```env
VITE_TRAIN_MANAGER_BASE_URL=http://localhost:8080
```

From repo root:

```bash
cd trainbuilder-ui
npm install
npm run dev
```

Open the URL printed by Vite (usually `http://localhost:5173`).

---

## Troubleshooting

### PostgreSQL connection errors

- confirm the container is running and port mapped: `docker ps`
- confirm train-manager config matches `jdbc:postgresql://localhost:5432/trainbuilder`

### JNI library load errors (`UnsatisfiedLinkError`)

- ensure `sensor_bridge.dll` is discoverable via `%PATH%` or `-Djava.library.path=...`
- ensure architecture matches (x64 JVM must load x64 DLL)
