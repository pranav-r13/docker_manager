import os
import time
import argparse
import threading
import subprocess
import psutil
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

# Configuration
app = Flask(__name__)
# Enable cors_allowed_origins='*' for development flexibility
socketio = SocketIO(app, cors_allowed_origins='*')

# Global background thread control
monitor_thread = None
thread_lock = threading.Lock()

# Paths (Using os.path.expanduser to handle ~)
HOME_DIR = os.path.expanduser('~')
OPENCTI_DIR = os.path.join(HOME_DIR, 'opencti')
DOCKER_DIR = os.path.join(OPENCTI_DIR, 'docker')
CONNECTORS_DIR = os.path.join(OPENCTI_DIR, 'connectors')

def get_system_stats():
    """Collects system metrics (CPU, RAM, Disk, Network)."""
    # Network (bytes per sec calculation needs state, simplifying to total or snapshot)
    # For a simple 'live' speed, we compare two snapshots. 
    # Here we will just send totals or raw values, or simple percentage.
    # Let's do a simple calculation inside the loop if needed, 
    # but for simplicity in this function we return current snapshots.
    net1 = psutil.net_io_counters()
    time.sleep(0.1) # Brief pause to calculate rate if called in loop, 
                    # but real loop is in background_thread
    net2 = psutil.net_io_counters()
    
    # Bytes per second (approximation over small window)
    bytes_sent_sec = (net2.bytes_sent - net1.bytes_sent) / 0.1
    bytes_recv_sec = (net2.bytes_recv - net1.bytes_recv) / 0.1

    return {
        'cpu': psutil.cpu_percent(interval=None),
        'ram': psutil.virtual_memory().percent,
        'disk': psutil.disk_usage('/').percent,
        'net_in': bytes_recv_sec / 1024, # KB/s
        'net_out': bytes_sent_sec / 1024 # KB/s
    }


def check_docker_status(cwd):
    """
    Checks if containers in a docker-compose directory are running.
    Returns 'running' if at least one container is Up, else 'stopped'.
    """
    try:
        if not os.path.exists(cwd):
            return 'stopped'
        
        # 'docker compose ps --services --filter "status=running"' gives a list of running services
        # If output is not empty, something is running.
        result = subprocess.run(
            ['docker', 'compose', 'ps', '--services', '--filter', 'status=running'],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return 'running'
        return 'stopped'
    except Exception:
        return 'stopped'

def background_monitor():
    """Emits system stats and container status periodically."""
    # Counter to run docker checks less frequently than system stats
    tick = 0
    while True:
        # 1. System Stats (Every 2s)
        try:
            stats = get_system_stats()
            socketio.emit('system_stats', stats)
        except Exception as e:
            print(f"Stats Error: {e}")

        # 2. Docker Status (Every 4s -> every 2nd tick)
        if tick % 2 == 0:
            status_update = {}
            
            # Check Core
            status_update['core'] = check_docker_status(DOCKER_DIR)
            
            # Check Connectors
            if os.path.isdir(CONNECTORS_DIR):
                try:
                    for name in os.listdir(CONNECTORS_DIR):
                        path = os.path.join(CONNECTORS_DIR, name)
                        if os.path.isdir(path) and 'docker-compose.yml' in os.listdir(path):
                            status_update[f'connector_{name}'] = check_docker_status(path)
                except Exception as e:
                    print(f"Scan Error: {e}")
            
            socketio.emit('status_update', status_update)
        
        tick += 1
        socketio.sleep(2)

@app.route('/')
def index():
    """Render the dashboard with list of available connectors."""
    connectors = []
    if os.path.isdir(CONNECTORS_DIR):
        try:
            # List subdirectories in ~/opencti/connectors that contain docker-compose.yml
            for name in os.listdir(CONNECTORS_DIR):
                path = os.path.join(CONNECTORS_DIR, name)
                if os.path.isdir(path) and 'docker-compose.yml' in os.listdir(path):
                    connectors.append(name)
        except Exception as e:
            print(f"Error scanning connectors: {e}")
    
    connectors.sort()
    return render_template('index.html', connectors=connectors)

def execute_docker_command(command, cwd):
    """
    Executes a shell command in a specific directory.
    Streams output line-by-line to SocketIO.
    """
    try:
        # Check if directory exists
        if not os.path.isdir(cwd):
            socketio.emit('command_output', {'line': f"Error: Directory not found: {cwd}"})
            return

        socketio.emit('command_output', {'line': f"Executing: {' '.join(command)} in {cwd}"})

        # Using Popen for non-blocking execution
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False, # False for security, passing list of args
            text=True
        )

        for line in process.stdout:
            socketio.emit('command_output', {'line': line.strip()})
        
        process.wait()
        
        if process.returncode == 0:
            socketio.emit('command_output', {'line': "SUCCESS: Command completed successfully."})
        else:
            socketio.emit('command_output', {'line': f"FAILURE: Command exited with code {process.returncode}"})
        
        # Force an immediate status update after command
        # (Optional, but good UX to see the dot change faster)
        # We can't easy call background_monitor here, but next tick will catch it.

    except Exception as e:
        socketio.emit('command_output', {'line': f"EXCEPTION: {str(e)}"})


@socketio.on('docker_action')
def handle_docker_action(data):
    """
    Handles Start/Stop requests.
    data: { 'type': 'core'|'connector', 'action': 'up'|'down', 'target_name': '...' }
    """
    action_type = data.get('type')
    action = data.get('action')
    target_name = data.get('target_name')

    # Security: Validate Action
    if action not in ['up', 'down']:
        socketio.emit('command_output', {'line': "Error: Invalid action."})
        return

    command = ['docker', 'compose', 'up', '-d'] if action == 'up' else ['docker', 'compose', 'down']
    
    target_dir = ""

    if action_type == 'core':
        target_dir = DOCKER_DIR
    elif action_type == 'connector':
        if not target_name or '/' in target_name or '\\' in target_name or '..' in target_name:
            socketio.emit('command_output', {'line': "Error: Invalid connector name."})
            return
        target_dir = os.path.join(CONNECTORS_DIR, target_name)
    else:
        socketio.emit('command_output', {'line': "Error: Unknown target type."})
        return

    # Run in a separate thread so it doesn't block the event loop
    thread = threading.Thread(target=execute_docker_command, args=(command, target_dir))
    thread.start()

@socketio.on('connect')
def handle_connect():
    global monitor_thread
    with thread_lock:
        if monitor_thread is None:
            monitor_thread = socketio.start_background_task(background_monitor)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='OpenCTI Manager GUI')
    parser.add_argument('--port', type=int, default=5000, help='Port to run the server on')
    args = parser.parse_args()

    print(f"Starting OpenCTI Manager on port {args.port}...")
    print(f"Managing Core: {DOCKER_DIR}")
    print(f"Managing Connectors: {CONNECTORS_DIR}")
    
    socketio.run(app, host='0.0.0.0', port=args.port, debug=True)
