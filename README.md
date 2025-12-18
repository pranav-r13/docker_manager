# OpenCTI Manager

A Python Flask-based web interface for managing OpenCTI Docker deployments and monitoring system health in real-time.

## Features

- **Real-Time System Monitoring**: Live CPU, RAM, Disk, and Network usage stats.
- **Container Management**: Start/Stop the Core OpenCTI platform and individual Connectors.
- **Live Status Checks**: Visual indicators (Running/Stopped) for all managed stacks.
- **Live Log Console**: Streams `stdout`/`stderr` from Docker commands directly to the browser.
- **Security**: Basic path traversal protection and directory validation.

## Prerequisites

- **Python 3.x** (Tested generic, specific async libraries like `eventlet` removed for compatibility)
- **Docker & Docker Compose** installed and accessible via command line.
- **Directory Structure**:
    The application assumes the following directory structure on the host:
    ```
    ~/
    └── opencti/
        ├── docker/               # Core Platform (docker-compose.yml here)
        └── connectors/           # Connectors Directory
            ├── connector-A/      # (docker-compose.yml or .yaml here)
            └── connector-B/      # ...
    ```

## Installation

1.  Clone or copy this repository to the target server.
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

Run the application:

```bash
python app.py --port 5000
```

Open your browser and navigate to `http://<server-ip>:5000`.

## Configuration

- **Port**: Change the port using the `--port` argument.
- **Paths**: Default paths are set to `~/opencti/docker` and `~/opencti/connectors`. Modify `app.py` variables `DOCKER_DIR` and `CONNECTORS_DIR` if your structure differs.
