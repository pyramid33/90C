/**
 * Polymarket BTC Casino - Interactive Roulette Game
 * Real-time casino where roulette probabilities change with BTC market prices
 * 
 * @author Polymarket Casino Team
 * @version 2.0.0
 */

class PolymarketCasino {
    constructor() {
        // Game state
        this.balance = 1000.00;
        this.selectedNumber = null;
        this.selectedAmount = 0;
        this.spinning = false;
        this.lastMarketData = null;
        this.marketUpdateInterval = null;

        // American roulette configuration
        this.rouletteNumbers = [
            0, 28, 9, 26, 30, 11, 7, 20, 32, 17, 5, 22, 34, 15, 3, 24, 36, 13, 1,
            27, 10, 25, 29, 12, 8, 19, 31, 18, 6, 21, 33, 16, 4, 23, 35, 14, 2
        ];
        this.redNumbers = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36];

        // Initialize
        this.initializeElements();
        this.setupEventListeners();
        this.startMarketUpdates();
        this.hideLoading();
    }

    // =========================================================================
    // Initialization
    // =========================================================================

    initializeElements() {
        // Main elements
        this.balanceElement = document.getElementById('balance');
        this.spinButton = document.getElementById('spin-button');
        this.spinCost = document.getElementById('spin-cost');
        this.resultDisplay = document.getElementById('result-display');
        this.rouletteWheel = document.getElementById('roulette-wheel');

        // Market data elements
        this.btcUpProb = document.getElementById('btc-up-prob');
        this.btcDownProb = document.getElementById('btc-down-prob');
        this.spreadElement = document.getElementById('spread');
        this.redSlots = document.getElementById('red-slots');
        this.blackSlots = document.getElementById('black-slots');

        // Betting elements
        this.selectedNumberDisplay = document.getElementById('selected-number');
        this.currentBetAmount = document.getElementById('current-bet-amount');
        this.customAmountInput = document.getElementById('custom-amount');

        // Roulette elements
        this.rouletteBall = document.getElementById('roulette-ball');

        // Loading
        this.loadingOverlay = document.getElementById('loading-overlay');

        // Generate UI components
        this.generateNumberGrid();
        this.generateWheelNumbers(18, 18); // Default 50/50 split
    }

    setupEventListeners() {
        // Spin button
        this.spinButton.addEventListener('click', () => this.spin());

        // Color buttons
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.selectColorBet(e.currentTarget));
        });

        // Amount buttons
        document.querySelectorAll('.amount-btn').forEach(btn => {
            btn.addEventListener('click', () => this.selectAmount(parseFloat(btn.dataset.amount)));
        });

        // Custom amount
        const setCustomBtn = document.getElementById('set-custom-amount');
        if (setCustomBtn) {
            setCustomBtn.addEventListener('click', () => {
                const amount = parseFloat(this.customAmountInput.value);
                if (amount > 0) {
                    this.selectAmount(amount);
                }
            });
        }

        if (this.customAmountInput) {
            this.customAmountInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    const amount = parseFloat(this.customAmountInput.value);
                    if (amount > 0) {
                        this.selectAmount(amount);
                    }
                }
            });
        }

        // Update spin cost
        this.updateSpinCost();
    }

    // =========================================================================
    // Number Grid Generation
    // =========================================================================

    generateNumberGrid() {
        const numberGrid = document.getElementById('number-grid');
        if (!numberGrid) return;

        numberGrid.innerHTML = '';

        // Create numbers 0-36
        for (let i = 0; i <= 36; i++) {
            const numberBtn = document.createElement('button');
            numberBtn.className = `number-btn ${i === 0 ? 'zero' : ''}`;
            numberBtn.textContent = i;
            numberBtn.dataset.number = i;
            numberBtn.setAttribute('aria-label', `Bet on number ${i}`);
            numberBtn.addEventListener('click', () => this.selectNumber(numberBtn));
            numberGrid.appendChild(numberBtn);
        }
    }

    // =========================================================================
    // Betting Logic
    // =========================================================================

    selectNumber(button) {
        // Clear previous selections
        document.querySelectorAll('.number-btn.selected').forEach(btn => {
            btn.classList.remove('selected');
        });
        document.querySelectorAll('.color-btn.selected').forEach(btn => {
            btn.classList.remove('selected');
        });

        // Select new number
        button.classList.add('selected');
        this.selectedNumber = parseInt(button.dataset.number);
        this.selectedNumberDisplay.textContent = this.selectedNumber;

        this.updateBetDisplay();
    }

    selectColorBet(button) {
        // Clear previous selections
        document.querySelectorAll('.number-btn.selected').forEach(btn => {
            btn.classList.remove('selected');
        });
        document.querySelectorAll('.color-btn.selected').forEach(btn => {
            btn.classList.remove('selected');
        });

        // Select color bet
        button.classList.add('selected');
        this.selectedNumber = parseInt(button.dataset.number);

        const labels = {
            '-1': 'BTC Up (🔴)',
            '-2': 'BTC Down (⚫)',
            '-3': 'Zero (🟢)'
        };
        this.selectedNumberDisplay.textContent = labels[this.selectedNumber] || 'None';

        this.updateBetDisplay();
    }

    selectAmount(amount) {
        // Clear previous amount selection
        document.querySelectorAll('.amount-btn.selected').forEach(btn => {
            btn.classList.remove('selected');
        });

        // Validate amount
        if (amount > this.balance) {
            this.showNotification('Insufficient balance', 'error');
            return;
        }

        if (amount <= 0) {
            this.showNotification('Invalid amount', 'error');
            return;
        }

        this.selectedAmount = amount;

        // Highlight selected amount button
        const selectedBtn = document.querySelector(`.amount-btn[data-amount="${amount}"]`);
        if (selectedBtn) {
            selectedBtn.classList.add('selected');
        }

        this.updateBetDisplay();
        this.updateSpinCost();
    }

    updateBetDisplay() {
        this.currentBetAmount.textContent = this.selectedAmount.toFixed(2);

        // Enable/disable spin button
        const canSpin = this.selectedNumber !== null && this.selectedAmount > 0 && !this.spinning;
        this.spinButton.disabled = !canSpin;
    }

    updateSpinCost() {
        this.spinCost.textContent = this.selectedAmount.toFixed(2);
    }

    // =========================================================================
    // Market Data
    // =========================================================================

    async startMarketUpdates() {
        await this.updateMarketData();
        this.marketUpdateInterval = setInterval(() => this.updateMarketData(), 2000);
    }

    async updateMarketData() {
        try {
            const response = await fetch('/api/market-data');
            const data = await response.json();

            if (data.error) {
                this.updateMarketStatus('error', data.error);
                return;
            }

            this.lastMarketData = data;
            this.updateMarketDisplay(data);
            this.updateWheelVisualization(data);

        } catch (error) {
            this.updateMarketStatus('error', 'Connection error');
        }
    }

    updateMarketDisplay(data) {
        // Update probabilities
        const upProb = (data.yes_price * 100).toFixed(1);
        const downProb = (data.no_price * 100).toFixed(1);
        const spread = (data.spread * 100).toFixed(2);

        this.animateValue(this.btcUpProb, upProb + '%');
        this.animateValue(this.btcDownProb, downProb + '%');
        this.animateValue(this.spreadElement, spread + '¢');

        // Update connection status
        if (data.is_demo || data.market_name.includes('DEMO')) {
            this.updateMarketStatus('demo', 'Demo Mode');
        } else {
            this.updateMarketStatus('live', 'Live');
        }

        // Update colors based on trend
        this.updateTrendColors(data);
    }

    updateMarketStatus(status, text) {
        const marketIndicator = document.getElementById('market-status');
        if (!marketIndicator) return;

        const statusColors = {
            'live': '#00ff00',
            'demo': '#ffa500',
            'error': '#ff4444'
        };

        marketIndicator.textContent = `● ${text}`;
        marketIndicator.style.color = statusColors[status] || '#ffffff';
    }

    updateTrendColors(data) {
        const upElement = document.querySelector('.stat-card.up .stat-value');
        const downElement = document.querySelector('.stat-card.down .stat-value');

        if (!upElement || !downElement) return;

        if (data.yes_price > 0.5) {
            upElement.style.color = 'var(--green)';
            downElement.style.color = 'var(--red)';
        } else {
            upElement.style.color = 'var(--red)';
            downElement.style.color = 'var(--green)';
        }
    }

    // =========================================================================
    // Wheel Visualization
    // =========================================================================

    updateWheelVisualization(data) {
        const upProb = data.yes_price;
        const totalSlots = 37;
        const zeroSlots = 1;

        const redSlots = Math.floor(upProb * (totalSlots - zeroSlots));
        const blackSlots = (totalSlots - zeroSlots) - redSlots;

        // Update CSS custom properties for wheel gradient
        const redPercentage = (redSlots / totalSlots) * 100;
        const blackPercentage = (blackSlots / totalSlots) * 100;

        this.rouletteWheel.style.setProperty('--red-percentage', `${redPercentage}%`);
        this.rouletteWheel.style.setProperty('--black-percentage', `${blackPercentage}%`);

        // Update info display
        this.redSlots.textContent = redSlots;
        this.blackSlots.textContent = blackSlots;

        // Generate actual wheel with numbers
        this.generateWheelNumbers(redSlots, blackSlots);
    }

    generateWheelNumbers(redSlots, blackSlots) {
        const wheelNumbers = document.getElementById('wheel-numbers');
        if (!wheelNumbers) return;

        wheelNumbers.innerHTML = '';

        // Create number slots positioned around the wheel
        this.rouletteNumbers.forEach((number, index) => {
            const angle = (index / this.rouletteNumbers.length) * 360;
            const slot = document.createElement('div');
            slot.className = 'number-slot';
            slot.textContent = number;
            slot.setAttribute('data-number', number);

            // Determine color based on roulette rules
            if (number === 0) {
                slot.classList.add('green');
            } else if (this.redNumbers.includes(number)) {
                slot.classList.add('red');
            } else {
                slot.classList.add('black');
            }

            // Position around the wheel circumference
            slot.style.transform = `rotate(${angle}deg) translateY(-174px) rotate(-${angle}deg)`;
            wheelNumbers.appendChild(slot);
        });
    }

    animateValue(element, newValue) {
        if (!element) return;

        const currentValue = element.textContent;
        if (currentValue !== newValue) {
            element.style.transform = 'scale(1.1)';
            element.textContent = newValue;

            setTimeout(() => {
                element.style.transform = 'scale(1)';
            }, 200);
        }
    }

    // =========================================================================
    // Spin Logic
    // =========================================================================

    async spin() {
        if (this.spinning || this.selectedNumber === null || this.selectedAmount <= 0) {
            return;
        }

        this.spinning = true;
        this.spinButton.disabled = true;
        this.spinButton.innerHTML = `
            <div class="spin-icon">🎰</div>
            <div class="spin-text">SPINNING...</div>
        `;
        this.spinButton.classList.add('spinning');

        // Start animations
        this.rouletteWheel.classList.add('wheel-spinning');
        this.startBallAnimation();
        this.addSpinEffects();
        this.activateCasinoSounds();

        try {
            const response = await fetch('/api/spin', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    bet_number: this.selectedNumber,
                    bet_amount: this.selectedAmount
                })
            });

            const result = await response.json();

            if (result.error) {
                this.showNotification(result.error, 'error');
                this.resetSpinButton();
                return;
            }

            // Wait for animation, then show result
            setTimeout(() => {
                this.rouletteWheel.classList.remove('wheel-spinning');
                this.dropBallIntoSlot(result.winning_number);

                setTimeout(() => {
                    this.highlightWinningNumber(result.winning_number);
                    this.displayResult(result);
                    this.updateBalance(result.net_result);
                    this.resetSpinButton();
                }, 1000);
            }, 3000);

        } catch (error) {
            this.showNotification('Error spinning the roulette', 'error');
            this.resetSpinButton();
        }
    }

    startBallAnimation() {
        this.rouletteBall.classList.add('visible');
        this.rouletteBall.style.animation = 'ball-roll 2.5s ease-out infinite';
    }

    addSpinEffects() {
        this.rouletteWheel.classList.add('spinning-effects');

        // Add speed lines effect
        const speedLines = document.createElement('div');
        speedLines.className = 'speed-lines';
        speedLines.innerHTML = '💫'.repeat(12);
        this.rouletteWheel.appendChild(speedLines);

        // Remove effects after spin
        setTimeout(() => {
            this.rouletteWheel.classList.remove('spinning-effects');
            if (speedLines.parentNode) {
                speedLines.parentNode.removeChild(speedLines);
            }
        }, 3500);
    }

    activateCasinoSounds() {
        const soundIndicators = document.getElementById('casino-sounds');
        if (!soundIndicators) return;

        soundIndicators.style.display = 'flex';
        setTimeout(() => soundIndicators.classList.add('active-sounds'), 500);
        setTimeout(() => {
            soundIndicators.classList.remove('active-sounds');
            soundIndicators.style.display = 'none';
        }, 4000);
    }

    dropBallIntoSlot(winningNumber) {
        const angle = this.getNumberAngle(winningNumber);

        this.rouletteBall.style.animation = 'none';
        this.rouletteBall.style.transform = `translate(-50%, -50%) rotate(${angle}deg) translateY(-174px) rotate(-${angle}deg)`;
        this.rouletteBall.classList.add('ball-in-slot');

        // Add winning flash effect
        setTimeout(() => {
            this.rouletteWheel.classList.add('winning-flash');
            setTimeout(() => {
                this.rouletteWheel.classList.remove('winning-flash');
            }, 600);
        }, 200);
    }

    getNumberAngle(number) {
        const index = this.rouletteNumbers.indexOf(number);
        if (index !== -1) {
            return (index / this.rouletteNumbers.length) * 360;
        }
        return 0;
    }

    highlightWinningNumber(winningNumber) {
        // Remove previous highlights
        document.querySelectorAll('.number-btn.highlighted').forEach(btn => {
            btn.classList.remove('highlighted');
        });

        // Highlight winning number
        const winningBtn = document.querySelector(`.number-btn[data-number="${winningNumber}"]`);
        if (winningBtn) {
            winningBtn.classList.add('highlighted');
            setTimeout(() => winningBtn.classList.remove('highlighted'), 5000);
        }
    }

    resetSpinButton() {
        this.spinning = false;
        this.spinButton.disabled = false;
        this.spinButton.classList.remove('spinning');
        this.spinButton.innerHTML = `
            <div class="spin-icon">🎰</div>
            <div class="spin-text">SPIN!</div>
        `;

        // Reset ball
        this.rouletteBall.style.transform = 'translate(-50%, -50%)';
        this.rouletteBall.style.animation = '';
        this.rouletteBall.classList.remove('ball-in-slot');
    }

    // =========================================================================
    // Results Display
    // =========================================================================

    displayResult(result) {
        const isJackpot = result.payout >= 35;
        const winEmoji = result.payout > 0 ? (isJackpot ? '💰🎰' : '🎉') : '😞';
        const winText = result.payout > 0 ? (isJackpot ? 'JACKPOT!' : 'YOU WIN!') : 'You Lose';
        const winColor = result.payout > 0 ? 'var(--green)' : 'var(--red)';
        const betTypeLabel = this.getBetTypeLabel(this.selectedNumber);

        const jackpotClass = isJackpot ? 'jackpot-result' : '';

        this.resultDisplay.innerHTML = `
            <div class="result-content ${jackpotClass}">
                <div class="result-emoji">${winEmoji}</div>
                <div class="result-text" style="color: ${winColor}">${winText}</div>
                
                <div class="result-details">
                    <div class="result-row">
                        <span class="result-label">Winning Number:</span>
                        <span class="winning-number" style="color: ${this.getColorForNumber(result.winning_number)}">
                            ${result.winning_number}
                        </span>
                    </div>
                    <div class="result-row">
                        <span class="result-label">Your Bet:</span>
                        <span>${betTypeLabel}</span>
                    </div>
                    <div class="result-row">
                        <span class="result-label">Bet Amount:</span>
                        <span>${result.bet_amount.toFixed(2)} USDC</span>
                    </div>
                    <div class="result-row result-payout">
                        <span class="result-label">Winnings:</span>
                        <span style="color: ${winColor}">
                            ${result.net_result >= 0 ? '+' : ''}${result.net_result.toFixed(2)} USDC
                        </span>
                    </div>
                </div>
                
                <div class="result-market-info">
                    <div class="market-info-title">Market Probabilities</div>
                    <div>BTC Up: ${(result.market_data.yes_price * 100).toFixed(1)}% | BTC Down: ${(result.market_data.no_price * 100).toFixed(1)}%</div>
                    <div>Spread: ${(result.market_data.spread * 100).toFixed(2)}¢</div>
                </div>
            </div>
        `;

        this.resultDisplay.style.animation = 'result-fade-in 0.5s ease-out';
    }

    getBetTypeLabel(number) {
        switch (number) {
            case -1: return '🔴 BTC Up';
            case -2: return '⚫ BTC Down';
            case -3: return '🟢 Zero';
            default: return `Number ${number}`;
        }
    }

    getColorForNumber(number) {
        if (number === 0) return 'var(--green)';
        if (this.redNumbers.includes(number)) return 'var(--red)';
        return 'var(--text-secondary)';
    }

    // =========================================================================
    // Balance & Notifications
    // =========================================================================

    updateBalance(change) {
        this.balance += change;
        this.balanceElement.textContent = this.balance.toFixed(2);

        // Update color based on balance
        this.balanceElement.style.color = this.balance < 100 ? 'var(--red)' : 'var(--green)';

        // Animate balance change
        this.balanceElement.parentElement.classList.add('balance-updated');
        setTimeout(() => {
            this.balanceElement.parentElement.classList.remove('balance-updated');
        }, 500);
    }

    showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.innerHTML = `
            <span class="notification-icon">${type === 'error' ? '❌' : 'ℹ️'}</span>
            <span class="notification-message">${message}</span>
        `;

        document.body.appendChild(notification);

        // Animate in
        requestAnimationFrame(() => notification.classList.add('visible'));

        // Remove after delay
        setTimeout(() => {
            notification.classList.remove('visible');
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }

    hideLoading() {
        setTimeout(() => {
            if (this.loadingOverlay) {
                this.loadingOverlay.style.opacity = '0';
                setTimeout(() => {
                    this.loadingOverlay.style.display = 'none';
                }, 500);
            }
        }, 1000);
    }
}

// =============================================================================
// CSS Animations (injected)
// =============================================================================

const style = document.createElement('style');
style.textContent = `
    @keyframes result-fade-in {
        0% { opacity: 0; transform: translateY(20px); }
        100% { opacity: 1; transform: translateY(0); }
    }

    .notification {
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        border-radius: 10px;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        z-index: 10000;
        display: flex;
        align-items: center;
        gap: 10px;
        transform: translateX(120%);
        transition: transform 0.3s ease-out;
    }

    .notification.visible {
        transform: translateX(0);
    }

    .notification-error {
        background: linear-gradient(145deg, #ff4757, #c0392b);
        color: white;
    }

    .notification-info {
        background: linear-gradient(145deg, #3742fa, #2f3542);
        color: white;
    }

    .notification-success {
        background: linear-gradient(145deg, #2ecc71, #27ae60);
        color: white;
    }

    .result-content {
        text-align: center;
        animation: result-fade-in 0.5s ease-out;
    }

    .result-emoji {
        font-size: 3em;
        margin-bottom: 15px;
    }

    .result-text {
        font-size: 1.8em;
        font-weight: bold;
        margin-bottom: 20px;
    }

    .result-details {
        background: rgba(0, 0, 0, 0.2);
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 15px;
    }

    .result-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }

    .result-row:last-child {
        border-bottom: none;
    }

    .result-label {
        color: var(--text-secondary);
    }

    .winning-number {
        font-size: 1.5em;
        font-weight: bold;
    }

    .result-payout span:last-child {
        font-size: 1.2em;
        font-weight: bold;
    }

    .result-market-info {
        font-size: 0.9em;
        color: var(--text-secondary);
        border-top: 1px solid var(--border-color);
        padding-top: 15px;
    }

    .market-info-title {
        font-weight: bold;
        margin-bottom: 5px;
        color: var(--gold);
    }

    .balance-updated {
        animation: balance-pulse 0.5s ease-out;
    }

    @keyframes balance-pulse {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.1); }
    }
`;
document.head.appendChild(style);

// =============================================================================
// Initialize
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    window.casino = new PolymarketCasino();
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (window.casino && window.casino.marketUpdateInterval) {
        clearInterval(window.casino.marketUpdateInterval);
    }
});
