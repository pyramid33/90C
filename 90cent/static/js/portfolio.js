/**
 * Portfolio Risk Calculator - Frontend Logic
 */

class PortfolioApp {
    constructor() {
        this.currentWallet = null;
        this.init();
    }

    init() {
        this.setupEventListeners();
    }

    setupEventListeners() {
        // Load portfolio button
        document.getElementById('load-portfolio').addEventListener('click', () => {
            this.loadPortfolio();
        });

        // Enter key on wallet input
        document.getElementById('wallet-address').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.loadPortfolio();
            }
        });

        // Green Up modal close
        document.getElementById('greenup-close').addEventListener('click', () => {
            this.closeGreenUpModal();
        });
    }

    async loadPortfolio() {
        const walletInput = document.getElementById('wallet-address');
        const wallet = walletInput.value.trim();

        if (!wallet) {
            alert('Please enter a wallet address');
            return;
        }

        this.currentWallet = wallet;
        this.showLoading();

        try {
            // First fetching positions (now handles manual too on backend if we send them, 
            // but for now we'll just reload everything)

            // Note: to persist manual positions, we'd typically need a backend database.
            // For a static scanner, we can simulate by sending manual data in the request or temporary in-memory.
            // Here we assume the backend has an endpoint to accept manual positions or we send them.

            // SIMPLIFICATION: We will implement a backend endpoint to receive manual positions first.
            // See next step.

            // Fetch exposure data (includes summary)
            const response = await fetch(`/api/portfolio/exposure?wallet=${encodeURIComponent(wallet)}`);
            if (!response.ok) {
                throw new Error('Failed to fetch portfolio data');
            }

            const data = await response.json();

            if (data.error) {
                this.showError(data.error);
                return;
            }

            // Also fetch positions
            const posResponse = await fetch(`/api/portfolio/positions?wallet=${encodeURIComponent(wallet)}`);
            const posData = await posResponse.json();

            this.renderDashboard(data, posData);

        } catch (error) {
            console.error('Error loading portfolio:', error);
            this.showError('Failed to load portfolio. Please check the wallet address.');
        }
    }

    showLoading() {
        document.getElementById('loading-state').style.display = 'flex';
        document.getElementById('portfolio-dashboard').style.display = 'none';
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

    renderDashboard(exposureData, positionsData) {
        this.hideLoading();

        const summary = exposureData.summary;
        const positions = positionsData.positions || [];

        if (positions.length === 0) {
            this.showError('No positions found for this wallet');
            return;
        }

        document.getElementById('portfolio-dashboard').style.display = 'block';

        // Render summary
        document.getElementById('total-positions').textContent = summary.total_positions;
        document.getElementById('total-cost').textContent = this.formatCurrency(summary.total_cost);
        document.getElementById('current-value').textContent = this.formatCurrency(summary.total_current_value);

        const pnlElement = document.getElementById('total-pnl');
        pnlElement.textContent = this.formatCurrency(summary.total_pnl) + ` (${summary.total_pnl_percent.toFixed(2)}%)`;
        pnlElement.className = 'summary-value ' + (summary.total_pnl >= 0 ? 'positive' : 'negative');

        // Render category exposure
        this.renderExposure('category-exposure', exposureData.by_category);

        // Render theme exposure
        this.renderExposure('theme-exposure', exposureData.by_theme);

        // Render positions table
        this.renderPositions(positions);
    }

    renderExposure(containerId, exposureData) {
        const container = document.getElementById(containerId);

        if (!exposureData || Object.keys(exposureData).length === 0) {
            container.innerHTML = '<div class="empty-text">No exposure data</div>';
            return;
        }

        container.innerHTML = Object.values(exposureData)
            .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))
            .map(exp => `
                <div class="exposure-card">
                    <div class="exposure-header">
                        <span class="exposure-category">${this.escapeHtml(exp.category)}</span>
                        <span class="exposure-count">${exp.positions_count} position${exp.positions_count !== 1 ? 's' : ''}</span>
                    </div>
                    <div class="exposure-metrics">
                        <div class="exposure-metric">
                            <span class="metric-label">Cost</span>
                            <span class="metric-value">${this.formatCurrency(exp.total_cost)}</span>
                        </div>
                        <div class="exposure-metric">
                            <span class="metric-label">Value</span>
                            <span class="metric-value">${this.formatCurrency(exp.total_current_value)}</span>
                        </div>
                        <div class="exposure-metric">
                            <span class="metric-label">P&L</span>
                            <span class="metric-value ${exp.pnl >= 0 ? 'positive' : 'negative'}">
                                ${this.formatCurrency(exp.pnl)} (${exp.pnl_percent.toFixed(2)}%)
                            </span>
                        </div>
                    </div>
                </div>
            `).join('');
    }

    renderPositions(positions) {
        const container = document.getElementById('positions-table');

        container.innerHTML = `
            <div class="positions-grid">
                ${positions.map(pos => `
                    <div class="position-card">
                        <div class="position-header">
                            <div class="position-question">${this.escapeHtml(pos.question)}</div>
                            <span class="position-side ${pos.side.toLowerCase()}">${pos.side}</span>
                        </div>
                        <div class="position-metrics">
                            <div class="position-metric">
                                <span class="metric-label">Size</span>
                                <span class="metric-value">${pos.size.toFixed(2)} shares</span>
                            </div>
                            <div class="position-metric">
                                <span class="metric-label">Avg Entry</span>
                                <span class="metric-value">${this.formatPercent(pos.avg_entry_price)}</span>
                            </div>
                            <div class="position-metric">
                                <span class="metric-label">Current</span>
                                <span class="metric-value">${this.formatPercent(pos.current_price)}</span>
                            </div>
                            <div class="position-metric">
                                <span class="metric-label">P&L</span>
                                <span class="metric-value ${pos.pnl >= 0 ? 'positive' : 'negative'}">
                                    ${this.formatCurrency(pos.pnl)} (${pos.pnl_percent.toFixed(2)}%)
                                </span>
                            </div>
                        </div>
                        <div class="position-actions">
                            <a href="${pos.market_url}" target="_blank" class="position-link">View Market</a>
                            <button class="greenup-btn" data-condition="${pos.condition_id}">
                                🎯 Calculate Green Up
                            </button>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;

        // Add green up button listeners
        container.querySelectorAll('.greenup-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const conditionId = e.currentTarget.dataset.condition;
                this.showGreenUp(conditionId);
            });
        });
    }

    async showGreenUp(conditionId) {
        try {
            const response = await fetch(
                `/api/portfolio/greenup?wallet=${encodeURIComponent(this.currentWallet)}&condition_id=${encodeURIComponent(conditionId)}`
            );
            const data = await response.json();

            if (data.error) {
                alert(data.error);
                return;
            }

            this.renderGreenUpModal(data);

        } catch (error) {
            console.error('Error calculating Green Up:', error);
            alert('Failed to calculate Green Up');
        }
    }

    renderGreenUpModal(data) {
        const body = document.getElementById('greenup-body');
        const result = data.result;
        const hedge = data.hedge_recommendation;
        const current = data.current_position;

        body.innerHTML = `
            <div class="greenup-section">
                <h3>Current Position</h3>
                <div class="greenup-metrics">
                    <div class="greenup-metric">
                        <span>Side:</span>
                        <span class="position-side ${current.side.toLowerCase()}">${current.side}</span>
                    </div>
                    <div class="greenup-metric">
                        <span>Size:</span>
                        <span>${current.size.toFixed(2)} shares</span>
                    </div>
                    <div class="greenup-metric">
                        <span>Avg Price:</span>
                        <span>${this.formatPercent(current.avg_price)}</span>
                    </div>
                    <div class="greenup-metric">
                        <span>Current Value:</span>
                        <span>${this.formatCurrency(current.current_value)}</span>
                    </div>
                </div>
            </div>

            <div class="greenup-section">
                <h3>Hedge Recommendation</h3>
                ${result.locked_in ? `
                    <div class="greenup-success">
                        ✅ You can lock in a guaranteed profit!
                    </div>
                    <div class="greenup-metrics">
                        <div class="greenup-metric">
                            <span>Buy:</span>
                            <span>${hedge.size.toFixed(2)} shares of ${hedge.side}</span>
                        </div>
                        <div class="greenup-metric">
                            <span>At Price:</span>
                            <span>${this.formatPercent(hedge.at_price)}</span>
                        </div>
                        <div class="greenup-metric">
                            <span>Cost:</span>
                            <span>${this.formatCurrency(hedge.cost)}</span>
                        </div>
                        <div class="greenup-metric highlight">
                            <span><strong>Guaranteed Profit:</strong></span>
                            <span class="positive"><strong>${this.formatCurrency(result.guaranteed_profit)}</strong></span>
                        </div>
                    </div>
                ` : `
                    <div class="greenup-warning">
                        ⚠️ This position is currently at a loss. Hedging now would lock in the loss.
                    </div>
                    <div class="greenup-metrics">
                        <div class="greenup-metric">
                            <span>Current P&L:</span>
                            <span class="negative">${this.formatCurrency(result.pnl_before_hedge)}</span>
                        </div>
                    </div>
                    <div class="greenup-explanation">
                        Consider waiting for the price to move in your favor before hedging.
                    </div>
                `}
            </div>
        `;

        document.getElementById('greenup-modal').classList.add('active');
    }

    closeGreenUpModal() {
        document.getElementById('greenup-modal').classList.remove('active');
    }

    formatCurrency(value) {
        if (value === null || value === undefined || isNaN(value)) return '$0';
        return `$${Math.abs(value).toFixed(2)}`;
    }

    formatPercent(value) {
        return `${(value * 100).toFixed(1)}%`;
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

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    window.portfolioApp = new PortfolioApp();
});
