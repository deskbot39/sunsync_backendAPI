const sideTabs = document.querySelectorAll('.side-tabs');
if (!(localStorage.getItem('sunSyncAccessToken') || sessionStorage.getItem('sunSyncAccessToken'))) {
    window.location.replace('./login.html');
}
const contentPanels = document.querySelectorAll('.tab-panel')
const dashboard = document.querySelector('.container');
const sidebarBackdrop = document.querySelector('.sidebar-backdrop');
const logoutButton = document.querySelector('.logout-button');
const menuToggle = document.querySelector('.menu-toggle');
const dashboardContent = document.querySelector('#dashboard-content');
let lastFocusedElement = null;
const API_BASE_URL = '';  // the pages are served by the backend itself
// Times and sales days follow the kiosk's location, not the admin's browser.
const KIOSK_TIME_ZONE = 'Asia/Manila';
function fetchDownload(path) {
    return fetch(`${API_BASE_URL}${path}`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
}
// Battery percentage is linear from 0 V (0%) to 3.6 V (100%).
const BATTERY_EMPTY_V = 0.0;
const BATTERY_FULL_V = 3.6;
const BATTERY_DEMO_MODE = new URLSearchParams(window.location.search).get('demo') === 'battery';
const SETTINGS_ID = 1;
let routerIsOff = false;
const SALES_PAGE_SIZE = 5;
const WIFI_PAGE_SIZE = 5;
const WIFI_AP_PAGE_SIZE = 3;
const LIVE_WIFI_NAME_PREFIX = 'sunSyncApClientName:';
let salesTransactions = [];
let salesPage = 1;
let wifiClients = [];
let wifiApClients = [];
const OVERVIEW_PAGE_SIZE = 5;
let wifiPage = 1;
let chargingSessions = [];
let chargingPage = 1;
let wifiApPage = 1;
let chargingChart;
let overviewActivity = [];
let overviewPage = 1;
let solarChart;
let salesChart;
let wifiDataChart;
let sensorRequestFailed = false;
let availableBackups = [];
let systemUptimeAtResponse = null;
let systemUptimeResponseAt = 0;
let systemHealthPollTimer;
const settingsDialog = document.querySelector('#settings-dialog');
const settingsForm = document.querySelector('#settings-form');
const wifiRateInput = settingsForm.elements.namedItem('wifi_rate_per_coin');
const chargingRateInput = settingsForm.elements.namedItem('charging_rate_per_coin');
const settingsStatus = document.querySelector('#settings-form-status');
const settingsToast = document.querySelector('#settings-toast');

function showToast(message, type = 'success') {
    if (!settingsToast) return;

    settingsToast.textContent = message;
    settingsToast.dataset.type = type;
    settingsToast.classList.add('show');

    window.clearTimeout(showToast.timerId);
    showToast.timerId = window.setTimeout(() => {
        settingsToast.classList.remove('show');
    }, 2600);
}

function closeSidebar(restoreFocus = true) {
    dashboard.classList.remove('menu-open');
    menuToggle.setAttribute('aria-expanded', 'false');
    dashboardContent.inert = false;
    dashboardContent.removeAttribute('aria-hidden');

    if (restoreFocus && lastFocusedElement) {
        lastFocusedElement.focus();
    }
}

function openSidebar() {
    if (!window.matchMedia('(max-width: 760px)').matches) return;
    lastFocusedElement = document.activeElement;
    dashboard.classList.add('menu-open');
    menuToggle.setAttribute('aria-expanded', 'true');
    dashboardContent.inert = true;
    dashboardContent.setAttribute('aria-hidden', 'true');
    sideTabs[0].focus();
}
menuToggle.addEventListener('click', () => {
    if (dashboard.classList.contains('menu-open')) {
        closeSidebar();
    } else {
        openSidebar();
    }
});

sideTabs.forEach(item =>{
    item.addEventListener('click', () => {
        const targetTab = item.getAttribute('data-target');
        switchTab(targetTab);
        localStorage.setItem('activeTab', targetTab);
    });
});

document.addEventListener('touchstart', event => {
    const touchList = event.changedTouches || event.touches;
    const touch = touchList && touchList[0];
    if (!touch) {
        return;
    }
    touchStartX = touch.clientX;
    touchStartY = touch.clientY;
}, { passive: true });

document.addEventListener('touchend', event => {
    const touchList = event.changedTouches || event.touches;
    const touch = touchList && touchList[0];
    if (!touch) {
        return;
    }
    const horizontalDistance = touch.clientX - touchStartX;
    const verticalDistance = Math.abs(touch.clientY - touchStartY);

    if (touchStartX <= 32 && horizontalDistance >= 60 && horizontalDistance > verticalDistance * 1.25) {
        openSidebar();
    }
}, { passive: true });

sidebarBackdrop.addEventListener('click', closeSidebar);

logoutButton.addEventListener('click', async () => {
    const token = getAccessToken();
    logoutButton.disabled = true;
    // Revoke the token on the server too; leave the page even if the request fails.
    if (token) {
        try {
            await fetch(`${API_BASE_URL}/auth/logout`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
            });
        } catch (error) {
            console.error(error);
        }
    }
    localStorage.removeItem('activeTab');
    localStorage.removeItem('sunSyncAccessToken');
    sessionStorage.removeItem('sunSyncAccessToken');
    window.location.href = './login.html';
});

document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
        closeSidebar();
    }
});

function switchTab(targetId) {
    sideTabs.forEach(tab => tab.classList.remove('active'));
    contentPanels.forEach(panel => panel.classList.remove('active'));

    const targetTab = document.querySelector(`.side-tabs[data-target="${targetId}"]`);
    const targetPanel = document.getElementById(targetId);

    if (targetTab && targetPanel) {
        targetTab.classList.add('active');
        targetPanel.classList.add('active');
        closeSidebar(false);
    }
}

function getAccessToken() {
    return localStorage.getItem('sunSyncAccessToken') ||
        sessionStorage.getItem('sunSyncAccessToken');
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-PH', {
        style: 'currency',
        currency: 'PHP',
    }).format(Number(amount));
}

// The API stores UTC but SQLite drops the zone, so a bare timestamp must be read as UTC.
function parseServerTime(timestamp) {
    const text = String(timestamp);
    return new Date(/(Z|[+-]\d\d:?\d\d)$/.test(text) ? text : `${text}Z`);
}

function formatTransactionTime(timestamp) {
    return new Intl.DateTimeFormat([], {
        timeZone: KIOSK_TIME_ZONE,
        dateStyle: 'short',
        timeStyle: 'short',
    }).format(parseServerTime(timestamp));
}

// Totals come from the server, which counts every transaction (not just the latest page)
// and groups days in the kiosk's time zone.
function renderSalesSummary(summary) {
    document.querySelector('#sales-today-value').textContent = formatCurrency(summary.today_total);
    document.querySelector('#sales-count-value').textContent = summary.transaction_count;
    document.querySelector('#sales-month-value').textContent = formatCurrency(summary.month_total);
}

function renderSalesTable() {
    const tableBody = document.querySelector('#recent-transactions-body');
    tableBody.replaceChildren();
    const totalPages = Math.max(1, Math.ceil(salesTransactions.length / SALES_PAGE_SIZE));
    salesPage = Math.min(salesPage, totalPages);
    if (!salesTransactions.length) {
        tableBody.innerHTML = '<tr><td colspan="5">No transactions recorded.</td></tr>';
    } else {
        const start = (salesPage - 1) * SALES_PAGE_SIZE;
        salesTransactions.slice(start, start + SALES_PAGE_SIZE).forEach(transaction => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td data-label="Time">${formatTransactionTime(transaction.timestamp)}</td>
                <td data-label="Device">${escapeHtml(transaction.device_id || 'Unknown')}</td>
                <td data-label="Type">${escapeHtml(transaction.transaction_type)}</td>
                <td data-label="Coins">${escapeHtml(transaction.coin_denomination)} x ${escapeHtml(transaction.coins_count)}</td>
                <td data-label="Amount">${formatCurrency(transaction.total_amount)}</td>
            `;
            tableBody.appendChild(row);
        });
    }

    document.querySelector('#sales-page-status').textContent =
        `Page ${salesPage} of ${totalPages}`;
    document.querySelector('#sales-previous').disabled = salesPage === 1;
    document.querySelector('#sales-next').disabled = salesPage === totalPages;
}

function renderSalesChart(summary) {
    const canvas = document.querySelector('#sales-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    const days = summary.daily || [];
    const totals = days.map(day => Number(day.total));
    if (salesChart) salesChart.destroy();
    salesChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: days.map(day => new Intl.DateTimeFormat([], {
                month: 'short', day: 'numeric', timeZone: 'UTC',
            }).format(new Date(`${day.date}T00:00:00Z`))),
            datasets: [{
                label: 'Sales (PHP)',
                data: totals,
                borderColor: '#f59e0b',
                backgroundColor: 'rgba(245, 158, 11, 0.45)',
                borderWidth: 1,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: true } },
            plugins: { legend: { position: 'bottom' } },
        },
    });
}

function renderSales(transactions, summary) {
    salesTransactions = transactions;
    renderSalesSummary(summary);
    renderSalesChart(summary);
    renderSalesTable();
}

async function loadSales() {
    const response = await fetch(`${API_BASE_URL}/transactions/?limit=500`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Sales request failed (${response.status})`);
    const summaryResponse = await fetch(`${API_BASE_URL}/transactions/summary`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (!summaryResponse.ok) throw new Error(`Sales summary request failed (${summaryResponse.status})`);
    renderSales(await response.json(), await summaryResponse.json());
}

async function loadSettingsForm() {
    settingsStatus.textContent = 'Loading settings...';
    const response = await fetch(`${API_BASE_URL}/settings/${SETTINGS_ID}`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Settings request failed (${response.status})`);
    const settings = await response.json();
    Object.entries(settings).forEach(([key, value]) => {
        const field = settingsForm.elements.namedItem(key);
        if (field) field.value = value;
    });
    chargingRateInput.value = wifiRateInput.value;
    settingsStatus.textContent = '';
}

function clearSettingsStatus() {
    settingsStatus.textContent = '';
}

function renderRouterPower(state) {
    const button = document.querySelector('#router-power-toggle');
    routerIsOff = state.power === 'off';
    button.disabled = false;
    button.textContent = routerIsOff ? 'Turn Router ON' : 'Turn Router OFF';
    document.querySelector('#router-power-status').textContent = routerIsOff
        ? `Router is OFF. It switches back on automatically in about ${Math.ceil(state.off_seconds_remaining / 60)} min.`
        : 'Router is ON.';
}

async function loadRouterPower() {
    const response = await fetch(`${API_BASE_URL}/devices/router`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (!response.ok) throw new Error(`Router state request failed (${response.status})`);
    renderRouterPower(await response.json());
}

document.querySelector('#router-power-toggle').addEventListener('click', async event => {
    const button = event.currentTarget;
    const turnOff = !routerIsOff;
    if (turnOff && !window.confirm('Turn the router OFF? Everyone loses internet until it is turned back on (automatically after 10 minutes).')) {
        return;
    }
    button.disabled = true;
    const status = document.querySelector('#router-power-status');
    try {
        const response = await fetch(`${API_BASE_URL}/devices/router`, {
            method: 'POST',
            headers: {
                Authorization: `Bearer ${getAccessToken()}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ power: turnOff ? 'off' : 'on' }),
        });
        if (!response.ok) throw new Error(`Router command failed (${response.status})`);
        renderRouterPower(await response.json());
        status.textContent += ' The kiosk applies it within about 15 seconds.';
    } catch (error) {
        console.error(error);
        status.textContent = error.message;
        button.disabled = false;
    }
});

document.querySelector('#open-settings').addEventListener('click', () => {
    settingsDialog.showModal();
    loadRouterPower().catch(error => {
        console.error(error);
        document.querySelector('#router-power-status').textContent = 'Unable to load router state.';
    });
    loadSettingsForm().catch(error => {
        console.error(error);
        settingsStatus.textContent = 'Unable to load settings.';
    });
    loadBackupStatus().catch(error => {
        console.error(error);
        document.querySelector('#backup-recovery-status').textContent =
            'Unable to load available backups.';
    });
});

function closeSettings() {
    settingsDialog.close();
    clearSettingsStatus();
    document.querySelector('#backup-recovery-status').textContent = '';
}

document.querySelector('#close-settings').addEventListener('click', closeSettings);
document.querySelector('#cancel-settings').addEventListener('click', closeSettings);

wifiRateInput.addEventListener('input', () => {
    chargingRateInput.value = wifiRateInput.value;
});

settingsForm.addEventListener('submit', async event => {
    event.preventDefault();
    settingsStatus.textContent = 'Saving settings...';
    const formData = new FormData(settingsForm);
    const updates = {
        wifi_rate_per_coin: Number(formData.get('wifi_rate_per_coin')),
        charging_rate_per_coin: Number(formData.get('charging_rate_per_coin')),
    };

    try {
        const response = await fetch(`${API_BASE_URL}/settings/${SETTINGS_ID}`, {
            method: 'PATCH',
            headers: {
                Authorization: `Bearer ${getAccessToken()}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(updates),
        });
        if (response.status === 401) {
            localStorage.removeItem('sunSyncAccessToken');
            sessionStorage.removeItem('sunSyncAccessToken');
            window.location.href = './login.html';
            return;
        }
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `Settings save failed (${response.status})`);
        }
        settingsStatus.textContent = 'Settings saved.';
        showToast('System settings updated successfully.', 'success');
        setTimeout(closeSettings, 700);
    } catch (error) {
        console.error(error);
        settingsStatus.textContent = error.message;
        showToast(error.message || 'Unable to save settings.', 'error');
    }
});

function formatSessionDuration(seconds) {
    const minutes = Math.floor(Number(seconds) / 60);
    return `${minutes} min`;
}

function renderChargingSummary(summary) {
    document.querySelector('#charging-used-value').textContent =
        `${summary.used_ports}/${summary.used_ports + summary.inactive_ports}`;
    document.querySelector('#charging-total-value').textContent =
        String(summary.total_sessions ?? 0);
    document.querySelector('#charging-average-value').textContent =
        formatSessionDuration(summary.average_session_seconds);

    chargingSessions = Array.isArray(summary.sessions) ? summary.sessions : [];
    renderChargingTable();

    const chartData = [summary.used_ports, summary.inactive_ports];
    if (chargingChart) {
        chargingChart.data.datasets[0].data = chartData;
        chargingChart.update();
        return;
    }

    const canvas = document.querySelector('#charging-port-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    chargingChart = new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: ['In use', 'Inactive'],
            datasets: [{
                data: chartData,
                backgroundColor: ['#0284c7', '#64748b'],
                borderColor: '#0f172a',
                borderWidth: 3,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'bottom' },
            },
        },
    });
}

function renderChargingTable() {
    const tableBody = document.querySelector('#charging-sessions-body');
    tableBody.replaceChildren();
    const totalPages = Math.max(1, Math.ceil(chargingSessions.length / WIFI_PAGE_SIZE));
    chargingPage = Math.min(chargingPage, totalPages);
    if (!chargingSessions.length) {
        tableBody.innerHTML = '<tr><td colspan="5">No charging sessions recorded.</td></tr>';
    } else {
        const start = (chargingPage - 1) * WIFI_PAGE_SIZE;
        chargingSessions.slice(start, start + WIFI_PAGE_SIZE).forEach(item => {
            const started = parseServerTime(item.session_start);
            const row = document.createElement('tr');
            row.innerHTML = `
                <td data-label="Device">${escapeHtml(item.device_id)}</td>
                <td data-label="Started">${Number.isNaN(started.getTime()) ? '--' : started.toLocaleString([], { timeZone: KIOSK_TIME_ZONE })}</td>
                <td data-label="Status">${item.session_active ? 'Active' : 'Inactive'}</td>
                <td data-label="Duration">${formatSessionDuration(item.duration_seconds || 0)}</td>
                <td data-label="Action">${item.session_active && item.id
                    ? `<button type="button" class="param-btn stop-session-btn" data-session-id="${escapeHtml(item.id)}">Stop</button>`
                    : ''}</td>
            `;
            tableBody.appendChild(row);
        });
    }
    document.querySelector('#charging-page-status').textContent =
        `Page ${chargingPage} of ${totalPages}`;
    document.querySelector('#charging-previous').disabled = chargingPage === 1;
    document.querySelector('#charging-next').disabled = chargingPage === totalPages;
}

async function loadChargingSummary() {
    const response = await fetch(`${API_BASE_URL}/sessions/charging-summary`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Charging request failed (${response.status})`);
    renderChargingSummary(await response.json());
}

function renderSolarSummary(summary) {
    const canvas = document.querySelector('#solar-output-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (solarChart) solarChart.destroy();
    solarChart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: summary.history_labels.map(label =>
                new Intl.DateTimeFormat([], { timeStyle: 'short', timeZone: KIOSK_TIME_ZONE }).format(parseServerTime(label))
            ),
            datasets: [{
                label: 'Output (W)',
                data: summary.output_history,
                borderColor: '#f59e0b',
                backgroundColor: 'rgba(245, 158, 11, 0.18)',
                fill: true,
                tension: 0.25,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: true } },
            plugins: { legend: { position: 'bottom' } },
        },
    });
}

function formatSensorReading(value, precision, unit) {
    const reading = Number(value);
    if (value === null || value === undefined || !Number.isFinite(reading)) {
        return 'Unavailable';
    }
    return `${reading.toFixed(precision)} ${unit}`;
}

let batterySensorPercent = null;

function sensorBatteryPercent(ina219) {
    const voltage = Number(ina219.bus_voltage_v);
    if (!ina219.available || ina219.bus_voltage_v === null ||
        ina219.bus_voltage_v === undefined || !Number.isFinite(voltage)) {
        return null;
    }
    return Math.min(
        100,
        Math.max(0, (voltage - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V) * 100)
    );
}

function batteryPercentText() {
    return batterySensorPercent === null ? 'Unavailable' : `${batterySensorPercent.toFixed(1)}%`;
}

function renderChargingBattery(ina219) {
    batterySensorPercent = sensorBatteryPercent(ina219);
    const value = document.querySelector('#charging-battery-value');
    const voltageValue = document.querySelector('#charging-battery-voltage');
    const currentValue = document.querySelector('#charging-battery-current');
    const powerValue = document.querySelector('#charging-battery-power');
    const bar = document.querySelector('#charging-battery-bar');
    const fill = document.querySelector('#charging-battery-fill');
    const status = document.querySelector('#charging-battery-status');
    const voltage = Number(ina219.bus_voltage_v);
    currentValue.textContent = formatSensorReading(ina219.current_ma, 1, 'mA');
    powerValue.textContent = formatSensorReading(ina219.power_w, 3, 'W');
    const available = ina219.available &&
        ina219.bus_voltage_v !== null &&
        ina219.bus_voltage_v !== undefined &&
        Number.isFinite(voltage);

    if (!available) {
        value.textContent = 'Unavailable';
        voltageValue.textContent = 'Unavailable';
        fill.style.width = '0%';
        bar.setAttribute('aria-valuenow', '0');
        bar.setAttribute('aria-valuetext', 'Battery reading unavailable');
        status.textContent = 'INA219 reading unavailable';
        status.dataset.state = 'unavailable';
        return;
    }

    const percentage = Math.min(
        100,
        Math.max(0, (voltage - BATTERY_EMPTY_V) / (BATTERY_FULL_V - BATTERY_EMPTY_V) * 100)
    );
    value.textContent = `${percentage.toFixed(1)}%`;
    voltageValue.textContent = `${voltage.toFixed(2)} V`;
    fill.style.width = `${percentage}%`;
    bar.setAttribute('aria-valuenow', percentage.toFixed(0));
    bar.setAttribute(
        'aria-valuetext',
        `Estimated battery charge ${percentage.toFixed(0)} percent at ${voltage.toFixed(2)} volts`
    );
    status.textContent = voltage < BATTERY_EMPTY_V
        ? `Below ${BATTERY_EMPTY_V.toFixed(1)} V minimum`
        : 'Voltage-based estimate';
    status.dataset.state = voltage < BATTERY_EMPTY_V ? 'unavailable' : 'online';
}

function renderDeviceSensors(data) {
    const panelIna219 = data.ina219_panel || { available: false };
    document.querySelector('#sensor-bus-voltage').textContent =
        formatSensorReading(panelIna219.bus_voltage_v, 2, 'V');
    document.querySelector('#sensor-current').textContent =
        formatSensorReading(panelIna219.current_ma, 1, 'mA');
    document.querySelector('#sensor-power').textContent =
        formatSensorReading(panelIna219.power_w, 3, 'W');

    const status = document.querySelector('#sensor-status');
    status.textContent = panelIna219.available
        ? 'Panel INA219 online'
        : 'Panel INA219 not detected';
    status.dataset.state = panelIna219.available ? 'online' : 'unavailable';
    document.querySelector('#sensor-updated-at').textContent =
        new Date().toLocaleTimeString();
}

async function refreshDeviceSensors() {
    if (document.hidden) {
        window.setTimeout(refreshDeviceSensors, 5000);
        return;
    }
    try {
        const response = await fetch(`${API_BASE_URL}/status/sensors`, {
            cache: 'no-store',
            headers: { Authorization: `Bearer ${getAccessToken()}` },
        });
        if (response.status === 401) {
            localStorage.removeItem('sunSyncAccessToken');
            sessionStorage.removeItem('sunSyncAccessToken');
            window.location.href = './login.html';
            return;
        }
        if (!response.ok) throw new Error(`Sensor request failed (${response.status})`);
        const data = await response.json();
        if (!data.ina219) {
            throw new Error('Sensor response is missing battery INA219 data');
        }
        renderDeviceSensors(data);
        sensorRequestFailed = false;
    } catch (error) {
        if (!sensorRequestFailed) console.error(error);
        sensorRequestFailed = true;
        const status = document.querySelector('#sensor-status');
        status.textContent = 'Sensor data unavailable';
        status.dataset.state = 'unavailable';
        document.querySelector('#sensor-bus-voltage').textContent = 'Unavailable';
        document.querySelector('#sensor-current').textContent = 'Unavailable';
        document.querySelector('#sensor-power').textContent = 'Unavailable';
        document.querySelector('#sensor-updated-at').textContent = '--';
    } finally {
        window.setTimeout(refreshDeviceSensors, 5000);
    }
}

// The charging tab shows the same backend battery sensor as the overview battery card.
async function refreshBatterySensor() {
    if (document.hidden) {
        window.setTimeout(refreshBatterySensor, 5000);
        return;
    }
    try {
        const response = await fetch(`${API_BASE_URL}/status/sensors`, {
            cache: 'no-store',
            headers: { Authorization: `Bearer ${getAccessToken()}` },
        });
        if (response.status === 401) {
            localStorage.removeItem('sunSyncAccessToken');
            sessionStorage.removeItem('sunSyncAccessToken');
            window.location.href = './login.html';
            return;
        }
        if (!response.ok) throw new Error(`Battery sensor request failed (${response.status})`);
        const data = await response.json();
        renderChargingBattery(data.ina219 || { available: false, bus_voltage_v: null });
        await loadOverviewSummary();
    } catch (error) {
        console.error(error);
        renderChargingBattery({ available: false, bus_voltage_v: null });
    }
    window.setTimeout(refreshBatterySensor, 5000);
}

async function loadSolarSummary() {
    const response = await fetch(`${API_BASE_URL}/status/solar-summary`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Solar request failed (${response.status})`);
    renderSolarSummary(await response.json());
}

function formatDataSize(kb) {
    const mb = Number(kb) / 1024;
    return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

function renderWifiUsage(usage) {
    const now = new Date();
    const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const daily = new Array(daysInMonth).fill(0);
    let sessions = 0;
    usage.forEach(point => {
        const when = parseServerTime(point.session_start);
        if (when.getFullYear() !== now.getFullYear() || when.getMonth() !== now.getMonth()) return;
        daily[when.getDate() - 1] += Number(point.data_download_kb) / 1024;
        sessions += 1;
    });
    const totalMb = daily.reduce((sum, value) => sum + value, 0);
    document.querySelector('#wifi-data-value').textContent = formatDataSize(totalMb * 1024);
    document.querySelector('#wifi-avg-value').textContent =
        sessions ? formatDataSize((totalMb * 1024) / sessions) : '0.0 MB';
    document.querySelector('#wifi-avg-sub').textContent =
        `Per session (${sessions} this month), ${formatDataSize((totalMb * 1024) / now.getDate())}/day`;

    const canvas = document.querySelector('#wifi-data-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (wifiDataChart) wifiDataChart.destroy();
    wifiDataChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: daily.map((_, index) => String(index + 1)),
            datasets: [{
                label: 'Downloaded (MB)',
                data: daily.map(value => Number(value.toFixed(2))),
                borderColor: '#0ea5e9',
                backgroundColor: 'rgba(14, 165, 233, 0.45)',
                borderWidth: 1,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: true } },
            plugins: { legend: { position: 'bottom' } },
        },
    });
}

function renderWifiSummary(summary) {
    document.querySelector('#wifi-total-value').textContent = summary.total_sessions;
    renderWifiUsage(summary.usage || []);

    wifiClients = summary.clients;
    renderWifiTable();
}

function readApClientNames() {
    const names = new Map();
    for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key?.startsWith(LIVE_WIFI_NAME_PREFIX)) {
            names.set(key.slice(LIVE_WIFI_NAME_PREFIX.length), localStorage.getItem(key));
        }
    }
    return names;
}

function renderEsp32WifiClients(data) {
    const tableBody = document.querySelector('#esp32-wifi-clients-body');
    const status = document.querySelector('#esp32-wifi-status');
    wifiApClients = Array.isArray(data.clients) ? data.clients : [];
    const totalPages = Math.max(1, Math.ceil(wifiApClients.length / WIFI_AP_PAGE_SIZE));
    wifiApPage = Math.min(wifiApPage, totalPages);
    const pageStart = (wifiApPage - 1) * WIFI_AP_PAGE_SIZE;
    const clients = wifiApClients.slice(pageStart, pageStart + WIFI_AP_PAGE_SIZE);
    const names = readApClientNames();
    document.querySelector('#wifi-ap-active-value').textContent =
        Number.isInteger(data.client_count) ? String(data.client_count) : String(wifiApClients.length);
    status.textContent = data.traffic_counters_available
        ? 'Live AP clients. Device names are saved in this browser.'
        : 'Live AP clients. Data usage counters are not available in the current ESP32 SDK; device names are saved in this browser.';
    if (tableBody.contains(document.activeElement)) return;

    tableBody.replaceChildren();
    if (wifiApClients.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="5">No devices are connected to the ESP32 access point.</td></tr>';
    } else {
        clients.forEach(client => {
            const mac = String(client.mac || '');
            const row = document.createElement('tr');
            const nameCell = document.createElement('td');
            const nameInput = document.createElement('input');
            nameInput.type = 'text';
            nameInput.maxLength = 32;
            nameInput.autocomplete = 'off';
            nameInput.setAttribute('aria-label', `Friendly name for ${mac || 'AP client'}`);
            nameInput.placeholder = client.hostname || 'Name this device';
            nameInput.value = names.get(mac) || '';
            nameInput.addEventListener('change', () => {
                const value = nameInput.value.trim();
                if (value) {
                    localStorage.setItem(`${LIVE_WIFI_NAME_PREFIX}${mac}`, value);
                } else {
                    localStorage.removeItem(`${LIVE_WIFI_NAME_PREFIX}${mac}`);
                }
                nameInput.value = value;
            });
            nameInput.addEventListener('blur', () => {
                loadEsp32WifiClients().catch(error => console.error(error));
            }, { once: true });
            nameCell.appendChild(nameInput);
            row.appendChild(nameCell);

            const fields = [
                ['IP address', client.ip || 'Unknown'],
                ['MAC address', mac || 'Unknown'],
                ['Signal', Number.isFinite(client.rssi_dbm) ? `${client.rssi_dbm} dBm` : 'Unavailable'],
                [
                    'Downloaded',
                    Number.isFinite(client.downloaded_bytes)
                        ? `${(client.downloaded_bytes / (1024 * 1024)).toFixed(2)} MB`
                        : 'Not measured by current firmware'
                ]
            ];
            fields.forEach(([label, value]) => {
                const cell = document.createElement('td');
                cell.dataset.label = label;
                cell.textContent = value;
                row.appendChild(cell);
            });
            tableBody.appendChild(row);
        });
    }

    document.querySelector('#wifi-ap-page-status').textContent =
        `Page ${wifiApPage} of ${totalPages}`;
    document.querySelector('#wifi-ap-previous').disabled = wifiApPage === 1;
    document.querySelector('#wifi-ap-next').disabled = wifiApPage === totalPages;
}

// The kiosk pushes its live state to the backend; the dashboard reads the latest snapshot.
async function fetchLiveDevice() {
    const response = await fetch(`${API_BASE_URL}/devices/live`, {
        cache: 'no-store',
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return null;
    }
    if (!response.ok) throw new Error(`Live device request failed (${response.status})`);
    return response.json();
}

async function loadEsp32WifiClients() {
    const live = await fetchLiveDevice();
    if (!live) return;
    if (!live.online) {
        renderEsp32WifiClients({ clients: [], client_count: 0, traffic_counters_available: true });
        document.querySelector('#esp32-wifi-status').textContent =
            'The kiosk is offline, so live clients are unavailable.';
        return;
    }
    renderEsp32WifiClients({
        clients: live.clients,
        client_count: live.clients.length,
        traffic_counters_available: true,
    });
}

function renderWifiTable() {
    const tableBody = document.querySelector('#wifi-clients-body');
    tableBody.replaceChildren();
    const totalPages = Math.max(1, Math.ceil(wifiClients.length / WIFI_PAGE_SIZE));
    wifiPage = Math.min(wifiPage, totalPages);
    if (!wifiClients.length) {
        tableBody.innerHTML = '<tr><td colspan="6">No Wi-Fi sessions recorded.</td></tr>';
    } else {
        const start = (wifiPage - 1) * WIFI_PAGE_SIZE;
        wifiClients.slice(start, start + WIFI_PAGE_SIZE).forEach(client => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td data-label="Device">${escapeHtml(client.device_id)}</td>
                <td data-label="Target">${escapeHtml(client.session_target)}</td>
                <td data-label="Status">${client.session_active ? 'Active' : 'Inactive'}</td>
                <td data-label="Data">${(Number(client.data_download_kb) / 1024).toFixed(1)} MB</td>
                <td data-label="Duration">${formatSessionDuration(client.duration_seconds || 0)}</td>
                <td data-label="Action">${client.session_active && client.id
                    ? `<button type="button" class="param-btn stop-session-btn" data-session-id="${escapeHtml(client.id)}">Stop</button>`
                    : ''}</td>
            `;
            tableBody.appendChild(row);
        });
    }

    document.querySelector('#wifi-page-status').textContent =
        `Page ${wifiPage} of ${totalPages}`;
    document.querySelector('#wifi-previous').disabled = wifiPage === 1;
    document.querySelector('#wifi-next').disabled = wifiPage === totalPages;
}

async function loadWifiSummary() {
    const response = await fetch(`${API_BASE_URL}/sessions/wifi-summary`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Wi-Fi request failed (${response.status})`);
    renderWifiSummary(await response.json());
}

function formatUptime(seconds) {
    const totalSeconds = Math.max(0, Number(seconds));
    const days = Math.floor(totalSeconds / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const remainder = Math.floor(totalSeconds % 60);
    return `${days}d ${hours}h ${minutes}m ${remainder}s`;
}

function renderSystemUptime() {
    if (systemUptimeAtResponse === null) return;
    const elapsedSeconds = (Date.now() - systemUptimeResponseAt) / 1000;
    document.querySelector('#system-uptime-value').textContent =
        formatUptime(systemUptimeAtResponse + elapsedSeconds);
}

async function loadSystemHealth() {
    const live = await fetchLiveDevice();
    if (!live) return;
    const statusValue = document.querySelector('#system-status-value');
    if (!live.online || !live.health) {
        throw new Error('The kiosk has not reported to the backend recently');
    }
    const health = live.health;
    if (health.internet_sharing_enabled === true) {
        statusValue.textContent = 'Online · internet shared';
    } else if (health.internet_sharing_supported === false) {
        statusValue.textContent = 'Online · local AP only (forwarding unavailable)';
    } else if (health.upstream_connected === true) {
        statusValue.textContent = 'Online · upstream connected';
    } else {
        statusValue.textContent = 'Online · local AP only';
    }
    statusValue.dataset.state = 'online';
    systemUptimeAtResponse = health.uptime_ms / 1000;
    systemUptimeResponseAt = Date.now();
    renderSystemUptime();
}

async function refreshSystemHealth() {
    try {
        await loadSystemHealth();
        return true;
    } catch (error) {
        console.error(error);
        const statusValue = document.querySelector('#system-status-value');
        statusValue.textContent = 'Offline';
        statusValue.dataset.state = 'unavailable';
        document.querySelector('#system-uptime-value').textContent = 'Unavailable';
        systemUptimeAtResponse = null;
        return false;
    }
}

function renderOverviewSummary(summary) {
    document.querySelector('#overview-parameters-value').textContent =
        `Wi-Fi & charging: ${summary.wifi_rate_per_coin} sec/coin`;
    document.querySelector('#overview-battery-value').textContent = batteryPercentText();
    document.querySelector('#overview-wifi-value').textContent = summary.active_wifi_users;
    document.querySelector('#overview-charging-value').textContent =
        summary.active_charging_users;
    document.querySelector('#overview-sales-value').textContent =
        formatCurrency(summary.total_sales);

    overviewActivity = [
        ['Registered devices', summary.registered_devices, summary.last_status_update],
        ['Active sessions', summary.active_sessions, summary.last_status_update],
        ['Battery level', batteryPercentText(), summary.last_status_update],
        ['Total sales', formatCurrency(summary.total_sales), summary.last_status_update],
    ];
    renderOverviewActivity();
}

function renderOverviewActivity() {
    const activityBody = document.querySelector('#overview-activity-body');
    activityBody.replaceChildren();
    const totalPages = Math.max(1, Math.ceil(overviewActivity.length / OVERVIEW_PAGE_SIZE));
    overviewPage = Math.min(overviewPage, totalPages);
    const start = (overviewPage - 1) * OVERVIEW_PAGE_SIZE;
    overviewActivity.slice(start, start + OVERVIEW_PAGE_SIZE).forEach(([metric, value, updated]) => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td data-label="Metric">${escapeHtml(metric)}</td>
            <td data-label="Value">${escapeHtml(value)}</td>
            <td data-label="Updated">${formatTransactionTime(updated)}</td>
        `;
        activityBody.appendChild(row);
    });
    document.querySelector('#overview-page-status').textContent =
        `Page ${overviewPage} of ${totalPages}`;
    document.querySelector('#overview-previous').disabled = overviewPage === 1;
    document.querySelector('#overview-next').disabled = overviewPage === totalPages;
}

async function loadOverviewSummary() {
    const response = await fetch(`${API_BASE_URL}/status/overview-summary`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Overview request failed (${response.status})`);
    renderOverviewSummary(await response.json());
}

document.querySelector('#check-diagnostic').addEventListener('click', async () => {
    const healthStatus = document.querySelector('#health-status-value');
    healthStatus.textContent = 'Checking...';
    const isOnline = await refreshSystemHealth();
    healthStatus.textContent = isOnline
        ? 'Kiosk status refreshed.'
        : 'The kiosk is not reporting to the backend.';
});

document.querySelector('#export-business-data').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Preparing CSV...';

    try {
        const response = await fetchDownload('/status/export.csv');
        if (response.status === 401) {
            localStorage.removeItem('sunSyncAccessToken');
            sessionStorage.removeItem('sunSyncAccessToken');
            window.location.href = './login.html';
            return;
        }
        if (!response.ok) throw new Error(`Export request failed (${response.status})`);

        const blob = await response.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = 'sunsync-business-data.csv';
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(downloadUrl);
        showToast('Business data CSV downloaded.', 'success');
    } catch (error) {
        console.error(error);
        showToast('Unable to export business data.', 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Export Business Data';
    }
});

function formatBackupSize(bytes) {
    const size = Number(bytes);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatBackupDate(value) {
    if (!value) return '--';
    return formatTransactionTime(value);
}

function renderBackupStatus(status) {
    document.querySelector('#backup-frequency').value = status.frequency;
    const summary = document.querySelector('#backup-summary');
    summary.innerHTML = `
        <span>Last backup: ${formatBackupDate(status.last_backup_at)}</span>
        <span>Next backup: ${formatBackupDate(status.next_backup_at)}</span>
    `;
    const history = document.querySelector('#backup-history');
    history.replaceChildren();
    if (!status.backups.length) {
        history.innerHTML = '<li>No backups created yet.</li>';
    }
    availableBackups = status.backups;
    const recoverySelect = document.querySelector('#backup-recovery-select');
    const recoveryButton = document.querySelector('#restore-backup');
    if (recoverySelect) {
        recoverySelect.replaceChildren();
        if (!availableBackups.length) {
            recoverySelect.add(new Option('No backups available', ''));
        } else {
            availableBackups.forEach(backup => {
                recoverySelect.add(new Option(
                    `${formatBackupDate(backup.created_at)} - ${formatBackupSize(backup.size_bytes)}`,
                    backup.filename,
                ));
            });
        }
    }
    if (recoveryButton) {
        recoveryButton.disabled = !availableBackups.length;
    }
    availableBackups.forEach(backup => {
        const item = document.createElement('li');
        item.textContent = `${formatBackupDate(backup.created_at)} - ${formatBackupSize(backup.size_bytes)}`;
        history.appendChild(item);
    });
}

async function loadBackupStatus() {
    const response = await fetch(`${API_BASE_URL}/backups/status`, {
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Backup status request failed (${response.status})`);
    renderBackupStatus(await response.json());
}

document.querySelector('#backup-frequency').addEventListener('change', async event => {
    const frequency = event.currentTarget.value;
    const status = document.querySelector('#backup-status-value');
    status.textContent = 'Saving backup schedule...';
    try {
        const response = await fetch(`${API_BASE_URL}/backups/schedule`, {
            method: 'PATCH',
            headers: {
                Authorization: `Bearer ${getAccessToken()}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ frequency }),
        });
        if (response.status === 401) {
            localStorage.removeItem('sunSyncAccessToken');
            sessionStorage.removeItem('sunSyncAccessToken');
            window.location.href = './login.html';
            return;
        }
        if (!response.ok) throw new Error(`Backup schedule save failed (${response.status})`);
        status.textContent = 'Backup schedule saved.';
        await loadBackupStatus();
        showToast(`Automatic backups set to ${frequency}.`, 'success');
    } catch (error) {
        console.error(error);
        status.textContent = 'Unable to save backup schedule.';
        showToast('Unable to save backup schedule.', 'error');
    }
});

document.querySelector('#backup-now').addEventListener('click', async event => {
    const button = event.currentTarget;
    const status = document.querySelector('#backup-status-value');
    button.disabled = true;
    button.textContent = 'Creating Backup...';
    status.textContent = 'Creating database backup...';

    try {
        const response = await fetch(`${API_BASE_URL}/backups/`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${getAccessToken()}` },
        });
        if (response.status === 401) {
            localStorage.removeItem('sunSyncAccessToken');
            sessionStorage.removeItem('sunSyncAccessToken');
            window.location.href = './login.html';
            return;
        }
        if (!response.ok) throw new Error(`Backup request failed (${response.status})`);
        const backup = await response.json();
        const fileResponse = await fetchDownload(backup.download_url);
        if (!fileResponse.ok) throw new Error(`Backup download failed (${fileResponse.status})`);
        const blob = await fileResponse.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = backup.filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(downloadUrl);
        status.textContent = 'Database backup downloaded.';
        await loadBackupStatus();
        showToast('Database backup created and downloaded.', 'success');
    } catch (error) {
        console.error(error);
        status.textContent = 'Unable to create database backup.';
        showToast('Unable to create database backup.', 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Backup Database Now';
    }
});

document.querySelector('#restore-backup').addEventListener('click', async event => {
    const select = document.querySelector('#backup-recovery-select');
    const status = document.querySelector('#backup-recovery-status');
    const filename = select.value;
    if (!filename) return;
    if (!window.confirm('Recover this backup? Current data will be replaced after a safety backup is created.')) {
        return;
    }

    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Recovering Database...';
    status.textContent = 'Validating and restoring backup...';
    try {
        const response = await fetch(`${API_BASE_URL}/backups/restore`, {
            method: 'POST',
            headers: {
                Authorization: `Bearer ${getAccessToken()}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ filename }),
        });
        if (response.status === 401) {
            localStorage.removeItem('sunSyncAccessToken');
            sessionStorage.removeItem('sunSyncAccessToken');
            window.location.href = './login.html';
            return;
        }
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.detail || `Recovery failed (${response.status})`);
        }
        status.textContent = 'Database restored. Reloading dashboard...';
        showToast('Database recovered successfully.', 'success');
        window.setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
        console.error(error);
        status.textContent = error.message || 'Unable to recover database.';
        showToast(status.textContent, 'error');
        button.disabled = false;
        button.textContent = 'Recover Selected Backup';
    }
});

document.querySelector('#sales-previous').addEventListener('click', () => {
    if (salesPage > 1) {
        salesPage -= 1;
        renderSalesTable();
    }
});

document.querySelector('#sales-next').addEventListener('click', () => {
    const totalPages = Math.ceil(salesTransactions.length / SALES_PAGE_SIZE);
    if (salesPage < totalPages) {
        salesPage += 1;
        renderSalesTable();
    }
});

if (BATTERY_DEMO_MODE) {
    switchTab('solar-panel');
    renderSolarSummary({
        current_output_watts: 0,
        battery_pct: 68,
        peak_output_watts: 42,
        output_history: [42, 29, 13, 0],
        history_labels: Array.from({ length: 4 }, (_, index) =>
            new Date(Date.now() - (3 - index) * 60_000).toISOString()
        ),
    });
} else {
    switchTab(localStorage.getItem('activeTab') || 'overview-panel');
    loadSales().catch(error => {
        console.error(error);
        document.querySelector('#recent-transactions-body').innerHTML =
            '<tr><td colspan="5">Unable to load transactions.</td></tr>';
    });

    loadChargingSummary().catch(error => console.error(error));
    loadSolarSummary().catch(error => console.error(error));
    loadWifiSummary().catch(error => console.error(error));
    loadEsp32WifiClients().catch(error => {
        console.error(error);
        document.querySelector('#wifi-ap-active-value').textContent = 'Unavailable';
        document.querySelector('#esp32-wifi-status').textContent =
            'Unable to reach the ESP32 AP client list.';
        document.querySelector('#esp32-wifi-clients-body').innerHTML =
            '<tr><td colspan="5">ESP32 client data unavailable.</td></tr>';
    });
    window.setInterval(() => !document.hidden && loadEsp32WifiClients().catch(error => {
        console.error(error);
        document.querySelector('#esp32-wifi-status').textContent =
            'Unable to refresh ESP32 AP client data.';
    }), 5000);
    loadOverviewSummary().catch(error => console.error(error));
    refreshSystemHealth();
    systemHealthPollTimer = window.setInterval(() => {
        if (!document.hidden) refreshSystemHealth();
    }, 10000);
    window.setInterval(renderSystemUptime, 1000);
    loadBackupStatus().catch(error => console.error(error));
    window.setInterval(() => {
        if (document.hidden) return;
        [loadSales, loadChargingSummary, loadSolarSummary, loadWifiSummary].forEach(load =>
            load().catch(error => console.error(error)));
    }, 15000);
}

refreshDeviceSensors();
refreshBatterySensor();

document.querySelector('#overview-previous').addEventListener('click', () => {
    if (overviewPage > 1) {
        overviewPage -= 1;
        renderOverviewActivity();
    }
});

document.querySelector('#overview-next').addEventListener('click', () => {
    const totalPages = Math.ceil(overviewActivity.length / OVERVIEW_PAGE_SIZE);
    if (overviewPage < totalPages) {
        overviewPage += 1;
        renderOverviewActivity();
    }
});

async function stopWifiSessions(path) {
    const response = await fetch(`${API_BASE_URL}${path}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${getAccessToken()}` },
    });
    if (response.status === 401) {
        localStorage.removeItem('sunSyncAccessToken');
        sessionStorage.removeItem('sunSyncAccessToken');
        window.location.href = './login.html';
        return;
    }
    if (!response.ok) throw new Error(`Stop request failed (${response.status})`);
    await Promise.all([loadWifiSummary(), loadChargingSummary()]);
}

document.querySelector('#wifi-clients-body').addEventListener('click', event => {
    const button = event.target.closest('.stop-session-btn');
    if (!button) return;
    if (!window.confirm('Stop this session?')) return;
    button.disabled = true;
    stopWifiSessions(`/sessions/${button.dataset.sessionId}/end`).catch(error => {
        console.error(error);
        button.disabled = false;
        window.alert(error.message);
    });
});

document.querySelector('#stop-all-sessions').addEventListener('click', event => {
    if (!window.confirm('Stop ALL running sessions?')) return;
    const button = event.currentTarget;
    button.disabled = true;
    stopWifiSessions('/sessions/end-all/wifi')
        .catch(error => {
            console.error(error);
            window.alert(error.message);
        })
        .finally(() => { button.disabled = false; });
});

document.querySelector('#charging-sessions-body').addEventListener('click', event => {
    const button = event.target.closest('.stop-session-btn');
    if (!button) return;
    if (!window.confirm('Stop this charging session?')) return;
    button.disabled = true;
    stopWifiSessions(`/sessions/${button.dataset.sessionId}/end`).catch(error => {
        console.error(error);
        button.disabled = false;
        window.alert(error.message);
    });
});

document.querySelector('#stop-all-charging').addEventListener('click', event => {
    if (!window.confirm('Stop ALL running charging sessions?')) return;
    const button = event.currentTarget;
    button.disabled = true;
    stopWifiSessions('/sessions/end-all/charging')
        .catch(error => {
            console.error(error);
            window.alert(error.message);
        })
        .finally(() => { button.disabled = false; });
});

document.querySelector('#charging-previous').addEventListener('click', () => {
    if (chargingPage > 1) {
        chargingPage -= 1;
        renderChargingTable();
    }
});

document.querySelector('#charging-next').addEventListener('click', () => {
    if (chargingPage < Math.ceil(chargingSessions.length / WIFI_PAGE_SIZE)) {
        chargingPage += 1;
        renderChargingTable();
    }
});

document.querySelector('#wifi-previous').addEventListener('click', () => {
    if (wifiPage > 1) {
        wifiPage -= 1;
        renderWifiTable();
    }
});

document.querySelector('#wifi-next').addEventListener('click', () => {
    const totalPages = Math.ceil(wifiClients.length / WIFI_PAGE_SIZE);
    if (wifiPage < totalPages) {
        wifiPage += 1;
        renderWifiTable();
    }
});

document.querySelector('#wifi-ap-previous').addEventListener('click', () => {
    if (wifiApPage > 1) {
        wifiApPage -= 1;
        renderEsp32WifiClients({ clients: wifiApClients });
    }
});

document.querySelector('#wifi-ap-next').addEventListener('click', () => {
    const totalPages = Math.ceil(wifiApClients.length / WIFI_AP_PAGE_SIZE);
    if (wifiApPage < totalPages) {
        wifiApPage += 1;
        renderEsp32WifiClients({ clients: wifiApClients });
    }
});
