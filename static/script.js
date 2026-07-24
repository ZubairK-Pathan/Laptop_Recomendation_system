document.getElementById('search-btn').addEventListener('click', executeSearch);
document.getElementById('prompt-input').addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
        executeSearch();
    }
});

async function executeSearch() {
    const prompt = document.getElementById('prompt-input').value.trim();
    if (!prompt) return;


    document.getElementById('loader').classList.remove('hidden');
    document.getElementById('cards-grid').classList.add('hidden');
    document.getElementById('dev-data').classList.add('hidden');
    const warningEl = document.getElementById('api-warning');
    if (warningEl) warningEl.classList.add('hidden');
    document.getElementById('cards-grid').innerHTML = ''; // Clear previous

    try {
        const response = await fetch('/api/recommend', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: prompt })
        });

        const data = await response.json();
        
        document.getElementById('loader').classList.add('hidden');
        
        if (data.error) {
            alert(data.error);
            return;
        }

        if (data.api_fallback && warningEl) {
            warningEl.classList.remove('hidden');
        }

        // Update global budget and unhide TOPSIS sliders
        if (data.calculations && data.calculations.extracted_intent) {
            const intent = data.calculations.extracted_intent;
            lastBudget = intent.budget || 80000;
            const revMap = {'C': '1', 'B': '2', 'A': '3'};
            if (intent.q_perf) document.getElementById('slider-perf').value = revMap[intent.q_perf] || 2;
            if (intent.q_port) document.getElementById('slider-port').value = revMap[intent.q_port] || 2;
            if (intent.q_batt) document.getElementById('slider-batt').value = revMap[intent.q_batt] || 2;
            
            // trigger input event to update labels
            ['slider-perf', 'slider-port', 'slider-batt'].forEach(id => {
                const el = document.getElementById(id);
                if(el) el.dispatchEvent(new Event('input'));
            });
            document.getElementById('topsis-controls').classList.remove('hidden');
        }

        renderCards(data.results);
        renderDevData(data);
    } catch (error) {
        document.getElementById('loader').classList.add('hidden');
        alert("Server error. Ensure backend is running.");
        console.error(error);
    }
}

function renderCards(laptops) {
    const grid = document.getElementById('cards-grid');
    grid.classList.remove('hidden');

    laptops.forEach((laptop, index) => {
        const card = document.createElement('div');
        card.className = 'card';
        card.style.animationDelay = `${index * 0.15}s`;

        const matchPct = (laptop.match_score * 100).toFixed(1);
        
        // Format price to Indian Rupee
        const priceFmt = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(laptop.price);

        let specsHtml = '';
        if (laptop.specs) {
            specsHtml = `
            <div class="laptop-specs">
                <span class="spec-badge">⚙️ ${laptop.specs.processor}</span>
                <span class="spec-badge">💾 ${laptop.specs.ram} RAM</span>
                <span class="spec-badge">💽 ${laptop.specs.storage}</span>
                <span class="spec-badge">🎮 ${laptop.specs.graphics_card}</span>
            </div>
            `;
        }

        card.innerHTML = `
            <div class="card-header">
                <span class="rank">#${index + 1} MATCH</span>
            </div>
            <img class="card-img" src="${laptop.image_url}" alt="${laptop.name}" onerror="this.src='https://via.placeholder.com/400x250/111827/3b82f6?text=Image+Unavailable'" />
            <h3>${laptop.name}</h3>
            <div class="metrics">
                <div class="metric">
                    <span class="metric-label">Price</span>
                    <span class="metric-value">${priceFmt}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Suitability</span>
                    <span class="metric-value">${matchPct}%</span>
                </div>
            </div>
            ${specsHtml}
            <div class="pitch">
                ${laptop.pitch}
            </div>
        `;
        grid.appendChild(card);
    });
}

function renderDevData(data) {
    const devBox = document.getElementById('dev-data');
    const isDevMode = document.getElementById('dev-mode-cb').checked;
    
    if (isDevMode && data.calculations) {
        let statsStr = "";
        data.results.forEach((r, i) => {
            statsStr += `[#${i+1} Match: ${r.name}]\n↳ TOPSIS Distance Score: ${(r.match_score * 100).toFixed(2)}%\n↳ Raw Dimensions: [Weight Proxy: ${r.weight}, Battery Proxy: ${r.battery}]\n\n`;
        });
        
        devBox.innerHTML = `
            <h4> Edge AI Intent Extraction:</h4>
            <code>${JSON.stringify(data.calculations.extracted_intent, null, 2)}</code>
            <hr style="border:0; border-top:1px solid rgba(255,255,255,0.1); margin: 1rem 0;">
            <h4>🧮 Mathematical TOPSIS Vectors:</h4>
            <pre style="margin:0; font-family:inherit;">${statsStr}</pre>
        `;
        devBox.classList.remove('hidden');
    }
}

let lastBudget = 80000;
let marketChartInstance = null;

document.getElementById('market-insights-btn').addEventListener('click', toggleMarketInsights);

// Listeners for sliders
const sliders = ['slider-perf', 'slider-port', 'slider-batt'];
sliders.forEach(id => {
    const el = document.getElementById(id);
    if(el) {
        el.addEventListener('input', (e) => {
            const valMap = {1: 'C', 2: 'B', 3: 'A'};
            const labelId = id.replace('slider-', 'val-');
            document.getElementById(labelId).innerText = valMap[e.target.value];
        });
        el.addEventListener('change', executeRecalculation);
    }
});

async function toggleMarketInsights() {
    const container = document.getElementById('market-insights-container');
    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        if (!marketChartInstance) {
            await loadMarketChart();
        }
    } else {
        container.classList.add('hidden');
    }
}

async function loadMarketChart() {
    try {
        const response = await fetch('/api/stats');
        const data = await response.json();
        if(data.error) {
            alert(data.error);
            return;
        }
        
        const labels = Object.keys(data.market_insights.top_brands);
        const values = Object.values(data.market_insights.top_brands);
        
        const ctx = document.getElementById('marketChart').getContext('2d');
        marketChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Number of Models',
                    data: values,
                    backgroundColor: 'rgba(59, 130, 246, 0.5)',
                    borderColor: 'rgba(59, 130, 246, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { labels: { color: 'white' } },
                    title: { display: true, text: `Top Brands (Total Laptops: ${data.market_insights.total_laptops} | Avg Price: ₹${data.market_insights.avg_price.toFixed(0)})`, color: 'white' }
                },
                scales: {
                    y: { ticks: { color: 'white' }, grid: { color: 'rgba(255,255,255,0.1)' } },
                    x: { ticks: { color: 'white' }, grid: { color: 'rgba(255,255,255,0.1)' } }
                }
            }
        });
    } catch(err) {
        console.error("Failed to load chart", err);
    }
}

async function executeRecalculation() {
    const valMap = {1: 'C', 2: 'B', 3: 'A'};
    const q_perf = valMap[document.getElementById('slider-perf').value];
    const q_port = valMap[document.getElementById('slider-port').value];
    const q_batt = valMap[document.getElementById('slider-batt').value];
    
    document.getElementById('cards-grid').classList.add('hidden');
    document.getElementById('loader').classList.remove('hidden');
    
    try {
        const response = await fetch('/api/recalculate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ budget: lastBudget, q_perf, q_port, q_batt })
        });
        const data = await response.json();
        
        document.getElementById('loader').classList.add('hidden');
        if (data.error) {
            alert(data.error);
            return;
        }
        
        document.getElementById('cards-grid').innerHTML = '';
        renderCards(data.results);
        renderDevData(data);
    } catch (error) {
        document.getElementById('loader').classList.add('hidden');
        alert("Server error during recalculation.");
    }
}
