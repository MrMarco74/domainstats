let charts = {};

async function fetchData(endpoint, domain = '') {
    const url = `/stats/${endpoint}${domain ? `?domain=${domain}` : ''}`;
    const response = await fetch(url);
    return await response.json();
}

function getFlagEmoji(countryCode) {
    if (!countryCode || countryCode === 'Unknown') return '❓';
    const codePoints = countryCode
        .toUpperCase()
        .split('')
        .map(char => 127397 + char.charCodeAt());
    return String.fromCodePoint(...codePoints);
}

function calculateTrendline(data) {
    const n = data.length;
    if (n < 2) return data;
    
    let sumX = 0; sumY = 0; sumXY = 0; sumXX = 0;
    for (let i = 0; i < n; i++) {
        sumX += i;
        sumY += data[i];
        sumXY += i * data[i];
        sumXX += i * i;
    }
    
    const slope = (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX);
    const intercept = (sumY - slope * sumX) / n;
    
    const trendline = [];
    for (let i = 0; i < n; i++) {
        trendline.push(Math.max(0, slope * i + intercept));
    }
    return trendline;
}

async function updateDashboard(domain = '') {
    // Show/Hide YADS Releases tab
    const releasesBtn = document.getElementById('releases-tab-btn');
    if (!domain || domain.includes('yads')) {
        releasesBtn.style.display = 'block';
    } else {
        releasesBtn.style.display = 'none';
        if (document.getElementById('releases-tab').classList.contains('active')) {
            document.querySelector('[data-tab="overview"]').click();
        }
    }

    // 1. Overview
    try {
        const overview = await fetchData('overview', domain);
        document.getElementById('total-requests').textContent = overview.total_requests.toLocaleString();
        document.getElementById('unique-ips').textContent = overview.unique_ips.toLocaleString();
        document.getElementById('total-traffic').textContent = overview.total_traffic_mb.toLocaleString();
        document.getElementById('error-rate').textContent = overview.error_rate + '%';
        document.getElementById('last-update').textContent = 'Stand: ' + new Date().toLocaleString();

        // 2. Refresh current tab data
        const activeTab = document.querySelector('.tab-btn.active').getAttribute('data-tab');
        refreshTabData(activeTab, domain);
        
    } catch (e) {
        console.error("Dashboard update failed:", e);
    }
}

async function refreshTabData(tab, domain = '') {
    switch(tab) {
        case 'overview':
            updateOverviewCharts(domain);
            updateOverviewTables(domain);
            break;
        case 'errors':
            updateErrorTables(domain);
            break;
        case 'security':
            updateSecurityTable(domain);
            break;
        case 'insights':
            updateInsights(domain);
            break;
        case 'releases':
            updateReleases(domain);
            break;
        case 'registry':
            updateRegistry();
            break;
        case 'validator':
            updateValidatorTable(domain);
            break;
        case 'integrity':
            updateIntegrityLogs(domain);
            break;
        case 'config':
            updateConfigTable();
            break;
        case 'monitoring':
            updateMonitoring();
            break;
    }
}

async function updateOverviewCharts(domain = '') {
    // Timeseries
    const timeseries = await fetchData('timeseries', domain);
    if (charts.timeseries) charts.timeseries.destroy();
    charts.timeseries = new Chart(document.getElementById('timeseriesChart'), {
        type: 'line',
        data: {
            labels: timeseries.map(d => d.date),
            datasets: [{
                label: 'Requests',
                data: timeseries.map(d => d.count),
                borderColor: '#38bdf8',
                backgroundColor: 'rgba(56, 189, 248, 0.2)',
                fill: true,
                tension: 0.4
            }, {
                label: 'Trend',
                data: calculateTrendline(timeseries.map(d => d.count)),
                borderColor: 'rgba(251, 113, 133, 0.8)',
                borderDash: [5, 5],
                borderWidth: 2,
                pointRadius: 0,
                fill: false,
                tension: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.1)' } },
                x: { grid: { display: false } }
            }
        }
    });

    // Geo
    const geo = await fetchData('geo', domain);
    if (charts.geo) charts.geo.destroy();
    charts.geo = new Chart(document.getElementById('geoChart'), {
        type: 'doughnut',
        data: {
            labels: geo.map(d => d.country_code),
            datasets: [{
                data: geo.map(d => d.count),
                backgroundColor: ['#38bdf8', '#818cf8', '#c084fc', '#fb7185', '#fbbf24', '#34d399']
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'right' } }
        }
    });

    // Browsers
    const browsers = await fetchData('browsers', domain);
    if (charts.browsers) charts.browsers.destroy();
    charts.browsers = new Chart(document.getElementById('browserChart'), {
        type: 'bar',
        data: {
            labels: browsers.map(b => b.browser),
            datasets: [{
                label: 'Requests',
                data: browsers.map(b => b.count),
                backgroundColor: '#818cf8'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.1)' } }
            }
        }
    });
}

async function updateOverviewTables(domain = '') {
    const pages = await fetchData('pages', domain);
    const pBody = document.querySelector('#pages-table tbody');
    pBody.innerHTML = '';
    pages.forEach(p => {
        const row = `<tr><td>${p.path}</td><td>${p.count.toLocaleString()}</td></tr>`;
        pBody.innerHTML += row;
    });
}

async function updateErrorTables(domain = '') {
    const eBody = document.querySelector('#errors-table tbody');
    const ipBody = document.querySelector('#error-ips-table tbody');
    
    try {
        const errors = await fetchData('errors_detail', domain);
        eBody.innerHTML = '';
        errors.forEach(e => {
            const row = eBody.insertRow();
            
            const tdPath = row.insertCell();
            tdPath.style.fontFamily = 'monospace';
            tdPath.style.fontSize = '0.8rem';
            tdPath.style.maxWidth = '400px';
            tdPath.style.overflow = 'hidden';
            tdPath.style.textOverflow = 'ellipsis';
            tdPath.style.whiteSpace = 'nowrap';
            tdPath.textContent = e.path;
            tdPath.title = e.path;

            const tdStatus = row.insertCell();
            const span = document.createElement('span');
            span.style.color = '#fb7185';
            span.textContent = e.status;
            tdStatus.appendChild(span);

            const tdCount = row.insertCell();
            tdCount.textContent = e.count.toLocaleString();
        });

        const ips = await fetchData('error_ips', domain);
        ipBody.innerHTML = '';
        ips.forEach(i => {
            const row = ipBody.insertRow();
            const flag = getFlagEmoji(i.country_code);
            
            row.insertCell().textContent = i.ip;
            row.insertCell().textContent = `${flag} ${i.country_code}`;
            row.insertCell().textContent = i.count.toLocaleString();
        });
    } catch (e) {
        console.error("Error analysis fetch failed", e);
        const errRow = '<tr><td colspan="3" style="text-align: center; color: #fb7185;">Fehler beim Laden der Analysedaten.</td></tr>';
        eBody.innerHTML = errRow;
        ipBody.innerHTML = errRow;
    }
}

async function updateSecurityTable(domain = '') {
    const tbody = document.querySelector('#security-table tbody');
    try {
        const data = await fetchData('security_audit', domain);
        tbody.innerHTML = '';
        
        let totalThreats = 0;
        const uniqueIps = new Set();
        const pathCounts = {};

        data.forEach(item => {
            const row = tbody.insertRow();
            
            const tdThreat = row.insertCell();
            tdThreat.style.color = '#ff4d4d';
            tdThreat.style.fontWeight = 'bold';
            tdThreat.textContent = item.threat_type;

            const tdIp = row.insertCell();
            tdIp.textContent = item.ip;

            const tdCountry = row.insertCell();
            tdCountry.textContent = `${getFlagEmoji(item.country)} ${item.country}`;

            const tdPath = row.insertCell();
            tdPath.style.fontFamily = 'monospace';
            tdPath.style.fontSize = '0.9rem';
            tdPath.style.maxWidth = '400px';
            tdPath.style.overflow = 'hidden';
            tdPath.style.textOverflow = 'ellipsis';
            tdPath.style.whiteSpace = 'nowrap';
            tdPath.textContent = item.path;
            tdPath.title = item.path;

            const tdCount = row.insertCell();
            tdCount.style.textAlign = 'right';
            tdCount.textContent = item.count.toLocaleString();

            const tdAction = row.insertCell();
            const btn = document.createElement('button');
            btn.className = 'ignore-btn';
            btn.style.padding = '0.2rem 0.5rem';
            btn.style.fontSize = '0.75rem';
            btn.style.borderColor = '#94a3b8';
            btn.style.color = '#94a3b8';
            btn.textContent = 'Hide';
            btn.onclick = () => toggleFalsePositive(item.domain, item.path);
            tdAction.appendChild(btn);
            
            totalThreats += item.count;
            uniqueIps.add(item.ip);
            pathCounts[item.path] = (pathCounts[item.path] || 0) + item.count;
        });

        // Update summary cards
        document.getElementById('total-threats').textContent = totalThreats.toLocaleString();
        document.getElementById('total-threat-ips').textContent = uniqueIps.size.toLocaleString();
        
        const topPath = Object.entries(pathCounts).sort((a,b) => b[1] - a[1])[0];
        document.getElementById('top-threat-path').textContent = topPath ? topPath[0] : '-';
        document.getElementById('top-threat-path').title = topPath ? topPath[0] : '';
    } catch (e) {
        console.error("Security audit fetch failed", e);
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #fb7185;">Fehler beim Laden des Security Audits.</td></tr>';
    }
    updateFalsePositivesList();
}

async function updateInsights(domain = '') {
    // Peak Hours
    const peak = await fetchData('peak_hours', domain);
    if (charts.peakHours) charts.peakHours.destroy();
    charts.peakHours = new Chart(document.getElementById('peakHoursChart'), {
        type: 'bar',
        data: {
            labels: peak.map(p => p.hour + ':00'),
            datasets: [{
                label: 'Requests',
                data: peak.map(p => p.count),
                backgroundColor: '#34d399'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false
        }
    });

    // Bandwidth
    const bandwidth = await fetchData('bandwidth', domain);
    const bBody = document.querySelector('#bandwidth-table tbody');
    bBody.innerHTML = '';
    bandwidth.forEach(b => {
        const row = `<tr><td>${b.path}</td><td>${b.size_mb.toLocaleString()}</td></tr>`;
        bBody.innerHTML += row;
    });

    // OS & Bot Detail
    const ua = await fetchData('user_agents_detail', domain);
    if (charts.ua) charts.ua.destroy();
    charts.ua = new Chart(document.getElementById('uaChart'), {
        type: 'pie',
        data: {
            labels: [...ua.os.map(o => o.os), 'Bots', 'Humans'],
            datasets: [{
                data: [...ua.os.map(o => o.count), ua.bot_vs_human.find(b => b.is_bot)?.count || 0, ua.bot_vs_human.find(b => !b.is_bot)?.count || 0],
                backgroundColor: ['#38bdf8', '#818cf8', '#c084fc', '#fb7185', '#fbbf24', '#34d399', '#94a3b8']
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false
        }
    });

    // Referrers
    const referrers = await fetchData('referrers', domain);
    const refBody = document.querySelector('#referrers-table tbody');
    refBody.innerHTML = '';
    referrers.forEach(r => {
        const row = `<tr>
            <td style="font-size: 0.8rem; word-break: break-all;">${r.referrer}</td>
            <td style="text-align: right;">${r.count.toLocaleString()}</td>
        </tr>`;
        refBody.innerHTML += row;
    });

    // File Types
    const fileTypes = await fetchData('file_types', domain);
    renderFileTypesChart(fileTypes);
}

function renderFileTypesChart(data) {
    const ctx = document.getElementById('fileTypesChart').getContext('2d');
    if (charts.fileTypes) charts.fileTypes.destroy();

    charts.fileTypes = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(d => d.type),
            datasets: [{
                data: data.map(d => d.count),
                backgroundColor: [
                    '#00f2fe', '#4facfe', '#0072ff', '#333333', '#ff4d4d', '#ffa500', '#fbbf24'
                ],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'right', labels: { color: '#e2e8f0', boxWidth: 12 } }
            }
        }
    });
}

async function updateReleaseStallBanner() {
    const STALL_THRESHOLD_DAYS = 60;
    const banner = document.getElementById('release-stall-banner');
    if (!banner) return;

    const status = await fetchData('releases/status');
    if (!status || status.days_since_last_release == null) {
        banner.style.display = 'none';
        return;
    }

    if (status.days_since_last_release >= STALL_THRESHOLD_DAYS) {
        banner.textContent = `⚠ No new release in ${status.days_since_last_release} days (last: ${status.latest_version}, ${status.latest_date})`;
        banner.style.display = 'block';
    } else {
        banner.style.display = 'none';
    }
}

async function updateReleases(domain = '') {
    const timeseries = await fetchData('timeseries', domain);
    const releases = await fetchData('releases', domain);
    const downloads = await fetchData('release_downloads', domain);
    updateReleaseStallBanner();

    const rBody = document.querySelector('#releases-table tbody');
    if (rBody) {
        rBody.innerHTML = '';
        
        // Sort releases descending by date
        releases.sort((a, b) => new Date(b.date) - new Date(a.date));
        
        releases.forEach(r => {
            // Find downloads for this version
            const versionStr = r.version.startsWith('v') ? r.version : 'v' + r.version;
            const matchingPath = Object.keys(downloads).find(p => p.includes(versionStr));
            let count = 0;
            if (matchingPath) {
                count = downloads[matchingPath].reduce((sum, s) => sum + s.count, 0);
            }
            
            const countStr = count > 0 ? count.toLocaleString() : '-';
            const row = `<tr>
                <td>${r.date}</td>
                <td><span class="status-badge status-dir" style="padding: 0.1rem 0.4rem;">${r.version}</span></td>
                <td>${r.title}</td>
                <td style="text-align: right; font-weight: 700; color: var(--accent-color);">${countStr}</td>
            </tr>`;
            rBody.innerHTML += row;
        });
    }

    if (charts.releases) charts.releases.destroy();
    const maxTraffic = Math.max(...timeseries.map(d => d.count)) || 1;
    
    charts.releases = new Chart(document.getElementById('releasesChart'), {
        type: 'line',
        data: {
            labels: timeseries.map(d => d.date),
            datasets: [
                {
                    label: 'Traffic',
                    data: timeseries.map(d => d.count),
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56, 189, 248, 0.1)',
                    fill: true,
                    tension: 0.4
                },
                {
                    label: 'Releases',
                    data: timeseries.map(d => {
                        const rel = releases.find(r => r.date === d.date);
                        return rel ? maxTraffic : null;
                    }),
                    type: 'bar',
                    backgroundColor: 'rgba(251, 191, 36, 0.5)',
                    barThickness: 2
                },
                {
                    label: 'Downloads',
                    data: timeseries.map(d => d.downloads || 0),
                    borderColor: '#fb923c',
                    backgroundColor: 'rgba(251, 146, 60, 0.1)',
                    fill: false,
                    tension: 0.4,
                    borderWidth: 3,
                    pointRadius: 2
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            if (context.dataset.label === 'Releases') {
                                const rel = releases.find(r => r.date === context.label);
                                return rel ? `Release: ${rel.version} - ${rel.title}` : '';
                            }
                            if (context.dataset.label === 'Downloads') {
                                return `Downloads: ${context.raw}`;
                            }
                            return `Traffic: ${context.raw}`;
                        }
                    }
                }
            }
        }
    });

    // 3. Download Statistics
    const dContainer = document.getElementById('release-downloads-container');
    if (dContainer) {
        dContainer.innerHTML = '';
        
        Object.entries(downloads).forEach(([path, stats]) => {
            const card = document.createElement('div');
            card.className = 'stat-card';
            card.style.background = 'rgba(255,255,255,0.03)';
            card.style.border = '1px solid rgba(255,255,255,0.05)';
            card.style.padding = '1.5rem';
            
            const fileName = path.split('/').pop();
            const isMainRelease = fileName.match(/^yads_v.*_customer_pkg\.zip$/i);
            const typeLabel = isMainRelease ? 'Release' : 'Addon';
            const typeColor = isMainRelease ? 'var(--accent-color)' : '#94a3b8';
            
            let total = stats.reduce((sum, s) => sum + s.count, 0);
            
            let html = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem;">
                    <div class="stat-label" style="font-family: monospace; font-size: 0.8rem; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-grow: 1; margin-bottom: 0;" title="${path}">${fileName}</div>
                    <span style="font-size: 0.65rem; padding: 0.1rem 0.4rem; border-radius: 1rem; background: ${typeColor}22; color: ${typeColor}; border: 1px solid ${typeColor}44; margin-left: 0.5rem; white-space: nowrap;">${typeLabel}</span>
                </div>
                <div class="stat-value" style="font-size: 1.5rem; margin-bottom: 1.5rem;">${total.toLocaleString()} <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: normal;">Downloads</span></div>
                <div style="display: flex; flex-direction: column; gap: 0.8rem;">
            `;
            
            // Show top 5 countries
            stats.slice(0, 5).forEach(s => {
                const flag = getFlagEmoji(s.country);
                const percent = Math.round((s.count / total) * 100);
                html += `
                    <div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; margin-bottom: 0.3rem;">
                            <span>${flag} ${s.country}</span>
                            <span class="stat-label">${s.count.toLocaleString()} (${percent}%)</span>
                        </div>
                        <div style="height: 4px; background: rgba(255,255,255,0.05); border-radius: 2px; overflow: hidden;">
                            <div style="width: ${percent}%; height: 100%; background: var(--accent-color); border-radius: 2px;"></div>
                        </div>
                    </div>
                `;
            });
            
            html += `</div>`;
            card.innerHTML = html;
            dContainer.appendChild(card);
        });

        if (Object.keys(downloads).length === 0) {
            dContainer.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-secondary); font-style: italic;">Keine Downloads in den Logs gefunden.</div>';
        }
    }

    // 4. Adoption per release (first-download lag + top countries)
    const adoption = await fetchData('release_adoption', domain);
    const aBody = document.querySelector('#release-adoption-table tbody');
    if (aBody) {
        aBody.innerHTML = '';
        if (adoption.length === 0) {
            aBody.innerHTML = '<tr><td colspan="5" style="text-align: center; padding: 2rem; color: var(--text-secondary); font-style: italic;">Keine Download-Daten verfügbar.</td></tr>';
        }
        adoption.forEach(a => {
            const lagStr = a.days_to_first_download == null ? '-'
                : a.days_to_first_download === 0 ? 'am selben Tag'
                : `${a.days_to_first_download} Tag(e)`;
            const countriesStr = Object.entries(a.countries)
                .map(([cc, n]) => `${getFlagEmoji(cc)} ${cc} (${n})`)
                .join(', ');
            const row = `<tr>
                <td><span class="status-badge status-dir" style="padding: 0.1rem 0.4rem;">${a.version}</span></td>
                <td>${a.release_date || '-'}</td>
                <td style="text-align: right; font-weight: 700; color: var(--accent-color);">${a.total_downloads.toLocaleString()}</td>
                <td style="text-align: right;">${lagStr}</td>
                <td style="font-size: 0.85rem;">${countriesStr}</td>
            </tr>`;
            aBody.innerHTML += row;
        });
    }

    // 5. Version churn (monthly download mix across versions)
    const churn = await fetchData('release_version_churn', domain);
    const churnCanvas = document.getElementById('versionChurnChart');
    if (charts.versionChurn) charts.versionChurn.destroy();
    if (churnCanvas) {
        const months = Object.keys(churn).sort();
        const totalsByVersion = {};
        months.forEach(m => {
            Object.entries(churn[m]).forEach(([v, c]) => {
                totalsByVersion[v] = (totalsByVersion[v] || 0) + c;
            });
        });
        // Keep the chart readable: show the top 7 versions by total downloads, bucket the rest as "Andere"
        const topVersions = Object.entries(totalsByVersion)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 7)
            .map(([v]) => v);

        const palette = ['#38bdf8', '#818cf8', '#c084fc', '#fb7185', '#fbbf24', '#34d399', '#94a3b8'];
        const datasets = topVersions.map((v, i) => ({
            label: v,
            data: months.map(m => churn[m][v] || 0),
            backgroundColor: palette[i % palette.length],
        }));
        const otherData = months.map(m =>
            Object.entries(churn[m]).reduce((sum, [v, c]) => sum + (topVersions.includes(v) ? 0 : c), 0)
        );
        if (otherData.some(v => v > 0)) {
            datasets.push({ label: 'Andere', data: otherData, backgroundColor: '#475569' });
        }

        charts.versionChurn = new Chart(churnCanvas, {
            type: 'bar',
            data: { labels: months, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { x: { stacked: true }, y: { stacked: true } },
                plugins: { legend: { position: 'right', labels: { color: '#e2e8f0', boxWidth: 12 } } }
            }
        });
    }
}

const REGISTRY_DOMAIN = 'registry.yads-security.com';

async function updateRegistry() {
    // 1. Pulls over time - reuses the generic /stats/timeseries endpoint, just scoped
    // to the registry "domain" (registry pulls are logged with this as their domain).
    const timeseries = await fetchData('timeseries', REGISTRY_DOMAIN);
    if (charts.registryTimeseries) charts.registryTimeseries.destroy();
    const tsCanvas = document.getElementById('registryTimeseriesChart');
    if (tsCanvas) {
        charts.registryTimeseries = new Chart(tsCanvas, {
            type: 'line',
            data: {
                labels: timeseries.map(d => d.date),
                datasets: [{
                    label: 'Pulls',
                    data: timeseries.map(d => d.count),
                    borderColor: '#38bdf8',
                    backgroundColor: 'rgba(56, 189, 248, 0.1)',
                    fill: true,
                    tension: 0.4
                }]
            },
            options: { responsive: true, maintainAspectRatio: false }
        });
    }

    // 2. Pulls by image + tag
    const images = await fetchData('registry/images');
    const container = document.getElementById('registry-images-container');
    if (!container) return;
    container.innerHTML = '';

    if (images.length === 0) {
        container.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-secondary); font-style: italic;">Keine Registry-Pulls in den Logs gefunden.</div>';
        return;
    }

    images.forEach(img => {
        const card = document.createElement('div');
        card.className = 'stat-card';
        card.style.background = 'rgba(255,255,255,0.03)';
        card.style.border = '1px solid rgba(255,255,255,0.05)';
        card.style.padding = '1.5rem';

        let html = `
            <div class="stat-label" style="font-family: monospace; font-size: 0.85rem; color: var(--text-primary); margin-bottom: 0.5rem;">${img.image}</div>
            <div class="stat-value" style="font-size: 1.5rem; margin-bottom: 1.5rem;">${img.total_pulls.toLocaleString()} <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: normal;">Pulls</span></div>
            <div style="display: flex; flex-direction: column; gap: 0.8rem;">
        `;

        img.tags.slice(0, 6).forEach(t => {
            const percent = Math.round((t.count / img.total_pulls) * 100);
            const topCountry = Object.keys(t.countries)[0];
            const flag = topCountry ? getFlagEmoji(topCountry) : '';
            html += `
                <div>
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; margin-bottom: 0.3rem;">
                        <span style="font-family: monospace;">${t.tag}</span>
                        <span class="stat-label">${flag} ${t.count.toLocaleString()} (${percent}%)</span>
                    </div>
                    <div style="height: 4px; background: rgba(255,255,255,0.05); border-radius: 2px; overflow: hidden;">
                        <div style="width: ${percent}%; height: 100%; background: var(--accent-color); border-radius: 2px;"></div>
                    </div>
                </div>
            `;
        });

        html += `</div>`;
        card.innerHTML = html;
        container.appendChild(card);
    });
}

async function updateValidatorTable(domain = '') {
    const tbody = document.querySelector('#validator-table tbody');
    try {
        const data = await fetchData('validator', domain);
        tbody.innerHTML = '';
        
        if (data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 3rem; color: var(--text-secondary);">Keine zu prüfenden Fehler gefunden.</td></tr>';
            return;
        }

        data.filter(item => !item.is_ignored).forEach(item => {
            const row = tbody.insertRow();
            const statusClass = item.exists_on_server ? 'status-exists' : 'status-missing';
            const statusLabel = item.exists_on_server ? 'FILE OK' : 'MISSING';
            
            row.insertCell().textContent = item.domain;
            
            const tdPath = row.insertCell();
            tdPath.style.fontFamily = 'monospace';
            tdPath.style.fontSize = '0.8rem';
            tdPath.textContent = item.path;

            const tdStatus = row.insertCell();
            const spanStatus = document.createElement('span');
            spanStatus.style.color = '#fb7185';
            spanStatus.textContent = item.status;
            tdStatus.appendChild(spanStatus);

            row.insertCell().textContent = item.count.toLocaleString();

            const tdBadge = row.insertCell();
            const spanBadge = document.createElement('span');
            spanBadge.className = `status-badge ${statusClass}`;
            spanBadge.textContent = statusLabel;
            tdBadge.appendChild(spanBadge);

            const tdAction = row.insertCell();
            const btn = document.createElement('button');
            btn.className = 'ignore-btn';
            btn.textContent = 'Ignorieren';
            btn.onclick = () => ignoreError(item.domain, item.path, item.status);
            tdAction.appendChild(btn);
        });
    } catch (e) {
        console.error("Validator fetch failed", e);
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: #fb7185;">Fehler beim Laden der Validierung. Bitte erneut versuchen.</td></tr>';
    }
}

async function ignoreError(domain, path, status) {
    try {
        const response = await fetch('/stats/ignore_error', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domain, path, status })
        });
        if (response.ok) {
            updateValidatorTable(document.getElementById('domain-select').value);
        }
    } catch (e) {
        console.error("Failed to ignore error:", e);
    }
}
window.ignoreError = ignoreError; // Make it global for onclick
async function ignoreCurrentIP() {
    const ip = document.getElementById('current-ip').textContent;
    if (ip === 'Lade...' || ip === 'Fehler') return;
    
    await fetch('/stats/ignore_ip', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ip})
    });
    
    fetchIgnoredIPs();
    updateDashboard(document.getElementById('domain-select').value);
}

async function removeIgnoredIP(ip) {
    await fetch(`/stats/ignore_ip?ip=${encodeURIComponent(ip)}`, {
        method: 'DELETE'
    });
    fetchIgnoredIPs();
    updateDashboard(document.getElementById('domain-select').value);
}

async function fetchIgnoredIPs() {
    const ips = await fetch('/stats/ignored_ips').then(r => r.json());
    const list = document.getElementById('ignored-ips-list');
    list.innerHTML = '';
    
    if (ips.length === 0) {
        list.innerHTML = '<span class="stat-label" style="font-style: italic;">Keine IPs gefiltert.</span>';
        return;
    }

    ips.forEach(ip => {
        const badge = document.createElement('div');
        badge.style.display = 'inline-flex';
        badge.style.alignItems = 'center';
        badge.style.gap = '0.5rem';
        badge.style.background = 'var(--glass-bg)';
        badge.style.padding = '0.4rem 0.8rem';
        badge.style.borderRadius = '20px';
        badge.style.border = '1px solid var(--glass-border)';
        badge.style.fontSize = '0.85rem';
        badge.innerHTML = `
            <span>${ip}</span>
            <span onclick="removeIgnoredIP('${ip}')" style="cursor:pointer; color: #fb7185; font-weight: bold; font-size: 1.2rem; line-height: 1;">&times;</span>
        `;
        list.appendChild(badge);
    });
}

async function addManualIP() {
    const input = document.getElementById('manual-ip-input');
    const ip = input.value.trim();
    if (!ip) return;
    
    await fetch('/stats/ignore_ip', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ip})
    });
    
    input.value = '';
    fetchIgnoredIPs();
    updateDashboard(document.getElementById('domain-select').value);
}

async function refreshDailyStats() {
    const btn = document.getElementById('refresh-stats-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Bereinige...';
    
    try {
        const res = await fetch('/stats/refresh_daily', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
            alert('Statistiken erfolgreich bereinigt. Dashboard wird aktualisiert.');
            updateDashboard(document.getElementById('domain-select').value);
        } else {
            alert('Fehler: ' + data.detail);
        }
    } catch (e) {
        alert('Fehler: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

let currentScanId = null;

async function updateIntegrityLogs(domain = '') {
    const logs = await fetchData('integrity/logs', domain);
    const tbody = document.querySelector('#integrity-log-table tbody');
    tbody.innerHTML = '';
    
    if (logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 2rem; color: var(--text-secondary);">Keine Integritäts-Probleme gefunden.</td></tr>';
        return;
    }

    logs.forEach(log => {
        const tr = document.createElement('tr');
        const color = log.change_type === 'modified' ? '#f87171' : (log.change_type === 'added' ? '#4ade80' : '#94a3b8');
        
        tr.innerHTML = `
            <td><input type="checkbox" class="integrity-checkbox" data-domain="${log.domain}" data-path="${log.path}" data-hash="${log.new_checksum}" data-mtime="${log.new_mtime}"></td>
            <td>${log.domain}</td>
            <td style="font-family: monospace; font-size: 0.8rem;">${log.path}</td>
            <td><span style="color: ${color}; font-weight: bold;">${log.change_type.toUpperCase()}</span></td>
            <td class="stat-label">${new Date(log.timestamp).toLocaleString()}</td>
            <td>
                <div style="display: flex; gap: 0.5rem;">
                    ${log.change_type !== 'deleted' ? `<button onclick="viewIntegrityFile('${log.domain}', '${log.path}', '${log.new_checksum}', ${log.new_mtime})" class="ignore-btn" style="padding: 0.25rem 0.5rem; font-size: 0.8rem;">View</button>` : ''}
                    ${log.has_cache ? `<button onclick="restoreFile('${log.domain}', '${log.path}')" class="ignore-btn" style="padding: 0.25rem 0.5rem; font-size: 0.8rem; background: #10b981; border-color: #10b981; color: #fff;">Restore</button>` : ''}
                    <button onclick="acceptChange('${log.domain}', '${log.path}', '${log.new_checksum}', ${log.new_mtime})" class="save-btn" style="padding: 0.25rem 0.5rem; font-size: 0.8rem;">Accept</button>
                </div>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function startIntegrityScan(autoAccept = false) {
    const domain = document.getElementById('domain-select').value;
    const btn = document.getElementById('start-scan-btn');
    const baselineBtn = document.getElementById('baseline-scan-btn');
    const stopBtn = document.getElementById('stop-scan-btn');
    const container = document.getElementById('scan-progress-container');
    const console = document.getElementById('scan-console');
    const bar = document.getElementById('scan-progress-bar');
    
    btn.disabled = true;
    if (baselineBtn) baselineBtn.disabled = true;
    btn.textContent = 'Scan läuft...';
    stopBtn.style.display = 'block';
    container.style.display = 'block';
    console.innerHTML = 'Initialisiere Scan...<br>';
    bar.style.width = '0%';
    
    const url = `/stats/integrity/scan?auto_accept=${autoAccept}` + (domain ? `&domain=${domain}` : '');
    const res = await fetch(url, {method: 'POST'});
    const {scan_id} = await res.json();
    currentScanId = scan_id;
    
    const poll = setInterval(async () => {
        const statusRes = await fetch(`/stats/integrity/status/${currentScanId}`);
        const data = await statusRes.json();
        
        if (data.logs) {
            console.innerHTML = data.logs.map(l => `> ${l}`).join('<br>');
            console.scrollTop = console.scrollHeight;
        }
        
        if (data.status === 'finished' || data.status === 'not_found') {
            clearInterval(poll);
            btn.disabled = false;
            if (baselineBtn) baselineBtn.disabled = false;
            btn.textContent = 'Scan jetzt starten';
            stopBtn.style.display = 'none';
            document.getElementById('scan-status-text').textContent = data.status === 'finished' ? 'Scan abgeschlossen.' : 'Scan gestoppt.';
            bar.style.width = '100%';
            updateIntegrityLogs(domain);
            currentScanId = null;
        }
    }, 1000);
}

async function startBaselineScan() {
    if (confirm("Möchtest du wirklich einen Baseline-Scan durchführen? Alle aktuell gefundenen Dateien werden ohne manuelle Prüfung als vertrauenswürdig eingestuft.")) {
        startIntegrityScan(true);
    }
}

async function stopIntegrityScan() {
    if (!currentScanId) return;
    await fetch(`/stats/integrity/stop/${currentScanId}`, {method: 'POST'});
}

async function viewIntegrityFile(domain, path, new_hash, new_mtime) {
    const res = await fetch(`/stats/integrity/view?domain=${domain}&path=${path}`);
    const data = await res.json();
    
    document.getElementById('viewer-filename').textContent = `${domain}: ${path}`;
    document.getElementById('file-content-code').textContent = data.content;
    
    const acceptBtn = document.getElementById('accept-change-btn');
    acceptBtn.onclick = () => acceptChange(domain, path, new_hash, new_mtime);
    
    openModal('file-viewer-modal');
}

async function acceptChange(domain, path, new_hash, new_mtime) {
    if (!confirm(`Änderung für ${path} dauerhaft akzeptieren?`)) return;
    
    await fetch('/stats/integrity/accept', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({domain, path, new_checksum: new_hash, new_mtime: new_mtime})
    });
    
    closeModal('file-viewer-modal');
    updateIntegrityLogs(document.getElementById('domain-select').value);
}

function toggleAllIntegrity(master) {
    const checkboxes = document.querySelectorAll('.integrity-checkbox');
    checkboxes.forEach(cb => cb.checked = master.checked);
    updateBulkAcceptBtn();
}

function updateBulkAcceptBtn() {
    const checked = document.querySelectorAll('.integrity-checkbox:checked').length;
    const btn = document.getElementById('bulk-accept-btn');
    btn.style.display = checked > 0 ? 'block' : 'none';
}

async function acceptSelectedIntegrity() {
    const selected = document.querySelectorAll('.integrity-checkbox:checked');
    if (!confirm(`${selected.length} Änderungen akzeptieren?`)) return;
    
    for (const cb of selected) {
        await fetch('/stats/integrity/accept', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                domain: cb.dataset.domain,
                path: cb.dataset.path,
                new_checksum: cb.dataset.hash,
                new_mtime: parseFloat(cb.dataset.mtime)
            })
        });
    }
    
    updateIntegrityLogs(document.getElementById('domain-select').value);
    document.getElementById('select-all-integrity').checked = false;
    updateBulkAcceptBtn();
}

// Add event listeners for checkbox changes
document.addEventListener('change', e => {
    if (e.target.classList.contains('integrity-checkbox')) {
        updateBulkAcceptBtn();
    }
});

async function runLLMAnalysis(type) {
    const domain = document.getElementById('domain-select').value;
    const container = document.getElementById('insights-container');
    const loading = document.getElementById('insights-loading');
    const output = document.getElementById('insights-output');
    
    container.style.display = 'block';
    loading.style.display = 'block';
    output.style.display = 'none';
    
    try {
        const res = await fetch(`/stats/insights/${type}` + (domain ? `?domain=${domain}` : ''));
        const data = await res.json();
        
        loading.style.display = 'none';
        output.style.display = 'block';
        output.innerHTML = formatMarkdown(data.analysis);
    } catch (e) {
        loading.style.display = 'none';
        output.style.display = 'block';
        output.textContent = 'Fehler bei der Analyse: ' + e.message;
    }
}

function formatMarkdown(text) {
    // Simple markdown formatting for bold and headers
    return text
        .replace(/^### (.*$)/gim, '<h3 style="color: var(--accent-color); margin-top: 1rem;">$1</h3>')
        .replace(/^## (.*$)/gim, '<h2 style="color: var(--accent-color); margin-top: 1.5rem;">$1</h2>')
        .replace(/\*\*(.*)\*\*/gim, '<b style="color: #fff;">$1</b>')
        .replace(/^- (.*$)/gim, '<li style="margin-left: 1rem;">$1</li>');
}

window.ignoreCurrentIP = ignoreCurrentIP;
window.removeIgnoredIP = removeIgnoredIP;
window.addManualIP = addManualIP;
window.refreshDailyStats = refreshDailyStats;
window.startIntegrityScan = startIntegrityScan;
window.startBaselineScan = startBaselineScan;
window.startCachePreload = startCachePreload;
window.stopIntegrityScan = stopIntegrityScan;
window.restoreFile = restoreFile;
window.viewIntegrityFile = viewIntegrityFile;
window.runLLMAnalysis = runLLMAnalysis;
window.acceptChange = acceptChange;
window.toggleAllIntegrity = toggleAllIntegrity;
window.acceptSelectedIntegrity = acceptSelectedIntegrity;

async function updateConfigTable() {
    updateDiskUsage();
    const domains = await fetchData('domains');
    const configs = await fetchData('config');
    const settings = await fetch('/stats/settings').then(r => r.json());
    
    // IP Filter population
    fetchIgnoredIPs();
    fetch('/stats/my_ip').then(r => r.json()).then(data => {
        document.getElementById('current-ip').textContent = data.ip;
    }).catch(() => {
        document.getElementById('current-ip').textContent = 'Fehler';
    });
    
    if (settings.conn_type) {
        const radio = document.querySelector(`input[name="conn_type"][value="${settings.conn_type}"]`);
        if (radio) {
            radio.checked = true;
            toggleConnFields();
        }
    }
    if (settings.base_path) document.getElementById('global-base-path').value = settings.base_path;
    if (settings.remote_log_path) document.getElementById('remote-log-path').value = settings.remote_log_path;
    if (settings.ftp_host) document.getElementById('ftp-host').value = settings.ftp_host;
    if (settings.ftp_user) document.getElementById('ftp-user').value = settings.ftp_user;

    if (settings.key_pass) {
        document.getElementById('global-key-pass').placeholder = "******** (Gespeichert)";
    }
    if (settings.ftp_pass) {
        document.getElementById('ftp-pass').placeholder = "******** (Gespeichert)";
    }

    if (settings.ollama_url) document.getElementById('ollama-url').value = settings.ollama_url;
    if (settings.ollama_token) {
        document.getElementById('ollama-token').placeholder = "******** (Gespeichert)";
    }
    if (settings.ollama_model) {
        const select = document.getElementById('ollama-model');
        if (![...select.options].some(o => o.value === settings.ollama_model)) {
            const option = document.createElement('option');
            option.value = settings.ollama_model;
            option.textContent = settings.ollama_model;
            select.appendChild(option);
        }
        select.value = settings.ollama_model;
    }

    const tbody = document.querySelector('#config-table tbody');
    tbody.innerHTML = '';
    
    domains.forEach(d => {
        const row = tbody.insertRow();
        const currentPath = configs[d.domain] || '';
        
        row.innerHTML = `
            <td style="font-weight: 700;">${d.domain}</td>
            <td style="display: flex; gap: 0.5rem;">
                <input type="text" class="config-input" id="config-path-${d.domain.replace(/\./g, '-')}" 
                       value="${currentPath}" placeholder="/public_html/${d.domain}/">
                <button class="ignore-btn" onclick="openFileBrowser('${d.domain}', null)" title="Browse Remote Server">📂</button>
            </td>
            <td>
                <button class="save-btn" onclick="saveConfig('${d.domain}')">Save</button>
            </td>
        `;
    });
}

async function saveConfig(domain) {
    const inputId = `config-path-${domain.replace(/\./g, '-')}`;
    const path = document.getElementById(inputId).value;
    
    try {
        const response = await fetch('/stats/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domain, path })
        });
        if (response.ok) {
            alert(`Konfiguration für ${domain} gespeichert!`);
        }
    } catch (e) {
        console.error("Failed to save config:", e);
    }
}
async function saveConnectionSettings() {
    const conn_type = document.querySelector('input[name="conn_type"]:checked').value;
    const settings = {
        conn_type: conn_type,
        key_pass: document.getElementById('global-key-pass').value,
        ftp_host: document.getElementById('ftp-host').value,
        ftp_user: document.getElementById('ftp-user').value,
        ftp_pass: document.getElementById('ftp-pass').value
    };

    try {
        const response = await fetch('/stats/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        if (response.ok) {
            alert("Verbindungs-Einstellungen gespeichert!");
            if (settings.key_pass) document.getElementById('global-key-pass').value = '';
            if (settings.ftp_pass) document.getElementById('ftp-pass').value = '';
            await updateConfigTable(); // Refresh placeholders
        }
    } catch (e) {
        console.error("Failed to save connection settings:", e);
    }
}

async function savePathSettings() {
    const settings = {
        base_path: document.getElementById('global-base-path').value,
        remote_log_path: document.getElementById('remote-log-path').value
    };

    try {
        const response = await fetch('/stats/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        if (response.ok) {
            alert("Standard-Pfade gespeichert!");
            await updateConfigTable();
        }
    } catch (e) {
        console.error("Failed to save path settings:", e);
    }
}

function toggleConnFields() {
    const type = document.querySelector('input[name="conn_type"]:checked').value;
    document.getElementById('ssh-fields').style.display = type === 'ssh' ? 'block' : 'none';
    document.getElementById('ftp-fields').style.display = type === 'ftp' ? 'block' : 'none';
}

async function testConnection() {
    const resultEl = document.getElementById('test-conn-result');
    resultEl.textContent = "⏳ Testing...";
    resultEl.style.color = "var(--text-secondary)";
    
    try {
        const data = await fetch('/stats/test_connection').then(r => r.json());
        if (data.status === "success") {
            resultEl.textContent = "✅ " + data.message;
            resultEl.style.color = "#34d399";
        } else {
            resultEl.textContent = "❌ " + data.message;
            resultEl.style.color = "#fb7185";
        }
    } catch (e) {
        resultEl.textContent = "❌ Request failed: " + e.message;
        resultEl.style.color = "#fb7185";
    }
}

async function loadOllamaModels() {
    const resultEl = document.getElementById('ollama-models-result');
    const select = document.getElementById('ollama-model');
    const url = document.getElementById('ollama-url').value;
    const token = document.getElementById('ollama-token').value;

    resultEl.textContent = "⏳ Lade Modelle...";
    resultEl.style.color = "var(--text-secondary)";

    try {
        const body = {};
        if (url) body.base_url = url;
        if (token) body.token = token;

        const response = await fetch('/stats/ollama/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await response.json();

        if (!response.ok) {
            resultEl.textContent = "❌ " + (data.detail || 'Unbekannter Fehler');
            resultEl.style.color = "#fb7185";
            return;
        }

        const previousValue = select.value;
        select.innerHTML = '';
        data.models.forEach(name => {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            select.appendChild(option);
        });
        if (data.models.includes(previousValue)) {
            select.value = previousValue;
        }

        resultEl.textContent = `✅ ${data.models.length} Modelle geladen`;
        resultEl.style.color = "#34d399";
    } catch (e) {
        resultEl.textContent = "❌ Request failed: " + e.message;
        resultEl.style.color = "#fb7185";
    }
}

async function saveOllamaSettings() {
    const settings = {
        ollama_url: document.getElementById('ollama-url').value,
        ollama_token: document.getElementById('ollama-token').value,
        ollama_model: document.getElementById('ollama-model').value
    };

    try {
        const response = await fetch('/stats/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        if (response.ok) {
            alert("Ollama-Einstellungen gespeichert!");
            if (settings.ollama_token) document.getElementById('ollama-token').value = '';
            await updateConfigTable(); // Refresh placeholders + selected model
        }
    } catch (e) {
        console.error("Failed to save Ollama settings:", e);
    }
}

window.loadOllamaModels = loadOllamaModels;
window.saveOllamaSettings = saveOllamaSettings;

window.saveConfig = saveConfig; // Make it global for onclick
window.saveConnectionSettings = saveConnectionSettings;
window.savePathSettings = savePathSettings;
window.testConnection = testConnection;
window.toggleConnFields = toggleConnFields;

// SSH/FTP Browser Logic
let currentBrowserDomain = null;
let currentBrowserTargetId = null;
let currentBrowserPath = "/";

async function openFileBrowser(domain, targetId) {
    currentBrowserDomain = domain;
    currentBrowserTargetId = targetId;

    let initialPath = "/";
    if (currentBrowserTargetId) {
        const el = document.getElementById(currentBrowserTargetId);
        initialPath = el ? el.value : "/";
    } else if (currentBrowserDomain) {
        const inputId = `config-path-${currentBrowserDomain.replace(/\./g, '-')}`;
        const input = document.getElementById(inputId);
        const globalBase = document.getElementById('global-base-path').value || "/public_html";
        initialPath = input ? input.value : globalBase;
    } else {
        const el = document.getElementById('global-base-path');
        initialPath = el ? el.value : "/";
    }
    
    document.getElementById('browser-modal').style.display = 'block';
    await lsRemote(initialPath);
}

async function lsRemote(path) {
    currentBrowserPath = path;
    document.getElementById('browser-path').textContent = path;
    document.getElementById('browser-list').innerHTML = '<div style="padding: 2rem; text-align: center;">Loading...</div>';
    
    try {
        const response = await fetch(`/stats/remote_ls?path=${encodeURIComponent(path)}`);
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Request failed");
        }
        renderBrowser(data);
    } catch (e) {
        document.getElementById('browser-list').innerHTML = `<div style="padding: 2rem; text-align: center; color: #ff4d4d;">Error: ${e.message}</div>`;
    }
}

function renderBrowser(entries) {
    const list = document.getElementById('browser-list');
    list.innerHTML = '';
    
    if (!Array.isArray(entries)) {
        list.innerHTML = `<div style="padding: 2rem; text-align: center; color: #ff4d4d;">Error: Invalid response from server.</div>`;
        return;
    }

    // Add parent dir link
    if (currentBrowserPath !== '/') {
        const parentPath = currentBrowserPath.split('/').slice(0, -1).join('/') || '/';
        const item = document.createElement('div');
        item.className = 'browser-item dir';
        item.innerHTML = '<span>📁 .. (Parent Directory)</span>';
        item.onclick = () => lsRemote(parentPath);
        list.appendChild(item);
    }
    
    entries.forEach(entry => {
        const item = document.createElement('div');
        item.className = `browser-item ${entry.type}`;
        item.innerHTML = `<span>${entry.type === 'dir' ? '📁' : '📄'} ${entry.name}</span>`;
        if (entry.type === 'dir') {
            item.onclick = () => lsRemote(entry.path);
        }
        list.appendChild(item);
    });
}

async function autoSuggestPaths() {
    if (!confirm("Soll ich versuchen, die Pfade automatisch auf dem Server zu finden? Bestehende Pfade werden überschrieben.")) return;
    
    try {
        const suggestions = await fetch('/stats/suggest_paths').then(r => r.json());
        for (const [domain, path] of Object.entries(suggestions)) {
            const inputId = `config-path-${domain.replace(/\./g, '-')}`;
            const input = document.getElementById(inputId);
            if (input) {
                input.value = path;
                input.style.borderColor = 'var(--accent-color)';
            }
        }
        alert("Pfade wurden vorgeschlagen! Bitte prüfe sie und klicke auf 'Save' bei den gewünschten Domains.");
    } catch (e) {
        alert("Fehler beim Abrufen der Vorschläge: " + e.message);
    }
}

window.openFileBrowser = openFileBrowser;
window.autoSuggestPaths = autoSuggestPaths;

function initSubTabs(navSelector, contentSelectorPrefix) {
    document.querySelectorAll(`${navSelector} .subtab-btn`).forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll(`${navSelector} .subtab-btn`).forEach(b => b.classList.remove('active'));
            document.querySelectorAll(`.subtab-content`).forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            const subtabId = `${btn.getAttribute('data-subtab')}${contentSelectorPrefix}`;
            document.getElementById(subtabId).classList.add('active');
        });
    });
}

async function initDashboard() {
    try {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn, .tab-content').forEach(el => el.classList.remove('active'));
                btn.classList.add('active');
                const tabId = btn.getAttribute('data-tab') + '-tab';
                document.getElementById(tabId).classList.add('active');
                refreshTabData(btn.getAttribute('data-tab'), document.getElementById('domain-select').value);
            });
        });

        initSubTabs('#config-subtabs', '-subtab');

        const domains = await fetchData('domains');
        const select = document.getElementById('domain-select');

        // Add hosting-type filter group: External (Hetzner webspace) vs. Self-Hosted (own IP)
        const externalOpt = document.createElement('option');
        externalOpt.value = '__external__';
        externalOpt.textContent = '🌐 Externe Domains';
        select.appendChild(externalOpt);

        const selfHostedOpt = document.createElement('option');
        selfHostedOpt.value = '__selfhosted__';
        selfHostedOpt.textContent = '🏠 Self-Hosted (eigene IP)';
        select.appendChild(selfHostedOpt);

        // Add YADS Combined option if both exist
        const hasYadsDe = domains.some(d => d.domain === 'yads-security.de');
        const hasYadsCom = domains.some(d => d.domain === 'yads-security.com');
        
        if (hasYadsDe && hasYadsCom) {
            const opt = document.createElement('option');
            opt.value = 'yads-security-combined';
            opt.textContent = '🛡️ YADS Security (Combined)';
            select.appendChild(opt);
            select.value = 'yads-security-combined';
        }

        const sortedDomains = [...domains].sort((a, b) => a.domain.localeCompare(b.domain, undefined, { numeric: true, sensitivity: 'base' }));
        sortedDomains.forEach(d => {
            const opt = document.createElement('option');
            opt.value = d.domain;
            opt.textContent = `${d.domain} (${d.count.toLocaleString()})`;
            select.appendChild(opt);
        });

        select.addEventListener('change', (e) => {
            updateDashboard(e.target.value);
        });

        document.getElementById('run-validator').addEventListener('click', () => {
            updateValidatorTable(select.value);
        });

        // Modal close logic
        document.querySelector('.close-modal').addEventListener('click', () => {
            document.getElementById('browser-modal').style.display = 'none';
        });
        
        window.onclick = (event) => {
            const modal = document.getElementById('browser-modal');
            if (event.target == modal) {
                modal.style.display = 'none';
            }
        };

        document.getElementById('select-path-btn').addEventListener('click', () => {
            if (currentBrowserTargetId) {
                document.getElementById(currentBrowserTargetId).value = currentBrowserPath;
            } else if (currentBrowserDomain) {
                const inputId = `config-path-${currentBrowserDomain.replace(/\./g, '-')}`;
                document.getElementById(inputId).value = currentBrowserPath;
            } else {
                // Fallback
                document.getElementById('global-base-path').value = currentBrowserPath;
            }
            document.getElementById('browser-modal').style.display = 'none';
        });

        document.getElementById('suggest-all-btn').addEventListener('click', autoSuggestPaths);
        document.getElementById('test-conn-btn').addEventListener('click', testConnection);
        document.getElementById('ollama-load-models-btn').addEventListener('click', loadOllamaModels);

        await updateDashboard(select.value);
    } catch (error) {
        console.error('Error loading dashboard:', error);
    }
}

document.addEventListener('DOMContentLoaded', initDashboard);

async function startCachePreload() {
    const domain = document.getElementById('domain-select').value;
    const btn = document.getElementById('preload-btn');
    const stopBtn = document.getElementById('stop-scan-btn');
    const container = document.getElementById('scan-progress-container');
    const consoleEl = document.getElementById('scan-console');
    const statusText = document.getElementById('scan-status-text');
    
    btn.disabled = true;
    stopBtn.style.display = 'inline-block';
    stopBtn.onclick = () => stopPreload();
    container.style.display = 'block';
    statusText.textContent = 'Preload läuft...';
    consoleEl.innerHTML = '⏳ Initialisiere Preload...<br>';
    
    const res = await fetch('/stats/integrity/preload' + (domain ? `?domain=${domain}` : ''), {method: 'POST'});
    const {scan_id} = await res.json();
    currentScanId = scan_id;
    
    let errorCount = 0;
    const MAX_ERRORS = 20;

    const poll = setInterval(async () => {
        try {
            const statusRes = await fetch(`/stats/integrity/status/${currentScanId}`);
            const data = await statusRes.json();
            errorCount = 0; // reset on success

            if (data.logs && data.logs.length > 0) {
                consoleEl.innerHTML = data.logs.map(l => `> ${l}`).join('<br>');
                consoleEl.scrollTop = consoleEl.scrollHeight;
            }

            if (data.status === 'finished') {
                clearInterval(poll);
                btn.disabled = false;
                stopBtn.style.display = 'none';
                statusText.textContent = '✅ Preload abgeschlossen.';
                currentScanId = null;
            } else if (data.status === 'not_found') {
                // Scan lost from memory (e.g. service restarted) — treat as done
                clearInterval(poll);
                btn.disabled = false;
                stopBtn.style.display = 'none';
                statusText.textContent = '✅ Preload abgeschlossen (Service neugestartet).';
                currentScanId = null;
            }
        } catch (err) {
            errorCount++;
            console.warn(`Preload poll error (${errorCount}/${MAX_ERRORS}):`, err);
            if (errorCount >= MAX_ERRORS) {
                clearInterval(poll);
                btn.disabled = false;
                stopBtn.style.display = 'none';
                statusText.textContent = '⚠️ Verbindung verloren – Status unbekannt.';
                currentScanId = null;
            }
        }
    }, 1500);
}

async function stopPreload() {
    if (!currentScanId) return;
    const stopBtn = document.getElementById('stop-scan-btn');
    stopBtn.disabled = true;
    stopBtn.textContent = 'Wird gestoppt...';
    await fetch(`/stats/integrity/stop/${currentScanId}`, {method: 'POST'});
    stopBtn.style.display = 'none';
    stopBtn.disabled = false;
    stopBtn.textContent = 'Scan abbrechen';
    document.getElementById('preload-btn').disabled = false;
    document.getElementById('scan-status-text').textContent = '⛔ Preload abgebrochen.';
    currentScanId = null;
}


async function restoreFile(domain, path) {
    if (!confirm(`Möchtest du die Datei ${path} wirklich aus dem Cache wiederherstellen?`)) return;
    
    const res = await fetch(`/stats/integrity/restore?domain=${domain}&path=${path}`, {method: 'POST'});
    const data = await res.json();
    if (data.status === 'success') {
        alert("Datei erfolgreich wiederhergestellt.");
        updateIntegrityLogs(document.getElementById('domain-select').value);
    } else {
        alert("Fehler: " + data.message);
    }
}

async function updateMonitoring() {
    const data = await fetchData('monitoring/status');
    const tbody = document.querySelector('#monitoring-table tbody');
    tbody.innerHTML = '';
    
    data.forEach(m => {
        const tr = document.createElement('tr');
        const statusColor = m.is_up ? '#4ade80' : '#f87171';
        const sslColor = m.ssl_days < 7 ? '#f87171' : (m.ssl_days < 30 ? '#fbbf24' : '#4ade80');
        
        tr.innerHTML = `
            <td>${m.domain}</td>
            <td><span class="badge" style="background: ${statusColor}; color: #fff;">${m.is_up ? 'ONLINE (' + m.status_code + ')' : 'OFFLINE'}</span></td>
            <td class="stat-label">${m.latency} ms</td>
            <td><span style="color: ${sslColor}; font-weight: bold;">${m.ssl_days < 0 ? 'N/A' : m.ssl_days + ' Tage'}</span></td>
            <td class="stat-label">${new Date(m.last_check).toLocaleString()}</td>
        `;
        tbody.appendChild(tr);
    });
}

// Add to window
window.updateMonitoring = updateMonitoring;

// Periodic update
setInterval(updateMonitoring, 600000); // 10 minutes

async function updateDiskUsage() {
    const data = await fetchData('system/disk');
    const text = document.getElementById('disk-usage-text');
    const bar = document.getElementById('disk-usage-bar');
    
    if (text && bar) {
        const freeGB = (data.free / (1024*1024*1024)).toFixed(1);
        const totalGB = (data.total / (1024*1024*1024)).toFixed(1);
        text.textContent = `Pfad: ${data.path} | ${freeGB} GB frei von ${totalGB} GB (${data.percent.toFixed(1)}% belegt)`;
        bar.style.width = data.percent + '%';
        bar.style.background = data.percent > 90 ? '#f87171' : 'var(--accent-color)';
    }
}

async function clearOfflineCache() {
    if (!confirm("Möchtest du den gesamten lokalen Datei-Cache (GZ/SHA256) und alle Baselines wirklich löschen?")) return;
    const res = await fetch('/stats/integrity/clear_cache', {method: 'POST'});
    const data = await res.json();
    alert(data.message);
    location.reload();
}

async function identifyFalsePositives() {
    const res = await fetch('/stats/security/identify', {method: 'POST'});
    const data = await res.json();
    alert(data.message);
    updateSecurityTable(currentDomain);
}

async function toggleFalsePositive(domain, path) {
    const res = await fetch('/stats/security/false_positives/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({domain, path})
    });
    const data = await res.json();
    updateSecurityTable(currentDomain);
    updateFalsePositivesList();
}

async function updateFalsePositivesList() {
    const data = await fetchData('security/false_positives');
    const container = document.getElementById('false-positives-container');
    const emptyMsg = document.getElementById('no-false-positives');
    const tbody = document.querySelector('#false-positives-table tbody');
    
    if (!data || data.length === 0) {
        container.style.display = 'none';
        emptyMsg.style.display = 'block';
        return;
    }
    
    container.style.display = 'block';
    emptyMsg.style.display = 'none';
    tbody.innerHTML = '';
    
    data.forEach(item => {
        const row = tbody.insertRow();
        row.innerHTML = `
            <td>${item.domain}</td>
            <td style="font-family: monospace;">${item.path}</td>
            <td>
                <button onclick="toggleFalsePositive('${item.domain}', '${item.path}')" class="ignore-btn" style="padding: 0.1rem 0.4rem; font-size: 0.7rem;">Einblenden</button>
            </td>
        `;
    });
}

// Expose to window
window.identifyFalsePositives = identifyFalsePositives;
window.toggleFalsePositive = toggleFalsePositive;

window.clearOfflineCache = clearOfflineCache;

function exportStaticReport() {
    const domain = document.getElementById('domain-select').value;
    const activeTab = document.querySelector('.tab-btn.active')?.getAttribute('data-tab') || 'overview';
    const url = `/stats/export-static?domain=${encodeURIComponent(domain)}&tab=${encodeURIComponent(activeTab)}`;
    window.location.href = url;
}
window.exportStaticReport = exportStaticReport;
