import os
import time
import json
import argparse
import threading
import subprocess
import psutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

import requests

# Configuration
app = Flask(__name__)
# Enable cors_allowed_origins='*' for development flexibility
socketio = SocketIO(app, cors_allowed_origins='*')

# RabbitMQ Defaults
RABBITMQ_URL = "http://192.168.0.205:15672/api/overview"
RABBITMQ_AUTH = ('admin', 'Admin@123')

# History Persistence
HISTORY_FILE = 'stats_history.json'
MAX_HISTORY_POINTS = 288 # 24 hours * 12 points/hour (5 min interval)

# Global background thread control
monitor_thread = None
thread_lock = threading.Lock()

# Paths
# Default to /home/ctiserver if it exists (specific user request), otherwise fallback to ~
POSSIBLE_HOME = '/home/ctiserver/opencti'
HOME_DIR = os.path.expanduser('~')
OPENCTI_DIR = POSSIBLE_HOME if os.path.isdir('/home/ctiserver') else os.path.join(HOME_DIR, 'opencti')

DOCKER_DIR = os.path.join(OPENCTI_DIR, 'docker')
CONNECTORS_DIR = os.path.join(OPENCTI_DIR, 'connectors')

def get_system_stats():
    """Collects system metrics (CPU, RAM, Disk, Network)."""
    try:
        net1 = psutil.net_io_counters()
        # Use cpu_percent with interval to block for 0.1s, serving double duty for net stats delay
        cpu_usage = psutil.cpu_percent(interval=0.1)
        net2 = psutil.net_io_counters()
        
        # Bytes per second (approximation over small window)
        bytes_sent_sec = (net2.bytes_sent - net1.bytes_sent) / 0.1
        bytes_recv_sec = (net2.bytes_recv - net1.bytes_recv) / 0.1

        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        return {
            'cpu': cpu_usage,
            'ram': mem.percent,
            'ram_used': mem.used / (1024**3), # GB
            'ram_total': mem.total / (1024**3), # GB
            'disk': disk.percent,
            'disk_used': disk.used / (1024**3), # GB
            'disk_total': disk.total / (1024**3), # GB
            'net_in': bytes_recv_sec / 1024, # KB/s
            'net_out': bytes_sent_sec / 1024 # KB/s
        }
    except Exception as e:
        print(f"Error getting system stats: {e}")
        return {'cpu':0, 'ram':0, 'disk':0, 'net_in':0, 'net_out':0}

def get_rabbitmq_stats():
    """Fetches metrics from RabbitMQ Management API."""
    try:
        # /api/overview gives cluster-wide message rates and total queue stats
        resp = requests.get(RABBITMQ_URL, auth=RABBITMQ_AUTH, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            queue_totals = data.get('queue_totals', {})
            message_stats = data.get('message_stats', {})
            
            return {
                'status': 'online',
                'messages_ready': queue_totals.get('messages_ready', 0),
                'messages_unacknowledged': queue_totals.get('messages_unacknowledged', 0),
                'messages_total': queue_totals.get('messages', 0),
                'publish_rate': message_stats.get('publish_details', {}).get('rate', 0.0),
                'deliver_rate': message_stats.get('deliver_get_details', {}).get('rate', 0.0)
            }
        else:
            return {'status': 'error', 'code': resp.status_code}
    except Exception as e:
        return {'status': 'offline', 'error': str(e)}

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

def get_running_containers():
    """
    Returns a list of all running containers using docker ps.
    """
    containers = []
    try:
        # Format: ID|Names|Image|Status|RunningFor
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.RunningFor}}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                parts = line.split('|')
                if len(parts) >= 5:
                    containers.append({
                        'id': parts[0],
                        'name': parts[1],
                        'image': parts[2],
                        'status': parts[3],
                        'uptime': parts[4]
                    })
    except Exception as e:
        print(f"Error listing containers: {e}")
    return containers


def get_docker_status_update():
    """Helper to get full status update dict for all components."""
    status_update = {}
    # Check Core
    status_update['core'] = check_docker_status(DOCKER_DIR)
    
    # Check Connectors
    if os.path.isdir(CONNECTORS_DIR):
        try:
            for name in os.listdir(CONNECTORS_DIR):
                path = os.path.join(CONNECTORS_DIR, name)
                if os.path.isdir(path):
                    # Check for yml or yaml
                    if 'docker-compose.yml' in os.listdir(path) or 'docker-compose.yaml' in os.listdir(path):
                        status_update[f'connector_{name}'] = check_docker_status(path)
        except Exception as e:
            print(f"Scan Error: {e}")
    return status_update

def scan_connectors():
    """Calculates the list of available connectors and their config status."""
    connectors = []
    if os.path.isdir(CONNECTORS_DIR):
        try:
            for name in os.listdir(CONNECTORS_DIR):
                path = os.path.join(CONNECTORS_DIR, name)
                if os.path.isdir(path):
                     has_config = 'docker-compose.yml' in os.listdir(path) or 'docker-compose.yaml' in os.listdir(path)
                     connectors.append({'name': name, 'has_config': has_config})
        except Exception as e:
            print(f"Scan Error: {e}")
    connectors.sort(key=lambda x: x['name'])
    return connectors

def background_monitor():
    """Emits system stats and container status periodically."""
    # Counter to run docker checks less frequently than system stats
    tick = 0
    while True:
        # 1. System Stats (Every 2s)
        try:
            stats = get_system_stats()
            # Append RabbitMQ stats to system stats
            stats['rabbitmq'] = get_rabbitmq_stats()
            socketio.emit('system_stats', stats)
        except Exception as e:
            print(f"Stats Error: {e}")

        # 2. Docker Status & Container List (Every 4s -> every 2nd tick)
        if tick % 2 == 0:
            status_update = get_docker_status_update()
            socketio.emit('status_update', status_update)
            
            container_list = get_running_containers()
            socketio.emit('container_list', container_list)

            # 3. Known Connectors list (Dynamic Discovery)
            connector_list = scan_connectors()
            socketio.emit('known_connectors', connector_list)

        # 4. History Recording (Every 300s = 5 mins -> Every 150 ticks @ 2s each)
        if tick % 150 == 0:
            # Re-fetch fresh stats to ensure accuracy or just reuse 'stats' from above
            # Using 'stats' from line 159 is fine
            if 'stats' in locals():
                save_history_point(stats)
        
        tick += 1
        socketio.sleep(2)

@app.route('/')
def index():
    """Render the dashboard with list of available connectors."""
    connectors = []
    if os.path.isdir(CONNECTORS_DIR):
        try:
            # List subdirectories in ~/opencti/connectors that contain docker-compose.yml OR .yaml
            for name in os.listdir(CONNECTORS_DIR):
                path = os.path.join(CONNECTORS_DIR, name)
                if os.path.isdir(path):
                     files = os.listdir(path)
                     if 'docker-compose.yml' in files or 'docker-compose.yaml' in files:
                        connectors.append(name)
        except Exception as e:
            print(f"Error scanning connectors: {e}")
    
    connectors.sort()
    return render_template('index.html', connectors=connectors, connectors_dir_display=CONNECTORS_DIR)

@app.route('/api/connector/<name>/config', methods=['GET'])
def get_connector_config(name):
    """Returns the content of the docker-compose file for a connector."""
    try:
        path = os.path.join(CONNECTORS_DIR, name)
        if not os.path.isdir(path):
            return {'error': 'Connector not found'}, 404
        
        # Check for yml or yaml
        config_file = None
        if 'docker-compose.yml' in os.listdir(path):
            config_file = os.path.join(path, 'docker-compose.yml')
        elif 'docker-compose.yaml' in os.listdir(path):
            config_file = os.path.join(path, 'docker-compose.yaml')
            
        if not config_file:
            return {'error': 'No docker-compose file found'}, 404
            
        with open(config_file, 'r') as f:
            content = f.read()
            
        return {'content': content, 'filename': os.path.basename(config_file)}
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/api/connector/<name>/config', methods=['POST'])
def save_connector_config(name):
    """Saves the config file, creating a backup first."""
    try:
        path = os.path.join(CONNECTORS_DIR, name)
        data = request.json
        new_content = data.get('content')
        
        if not os.path.isdir(path):
            return {'error': 'Connector not found'}, 404
            
        # 1. Check Status (Must be STOPPED)
        status = check_docker_status(path)
        if status == 'running':
            return {'error': 'Cannot edit config while connector is running. Please stop it first.'}, 400
            
        # 2. Identify File
        config_file = None
        if 'docker-compose.yml' in os.listdir(path):
            config_file = os.path.join(path, 'docker-compose.yml')
        elif 'docker-compose.yaml' in os.listdir(path):
            config_file = os.path.join(path, 'docker-compose.yaml')
            
        if not config_file:
            return {'error': 'Original config file not found'}, 404

        # 3. Create Backup
        backup_file = os.path.join(path, f"docker-compose-old.{config_file.split('.')[-1]}")
        with open(config_file, 'r') as f:
            old_content = f.read()
        with open(backup_file, 'w') as f:
            f.write(old_content)
            
        # 4. Save New Content
        with open(config_file, 'w') as f:
            f.write(new_content)
            
        return {'status': 'success', 'backup': os.path.basename(backup_file)}
        
    except Exception as e:
        return {'error': str(e)}, 500

def load_history():
    """Lengths history from file, handling errors/empty file."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    except:
        return []

def save_history_point(stats):
    """Appends a new point and trims old ones."""
    history = load_history()
    
    point = {
        'timestamp': datetime.now().isoformat(),
        'cpu': stats.get('cpu', 0),
        'ram': stats.get('ram', 0),
        'disk': stats.get('disk', 0),
        # RabbitMQ Stats
        'mq_queued': stats.get('rabbitmq', {}).get('messages_ready', 0) if stats.get('rabbitmq') else 0,
        'mq_total': stats.get('rabbitmq', {}).get('messages_total', 0) if stats.get('rabbitmq') else 0,
        'mq_rate_in': stats.get('rabbitmq', {}).get('publish_rate', 0) if stats.get('rabbitmq') else 0,
        'mq_rate_out': stats.get('rabbitmq', {}).get('deliver_rate', 0) if stats.get('rabbitmq') else 0
    }
    history.append(point)
    
    # Prune (Keep last 24h)
    if len(history) > MAX_HISTORY_POINTS:
        history = history[-MAX_HISTORY_POINTS:]
        
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(history, f)
    except Exception as e:
        print(f"Error saving history: {e}")

@app.route('/api/stats/history', methods=['GET'])
def get_stats_history():
    """Returns the historical data."""
    return {'data': load_history()}

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
        
        # Force an immediate status update after command completion
        try:
            update = get_docker_status_update()
            socketio.emit('status_update', update)
        except Exception as e:
             print(f"Post-command status update failed: {e}")

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
    
    # Emit immediate status update for better UX on refresh
    try:
        update = get_docker_status_update()
        emit('status_update', update)
    except Exception as e:
        print(f"Connect status check failed: {e}")

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
    
    # Debug: List visible connectors at startup
    if os.path.exists(CONNECTORS_DIR):
        print("Scanned Connectors:")
        for name in os.listdir(CONNECTORS_DIR):
            p = os.path.join(CONNECTORS_DIR, name)
            if os.path.isdir(p):
                 has_yml = 'docker-compose.yml' in os.listdir(p) or 'docker-compose.yaml' in os.listdir(p)
                 print(f" - {name}: {'Valid (Found docker-compose)' if has_yml else 'Skipped (No docker-compose.yml/yaml)'}")
    else:
        print(f"Warning: Connectors directory not found at {CONNECTORS_DIR}")

    socketio.run(app, host='0.0.0.0', port=args.port, debug=True)
