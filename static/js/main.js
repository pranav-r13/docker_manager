const socket = io();
const terminal = document.getElementById('terminal');
const statusBadges = {
    connected: '<span class="status-dot status-online"></span>Connected',
    disconnected: '<span class="status-dot status-offline"></span>Disconnected'
};

// --- Chart.js Setup ---
// RabbitMQ Charts (Reuse histOptions style but mostly empty initial data)
const mqOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
        x: {
            display: true,
            ticks: { color: '#6c757d', maxTicksLimit: 6 },
            grid: { color: '#2c3034' }
        },
        y: {
            display: true,
            beginAtZero: true,
            ticks: { color: '#6c757d' },
            grid: { color: '#2c3034' }
        }
    },
    elements: { point: { radius: 1, backgroundColor: '#fff', hitRadius: 10 } }
};

const createMqChart = (id, color1, color2 = null) => {
    const datasets = [{
        data: [],
        borderColor: color1,
        borderWidth: 2,
        fill: true,
        backgroundColor: color1 + '20'
    }];
    if (color2) {
        datasets.push({
            data: [],
            borderColor: color2,
            borderWidth: 2,
            fill: true,
            backgroundColor: color2 + '20'
        });
    }
    return new Chart(document.getElementById(id).getContext('2d'), {
        type: 'line',
        data: { labels: [], datasets: datasets },
        options: mqOptions
    });
};

const chartQueued = createMqChart('chart-queued', '#fd7e14'); // Orange
const chartTotal = createMqChart('chart-total', '#6f42c1'); // Purple
const chartRate = createMqChart('chart-rate', '#198754', '#0dcaf0'); // Green & Blue

// History Charts
const histOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
        x: {
            display: true,
            ticks: { color: '#6c757d', maxTicksLimit: 6 },
            grid: { color: '#2c3034' }
        },
        y: {
            display: true,
            beginAtZero: true,
            max: 100,
            ticks: { color: '#6c757d' },
            grid: { color: '#2c3034' }
        }
    },
    elements: { point: { radius: 1, backgroundColor: '#fff', hitRadius: 10 } }
};

const createHistChart = (id, color) => new Chart(document.getElementById(id).getContext('2d'), {
    type: 'line',
    data: { labels: [], datasets: [{ data: [], borderColor: color, borderWidth: 2, fill: true, backgroundColor: color + '20' }] },
    options: histOptions
});

const histCpu = createHistChart('hist-cpu', '#0dcaf0');
const histRam = createHistChart('hist-ram', '#ffc107');
const histDisk = createHistChart('hist-disk', '#dc3545');

function calculateAverage(data) {
    if (!data || data.length === 0) return 0;
    const sum = data.reduce((a, b) => a + b, 0);
    return (sum / data.length).toFixed(1);
}

function loadHistory() {
    fetch('/api/stats/history')
        .then(r => r.json())
        .then(d => {
            const data = d.data || [];
            const labels = data.map(p => {
                const d = new Date(p.timestamp);
                return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
            });
            const cpuData = data.map(p => p.cpu);
            const ramData = data.map(p => p.ram);
            const diskData = data.map(p => p.disk);

            // Update Charts
            histCpu.data.labels = labels;
            histCpu.data.datasets[0].data = cpuData;
            histCpu.update();
            document.getElementById('avg-cpu').innerText = `Avg: ${calculateAverage(cpuData)}%`;

            histRam.data.labels = labels;
            histRam.data.datasets[0].data = ramData;
            histRam.update();
            document.getElementById('avg-ram').innerText = `Avg: ${calculateAverage(ramData)}%`;

            histDisk.data.labels = labels;
            histDisk.data.datasets[0].data = diskData;
            histDisk.update();
            document.getElementById('avg-disk').innerText = `Avg: ${calculateAverage(diskData)}%`;

            // RabbitMQ History
            const mqQueued = data.map(p => p.mq_queued || 0);
            const mqTotal = data.map(p => p.mq_total || 0);
            const mqRateIn = data.map(p => p.mq_rate_in || 0);
            const mqRateOut = data.map(p => p.mq_rate_out || 0);

            chartQueued.data.labels = labels;
            chartQueued.data.datasets[0].data = mqQueued;
            chartQueued.update();

            chartTotal.data.labels = labels;
            chartTotal.data.datasets[0].data = mqTotal;
            chartTotal.update();

            chartRate.data.labels = labels;
            chartRate.data.datasets[0].data = mqRateIn;
            // Check if second dataset exists (it should)
            if (chartRate.data.datasets.length > 1) {
                chartRate.data.datasets[1].data = mqRateOut;
            }
            chartRate.update();
        });
}
// Load on startup
loadHistory();
// Reload every 5 mins (approx)
setInterval(loadHistory, 300000);

function updateChart(chart, val1, val2 = null) {
    const now = new Date();
    const timeLabel = now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0') + ':' + now.getSeconds().toString().padStart(2, '0');

    // Limit history to 20 points
    if (chart.data.labels.length > 20) {
        chart.data.labels.shift();
        chart.data.datasets.forEach(ds => ds.data.shift());
    }

    chart.data.labels.push(timeLabel);
    chart.data.datasets[0].data.push(val1);

    if (chart.data.datasets.length > 1 && val2 !== null) {
        chart.data.datasets[1].data.push(val2);
    }
    chart.update('none'); // mode 'none' for performance
}
// ---------------------

// Connection Status
socket.on('connect', () => {
    const el = document.getElementById('connection-status');
    el.className = 'btn btn-outline-success btn-sm';
    el.innerHTML = statusBadges.connected;
    logToTerminal('System: Client connected to server.');
});

socket.on('disconnect', () => {
    const el = document.getElementById('connection-status');
    el.className = 'btn btn-outline-danger btn-sm';
    el.innerHTML = statusBadges.disconnected;
    logToTerminal('System: Connection lost.');
});

// System Stats Update (Includes RabbitMQ)
socket.on('system_stats', (data) => {
    // CPU (Just % for now)
    document.getElementById('cpu-val').innerText = data.cpu + '%';
    document.getElementById('cpu-bar').style.width = data.cpu + '%';

    // RAM
    const ramText = `${data.ram}% <small class="text-muted ms-1">(${data.ram_used.toFixed(1)}GB / ${data.ram_total.toFixed(1)}GB)</small>`;
    document.getElementById('ram-val').innerHTML = ramText;
    document.getElementById('ram-bar').style.width = data.ram + '%';

    // Disk
    const diskText = `${data.disk}% <small class="text-muted ms-1">(${data.disk_used.toFixed(1)}GB / ${data.disk_total.toFixed(1)}GB)</small>`;
    document.getElementById('disk-val').innerHTML = diskText;
    document.getElementById('disk-bar').style.width = data.disk + '%';

    // Net
    document.getElementById('net-in').innerText = data.net_in.toFixed(1);
    document.getElementById('net-out').innerText = data.net_out.toFixed(1);

    // RabbitMQ
    if (data.rabbitmq) {
        const mq = data.rabbitmq;
        const mqStatusEl = document.getElementById('mq-status');

        if (mq.status === 'online') {
            mqStatusEl.className = 'badge bg-success';
            mqStatusEl.innerText = 'Online';

            document.getElementById('mq-queued').innerText = mq.messages_ready.toLocaleString();
            document.getElementById('mq-total').innerText = mq.messages_total.toLocaleString();
            document.getElementById('mq-rate-in').innerText = mq.publish_rate.toFixed(1);
            document.getElementById('mq-rate-out').innerText = mq.deliver_rate.toFixed(1);

            // Update Graphs
            updateChart(chartQueued, mq.messages_ready);
            updateChart(chartTotal, mq.messages_total);
            updateChart(chartRate, mq.publish_rate, mq.deliver_rate);
        } else {
            mqStatusEl.className = 'badge bg-danger';
            mqStatusEl.innerText = `Offline (${mq.error || 'Err'})`;
        }
    }
});

// Container Status Update (Badges in Controls Tab)
socket.on('status_update', (data) => {
    for (const [key, status] of Object.entries(data)) {
        const el = document.getElementById(`status-${key}`);
        if (el) {
            if (status === 'running') {
                el.className = 'badge bg-success ms-2';
                el.innerText = 'Running';
            } else {
                el.className = 'badge bg-danger ms-2';
                el.innerText = 'Stopped';
            }
        }
    }
});

// Running Containers List Update
socket.on('container_list', (data) => {
    const tbody = document.getElementById('containers-table-body');
    tbody.innerHTML = '';
    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No running containers found.</td></tr>';
        return;
    }

    data.forEach(c => {
        const row = `<tr>
        <td class="text-info font-monospace">${c.id}</td>
        <td class="fw-bold">${c.name}</td>
        <td class="text-muted small">${c.image}</td>
        <td><span class="badge bg-success">${c.status}</span></td>
        <td>${c.uptime}</td>
    </tr>`;
        tbody.innerHTML += row;
    });
});

// Command Output Streaming
socket.on('command_output', (data) => {
    logToTerminal(data.line);
});

function logToTerminal(text) {
    const line = document.createElement('div');
    line.textContent = text;
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
}

function clearTerminal() {
    terminal.innerHTML = '<div class="text-muted">// Console cleared</div>';
}

// Actions
function sendAction(type, action, targetName = null) {
    let msg = `Requesting: Docker Compose ${action.toUpperCase()} for ${type}`;
    if (targetName) msg += ` (${targetName})`;
    logToTerminal(msg + '...');

    socket.emit('docker_action', {
        type: type,
        action: action,
        target_name: targetName
    });
}
let currentUnlockIndex = null; // Unused in new logic but kept if reference exists, or remove.
let currentEditingName = null;
let currentEditingUid = null;

const passwordModal = new bootstrap.Modal(document.getElementById('passwordModal'));
const configModal = new bootstrap.Modal(document.getElementById('configModal'));

function openEditor(name, uid) {
    currentEditingName = name;
    currentEditingUid = uid;

    // Reset UI
    const editor = document.getElementById('editor-modal');
    const msgEl = document.getElementById('editor-msg-modal');
    const btnLock = document.getElementById('btn-lock-modal');
    const btnSave = document.getElementById('btn-save-modal');

    editor.value = 'Loading...';
    editor.readOnly = true;
    editor.style.borderColor = '';
    msgEl.innerText = '';

    btnLock.classList.remove('d-none');
    btnSave.classList.add('d-none');
    document.getElementById('configModalLabel').innerText = `Config Editor: ${name}`;

    configModal.show();

    // Fetch config
    fetch(`/api/connector/${name}/config`)
        .then(r => r.json())
        .then(data => {
            if (data.content) {
                editor.value = data.content;
            } else {
                editor.value = '// Error loading file: ' + (data.error || 'Unknown');
            }
        })
        .catch(err => {
            editor.value = '// Network Error';
        });
}

// Copy/Paste Helpers
function editorCopy() {
    const editor = document.getElementById('editor-modal');
    editor.select();
    document.execCommand('copy'); // Fallback for older browsers / non-secure contexts
    // Or navigator.clipboard.writeText(editor.value);
    // Deselect
    window.getSelection().removeAllRanges();

    // Feedback
    const msg = document.getElementById('editor-msg-modal');
    const original = msg.innerText;
    msg.className = 'text-success small ms-2';
    msg.innerText = 'Copied to clipboard!';
    setTimeout(() => { msg.innerText = ''; }, 2000);
}

async function editorPaste() {
    const editor = document.getElementById('editor-modal');
    if (editor.readOnly) {
        alert('Unlock the editor first!');
        return;
    }
    try {
        const text = await navigator.clipboard.readText();
        if (text) {
            // Insert at cursor
            const start = editor.selectionStart;
            const end = editor.selectionEnd;
            const val = editor.value;
            editor.value = val.substring(0, start) + text + val.substring(end);
            editor.selectionStart = editor.selectionEnd = start + text.length;
        }
    } catch (err) {
        // Fallback for non-https?
        alert('Paste failed: Browser may block clipboard access in this context. Use Ctrl+V.');
    }
}

function unlockConfig() {
    // Check if Stopped
    const statusBadge = document.getElementById(`status-connector_${currentEditingName}`);
    if (statusBadge && statusBadge.innerText.toLowerCase() === 'running') {
        alert('You must STOP the connector before editing the configuration.');
        return;
    }

    // Show password modal
    document.getElementById('unlock-password').value = '';
    document.getElementById('password-error').innerText = '';
    passwordModal.show();
}

function checkPassword() {
    const pwd = document.getElementById('unlock-password').value;
    if (pwd === 'P@ssw0rd') {
        passwordModal.hide();
        // Enable Editor
        const editor = document.getElementById('editor-modal');
        editor.readOnly = false;
        editor.style.borderColor = '#ffc107'; // Yellow border

        // Toggle Buttons
        document.getElementById('btn-lock-modal').classList.add('d-none');
        document.getElementById('btn-save-modal').classList.remove('d-none');

    } else {
        document.getElementById('password-error').innerText = 'Incorrect password.';
    }
}

function saveConfig() {
    const content = document.getElementById('editor-modal').value;
    const msgEl = document.getElementById('editor-msg-modal');
    const name = currentEditingName;

    msgEl.innerText = 'Saving...';
    msgEl.className = 'text-warning small ms-2';

    fetch(`/api/connector/${name}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: content })
    })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                msgEl.className = 'text-success small ms-2';
                msgEl.innerText = `Saved! Backup: ${data.backup}`;

                // Reset UI Locked
                const editor = document.getElementById('editor-modal');
                editor.readOnly = true;
                editor.style.borderColor = '';
                document.getElementById('btn-lock-modal').classList.remove('d-none');
                document.getElementById('btn-save-modal').classList.add('d-none');

            } else {
                msgEl.className = 'text-danger small ms-2';
                msgEl.innerText = data.error || 'Save failed';
            }
        })
        .catch(err => {
            msgEl.className = 'text-danger small ms-2';
            msgEl.innerText = 'Network error during save';
        });
}

// --- Dynamic Connector Discovery ---
function refreshConnectors() {
    const btn = document.getElementById('btn-refresh-connectors');
    const icon = btn.querySelector('i');
    icon.classList.add('spin-animation');
    btn.disabled = true;
    icon.classList.remove('bi-arrow-clockwise');
    icon.classList.add('bi-arrow-repeat');
    icon.style.animation = 'spin 1s linear infinite';

    socket.emit('request_connectors');
}

socket.on('known_connectors', (list) => {
    // Stop Spinner
    const btn = document.getElementById('btn-refresh-connectors');
    if (btn) {
        btn.disabled = false;
        const icon = btn.querySelector('i');
        icon.style.animation = '';
        icon.classList.remove('bi-arrow-repeat');
        icon.classList.add('bi-arrow-clockwise');
    }

    const container = document.getElementById('connectors-list');
    const loading = document.getElementById('connectors-loading');
    if (loading) loading.remove();

    // 1. Mark all existing cards
    const existingNames = new Set();
    const cards = container.querySelectorAll('.connector-card-col');
    cards.forEach(card => {
        existingNames.add(card.getAttribute('data-name'));
    });

    // 2. Add New Cards
    list.forEach((item, index) => {
        if (!existingNames.has(item.name)) {
            // Create new
            const uid = item.name.replace(/[^a-zA-Z0-9]/g, '_');
            const html = createConnectorHtml(item.name, item.has_config, uid);
            container.insertAdjacentHTML('beforeend', html);
        } else {
            const existingCard = container.querySelector(`.connector-card-col[data-name="${item.name}"]`);
            const wasValid = existingCard.getAttribute('data-valid') === 'true';

            if (wasValid !== item.has_config) {
                const uid = item.name.replace(/[^a-zA-Z0-9]/g, '_');
                const html = createConnectorHtml(item.name, item.has_config, uid);
                existingCard.outerHTML = html;
            }
        }
    });

    // 3. Remove Deleted Cards
    const newNames = new Set(list.map(i => i.name));
    cards.forEach(card => {
        const n = card.getAttribute('data-name');
        if (!newNames.has(n)) {
            card.remove();
        }
    });

    // 4. Handle Empty State
    if (list.length === 0 && document.getElementById('connectors-list').children.length === 0) {
        document.getElementById('connectors-list').innerHTML = '<div class="col-12 text-center text-muted p-3">No connectors found.</div>';
    }
});

function createConnectorHtml(name, hasConfig, uid) {
    if (!hasConfig) {
        return `<div class="col-12 connector-card-col" data-name="${name}" data-valid="false">
            <div class="card border-secondary text-muted">
                <div class="card-body py-2">
                     <div class="d-flex align-items-center">
                        <i class="bi bi-folder text-secondary me-2"></i> 
                        <strong>${name}</strong>
                        <span class="badge bg-danger ms-2" style="font-size: 0.7em;">No docker-compose found</span>
                    </div>
                </div>
            </div>
         </div>`;
    }

    return `<div class="col-12 connector-card-col" data-name="${name}" data-valid="true">
        <div class="card border-secondary">
            <div class="card-body d-flex justify-content-between align-items-center py-2">
                <div class="d-flex align-items-center">
                    <i class="bi bi-plug-fill text-info me-2"></i> 
                    <strong>${name}</strong>
                    <span id="status-connector_${name}" class="badge bg-secondary ms-2" style="font-size: 0.7em;">Checking...</span>
                </div>
                <div>
                    <button class="btn btn-sm btn-outline-primary me-1" onclick="sendAction('connector', 'up', '${name}')">Start</button>
                    <button class="btn btn-sm btn-outline-danger me-1" onclick="sendAction('connector', 'down', '${name}')">Stop</button>
                    <button class="btn btn-sm btn-outline-light" onclick="openEditor('${name}', '${uid}')"><i class="bi bi-pencil-square"></i> Edit</button>
                </div>
            </div>
        </div>
    </div>`;
}
