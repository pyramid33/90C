/**
 * Sports Command Center Logic
 */

class SportsDashboard {
    constructor() {
        this.updateInterval = 10000; // 10 seconds
        this.init();
    }

    init() {
        this.loadDashboardData();
        this.loadSportsWhaleTracker();
        this.startAutoUpdate();
        this.initBacktestModal();
    }

    startAutoUpdate() {
        setInterval(() => {
            this.loadDashboardData();
            this.loadSportsWhaleTracker();
        }, this.updateInterval);
    }

    async loadDashboardData() {
        try {
            const response = await fetch('/api/sports/dashboard');
            if (!response.ok) throw new Error('Failed to fetch sports data');

            const data = await response.json();
            this.renderDashboard(data);

        } catch (error) {
            console.error('Error loading sports dashboard:', error);
        }
    }

    async loadSportsWhaleTracker() {
        try {
            const response = await fetch('/api/sports/whale_tracker');
            const data = await response.json();
            if (data.trades && data.trades.length > 0) {
                this.renderSportsWhaleTracker(data.trades);
            }
        } catch (error) {
            console.error('Error loading whale tracker:', error);
        }
    }

    renderDashboard(data) {
        this.renderHero(data.match_of_the_moment);
        this.renderMatchList('live-matches', data.live_matches);
        this.renderMatchList('upcoming-matches', data.upcoming_matches);
        this.renderPredictionsFeed(data.live_predictions);
        this.renderNews(data.news);
        this.updateTicker(data.news);

        if (data.value_bets && data.value_bets.length > 0) {
            this.renderValueBets(data.value_bets);
        }
    }

    renderValueBets(bets) {
        const container = document.getElementById('value-bets-feed');
        const header = document.getElementById('value-bets-header');

        if (!bets || bets.length === 0) {
            if (container) container.style.display = 'none';
            if (header) header.style.display = 'none';
            return;
        }

        if (container) container.style.display = 'block';
        if (header) header.style.display = 'flex';

        if (container) {
            container.innerHTML = bets.map(bet => `
                <a href="${bet.url}" target="_blank" class="match-card-link">
                    <div class="match-card" style="border-left: 4px solid #00ff88;">
                        <div class="match-header">
                            <span class="league-badge">${bet.category}</span>
                            <span class="live-indicator" style="color: #00ff88;">+${bet.ev.toFixed(1)}% EV</span>
                        </div>
                        <div class="match-teams">${bet.question}</div>
                        <div class="match-odds">
                            <div class="odds-group">
                                <span class="label">AI CONFIDENCE</span>
                                <span class="value" style="color: #00ff88;">${Math.round(bet.confidence)}%</span>
                            </div>
                            <div class="odds-group">
                                <span class="label">MARKET PRICE</span>
                                <span class="value">${Math.round(bet.market_price)}¢</span>
                            </div>
                        </div>
                        <div class="ai-insight" style="margin-top: 10px; font-size: 0.85rem; color: #ccc;">
                            ${bet.prediction}
                        </div>
                    </div>
                </a>
            `).join('');
        }
    }

    renderSportsWhaleTracker(trades) {
        const container = document.getElementById('sports-whale-feed');
        if (!container) return;

        container.innerHTML = trades.map(trade => {
            const sideClass = trade.side.toLowerCase() === 'buy' ? 'side-buy' : 'side-sell';
            return `
                <a href="${trade.url}" target="_blank" class="prediction-link">
                    <div class="prediction-card" style="border-left: 3px solid ${trade.side.toLowerCase() === 'buy' ? '#00ff88' : '#ff3e3e'};">
                        <div class="prediction-header">
                            <span class="prediction-type">🐋 WHALE ALERT</span>
                            <span class="prediction-time">${trade.timestamp}</span>
                        </div>
                        <div class="prediction-question" style="font-size: 0.9rem;">${trade.question}</div>
                        <div class="prediction-analysis" style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px;">
                            <span class="${sideClass}" style="font-weight: bold;">${trade.side.toUpperCase()} ${trade.outcome}</span>
                            <span style="font-family: 'JetBrains Mono'; color: #fff;">$${trade.size.toLocaleString()}</span>
                        </div>
                        <div style="font-size: 0.8rem; color: #888; margin-top: 5px;">
                            Trader: <span style="color: #ccc;">${trade.username}</span> (${trade.league})
                        </div>
                    </div>
                </a>
            `;
        }).join('');
    }

    renderPredictionsFeed(predictions) {
        const container = document.getElementById('ai-intel');
        if (!predictions || predictions.length === 0) {
            container.innerHTML = '<div class="placeholder-text">Analyzing market data...</div>';
            return;
        }

        container.innerHTML = predictions.map(pred => `
            <div class="ai-insight ${this.getConfidenceColor(pred.confidence / 100)}">
                <div class="insight-header">
                    <span class="agent-name">${this.getSportIcon(pred.category)} ${pred.category || 'SPORTS'}</span>
                    <span class="agent-role">${pred.type.replace('_', ' ')}</span>
                </div>
                <div class="insight-question">${pred.question}</div>
                <div class="insight-text">${pred.prediction}</div>
                <div class="insight-meta">
                    Confidence: ${pred.confidence.toFixed(0)}%
                </div>
            </div>
        `).join('');
    }

    getConfidenceColor(confidence) {
        if (confidence > 0.9) return 'cyan';
        if (confidence > 0.7) return 'green';
        return 'purple';
    }

    getSportIcon(category) {
        if (!category) return '🏆';
        const cat = category.toUpperCase();
        if (cat.includes('SOCCER') || cat.includes('FOOTBALL')) return '⚽';
        if (cat.includes('BASKETBALL') || cat.includes('NBA')) return '🏀';
        if (cat.includes('NFL')) return '🏈';
        if (cat.includes('MMA') || cat.includes('UFC')) return '🥊';
        if (cat.includes('TENNIS')) return '🎾';
        if (cat.includes('F1') || cat.includes('RACING')) return '🏎️';
        if (cat.includes('CRICKET')) return '🏏';
        if (cat.includes('HOCKEY') || cat.includes('NHL')) return '🏒';
        if (cat.includes('ESPORTS') || cat.includes('GAMING')) return '🎮';
        return '🏆';
    }

    renderHero(match) {
        const heroContainer = document.getElementById('hero-match');
        if (!match) {
            heroContainer.innerHTML = '<div class="hero-loading">No active sports events found</div>';
            return;
        }

        // Parse question to try and extract teams (simple heuristic)
        let team1 = "Team A";
        let team2 = "Team B";
        const parts = match.question.split(' vs ');
        if (parts.length === 2) {
            team1 = parts[0];
            team2 = parts[1];
        } else {
            team1 = match.question;
            team2 = "Field";
        }

        heroContainer.innerHTML = `
            <div class="hero-content">
                <div class="hero-label">
                    <span class="live-badge">LIVE MATCH OF THE MOMENT</span>
                    <span>${match.category || 'SPORTS'}</span>
                </div>
                
                <div class="hero-teams">
                    <div class="team">
                        <div class="team-name">${team1}</div>
                        <div class="team-odds">${(match.yes_price * 100).toFixed(0)}%</div>
                    </div>
                    <div class="vs">VS</div>
                    <div class="team">
                        <div class="team-name">${team2}</div>
                        <div class="team-odds">${(match.no_price * 100).toFixed(0)}%</div>
                    </div>
                </div>

                <div class="chart-container">
                    <canvas id="match-chart"></canvas>
                </div>

                <div class="hero-stats">
                    <div class="hero-stat">
                        <span class="stat-label">Volume</span>
                        <span class="stat-val">$${this.formatNumber(match.volume_24h)}</span>
                    </div>
                    <div class="hero-stat">
                        <span class="stat-label">Spread</span>
                        <span class="stat-val">${(match.spread * 100).toFixed(1)}¢</span>
                    </div>
                    <div class="hero-stat">
                        <span class="stat-label">Liquidity</span>
                        <span class="stat-val">$${this.formatNumber(match.liquidity)}</span>
                    </div>
                </div>
                
                <a href="${match.url}" target="_blank" class="hero-cta">Trade This Event</a>
            </div>
        `;

        if (match.chart_data) {
            this.renderChart(match.chart_data);
        }
    }

    renderChart(chartData) {
        const ctx = document.getElementById('match-chart').getContext('2d');

        // Destroy existing chart if it exists
        if (this.chart) {
            this.chart.destroy();
        }

        const gradientYes = ctx.createLinearGradient(0, 0, 0, 400);
        gradientYes.addColorStop(0, 'rgba(0, 243, 255, 0.6)');
        gradientYes.addColorStop(1, 'rgba(0, 243, 255, 0.1)');

        const gradientNo = ctx.createLinearGradient(0, 0, 0, 400);
        gradientNo.addColorStop(0, 'rgba(255, 0, 100, 0.6)');
        gradientNo.addColorStop(1, 'rgba(255, 0, 100, 0.1)');

        // Calculate NO data (1 - YES)
        const noData = chartData.data.map(p => 1 - p);

        this.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: chartData.labels,
                datasets: [
                    {
                        label: 'YES Probability',
                        data: chartData.data,
                        borderColor: '#00f3ff',
                        backgroundColor: gradientYes,
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.4
                    },
                    {
                        label: 'NO Probability',
                        data: noData,
                        borderColor: '#ff0062',
                        backgroundColor: gradientNo,
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: true,
                        tension: 0.4
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: true,
                        labels: { color: '#fff' }
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: 'rgba(5, 11, 20, 0.9)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(255, 255, 255, 0.1)',
                        borderWidth: 1,
                        callbacks: {
                            label: function (context) {
                                return context.dataset.label + ': ' + (context.raw * 100).toFixed(1) + '%';
                            }
                        }
                    }
                },
                scales: {
                    x: { display: false },
                    y: {
                        display: true,
                        min: 0,
                        max: 1,
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: {
                            callback: function (value) { return (value * 100) + '%'; },
                            color: '#666'
                        }
                    }
                },
                interaction: {
                    mode: 'nearest',
                    axis: 'x',
                    intersect: false
                }
            }
        });
    }

    renderMatchList(elementId, matches) {
        const container = document.getElementById(elementId);
        if (!matches || matches.length === 0) {
            container.innerHTML = '<div class="empty-text">No matches found</div>';
            return;
        }

        container.innerHTML = matches.map(m => `
            <a href="${m.url}" target="_blank" class="match-card-link">
                <div class="match-card">
                    <div class="match-info">
                        <div class="match-title">${m.question}</div>
                        <div class="match-meta">
                            <span>Vol: $${this.formatNumber(m.volume_24h)}</span>
                            <span>${m.category}</span>
                        </div>
                    </div>
                    <div class="match-odds">
                        <div class="odds-box">
                            <div class="odds-label">YES</div>
                            <div class="odds-value">${(m.yes_price * 100).toFixed(0)}%</div>
                        </div>
                        <div class="odds-box">
                            <div class="odds-label">NO</div>
                            <div class="odds-value">${(m.no_price * 100).toFixed(0)}%</div>
                        </div>
                    </div>
                </div>
            </a>
        `).join('');
    }

    renderIntel(match) {
        const container = document.getElementById('ai-intel');
        if (!match || !match.ai_council) {
            container.innerHTML = '<div class="placeholder-text">Analyzing market data...</div>';
            return;
        }

        container.innerHTML = match.ai_council.map(agent => `
            <div class="ai-insight ${agent.color}">
                <div class="insight-header">
                    <span class="agent-name">${agent.name}</span>
                    <span class="agent-role">${agent.role}</span>
                </div>
                <div class="insight-text">${agent.prediction}</div>
                <div class="insight-meta">
                    Confidence: ${(agent.confidence * 100).toFixed(0)}%
                </div>
            </div>
        `).join('');
    }

    renderNews(newsItems) {
        const container = document.getElementById('news-feed');
        if (!newsItems || newsItems.length === 0) return;

        container.innerHTML = newsItems.map(item => `
            <div class="news-item">
                <div class="news-headline">${item.headline}</div>
                <div class="news-time">${new Date(item.timestamp).toLocaleTimeString()}</div>
            </div>
        `).join('');
    }

    updateTicker(newsItems) {
        const ticker = document.getElementById('news-ticker');
        if (!newsItems || newsItems.length === 0) return;

        const text = newsItems.map(n => n.headline).join('  +++  ');
        ticker.innerHTML = `<div class="ticker-item">${text}  +++  ${text}</div>`;
    }

    formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toFixed(0);
    }

    initBacktestModal() {
        const modal = document.getElementById('backtest-modal');
        const btn = document.getElementById('run-backtest-btn');
        const close = document.querySelector('.close-modal');

        if (btn) {
            btn.onclick = () => {
                modal.style.display = 'block';
                this.runBacktest();
            };
        }

        if (close) {
            close.onclick = () => modal.style.display = 'none';
        }

        window.onclick = (event) => {
            if (event.target == modal) {
                modal.style.display = 'none';
            }
        };
    }

    async runBacktest() {
        const container = document.getElementById('backtest-results');
        container.innerHTML = '<div class="spinner"></div> Running backtest on top markets...';

        try {
            const response = await fetch('/api/sports/backtest');
            const data = await response.json();

            if (data.error) throw new Error(data.error);

            this.renderBacktestResults(data.backtests);
        } catch (error) {
            container.innerHTML = `<div style="color: red;">Error: ${error.message}</div>`;
        }
    }

    renderBacktestResults(results) {
        const container = document.getElementById('backtest-results');
        if (!results || results.length === 0) {
            container.innerHTML = '<div style="text-align: center; padding: 20px;">No historical data found for these markets.</div>';
            return;
        }

        // Split results
        const liveResults = results.filter(r => r.type === 'live');
        const simulatedResults = results.filter(r => r.type === 'simulated');

        const calculateStats = (resList) => {
            const correct = resList.filter(r => r.is_correct).length;
            const total = resList.length;
            const winRate = total > 0 ? ((correct / total) * 100).toFixed(1) : 0;
            return { correct, total, winRate };
        };

        const liveStats = calculateStats(liveResults);
        const simStats = calculateStats(simulatedResults);

        let html = '';

        // 1. Live Accuracy Summary (if available)
        if (liveStats.total > 0) {
            html += `
                <div class="backtest-summary" style="margin-bottom: 25px; padding: 20px; background: rgba(0, 255, 136, 0.05); border: 1px solid var(--accent-green); border-radius: 8px; text-align: center;">
                    <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 5px;">LIVE WATCHER ACCURACY (REAL ALERTS)</div>
                    <div style="font-size: 2.5rem; font-weight: 800; color: var(--accent-green); font-family: 'Orbitron';">${liveStats.winRate}%</div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary);">${liveStats.correct} Correct / ${liveStats.total} Total Real-world Predictions</div>
                </div>
            `;
        }

        // 2. Simulated Accuracy Summary
        if (simStats.total > 0) {
            html += `
                <div class="backtest-summary" style="margin-bottom: 25px; padding: 20px; background: rgba(0, 243, 255, 0.05); border: 1px solid var(--accent-cyan); border-radius: 8px; text-align: center;">
                    <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 5px;">HISTORICAL SIMULATION ACCURACY</div>
                    <div style="font-size: 2.5rem; font-weight: 800; color: var(--accent-cyan); font-family: 'Orbitron';">${simStats.winRate}%</div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary);">${simStats.correct} Correct / ${simStats.total} Total Simulated Predictions</div>
                </div>
            `;
        }

        html += '<div class="backtest-list">';

        results.forEach(res => {
            const statusIcon = res.is_correct ? '✅' : '❌';
            const statusText = res.is_correct ? 'CORRECT' : 'WRONG';
            const typeBadge = res.type === 'live' ?
                '<span style="background: rgba(0, 255, 136, 0.2); color: #00ff88; padding: 2px 6px; border-radius: 4px; font-size: 0.6rem; margin-right: 8px;">LIVE ALERT</span>' :
                '<span style="background: rgba(0, 243, 255, 0.2); color: #00f3ff; padding: 2px 6px; border-radius: 4px; font-size: 0.6rem; margin-right: 8px;">SIMULATED</span>';

            html += `
                <div class="backtest-item" style="margin-bottom: 15px; padding: 15px; background: rgba(255, 255, 255, 0.02); border-radius: 6px; border-left: 4px solid ${res.is_correct ? 'var(--accent-green)' : '#ff4444'};">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                        <div style="font-weight: 600; font-size: 0.9rem; flex: 1; color: #fff;">
                            ${typeBadge}${res.market}
                        </div>
                        <div style="font-size: 0.7rem; font-weight: 700; padding: 2px 8px; border-radius: 4px; background: ${res.is_correct ? 'rgba(0, 255, 136, 0.1)' : 'rgba(255, 68, 68, 0.1)'}; color: ${res.is_correct ? 'var(--accent-green)' : '#ff4444'};">
                            ${statusIcon} ${statusText}
                        </div>
                    </div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 10px;">
                        Watcher predicted <span style="color: #fff; font-weight: 600;">${res.prediction}</span> | Actual result was <span style="color: #fff; font-weight: 600;">${res.actual}</span>
                    </div>
                    <div style="font-size: 0.75rem; font-style: italic; color: var(--text-secondary); padding: 8px; background: rgba(0,0,0,0.2); border-radius: 4px;">
                        "${res.analysis}"
                    </div>
                </div>
            `;
        });

        html += '</div>';
        container.innerHTML = html;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new SportsDashboard();
});
