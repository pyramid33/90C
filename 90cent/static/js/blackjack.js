/**
 * Polymarket Blackjack - Market-Driven Card Game
 * Dealer rules and card values change based on Polymarket volatility and price movements
 */

class PolymarketBlackjack {
    constructor() {
        this.gameActive = false;
        this.marketConditions = {};
        this.currentGameState = {};

        this.initializeElements();
        this.setupEventListeners();
        this.startMarketUpdates();
        this.updateGameState();
        this.hideLoading();
    }

    initializeElements() {
        // Game areas
        this.dealerHand = document.getElementById('dealer-hand');
        this.playerHand = document.getElementById('player-hand');
        this.dealerValue = document.getElementById('dealer-value');
        this.playerValue = document.getElementById('player-value');

        // Buttons
        this.dealBtn = document.getElementById('deal-btn');
        this.hitBtn = document.getElementById('hit-btn');
        this.standBtn = document.getElementById('stand-btn');
        this.marketBetBtn = document.getElementById('market-bet-btn');

        // Bet elements
        this.betAmountInput = document.getElementById('bet-amount');
        this.placeBetBtn = document.getElementById('place-bet-btn');
        this.minBetDisplay = document.getElementById('min-bet');
        this.maxBetDisplay = document.getElementById('max-bet');

        // Status
        this.gameStatus = document.getElementById('game-status');
        this.marketBetIndicator = document.getElementById('market-bet-indicator');

        // Market displays
        this.volatilityElement = document.getElementById('volatility');
        this.trendStrengthElement = document.getElementById('trend-strength');
        this.dealerRuleElement = document.getElementById('dealer-rule');
        this.volatilityDesc = document.getElementById('volatility-desc');
        this.trendDesc = document.getElementById('trend-desc');
        this.dealerDesc = document.getElementById('dealer-desc');
        this.marketStatus = document.getElementById('market-status');

        // Event displays
        this.eventsContainer = document.createElement('div');
        this.eventsContainer.id = 'market-events-container';
        this.eventsContainer.className = 'market-events-panel';
        document.querySelector('.market-status').appendChild(this.eventsContainer);

        this.activePowersContainer = document.createElement('div');
        this.activePowersContainer.id = 'active-powers-container';
        this.activePowersContainer.className = 'active-powers-panel';
        document.querySelector('.game-controls').appendChild(this.activePowersContainer);

        // Loading
        this.loadingOverlay = document.getElementById('loading-overlay');
    }

    setupEventListeners() {
        this.dealBtn.addEventListener('click', () => this.deal());
        this.hitBtn.addEventListener('click', () => this.hit());
        this.standBtn.addEventListener('click', () => this.stand());
        this.marketBetBtn.addEventListener('click', () => this.activateMarketBet());

        // Bet functionality
        this.placeBetBtn.addEventListener('click', () => this.placeBet());
        this.betAmountInput.addEventListener('input', () => this.validateBetAmount());
        this.betAmountInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.placeBet();
            }
        });
    }

    async startMarketUpdates() {
        await this.updateMarketConditions();
        setInterval(() => this.updateMarketConditions(), 3000); // Update every 3 seconds

        // Start event updates
        setInterval(() => this.updateMarketEvents(), 2000); // Update events every 2 seconds
    }

    async updateMarketConditions() {
        try {
            const response = await fetch('/api/market-conditions');
            const data = await response.json();

            if (data.error) {
                console.warn('Error fetching market conditions:', data.error);
                this.marketStatus.textContent = '● Demo Mode';
                this.marketStatus.style.color = '#ffa500';
                return;
            }

            this.marketConditions = data;

            // Update displays
            this.updateMarketDisplays(data);

        } catch (error) {
            console.error('Error updating market conditions:', error);
            this.marketStatus.textContent = '● Error';
            this.marketStatus.style.color = '#ff4444';
        }
    }

    async updateMarketEvents() {
        try {
            const response = await fetch('/api/market-events');
            const data = await response.json();

            if (data.error) {
                return;
            }

            // Update active events display
            this.updateActiveEventsDisplay(data.active_events);

            // Update active powers display
            this.updateActivePowersDisplay(data.active_powers);

        } catch (error) {
            console.error('Error updating market events:', error);
        }
    }

    updateActiveEventsDisplay(events) {
        if (!events || events.length === 0) {
            this.eventsContainer.innerHTML = '';
            return;
        }

        let eventsHtml = '<div class="events-header">⚡ MARKET EVENTS ⚡</div>';

        events.forEach(event => {
            const remainingTime = Math.max(0, Math.ceil(event.expires_at - Date.now() / 1000));
            const progressPercent = (remainingTime / event.duration) * 100;

            eventsHtml += `
                <div class="active-event" style="border-color: ${event.color}; box-shadow: 0 0 15px ${event.color}40;">
                    <div class="event-icon">${event.icon}</div>
                    <div class="event-content">
                        <div class="event-title" style="color: ${event.color};">${event.title}</div>
                        <div class="event-description">${event.description}</div>
                        <div class="event-timer">
                            <div class="timer-bar" style="background: linear-gradient(90deg, ${event.color} 0%, ${event.color}${Math.max(20, progressPercent)}% 100%); width: ${progressPercent}%;"></div>
                            <div class="timer-text">${remainingTime}s</div>
                        </div>
                    </div>
                </div>
            `;
        });

        this.eventsContainer.innerHTML = eventsHtml;
    }

    updateActivePowersDisplay(powers) {
        if (!powers || powers.length === 0) {
            this.activePowersContainer.innerHTML = '';
            return;
        }

        let powersHtml = '<div class="powers-header">🔥 ACTIVE POWERS 🔥</div>';

        powers.forEach(powerData => {
            const power = powerData.power;
            const event = powerData.event;

            let powerIcon = '❓';
            let powerName = power.replace('_', ' ').toUpperCase();
            let powerDesc = 'Unknown power';

            switch (power) {
                case 'shield':
                    powerIcon = '🛡️';
                    powerName = 'SHIELD';
                    powerDesc = 'Prevents busts';
                    break;
                case 'double_down':
                    powerIcon = '💰';
                    powerName = 'DOUBLE DOWN';
                    powerDesc = '2x payout multiplier';
                    break;
                case 'lucky_seven':
                    powerIcon = '🎰';
                    powerName = 'LUCKY SEVEN';
                    powerDesc = 'Auto-win on 7';
                    break;
                case 'dealer_freeze':
                    powerIcon = '🧊';
                    powerName = 'DEALER FREEZE';
                    powerDesc = 'Dealer cannot hit';
                    break;
                case 'card_boost':
                    powerIcon = '⚡';
                    powerName = 'CARD BOOST';
                    powerDesc = '+2 to all cards';
                    break;
                case 'instant_win':
                    powerIcon = '👑';
                    powerName = 'INSTANT WIN';
                    powerDesc = 'Guaranteed victory';
                    break;
            }

            powersHtml += `
                <div class="active-power" style="border-color: ${event ? event.color : '#666'};">
                    <div class="power-icon">${powerIcon}</div>
                    <div class="power-content">
                        <div class="power-name" style="color: ${event ? event.color : '#666'};">${powerName}</div>
                        <div class="power-description">${powerDesc}</div>
                    </div>
                </div>
            `;
        });

        this.activePowersContainer.innerHTML = powersHtml;
    }

    updateMarketDisplays(data) {
        // Volatility
        const volatilityPercent = (data.volatility * 100).toFixed(1);
        this.volatilityElement.textContent = volatilityPercent + '%';

        if (data.volatility > 0.7) {
            this.volatilityDesc.textContent = 'Very High';
            this.volatilityDesc.style.color = '#ff4444';
        } else if (data.volatility > 0.5) {
            this.volatilityDesc.textContent = 'High';
            this.volatilityDesc.style.color = '#ffa500';
        } else if (data.volatility > 0.3) {
            this.volatilityDesc.textContent = 'Moderate';
            this.volatilityDesc.style.color = '#fff3cd';
        } else {
            this.volatilityDesc.textContent = 'Low';
            this.volatilityDesc.style.color = '#d4edda';
        }

        // Trend strength
        const trendPercent = (data.trend_strength * 100).toFixed(1);
        this.trendStrengthElement.textContent = trendPercent + '%';

        if (data.trend_strength > 0.6) {
            this.trendDesc.textContent = 'Very Strong';
            this.trendDesc.style.color = '#ff4444';
        } else if (data.trend_strength > 0.4) {
            this.trendDesc.textContent = 'Strong';
            this.trendDesc.style.color = '#ffa500';
        } else if (data.trend_strength > 0.2) {
            this.trendDesc.textContent = 'Moderate';
            this.trendDesc.style.color = '#fff3cd';
        } else {
            this.trendDesc.textContent = 'Weak';
            this.trendDesc.style.color = '#d4edda';
        }

        // Market status
        this.marketStatus.textContent = '● Connected';
        this.marketStatus.style.color = '#00ff00';
    }

    async updateGameState() {
        try {
            const response = await fetch('/api/game-state');
            const data = await response.json();

            if (data.error) {
                console.warn('Game not available:', data.error);
                return;
            }

            this.currentGameState = data;
            this.updateGameDisplay(data);

        } catch (error) {
            console.error('Error updating game state:', error);
        }
    }

    updateGameDisplay(data) {
        // Update dealer rule display
        if (data.dealer_rule) {
            this.dealerRuleElement.textContent = data.dealer_rule;
        }

        // Update market bet indicator
        if (data.market_bet_active) {
            this.marketBetIndicator.classList.add('active');
            this.marketBetBtn.disabled = true;
        } else {
            this.marketBetIndicator.classList.remove('active');
            this.marketBetBtn.disabled = !data.game_active;
        }

        // Update bet information
        if (data.player_balance !== undefined) {
            document.getElementById('balance').textContent = data.player_balance.toFixed(2);
        }
        if (data.min_bet !== undefined) {
            this.minBetDisplay.textContent = data.min_bet;
        }
        if (data.max_bet !== undefined) {
            this.maxBetDisplay.textContent = data.max_bet;
        }
        if (data.current_bet !== undefined && data.current_bet > 0) {
            this.placeBetBtn.disabled = true;
            this.placeBetBtn.textContent = `BET: ${data.current_bet} USDC`;
            this.betAmountInput.disabled = true;
        } else {
            this.placeBetBtn.disabled = false;
            this.placeBetBtn.textContent = 'INITIALIZE BET';
            this.betAmountInput.disabled = false;
        }

        // Update button states
        this.dealBtn.disabled = data.game_active || data.current_bet === 0;
        this.hitBtn.disabled = !data.game_active;
        this.standBtn.disabled = !data.game_active;
        this.marketBetBtn.disabled = !data.game_active || data.market_bet_active;
    }

    async deal() {
        try {
            this.showStatusMessage('Dealing cards...', 'info');

            const response = await fetch('/api/deal');
            const result = await response.json();

            if (result.error) {
                this.showStatusMessage(result.error, 'error');
                return;
            }

            // Update displays
            this.updateHands(result.player_hand, result.dealer_hand);
            this.updateValues(result.player_value, result.dealer_visible_value);
            this.updateMarketDisplays(result.market_conditions);

            // Update balance and bet info
            if (result.player_balance !== undefined) {
                document.getElementById('balance').textContent = result.player_balance.toFixed(2);
            }

            // Handle new events
            if (result.new_events && result.new_events.length > 0) {
                this.showEventNotifications(result.new_events);
            }

            this.gameActive = true;
            this.updateGameDisplay({
                game_active: true,
                market_bet_active: false,
                current_bet: result.current_bet,
                player_balance: result.player_balance,
                active_powers: result.active_powers || []
            });

            this.showStatusMessage(`Quantum deal initialized! Current bet: ${result.current_bet} USDC | Dealer rule: ${result.dealer_rule}`, 'success');

        } catch (error) {
            console.error('Deal error:', error);
            this.showStatusMessage('Error dealing cards', 'error');
        }
    }

    async hit() {
        try {
            const response = await fetch('/api/hit');
            const result = await response.json();

            if (result.error) {
                this.showStatusMessage(result.error, 'error');
                return;
            }

            if (result.action === 'bust') {
                // Player busted
                this.updateHands(result.player_hand, result.dealer_hand);
                this.updateValues(result.player_value, result.dealer_value);

                // Update balance if bet result is available
                if (result.bet_result) {
                    document.getElementById('balance').textContent = result.bet_result.new_balance.toFixed(2);
                }

                this.gameActive = false;
                this.updateGameDisplay({
                    game_active: false,
                    current_bet: 0,
                    player_balance: result.bet_result ? result.bet_result.new_balance : undefined
                });

                let message = result.message;
                if (result.bet_result) {
                    message += ` | ${result.bet_result.message}`;
                }

                this.showStatusMessage(message, 'lose');
            } else {
                // Continue playing
                this.updateHands(result.player_hand, result.dealer_hand);
                this.updateValues(result.player_value, result.dealer_visible_value);
                this.showStatusMessage('Hit! Take another card or stand.', 'info');
            }

        } catch (error) {
            console.error('Hit error:', error);
            this.showStatusMessage('Error taking card', 'error');
        }
    }

    async stand() {
        try {
            this.showStatusMessage('Standing... dealer plays.', 'info');

            const response = await fetch('/api/stand');
            const result = await response.json();

            if (result.error) {
                this.showStatusMessage(result.error, 'error');
                return;
            }

            // Show dealer's actions
            this.animateDealerActions(result.dealer_actions);

            // Update final hands
            setTimeout(() => {
                this.updateHands(result.player_hand, result.dealer_hand);
                this.updateValues(result.player_value, result.dealer_value);

                // Update balance if bet result is available
                if (result.bet_result) {
                    document.getElementById('balance').textContent = result.bet_result.new_balance.toFixed(2);
                }

                this.gameActive = false;
                this.updateGameDisplay({
                    game_active: false,
                    current_bet: 0,
                    player_balance: result.bet_result ? result.bet_result.new_balance : undefined
                });

                let message = result.message;
                if (result.bet_result) {
                    message += ` | ${result.bet_result.message}`;
                }

                // Handle dealer freeze messaging
                if (result.dealer_frozen) {
                    message += ' (Dealer was frozen!)';
                }

                if (result.result === 'player_win') {
                    this.showStatusMessage(message, 'win');
                } else if (result.result === 'dealer_win') {
                    this.showStatusMessage(message, 'lose');
                } else {
                    this.showStatusMessage(message, 'tie');
                }
            }, (result.dealer_actions.length * 1000) + 500);

        } catch (error) {
            console.error('Stand error:', error);
            this.showStatusMessage('Error standing', 'error');
        }
    }

    async activateMarketBet() {
        try {
            const response = await fetch('/api/market-bet');
            const result = await response.json();

            if (result.error) {
                this.showStatusMessage(result.error, 'error');
                return;
            }

            // Update displays
            if (result.player_value) {
                this.updateValues(result.player_value, null);
            }

            this.marketBetIndicator.classList.add('active');
            this.marketBetBtn.disabled = true;

            this.showStatusMessage(result.message, 'boost');

        } catch (error) {
            console.error('Market bet error:', error);
            this.showStatusMessage('Error activating market bet', 'error');
        }
    }

    async placeBet() {
        const amount = parseFloat(this.betAmountInput.value);

        if (isNaN(amount) || amount <= 0) {
            this.showStatusMessage('Please enter a valid bet amount', 'error');
            return;
        }

        try {
            this.showStatusMessage('Placing bet...', 'info');

            const response = await fetch('/api/place-bet', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ amount: amount })
            });

            const result = await response.json();

            if (result.error) {
                this.showStatusMessage(result.error, 'error');
                return;
            }

            // Update balance display
            document.getElementById('balance').textContent = result.remaining_balance.toFixed(2);

            // Update UI
            this.placeBetBtn.disabled = true;
            this.placeBetBtn.textContent = `BET: ${result.bet_amount} USDC`;
            this.betAmountInput.disabled = true;
            this.dealBtn.disabled = false;

            this.showStatusMessage(result.message, 'success');

        } catch (error) {
            console.error('Bet placement error:', error);
            this.showStatusMessage('Error placing bet', 'error');
        }
    }

    validateBetAmount() {
        const amount = parseFloat(this.betAmountInput.value);
        const minBet = parseFloat(this.minBetDisplay.textContent);
        const maxBet = parseFloat(this.maxBetDisplay.textContent);

        if (amount < minBet) {
            this.betAmountInput.value = minBet;
        } else if (amount > maxBet) {
            this.betAmountInput.value = maxBet;
        }
    }

    showEventNotifications(events) {
        events.forEach(event => {
            this.showStatusMessage(`${event.icon} ${event.title}: ${event.description}`, 'event', 5000);

            // Create special event notification overlay
            this.createEventOverlay(event);
        });
    }

    createEventOverlay(event) {
        const overlay = document.createElement('div');
        overlay.className = 'event-overlay';
        overlay.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: linear-gradient(145deg, rgba(0, 0, 26, 0.95), rgba(15, 15, 35, 0.9));
            border: 3px solid ${event.color};
            border-radius: 20px;
            padding: 30px;
            z-index: 10000;
            box-shadow: 0 0 50px ${event.color}80, inset 0 1px 0 ${event.color}40;
            animation: eventPopup 0.8s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            max-width: 500px;
            text-align: center;
        `;

        overlay.innerHTML = `
            <div style="font-size: 3em; margin-bottom: 15px;">${event.icon}</div>
            <h2 style="color: ${event.color}; font-size: 1.8em; margin-bottom: 10px; text-shadow: 0 0 20px ${event.color}60;">
                ${event.title}
            </h2>
            <p style="color: #ffffff; font-size: 1.2em; line-height: 1.4; margin-bottom: 20px;">
                ${event.description}
            </p>
            <div style="color: ${event.color}; font-size: 0.9em; font-weight: bold;">
                POWER ACTIVATED • ${event.duration}s DURATION
            </div>
        `;

        document.body.appendChild(overlay);

        // Remove after animation
        setTimeout(() => {
            overlay.style.animation = 'eventFadeOut 0.5s ease-out';
            setTimeout(() => {
                if (overlay.parentNode) {
                    overlay.parentNode.removeChild(overlay);
                }
            }, 500);
        }, 4000);
    }

    updateHands(playerCards, dealerCards) {
        // Update player hand
        this.playerHand.innerHTML = '';
        if (playerCards && playerCards.length > 0) {
            playerCards.forEach(card => {
                const cardElement = this.createCardElement(card);
                this.playerHand.appendChild(cardElement);
            });
        } else {
            this.playerHand.innerHTML = '<div class="card-placeholder">No cards</div>';
        }

        // Update dealer hand
        this.dealerHand.innerHTML = '';
        if (dealerCards && dealerCards.length > 0) {
            dealerCards.forEach(card => {
                const cardElement = this.createCardElement(card);
                this.dealerHand.appendChild(cardElement);
            });
        } else {
            this.dealerHand.innerHTML = '<div class="card-placeholder">No cards</div>';
        }
    }

    createCardElement(card) {
        const cardDiv = document.createElement('div');
        cardDiv.className = `card ${card.suit === '♥' || card.suit === '♦' ? 'red' : 'black'}`;

        cardDiv.innerHTML = `
            <div class="card-rank">${card.rank}</div>
            <div class="card-suit">${card.suit}</div>
            <div class="card-rank bottom">${card.rank}</div>
        `;

        return cardDiv;
    }

    updateValues(playerValue, dealerValue) {
        if (playerValue) {
            const valueText = playerValue.is_soft ? `${playerValue.total} (Soft)` : playerValue.total.toString();
            this.playerValue.textContent = `Value: ${valueText}`;
            this.playerValue.style.color = playerValue.busted ? '#ff4444' : '#ffd700';
        }

        if (dealerValue !== null && dealerValue !== undefined) {
            if (typeof dealerValue === 'number') {
                this.dealerValue.textContent = `Showing: ${dealerValue}`;
            } else if (dealerValue && typeof dealerValue === 'object') {
                const valueText = dealerValue.is_soft ? `${dealerValue.total} (Soft)` : dealerValue.total.toString();
                this.dealerValue.textContent = `Value: ${valueText}`;
                this.dealerValue.style.color = dealerValue.busted ? '#ff4444' : '#ffd700';
            }
        }
    }

    animateDealerActions(actions) {
        let delay = 0;
        actions.forEach(action => {
            setTimeout(() => {
                if (action.action === 'hit') {
                    this.showStatusMessage(`Dealer hits: ${action.card.rank}${action.card.suit} (Total: ${action.new_value})`, 'dealer');
                } else if (action.action === 'stand') {
                    this.showStatusMessage(`Dealer stands with ${action.value}`, 'dealer');
                }
            }, delay);
            delay += 1000;
        });
    }

    showStatusMessage(message, type = 'info', duration = 5000) {
        const statusDiv = this.gameStatus.querySelector('.status-message');

        // Clear existing classes
        statusDiv.className = 'status-message';

        // Add type class
        statusDiv.classList.add(type);

        statusDiv.textContent = message;

        // Special styling for event messages
        if (type === 'event') {
            statusDiv.style.borderColor = '#00ffff';
            statusDiv.style.boxShadow = '0 0 20px rgba(0, 255, 255, 0.5)';
            statusDiv.style.animation = 'eventGlow 0.5s ease-in-out';
        }

        // Auto-clear after specified duration for non-final messages
        if (type !== 'win' && type !== 'lose' && type !== 'tie') {
            setTimeout(() => {
                statusDiv.textContent = '';
                statusDiv.className = 'status-message';
                statusDiv.style.borderColor = '';
                statusDiv.style.boxShadow = '';
                statusDiv.style.animation = '';
            }, duration);
        }
    }

    hideLoading() {
        setTimeout(() => {
            this.loadingOverlay.style.opacity = '0';
            setTimeout(() => {
                this.loadingOverlay.style.display = 'none';
            }, 500);
        }, 1500);
    }
}

// Add CSS for status message types
const style = document.createElement('style');
style.textContent = `
    .status-message.win {
        color: #00ff00 !important;
        font-weight: bold;
        text-shadow: 0 0 10px rgba(0, 255, 0, 0.5);
    }

    .status-message.lose {
        color: #ff4444 !important;
        font-weight: bold;
        text-shadow: 0 0 10px rgba(255, 68, 68, 0.5);
    }

    .status-message.tie {
        color: #ffa500 !important;
        font-weight: bold;
        text-shadow: 0 0 10px rgba(255, 165, 0, 0.5);
    }

    .status-message.boost {
        color: #ffd700 !important;
        font-weight: bold;
        text-shadow: 0 0 10px rgba(255, 215, 0, 0.5);
    }

    .status-message.dealer {
        color: #8888ff !important;
        font-style: italic;
    }

    .status-message.error {
        color: #ff4444 !important;
        background: rgba(255, 68, 68, 0.1);
        border: 1px solid rgba(255, 68, 68, 0.3);
    }
`;
document.head.appendChild(style);

// Initialize blackjack when page loads
document.addEventListener('DOMContentLoaded', () => {
    new PolymarketBlackjack();
});
