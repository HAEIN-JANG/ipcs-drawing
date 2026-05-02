// Supabase Configuration
const SUPABASE_URL = 'https://ognhvfvlboqblueuldlm.supabase.co';
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9nbmh2ZnZsYm9xYmx1ZXVsZGxtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI3MzY2NTUsImV4cCI6MjA4ODMxMjY1NX0.paO5jr16M7yTySUAp9LgberoatDds9rTNa_eCU_ET_I';
let supabaseClient = null;

try {
    if (typeof window.supabase !== 'undefined' && SUPABASE_URL) {
        supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
    }
} catch (e) {
    console.error("Supabase initialization failed:", e);
}

let db = {
    drawings: []
};

function showLoading(show) {
    const loader = document.getElementById('globalLoader');
    if (loader) loader.style.display = show ? 'flex' : 'none';
}

async function fetchAllRows(tableName) {
    let allData = [];
    let from = 0;
    let step = 1000;
    let hasMore = true;

    while (hasMore) {
        const { data, error } = await supabaseClient
            .from(tableName)
            .select('*')
            .range(from, from + step - 1);
        
        if (error) {
            console.error(`Error fetching ${tableName}:`, error);
            break;
        }
        
        if (data && data.length > 0) {
            allData = allData.concat(data);
            from += step;
            if (data.length < step) hasMore = false;
        } else {
            hasMore = false;
        }
    }
    return allData;
}

async function syncData() {
    if (!supabaseClient) {
        console.warn("Supabase client not initialized.");
        return;
    }
    showLoading(true);
    try {
        // Using 'drawing_master' as the default table name. Adjust if different in Supabase.
        const rawData = await fetchAllRows('drawing_master');
        
        db.drawings = rawData.map(d => ({
            dwgNo: d.drawing_no || d.dwg_no || '-',
            description: d.description || d.title || '-',
            area: d.area || d.unit || 'General',
            rev: d.rev || d.revision || '0',
            state: (d.state || d.status || 'IFC').toUpperCase(),
            issueDate: d.issue_date || '-'
        }));

        updateDashboard();
        initFilters();
        renderRecentUpdates();
        renderDrawingTable();
    } catch (e) {
        console.error("Sync failed:", e);
    } finally {
        showLoading(false);
    }
}

function renderRecentUpdates() {
    const list = document.getElementById('recentUpdateList');
    if (!list) return;
    
    // Sort by issueDate descending (assuming YYYY-MM-DD or similar) - take top 5
    const latest = [...db.drawings]
        .filter(d => d.issueDate && d.issueDate !== '-')
        .sort((a, b) => b.issueDate.localeCompare(a.issueDate))
        .slice(0, 5);

    if (latest.length === 0) {
        list.innerHTML = '<div class="empty-state-small">No recent activity detected.</div>';
        return;
    }

    list.innerHTML = latest.map(d => `
        <li class="warning-item ${d.state.includes('IFC') ? '' : 'alert-warn'}">
            <div class="wi-icon"><i class="fas ${d.state.includes('IFC') ? 'fa-check-circle text-success' : 'fa-clock text-warning'}"></i></div>
            <div class="wi-content">
                <div class="wi-title">${d.dwgNo}</div>
                <div class="wi-desc">Rev ${d.rev} | ${d.state} | Issued: ${d.issueDate}</div>
            </div>
        </li>
    `).join('');
}

function updateDashboard() {
    document.getElementById('kpi-total').innerText = db.drawings.length;
    document.getElementById('kpi-ifc').innerText = db.drawings.filter(d => d.state.includes('IFC')).length;
    document.getElementById('kpi-pending').innerText = db.drawings.filter(d => !d.state.includes('IFC')).length;

    renderChart();
}

let myChart = null;
function renderChart() {
    const ctx = document.getElementById('drawingChart');
    if (!ctx) return;

    const areaCounts = {};
    db.drawings.forEach(d => {
        areaCounts[d.area] = (areaCounts[d.area] || 0) + 1;
    });

    const labels = Object.keys(areaCounts);
    const data = Object.values(areaCounts);

    if (myChart) myChart.destroy();
    myChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                backgroundColor: ['#0A2540', '#0288d1', '#2e7d32', '#f57f17', '#c62828']
            }]
        },
        options: { responsive: true, maintainAspectRatio: false }
    });
}

function initFilters() {
    const areas = [...new Set(db.drawings.map(d => d.area))].sort();
    const select = document.getElementById('areaFilter');
    if (select) {
        select.innerHTML = '<option value="All">All Areas</option>' + 
            areas.map(a => `<option value="${a}">${a}</option>`).join('');
    }
}

let drawingDataTable = null;
function renderDrawingTable() {
    const tableId = '#drawingTable';
    const tbody = document.querySelector(`${tableId} tbody`);
    if (!tbody) return;

    // Destroy existing DataTable if it exists
    if ($.fn.DataTable.isDataTable(tableId)) {
        $(tableId).DataTable().destroy();
    }
    
    tbody.innerHTML = '';

    const searchTerm = document.getElementById('mainSearch').value.toLowerCase();
    const areaFilter = document.getElementById('areaFilter').value;

    const filtered = db.drawings.filter(d => {
        const matchesSearch = !searchTerm || 
            (d.dwgNo && d.dwgNo.toLowerCase().includes(searchTerm)) || 
            (d.description && d.description.toLowerCase().includes(searchTerm));
        const matchesArea = areaFilter === 'All' || d.area === areaFilter;
        return matchesSearch && matchesArea;
    });

    filtered.forEach(d => {
        const tr = `<tr>
            <td><strong>${d.dwgNo}</strong></td>
            <td>${d.description}</td>
            <td>${d.area}</td>
            <td><span class="status-badge ok">${d.rev}</span></td>
            <td>${d.state}</td>
            <td>${d.issueDate}</td>
        </tr>`;
        tbody.innerHTML += tr;
    });

    // Initialize DataTable for better experience
    drawingDataTable = $(tableId).DataTable({
        pageLength: 25,
        order: [[5, 'desc']], // Sort by issue date by default
        language: {
            search: "_INPUT_",
            searchPlaceholder: "Search within results..."
        },
        dom: 'rtip' // Hide the default search box since we have our own
    });
}

document.addEventListener('DOMContentLoaded', () => {
    // Navigation
    const navItems = document.querySelectorAll('.nav-item');
    const sections = document.querySelectorAll('.view-section');
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const target = item.getAttribute('data-target');
            navItems.forEach(i => i.classList.remove('active'));
            sections.forEach(s => s.classList.remove('active'));
            item.classList.add('active');
            document.getElementById(target).classList.add('active');
        });
    });

    document.getElementById('btnRefresh').addEventListener('click', syncData);
    document.getElementById('btnFilterApply').addEventListener('click', renderDrawingTable);
    
    syncData();
});
