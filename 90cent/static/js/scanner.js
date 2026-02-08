/**
 * Polymarket Scanner - Frontend Logic
 * 
 * Handles real-time market data updates, filtering, and UI interactions
 */

class PolymarketScanner {
    constructor() {
        // State
        this.currentCategory = 'hot';
        this.markets = [];
        this.updateInterval = null;
        this.UPDATE_INTERVAL_MS = 60000; // 60 seconds (reduced from 30s for performance)

        // Initialize
        this.init();
    }

    // =========================================================================
    // Initialization
    // =========================================================================

    init() {
        this.setupEventListeners();

        // Phase 1: Critical data only (immediate)
        this.loadMarkets();
        this.loadStats();

        // Phase 2: Secondary panels (500ms delay for progressive loading)
        setTimeout(() => {
            this.loadAgentFeed();
            this.loadFreshMarkets();
            this.loadWhaleTracker();
        }, 500);

        // Phase 3: Deferred content (1500ms delay)
        setTimeout(() => {
            this.loadNews();
        }, 1500);

        this.initBacktestModal();

        // Delegate event listener for dynamic analyze buttons
        document.addEventListener('click', (e) => {
            if (e.target.closest('.btn-analyze')) {
                const btn = e.target.closest('.btn-analyze');
                const text = btn.dataset.text;
                const target = btn.dataset.target || 'General Market';
                const containerId = btn.dataset.container;
                this.analyzeSentiment(text, target, containerId, btn);
            }
        });

        this.startAutoUpdate();
    }

    setupEventListeners() {
        // Category tabs
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.switchCategory(e.currentTarget.dataset.category);
            });
        });

        // Modal close
        document.getElementById('modal-close').addEventListener('click', () => {
            this.closeModal();
        });

        document.getElementById('modal-overlay').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) {
                this.closeModal();
            }
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeModal();
            }
        });

        // Correlation Matrix
        const corrBtn = document.getElementById('btn-correlation');
        if (corrBtn) {
            corrBtn.addEventListener('click', () => this.openCorrelationModal());
        }

        const corrClose = document.getElementById('correlation-close');
        if (corrClose) {
            corrClose.addEventListener('click', () => this.closeCorrelationModal());
        }

        const corrSelect = document.getElementById('correlation-category');
        if (corrSelect) {
            corrSelect.addEventListener('change', () => this.loadCorrelationData());
        }

        // Market Score Info
        const infoIcon = document.querySelector('.legend-info');
        if (infoIcon) {
            infoIcon.addEventListener('click', () => this.openScoreExplanationModal());
        }

        // Tabs Help Info
        const tabsHelpBtn = document.getElementById('tabs-help-btn');
        if (tabsHelpBtn) {
            tabsHelpBtn.addEventListener('click', () => this.openTabsExplanationModal());
        }
    }

    // =========================================================================
    // Data Loading
    // =========================================================================

    async loadMarkets(isAutoUpdate = false) {
        if (!isAutoUpdate) this.showLoading();

        try {
            const response = await fetch(`/api/scanner/${this.currentCategory}`);
            if (!response.ok) throw new Error('Failed to fetch markets');

            this.markets = await response.json();
            this.renderMarkets();
            this.updateLastUpdate();

        } catch (error) {
            console.error('Error loading markets:', error);
            if (!isAutoUpdate) {
                this.showError('Failed to load markets. Please try again.');
            }
        }
    }

    async loadStats() {
        try {
            const response = await fetch('/api/scanner/stats');
            if (!response.ok) return;

            const stats = await response.json();
            this.updateStats(stats);

        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    async loadAgentFeed() {
        try {
            const response = await fetch('/api/scanner/agent/feed');
            if (!response.ok) return;

            const data = await response.json();
            this.renderAgentFeed(data.predictions);

        } catch (error) {
            console.error('Error loading agent feed:', error);
        }
    }

    async loadNews() {
        try {
            const response = await fetch('/api/scanner/news');
            if (!response.ok) return;

            const news = await response.json();
            this.renderNews(news);

        } catch (error) {
            console.error(error);
        }
    }

    async loadFreshMarkets() {
        try {
            const response = await fetch('/api/scanner/fresh');
            if (!response.ok) return;

            const markets = await response.json();
            this.renderFreshMarkets(markets);

        } catch (error) {
            console.error('Error loading fresh markets:', error);
        }
    }

    renderFreshMarkets(markets) {
        const feed = document.getElementById('fresh-markets-feed');
        if (!markets || markets.length === 0) {
            feed.innerHTML = '<div class="feed-placeholder">No new markets...</div>';
            return;
        }

        feed.innerHTML = markets.map(market => `
            <div class="prediction-card-wrapper">
                <a href="${market.url}" target="_blank" class="prediction-link">
                    <div class="prediction-card">
                        <div class="prediction-header">
                            <span class="prediction-type">${this.escapeHtml(market.category)}</span>
                            <span class="prediction-time">NEW</span>
                        </div>
                        <div class="prediction-question">${this.escapeHtml(market.question)}</div>
                        <div class="prediction-analysis">
                            <span class="yes">${this.formatPercent(market.yes_price)} YES</span> • 
                            <span class="no">${this.formatPercent(market.no_price)} NO</span>
                        </div>
                    </div>
                </a>
            </div>
        `).join('');
    }

    async loadArbitrage(isAutoUpdate = false) {
        if (!isAutoUpdate) this.showLoading();

        try {
            const response = await fetch('/api/scanner/arbitrage');
            if (!response.ok) throw new Error('Failed to fetch arbitrage data');

            const data = await response.json();
            this.renderArbitrage(data.opportunities || []);
            this.updateLastUpdate();

        } catch (error) {
            console.error('Error loading arbitrage:', error);
            if (!isAutoUpdate) {
                this.showError('Failed to load arbitrage opportunities.');
            }
        }
    }

    async loadCrossPlatformArb(isAutoUpdate = false) {
        if (!isAutoUpdate) this.showLoading();

        try {
            const response = await fetch('/api/scanner/cross_platform_arb');
            if (!response.ok) throw new Error('Failed to fetch cross-platform arbitrage data');

            const data = await response.json();
            this.renderCrossPlatformArb(data.opportunities || []);
            this.updateLastUpdate();

        } catch (error) {
            console.error('Error loading cross-platform arbitrage:', error);
            if (!isAutoUpdate) {
                this.showError('Failed to load cross-platform arbitrage opportunities.');
            }
        }
    }

    renderCrossPlatformArb(opportunities) {
        this.hideLoading();

        const grid = document.getElementById('markets-grid');
        const emptyState = document.getElementById('empty-state');

        if (opportunities.length === 0) {
            grid.style.display = 'none';
            emptyState.style.display = 'flex';
            document.querySelector('.empty-text').textContent = 'No cross-platform arbitrage opportunities found at this time.';
            return;
        }

        grid.style.display = 'grid';
        emptyState.style.display = 'none';

        grid.innerHTML = opportunities.map(opp => this.renderCrossPlatformCard(opp)).join('');
    }

    renderCrossPlatformCard(opp) {
        const profitClass = opp.profit_pct >= 5 ? 'hot' : '';

        return `
            <div class="market-card cross-platform-card ${profitClass}">
                <div class="card-header">
                    <span class="card-category">🌐 Cross-Platform</span>
                    <span class="card-score score-high">${opp.profit_pct.toFixed(2)}% Profit</span>
                </div>
                
                <div class="card-question">${this.escapeHtml(opp.question)}</div>
                
                <div class="platform-comparison">
                    ${opp.markets.map(m => `
                        <div class="platform-item">
                            <span class="platform-name">${this.escapeHtml(m.platform)}</span>
                            <span class="platform-price">${this.formatPercent(m.yes_price)}</span>
                        </div>
                    `).join('')}
                </div>

                <div class="arb-suggestion">
                    💡 Buy on <b>${this.escapeHtml(opp.best_buy_yes.platform)}</b> (${this.formatPercent(opp.best_buy_yes.yes_price)}) 
                    and Sell on <b>${this.escapeHtml(opp.best_sell_yes.platform)}</b> (${this.formatPercent(opp.best_sell_yes.yes_price)})
                </div>

                <div class="card-metrics">
                    <div class="metric">
                        <span class="metric-label">Spread</span>
                        <span class="metric-value">${(opp.spread * 100).toFixed(2)}¢</span>
                    </div>
                </div>
            </div>
        `;
    }

    renderArbitrage(opportunities) {
        this.hideLoading();

        const grid = document.getElementById('markets-grid');
        const emptyState = document.getElementById('empty-state');

        if (opportunities.length === 0) {
            grid.style.display = 'none';
            emptyState.style.display = 'flex';
            document.querySelector('.empty-text').textContent = 'No arbitrage opportunities found at this time.';
            return;
        }

        grid.style.display = 'grid';
        emptyState.style.display = 'none';

        grid.innerHTML = opportunities.map(opp => this.renderArbCard(opp)).join('');
    }

    renderArbCard(opp) {
        const mispricingClass = opp.mispricing_pct >= 5 ? 'hot' : '';
        const confidencePercent = Math.round(opp.confidence * 100);

        return `
            <div class="market-card arb-card ${mispricingClass}">
                <div class="card-header">
                    <span class="card-category">⚖️ ${this.escapeHtml(opp.group_name)}</span>
                    <span class="card-score score-high">${opp.mispricing_pct}% Mispricing</span>
                </div>
                
                <div class="arb-markets">
                    <div class="arb-market-item">
                        <a href="${opp.market_a.url}" target="_blank" class="arb-question">${this.escapeHtml(opp.market_a.question)}</a>
                        <div class="arb-price yes">${this.formatPercent(opp.market_a.yes_price)}</div>
                    </div>
                    <div class="arb-vs">vs</div>
                    <div class="arb-market-item">
                        <a href="${opp.market_b.url}" target="_blank" class="arb-question">${this.escapeHtml(opp.market_b.question)}</a>
                        <div class="arb-price yes">${this.formatPercent(opp.market_b.yes_price)}</div>
                    </div>
                </div>

                <div class="arb-relationship">
                    <span class="arb-type">${this.escapeHtml(opp.relationship_type)}</span>
                    <span class="arb-expected">${this.escapeHtml(opp.expected_relationship)}</span>
                </div>
                
                <div class="arb-suggestion">
                    💡 ${this.escapeHtml(opp.suggested_trade)}
                </div>

                <div class="card-metrics">
                    <div class="metric">
                        <span class="metric-label">Confidence</span>
                        <span class="metric-value">${confidencePercent}%</span>
                    </div>
                </div>
            </div>
        `;
    }


    startAutoUpdate() {
        this.updateInterval = setInterval(() => {
            this.refreshData(true);
            this.loadStats();
            this.loadAgentFeed();
            this.loadNews();
            this.loadFreshMarkets();
            this.loadWhaleTracker();
        }, this.UPDATE_INTERVAL_MS);
    }

    // =========================================================================
    // UI Updates
    // =========================================================================

    switchCategory(category) {
        // Update active tab
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.category === category);
        });

        this.currentCategory = category;
        this.refreshData(false);
    }

    refreshData(isAutoUpdate = false) {
        // Special handling for arbitrage tabs
        if (this.currentCategory === 'arbitrage') {
            this.loadArbitrage(isAutoUpdate);
        } else if (this.currentCategory === 'cross-platform') {
            this.loadCrossPlatformArb(isAutoUpdate);
        } else {
            this.loadMarkets(isAutoUpdate);
        }
    }

    showLoading() {
        document.getElementById('loading-state').style.display = 'flex';
        document.getElementById('markets-grid').style.display = 'none';
        document.getElementById('empty-state').style.display = 'none';
    }

    hideLoading() {
        document.getElementById('loading-state').style.display = 'none';
    }

    showError(message) {
        this.hideLoading();
        document.getElementById('empty-state').style.display = 'flex';
        document.querySelector('.empty-text').textContent = message;
    }

    updateStats(stats) {

        document.getElementById('total-volume').textContent = this.formatCurrency(stats.total_volume_24h);
        document.getElementById('hot-count').textContent = stats.hot_markets || 0;
        document.getElementById('insider-count').textContent = stats.insider_signals || 0;
    }

    updateLastUpdate() {
        const now = new Date();
        document.getElementById('last-update').textContent =
            `Last update: ${now.toLocaleTimeString()}`;
    }

    // =========================================================================
    // Rendering
    // =========================================================================

    renderMarkets() {
        this.hideLoading();

        const grid = document.getElementById('markets-grid');
        const emptyState = document.getElementById('empty-state');

        if (this.markets.length === 0) {
            grid.style.display = 'none';
            emptyState.style.display = 'flex';
            return;
        }

        grid.style.display = 'grid';
        emptyState.style.display = 'none';

        if (this.currentCategory === 'calendar') {
            // Calendar View Rendering
            grid.style.display = 'block'; // Block display for sections
            grid.innerHTML = this.renderCalendarView(this.markets);
        } else {
            // Standard Grid Rendering
            grid.style.display = 'grid';
            grid.innerHTML = this.markets.map(market => this.renderMarketCard(market)).join('');
        }

        // Add click handlers for cards
        grid.querySelectorAll('.market-card, .calendar-card').forEach((card) => {
            card.addEventListener('click', (e) => {
                // Find the market object from dataset or index
                // For calendar cards, I'll properly attach index or object id
                const marketId = card.dataset.id;
                const market = this.markets.find(m => m.id === marketId) || this.markets[Array.from(grid.children).indexOf(card)];

                if (market) this.openModal(market);
            });
        });
    }

    renderCalendarView(markets) {
        // Group markets by date
        const groups = {
            'Resolving Today': [],
            'Tomorrow': [],
            'This Week': [],
            'Upcoming': []
        };

        const now = new Date();
        const tomorrow = new Date(now); tomorrow.setDate(tomorrow.getDate() + 1);
        const nextWeek = new Date(now); nextWeek.setDate(nextWeek.getDate() + 7);

        markets.forEach(m => {
            if (!m.end_date) return;
            const endDate = new Date(m.end_date);

            if (endDate.toDateString() === now.toDateString()) {
                groups['Resolving Today'].push(m);
            } else if (endDate.toDateString() === tomorrow.toDateString()) {
                groups['Tomorrow'].push(m);
            } else if (endDate < nextWeek) {
                groups['This Week'].push(m);
            } else {
                groups['Upcoming'].push(m);
            }
        });

        let html = '';

        for (const [groupName, groupMarkets] of Object.entries(groups)) {
            if (groupMarkets.length === 0) continue;

            html += `
                <div class="calendar-section">
                    <h3 class="calendar-header">${groupName}</h3>
                    <div class="calendar-grid">
                        ${groupMarkets.map(m => this.renderCalendarCard(m)).join('')}
                    </div>
                </div>
            `;
        }

        if (html === '') return '<div style="text-align:center; padding: 20px;">No upcoming events found.</div>';
        return html;
    }

    renderCalendarCard(market) {
        const endDate = new Date(market.end_date);
        const dateStr = endDate.toLocaleDateString();
        const timeStr = endDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const scoreClass = this.getScoreClass(market.score);

        return `
            <div class="market-card calendar-card" data-id="${market.id}" style="border-left: 4px solid var(--accent-cyan);">
                <div class="card-header">
                    <span class="card-category">📅 ${dateStr} ${timeStr}</span>
                    <span class="card-score ${scoreClass}">${market.score_label}</span>
                </div>
                 <div class="card-question" style="font-size: 1rem;">${this.escapeHtml(market.question)}</div>
                 
                 <div class="card-metrics">
                    <div class="metric">
                        <span class="metric-label">Volume</span>
                        <span class="metric-value">${this.formatCurrency(market.volume_24h)}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Yes Price</span>
                        <span class="metric-value yes">${this.formatPercent(market.yes_price)}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Social</span>
                        <span class="metric-value" style="color: #1DA1F2;">${this.formatSocialVolume(market.social_volume)}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Watcher</span>
                        <span class="metric-value" style="color: var(--accent-cyan); font-weight: 800;">${market.watcher_score}</span>
                    </div>
                 </div>
            </div>
        `;
    }

    renderMarketCard(market) {
        const cardClass = this.getCardClass(market);
        const scoreClass = this.getScoreClass(market.score);

        // Determine what metric to show in the middle
        let middleMetricLabel = '24h';
        let middleMetricValue = this.formatPriceChange(market.price_change_24h);
        let middleMetricClass = market.price_change_24h >= 0 ? 'positive' : 'negative';

        if (this.currentCategory === 'spread' || (market.spread && market.spread > 0.05)) {
            middleMetricLabel = 'Spread';
            middleMetricValue = this.formatPercent(market.spread);
            middleMetricClass = 'score-high'; // Highlight wide spreads
        }

        return `
            <div class="market-card ${cardClass}">
                <div class="card-header">
                    <span class="card-category">${this.escapeHtml(market.category)}</span>
                    <span class="card-score ${scoreClass}">${market.score_label}</span>
                </div>
                
                <div class="card-question">${this.escapeHtml(market.question)}</div>
                
                <div class="card-prices">
                    <div class="price-item">
                        <div class="price-label">YES</div>
                        <div class="price-value yes">${this.formatPercent(market.yes_price)}</div>
                    </div>
                    <div class="price-item">
                        <div class="price-label">NO</div>
                        <div class="price-value no">${this.formatPercent(1 - market.yes_price)}</div> 
                    </div>
                </div>
                
                <div class="card-metrics">
                    <div class="metric">
                        <span class="metric-label">24h Vol</span>
                        <span class="metric-value">${this.formatCurrency(market.volume_24h)}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">${middleMetricLabel}</span>
                        <span class="metric-value ${middleMetricClass}">
                            ${middleMetricValue}
                        </span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Liquidity</span>
                        <span class="metric-value">${this.formatCurrency(market.liquidity)}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Social</span>
                        <span class="metric-value" style="color: #1DA1F2;">
                            ${this.formatSocialVolume(market.social_volume)} <span style="font-size: 0.7rem; opacity: 0.8;">(${Math.round(market.social_sentiment * 100)}%)</span>
                        </span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Watcher</span>
                        <span class="metric-value" style="color: var(--accent-cyan); font-weight: 800;">${market.watcher_score}</span>
                    </div>
                </div>
                
                ${this.renderSignals(market.signals)}
            </div>
        `;
    }

    renderSignals(signals) {
        if (!signals || signals.length === 0) return '';

        return `
            <div class="card-signals">
                ${signals.map(signal => `
                    <span class="signal-badge ${signal.signal_type}">
                        ${this.getSignalIcon(signal.signal_type)}
                        ${this.formatSignalType(signal.signal_type)}
                    </span>
                `).join('')}
            </div>
        `;
    }

    // =========================================================================
    // Modal Logic
    // =========================================================================

    openModal(market) {
        const modalBody = document.getElementById('modal-body');
        modalBody.innerHTML = `
            <div class="modal-section">
                <div class="card-category">${this.escapeHtml(market.category)}</div>
                <h2 class="modal-question" style="font-size: 1.5rem; margin: 15px 0; font-weight: 700;">${this.escapeHtml(market.question)}</h2>
                
                <div class="card-prices" style="margin-bottom: 30px;">
                    <div class="price-item">
                        <div class="price-label">YES PRICE</div>
                        <div class="price-value yes" style="font-size: 2.5rem;">${this.formatPercent(market.yes_price)}</div>
                    </div>
                    <div class="price-item">
                        <div class="price-label">NO PRICE</div>
                        <div class="price-value no" style="font-size: 2.5rem;">${this.formatPercent(market.no_price)}</div>
                    </div>
                </div>
            </div>

            <div class="modal-section">
                <div class="modal-section-title">Advanced Order Book Analytics</div>
                <div id="depth-chart-container" style="height: 250px; margin-bottom: 20px; background: rgba(0,0,0,0.2); border-radius: 8px; position: relative; overflow: hidden;">
                    <div class="loading-overlay" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center;">
                        <div class="spinner"></div>
                    </div>
                </div>
                <div id="slippage-table-container"></div>
            </div>

            <div class="modal-section">
                <div class="modal-section-title">Market Intelligence & Watcher Score</div>
                <div class="card-metrics" style="grid-template-columns: repeat(3, 1fr); background: rgba(0, 243, 255, 0.05); padding: 15px; border-radius: 8px; border: 1px solid rgba(0, 243, 255, 0.2); margin-bottom: 20px;">
                    <div class="metric">
                        <span class="metric-label">Watcher Score</span>
                        <span class="metric-value" style="color: var(--accent-cyan); font-size: 1.2rem; font-weight: 800;">${market.watcher_score}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Intensity Tier</span>
                        <span class="metric-value" style="color: var(--accent-cyan); font-size: 1.2rem;">${market.score_label}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Confidence</span>
                        <span class="metric-value" style="color: var(--accent-cyan); font-size: 1.2rem;">${Math.round(market.score)}%</span>
                    </div>
                </div>
                
                <div class="modal-section-title">Social Sentiment (X/Twitter)</div>
                <div class="card-metrics" style="grid-template-columns: repeat(2, 1fr); background: rgba(29, 161, 242, 0.05); padding: 15px; border-radius: 8px; border: 1px solid rgba(29, 161, 242, 0.2);">
                    <div class="metric">
                        <span class="metric-label">Mentions (24h)</span>
                        <span class="metric-value" style="color: #1DA1F2; font-size: 1.2rem;">${this.formatSocialVolume(market.social_volume)}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Bullishness</span>
                        <span class="metric-value" style="color: #1DA1F2; font-size: 1.2rem;">${Math.round(market.social_sentiment * 100)}%</span>
                    </div>
                </div>
            </div>

            <div class="modal-section">
                <div class="modal-section-title">Market Intelligence</div>
                <div class="modal-signals-list">
                    ${this.renderModalSignals(market.signals)}
                </div>
            </div>

            <a href="${market.url}" target="_blank" class="modal-cta">
                Trade on Polymarket
            </a>
        `;

        document.getElementById('modal-overlay').classList.add('active');

        // Load orderbook data
        this.loadOrderbook(market.id);
    }

    openScoreExplanationModal() {
        const modalBody = document.getElementById('modal-body');
        modalBody.innerHTML = `
            <div class="modal-section score-explanation-modal">
                <div class="modal-header-with-icon">
                    <span class="modal-category">SCORING SYSTEM</span>
                    <h2 class="modal-title">Market Score Calculation</h2>
                    <p class="modal-subtitle">How we identify high-opportunity markets (0-100 scale)</p>
                </div>

                <div class="score-breakdown-container">
                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">🔥</span>
                            <span class="breakdown-label">24h Volume</span>
                            <span class="breakdown-points">30 PTS</span>
                        </div>
                        <div class="breakdown-progress"><div class="progress-fill" style="width: 30%"></div></div>
                        <p class="breakdown-text">Reward for trading interest. Full points awarded to markets with over $100,000 in 24h volume.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">📈</span>
                            <span class="breakdown-label">Price Momentum</span>
                            <span class="breakdown-points">25 PTS</span>
                        </div>
                        <div class="breakdown-progress"><div class="progress-fill" style="width: 25%"></div></div>
                        <p class="breakdown-text">Captures volatility. Measures price changes over the last 24h. Peaks at 25% price deviation.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">💰</span>
                            <span class="breakdown-label">Liquidity / Spread</span>
                            <span class="breakdown-points">20 PTS</span>
                        </div>
                        <div class="breakdown-progress"><div class="progress-fill" style="width: 20%"></div></div>
                        <p class="breakdown-text">Measures ease of exit. Tighter bid-ask spreads result in significantly higher scores.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">🎯</span>
                            <span class="breakdown-label">Extreme Odds</span>
                            <span class="breakdown-points">15 PTS</span>
                        </div>
                        <div class="breakdown-progress"><div class="progress-fill" style="width: 15%"></div></div>
                        <p class="breakdown-text">Rewards asymmetric risk/reward. Markets under 10% or over 90% receive a weighting bonus.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">🔔</span>
                            <span class="breakdown-label">Signal Count</span>
                            <span class="breakdown-points">10 PTS</span>
                        </div>
                        <div class="breakdown-progress"><div class="progress-fill" style="width: 10%"></div></div>
                        <p class="breakdown-text">Accumulates points for every unique signal detected (Spikes, Trends, Activity patterns).</p>
                    </div>

                    <div class="score-bonus-item">
                        <div class="bonus-tag">BONUS</div>
                        <p><strong>🕵️ Insider Alert:</strong> Detecting unusual wallet activity provides a <strong>+10 point</strong> "Insider" boost.</p>
                    </div>
                </div>

                <div class="modal-section-title tiers-title">Score Intensity Tiers</div>
                <div class="score-tiers-container">
                    <div class="tier-item score-very-high">
                        <span class="tier-badge">🔥 Very High</span>
                        <span class="tier-range">80-100</span>
                        <p class="tier-text">Major moves. High liquidity, massive volume, and strong momentum signals.</p>
                    </div>
                    <div class="tier-item score-high">
                        <span class="tier-badge">🌟 High</span>
                        <span class="tier-range">60-79</span>
                        <p class="tier-text">Notable activity. Strong trading interest and clear price trends.</p>
                    </div>
                    <div class="tier-item score-medium">
                        <span class="tier-badge">📊 Medium</span>
                        <span class="tier-range">40-59</span>
                        <p class="tier-text">Standard active markets with decent liquidity.</p>
                    </div>
                    <div class="tier-item score-low">
                        <span class="tier-badge">📉 Low</span>
                        <span class="tier-range">20-39</span>
                        <p class="tier-text">Minimal activity or high spreads.</p>
                    </div>
                    <div class="tier-item score-minimal">
                        <span class="tier-badge">⚪ Minimal</span>
                        <span class="tier-range">&lt;20</span>
                        <p class="tier-text">"Quiet" markets with very little trading interest.</p>
                    </div>
                </div>

                <div class="modal-footer-note">
                    Scores refresh every 30 seconds based on live Gamma API data.
                </div>
            </div>
        `;
        document.getElementById('modal-overlay').classList.add('active');
    }

    renderModalSignals(signals) {
        if (!signals || signals.length === 0) {
            return '<div class="prediction-analysis">No specific signals detected for this market.</div>';
        }

        return signals.map(signal => `
            <div class="modal-signal">
                <span class="signal-icon">${this.getSignalIcon(signal.signal_type)}</span>
                <div class="signal-info">
                    <div class="signal-type">${this.formatSignalType(signal.signal_type)}</div>
                    <div class="signal-description">${this.escapeHtml(signal.description)}</div>
                </div>
                <div class="signal-strength-wrapper">
                    <div class="signal-strength">${Math.round(signal.strength * 100)}% Strength</div>
                    <div class="strength-bar">
                        <div class="strength-fill" style="width: ${signal.strength * 100}%"></div>
                    </div>
                </div>
            </div>
        `).join('');
    }

    closeModal() {
        document.getElementById('modal-overlay').classList.remove('active');
    }

    openTabsExplanationModal() {
        const modalBody = document.getElementById('modal-body');
        modalBody.innerHTML = `
            <div class="modal-section score-explanation-modal">
                <div class="modal-header-with-icon">
                    <span class="modal-category">SCANNER GUIDE</span>
                    <h2 class="modal-title">Category Tabs Explained</h2>
                    <p class="modal-subtitle">How to use each scanner filter to find opportunities</p>
                </div>

                <div class="score-breakdown-container">
                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">🔥</span>
                            <span class="breakdown-label">Hot</span>
                        </div>
                        <p class="breakdown-text">High-action markets with the most 24h trading volume and community interest.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">📈</span>
                            <span class="breakdown-label">Momentum</span>
                        </div>
                        <p class="breakdown-text">The biggest price movers. Highlights markets with significant shifts in sentiment over the last 24h.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">🕵️</span>
                            <span class="breakdown-label">Insider</span>
                        </div>
                        <p class="breakdown-text">Detects unusual activity patterns that may indicate "smart money" or insider entry.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">💰</span>
                            <span class="breakdown-label">Liquidity</span>
                        </div>
                        <p class="breakdown-text">Deep markets with high total liquidity and tight spreads for easy entry/exit.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">🎯</span>
                            <span class="breakdown-label">Extreme</span>
                        </div>
                        <p class="breakdown-text">Focuses on asymmetric risk/reward plays with odds under 10% or over 90%.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">⚖️</span>
                            <span class="breakdown-label">Arbitrage</span>
                        </div>
                        <p class="breakdown-text">Finds price discrepancies within Polymarket between related events.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">🌐</span>
                            <span class="breakdown-label">Cross-Platform</span>
                        </div>
                        <p class="breakdown-text">Scans for profit opportunities between Polymarket and other platforms (Kalshi, etc.).</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">💎</span>
                            <span class="breakdown-label">Wide Spreads</span>
                        </div>
                        <p class="breakdown-text">Highlights neglected markets with large bid-ask spreads for market making.</p>
                    </div>

                    <div class="score-breakdown-item">
                        <div class="breakdown-header">
                            <span class="breakdown-icon">📅</span>
                            <span class="breakdown-label">Calendar</span>
                        </div>
                        <p class="breakdown-text">Organizes markets by resolution date (Today, Tomorrow, This Week).</p>
                    </div>
                </div>

                <div class="modal-footer-note">
                    Use these filters to narrow down thousands of markets into actionable signals.
                </div>
            </div>
        `;
        document.getElementById('modal-overlay').classList.add('active');
    }

    // =========================================================================
    // Correlation Matrix Logic
    // =========================================================================

    openCorrelationModal() {
        document.getElementById('correlation-modal').classList.add('active');
        this.loadCorrelationData();
    }

    closeCorrelationModal() {
        document.getElementById('correlation-modal').classList.remove('active');
    }

    async loadCorrelationData() {
        const container = document.getElementById('correlation-heatmap');
        const category = document.getElementById('correlation-category').value;

        container.innerHTML = '<div class="loading-placeholder"><div class="spinner"></div> Calculating correlations...</div>';

        try {
            const response = await fetch(`/api/scanner/correlation?category=${category}&limit=15`);
            if (!response.ok) throw new Error('Failed to fetch correlation data');

            const data = await response.json();

            if (data.error) {
                container.innerHTML = `<div class="error-message">${data.error}</div>`;
                return;
            }
            this.renderCorrelationHeatmap(data);

        } catch (error) {
            console.error('Error loading correlation data:', error);
            container.innerHTML = '<div class="error-message">Failed to load correlation data.</div>';
        }
    }

    renderCorrelationHeatmap(data) {
        const container = document.getElementById('correlation-heatmap');
        if (!container) return;

        // Clear loading state
        container.innerHTML = '';

        // Truncate labels slightly for UI stability while keeping them descriptive
        const truncate = (str, len) => str.length > len ? str.substring(0, len - 3) + '...' : str;
        const displayX = data.x.map(label => truncate(label, 80));
        const displayY = data.y.map(label => truncate(label, 80));

        const trace = {
            z: data.z,
            x: displayX,
            y: displayY,
            type: 'heatmap',
            colorscale: 'RdBu', // Red-Blue scale
            zmin: data.min,
            zmax: data.max,
            hoverongaps: false,
            xgap: 1, // Grid lines
            ygap: 1, // Grid lines
            hovertemplate: '<b>%{y}</b><br>vs<br><b>%{x}</b><br>Correlation: %{z:.2f}<extra></extra>'
        };

        const layout = {
            paper_bgcolor: 'transparent',
            plot_bgcolor: 'transparent',
            font: {
                family: 'Inter, sans-serif',
                color: '#94a3b8',
                size: 11 // Larger font
            },
            margin: {
                l: 400, // Increased for long names
                r: 30,
                b: 250, // Increased for long names
                t: 30,
                pad: 4
            },
            xaxis: {
                tickangle: 45,
                automargin: true,
                gridcolor: 'rgba(255, 255, 255, 0.05)',
                zeroline: false
            },
            yaxis: {
                automargin: true,
                gridcolor: 'rgba(255, 255, 255, 0.05)',
                zeroline: false
            }
        };

        const config = {
            responsive: true,
            displayModeBar: false
        };

        Plotly.newPlot(container, [trace], layout, config);

        // Add click handler for redirection
        container.on('plotly_click', (eventData) => {
            if (eventData.points && eventData.points.length > 0) {
                const point = eventData.points[0];
                const label = point.y;
                const url = data.urls[label];
                if (url && url !== '#') {
                    window.open(url, '_blank');
                }
            }
        });
    }

    renderAgentFeed(predictions) {
        const feed = document.getElementById('agent-feed');
        if (!predictions || predictions.length === 0) {
            feed.innerHTML = '<div class="feed-placeholder">Waiting for signals...</div>';
            return;
        }

        feed.innerHTML = predictions.map(pred => {
            const recommendationClass = pred.prediction.includes('YES') ? 'positive' : (pred.prediction.includes('NO') ? 'negative' : '');
            return `
                <a href="${pred.url}" target="_blank" class="prediction-link">
                    <div class="prediction-card">
                        <div class="prediction-header">
                            <span class="prediction-type">MATCHUP ALERT</span>
                            <span class="prediction-time">${this.formatTime(pred.timestamp)}</span>
                        </div>
                        <div class="prediction-question">${this.escapeHtml(pred.question)}</div>
                        <div class="prediction-text ${recommendationClass}">
                            ${this.escapeHtml(pred.prediction)}
                        </div>
                        <div class="prediction-analysis">${this.escapeHtml(pred.analysis)}</div>
                        <div class="prediction-confidence">
                            <span>CONFIDENCE: ${Math.round(pred.confidence)}%</span>
                            <div class="confidence-bar">
                                <div class="confidence-fill" style="width: ${pred.confidence}%"></div>
                            </div>
                        </div>
                    </div>
                </a>
            `;
        }).join('');
    }

    renderNews(news) {
        const feed = document.getElementById('news-feed');
        if (!news || news.length === 0) {
            feed.innerHTML = '<div class="feed-placeholder">No recent news...</div>';
            return;
        }

        feed.innerHTML = news.map((item, index) => {
            let sentimentClass = '';
            let sentimentHtml = '';
            let score = 50;

            if (item.sentiment) {
                const bias = item.sentiment.bias.toLowerCase();
                score = item.sentiment.score;

                if (bias === 'bullish') sentimentClass = 'news-card-bullish';
                else if (bias === 'bearish') sentimentClass = 'news-card-bearish';
                else sentimentClass = 'news-card-neutral';

                sentimentHtml = `
                    <div class="news-sentiment-footer">
                        <div class="sentiment-info">
                            <span class="sentiment-label ${bias}">${item.sentiment.bias}</span>
                            <span class="sentiment-score">${score}/100</span>
                        </div>
                        <div class="news-progress-container">
                            <div class="news-progress-bar ${bias}" style="width: ${score}%"></div>
                        </div>
                    </div>
                `;
            }

            return `
            <div class="prediction-card-wrapper">
                <a href="${item.url || '#'}" target="_blank" class="prediction-link">
                    <div class="prediction-card ${sentimentClass}">
                        <div class="prediction-header">
                            <span class="prediction-type">${this.escapeHtml(item.source) || 'NEWS'}</span>
                            <span class="prediction-time">${this.formatTime(item.published_at)}</span>
                        </div>
                        <div class="prediction-question">${this.escapeHtml(item.title)}</div>
                        ${sentimentHtml}
                    </div>
                </a>
            </div>
        `}).join('');
    }

    async analyzeSentiment(text, target, containerId, btn) {
        const container = document.getElementById(containerId);

        // UI State: Loading
        btn.disabled = true;
        btn.innerHTML = '<span>🧠</span> Analyzing...';

        try {
            const response = await fetch('/api/scanner/analyze_sentiment', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, target })
            });

            if (!response.ok) throw new Error('Analysis failed');

            const result = await response.json();

            // Render Result
            container.innerHTML = this.renderSentimentCard(result);

            // Hide button after success (or change to "Re-analyze")
            btn.style.display = 'none';

        } catch (error) {
            console.error('Sentiment analysis error:', error);
            btn.innerHTML = '<span>⚠️</span> Failed. Try Again.';
            btn.disabled = false;
        }
    }

    renderSentimentCard(data) {
        // Determine colors based on score/bias
        let color = '#8b9bb4'; // Neutral
        let colorGlow = 'rgba(139, 155, 180, 0.5)';

        if (data.score >= 60) {
            color = '#0aff68'; // Green
            colorGlow = 'rgba(10, 255, 104, 0.5)';
        } else if (data.score <= 40) {
            color = '#ff2a2a'; // Red
            colorGlow = 'rgba(255, 42, 42, 0.5)';
        }

        const scorePct = `${data.score}% `;
        const scoreDegrees = `${data.score * 3.6} deg`;

        return `
            < div class="sentiment-card" style = "--score-color: ${color}; --score-color-glow: ${colorGlow}; border-left-color: ${color};" >
                <div class="sentiment-header">
                    <div class="sentiment-title-group">
                        <div class="sentiment-bias">${data.bias}</div>
                        <div class="sentiment-target">IMPACT ON: ${data.entity || 'MARKET'}</div>
                    </div>
                    
                    <div class="sentiment-score-container">
                        <div class="sentiment-score-ring" style="--score-pct: ${scoreDegrees}">
                            <div class="sentiment-value">${data.score}</div>
                        </div>
                    </div>
                </div>
                
                <div class="sentiment-body">
                    <ul class="sentiment-reasoning">
                        ${data.reasoning.map(r => `<li>${r}</li>`).join('')}
                    </ul>
                </div>
                
                <div class="sentiment-footer">
                    <div>AI CONFIDENCE</div>
                    <div style="text-align: right;">
                        <div>RELEVANCE: ${data.relevance}%</div>
                        <div class="relevance-bar">
                            <div class="relevance-fill" style="--relevance-width: ${data.relevance}%"></div>
                        </div>
                    </div>
                </div>
            </div >
            `;
    }

    // =========================================================================
    // Helper Functions
    // =========================================================================

    getCardClass(market) {
        if (market.score >= 80) return 'hot';
        if (market.signals.some(s => s.signal_type === 'insider_alert')) return 'insider';
        if (market.price_change_24h > 0.05) return 'momentum';
        return '';
    }

    getScoreClass(score) {
        if (score >= 80) return 'score-very-high';
        if (score >= 60) return 'score-high';
        if (score >= 40) return 'score-medium';
        return 'score-low';
    }

    getSignalIcon(type) {
        const icons = {
            volume_spike: '⚡',
            high_volume: '🔥',
            momentum: '📈',
            insider_alert: '🕵️',
            tight_spread: '💰',
            extreme_price: '🎯'
        };
        return icons[type] || '🔔';
    }

    formatSignalType(type) {
        return type.split('_').map(word =>
            word.charAt(0).toUpperCase() + word.slice(1)
        ).join(' ');
    }

    formatCurrency(value) {
        if (value === null || value === undefined || isNaN(value)) return '$0';
        const num = Number(value);
        if (num >= 1000000) return `$${(num / 1000000).toFixed(1)} M`;
        if (num >= 1000) return `$${(num / 1000).toFixed(1)} K`;
        return `$${Math.round(num)} `;
    }

    formatSocialVolume(value) {
        if (value === null || value === undefined || isNaN(value)) return '0';
        const num = Number(value);
        if (num >= 1000000) return `${(num / 1000000).toFixed(1)} M`;
        if (num >= 1000) return `${(num / 1000).toFixed(1)} K`;
        return Math.round(num).toString();
    }

    formatPercent(value) {
        if (value === null || value === undefined || isNaN(value)) return 'TBD';
        return `${Math.round(value * 100)}% `;
    }

    formatPriceChange(value) {
        const sign = value >= 0 ? '+' : '';
        return `${sign}${Math.round(value * 100)}% `;
    }

    formatNumber(value) {
        if (value === null || value === undefined || isNaN(value)) return '0';
        return new Intl.NumberFormat().format(Math.round(Number(value)));
    }

    formatTime(isoString) {
        if (!isoString) return '--';
        try {
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return isoString; // Return original if invalid

            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);

            if (diffMins < 1) return 'Just now';
            if (diffMins < 60) return `${diffMins}m ago`;

            const diffHours = Math.floor(diffMins / 60);
            if (diffHours < 24) return `${diffHours}h ago`;

            return date.toLocaleDateString();
        } catch (e) {
            return isoString;
        }
    }

    getConfidenceColor(confidence) {
        if (confidence > 0.9) return 'cyan';
        if (confidence > 0.7) return 'green';
        return 'purple';
    }

    getSportIcon(category) {
        if (!category) return '🤖';
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
        if (cat.includes('POLITICS')) return '⚖️';
        if (cat.includes('CRYPTO')) return '₿';
        return '🤖';
    }

    initBacktestModal() {
        const modal = document.getElementById('backtest-modal');
        const btn = document.getElementById('run-backtest-btn');
        const close = document.getElementById('backtest-close');

        if (btn) {
            btn.onclick = () => {
                modal.classList.add('active');
                this.runBacktest();
            };
        }

        if (close) {
            close.onclick = () => modal.classList.remove('active');
        }

        window.onclick = (event) => {
            if (event.target == modal) {
                modal.classList.remove('active');
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
            container.innerHTML = `< div style = "color: red; padding: 20px;" > Error: ${error.message}</div > `;
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
            < div class="backtest-summary" style = "margin-bottom: 25px; padding: 20px; background: rgba(0, 255, 136, 0.05); border: 1px solid var(--accent-green); border-radius: 8px; text-align: center;" >
                    <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 5px;">LIVE WATCHER ACCURACY (REAL ALERTS)</div>
                    <div style="font-size: 2.5rem; font-weight: 800; color: var(--accent-green); font-family: 'Orbitron';">${liveStats.winRate}%</div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary);">${liveStats.correct} Correct / ${liveStats.total} Total Real-world Predictions</div>
                </div >
            `;
        }

        // 2. Simulated Accuracy Summary
        if (simStats.total > 0) {
            html += `
            < div class="backtest-summary" style = "margin-bottom: 25px; padding: 20px; background: rgba(0, 243, 255, 0.05); border: 1px solid var(--accent-cyan); border-radius: 8px; text-align: center;" >
                    <div style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 5px;">HISTORICAL SIMULATION ACCURACY</div>
                    <div style="font-size: 2.5rem; font-weight: 800; color: var(--accent-cyan); font-family: 'Orbitron';">${simStats.winRate}%</div>
                    <div style="font-size: 0.8rem; color: var(--text-secondary);">${simStats.correct} Correct / ${simStats.total} Total Simulated Predictions</div>
                </div >
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
            < div class="backtest-item" style = "margin-bottom: 15px; padding: 15px; background: rgba(255, 255, 255, 0.02); border-radius: 6px; border-left: 4px solid ${res.is_correct ? 'var(--accent-green)' : '#ff4444'};" >
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
                </div >
            `;
        });

        html += '</div>';
        container.innerHTML = html;
    }

    async loadOrderbook(marketId) {
        const chartContainer = document.getElementById('depth-chart-container');
        const tableContainer = document.getElementById('slippage-table-container');

        try {
            const response = await fetch(`/api/market/${marketId}/orderbook`);
            const data = await response.json();

            if (data.error) throw new Error(data.error);
            if (data.orderbook && data.orderbook.error) throw new Error(data.orderbook.error);

            this.renderDepthChart(data.orderbook);
            this.renderSlippageTable(data.orderbook.slippage);
        } catch (error) {
            console.error('Error loading orderbook:', error);
            if (chartContainer) {
                chartContainer.innerHTML = `<div style="color: #ff3e3e; padding: 20px; text-align: center; font-size: 0.9rem;">
                    <div style="font-size: 1.5rem; margin-bottom: 10px;">⚠️</div>
                    Unable to load market depth.<br>
                    <span style="color: #888; font-size: 0.8rem;">${this.escapeHtml(error.message)}</span>
                </div>`;
            }
            if (tableContainer) {
                tableContainer.innerHTML = '';
            }
        }
    }

    renderDepthChart(orderbook) {
        const container = document.getElementById('depth-chart-container');
        if (!container) return;

        // Explicitly clear spinner/loading state
        container.innerHTML = '';

        if (!orderbook || !orderbook.bids || !orderbook.asks) {
            container.innerHTML = '<div style="color: #888; padding: 20px; text-align: center;">No order book data available</div>';
            return;
        }

        const bids = orderbook.bids;
        const asks = orderbook.asks;

        if (bids.length === 0 && asks.length === 0) {
            container.innerHTML = '<div style="color: #888; padding: 20px; text-align: center;">Order book is empty</div>';
            return;
        }

        const bidTrace = {
            x: bids.map(b => b.price),
            y: bids.map(b => b.total_value),
            fill: 'tozeroy',
            type: 'scatter',
            mode: 'lines',
            name: 'Bids (Buy)',
            line: { color: '#00ff88', width: 2 },
            fillcolor: 'rgba(0, 255, 136, 0.2)'
        };

        const askTrace = {
            x: asks.map(a => a.price),
            y: asks.map(a => a.total_value),
            fill: 'tozeroy',
            type: 'scatter',
            mode: 'lines',
            name: 'Asks (Sell)',
            line: { color: '#ff4444', width: 2 },
            fillcolor: 'rgba(255, 68, 68, 0.2)'
        };

        const layout = {
            height: 250,
            autosize: true,
            paper_bgcolor: 'transparent',
            plot_bgcolor: 'transparent',
            margin: { l: 60, r: 20, t: 20, b: 40 },
            showlegend: false,
            xaxis: {
                title: 'Price',
                gridcolor: 'rgba(255, 255, 255, 0.05)',
                tickfont: { color: '#888', size: 10 },
                titlefont: { color: '#888', size: 11 }
            },
            yaxis: {
                title: 'Cumulative Depth ($)',
                gridcolor: 'rgba(255, 255, 255, 0.05)',
                tickfont: { color: '#888', size: 10 },
                titlefont: { color: '#888', size: 11 },
                automargin: true
            },
            hovermode: 'x unified',
            hoverlabel: {
                bgcolor: '#1a1a1a',
                font: { color: '#fff' }
            }
        };

        try {
            Plotly.newPlot(container, [bidTrace, askTrace], layout, {
                responsive: true,
                displayModeBar: false
            });
        } catch (e) {
            console.error("Plotly error:", e);
            container.innerHTML = '<div style="color: #ff3e3e; padding: 20px; text-align: center;">Error rendering chart</div>';
        }
    }

    renderSlippageTable(slippage) {
        const container = document.getElementById('slippage-table-container');
        if (!container || !slippage) return;

        const targets = [100, 500, 1000, 5000];
        let html = `
            <div class="slippage-grid">
                <div class="slippage-header">Size</div>
                <div class="slippage-header">Buy Slippage</div>
                <div class="slippage-header">Sell Slippage</div>
        `;

        targets.forEach(t => {
            const buy = slippage.asks[t];
            const sell = slippage.bids[t];

            const buyVal = buy ? buy.slippage_pct.toFixed(2) + '%' : 'N/A';
            const sellVal = sell ? sell.slippage_pct.toFixed(2) + '%' : 'N/A';

            const buyColor = buy ? (buy.slippage_pct > 5 ? '#ff3e3e' : buy.slippage_pct > 2 ? '#ff9f43' : '#00ff88') : '#888';
            const sellColor = sell ? (sell.slippage_pct > 5 ? '#ff3e3e' : sell.slippage_pct > 2 ? '#ff9f43' : '#00ff88') : '#888';

            html += `
                <div class="slippage-cell size">$${t.toLocaleString()}</div>
                <div class="slippage-cell" style="color: ${buyColor}">${buyVal}</div>
                <div class="slippage-cell" style="color: ${sellColor}">${sellVal}</div>
            `;
        });

        html += `</div>`;
        container.innerHTML = html;
    }

    async loadWhaleTracker() {
        const feed = document.getElementById('whale-feed');
        if (!feed) return;

        try {
            const response = await fetch('/api/scanner/whale_tracker');
            const data = await response.json();

            if (data.trades && data.trades.length > 0) {
                this.renderWhaleTracker(data.trades);
            } else if (feed.innerHTML.includes('Monitoring whale activity...')) {
                feed.innerHTML = '<div class="feed-placeholder">No high-impact trades detected recently.</div>';
            }
        } catch (error) {
            console.error('Error loading whale tracker:', error);
        }
    }

    renderWhaleTracker(trades) {
        const feed = document.getElementById('whale-feed');
        if (!feed) return;

        feed.innerHTML = trades.map(trade => {
            const sideClass = trade.side.toLowerCase() === 'buy' ? 'side-buy' : 'side-sell';
            const whaleBadge = trade.is_whale ? '<span class="whale-badge badge-whale">Whale</span>' : '';
            const smartBadge = trade.is_smart_money ? '<span class="whale-badge badge-smart">Smart Money</span>' : '';
            const outcomeText = trade.outcome ? ` ${trade.outcome.toUpperCase()}` : '';

            return `
                <a href="${trade.url}" target="_blank" class="whale-card">
                    <div class="whale-card-header">
                        <div class="whale-user">
                            <img src="${trade.profile_image || '/static/img/default-avatar.svg'}" class="whale-avatar" onerror="this.src='/static/img/default-avatar.svg'">
                            <span class="whale-username">${this.escapeHtml(trade.username)}</span>
                        </div>
                        <div class="whale-badges">
                            ${whaleBadge}
                            ${smartBadge}
                        </div>
                    </div>
                    <div class="whale-question">${this.escapeHtml(trade.question)}</div>
                    <div class="whale-metrics">
                        <span class="whale-side ${sideClass}">${trade.side.toUpperCase()}${outcomeText}</span>
                        <span class="whale-size">$${trade.size.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                        <span class="whale-time">${trade.timestamp}</span>
                    </div>
                </a>
            `;
        }).join('');
    }

    escapeHtml(unsafe) {
        if (unsafe === null || unsafe === undefined) return '';
        const str = String(unsafe);
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
}

// Initialize scanner
document.addEventListener('DOMContentLoaded', () => {
    window.scanner = new PolymarketScanner();
});
