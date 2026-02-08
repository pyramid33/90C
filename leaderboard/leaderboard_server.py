"""
90CENT Bot - Leaderboard Server

Standalone Flask server that collects stats from all bot instances
and serves a public leaderboard page.

Deploy this on any hosting platform (Render, Railway, VPS, etc.)
Usage: python leaderboard_server.py
"""

import os
import sqlite3
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leaderboard.db")

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            wallet_hint TEXT DEFAULT '',
            total_pnl REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            roi REAL DEFAULT 0,
            pnl_24h REAL DEFAULT 0,
            pnl_7d REAL DEFAULT 0,
            streak INTEGER DEFAULT 0,
            last_report TEXT,
            first_seen TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)
    # Add wallet_hint column if missing (migration for existing DBs)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN wallet_hint TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pnl_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            total_pnl REAL,
            recorded_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()


def generate_user_id(username, wallet_hint=""):
    """Generate a unique user ID from wallet hint (stable across username changes).
    Falls back to username if no wallet hint is provided."""
    if wallet_hint:
        raw = wallet_hint
    else:
        raw = username.lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@app.route('/api/report', methods=['POST'])
def report_stats():
    """Receive stats from a bot instance."""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    username = data.get("username", "Anonymous").strip()
    if not username:
        username = "Anonymous"

    wallet_hint = data.get("wallet_hint", "")
    user_id = generate_user_id(username, wallet_hint)
    now = datetime.utcnow().isoformat()

    conn = get_db()

    # Clean up duplicate entries from old username-based ID logic
    if wallet_hint:
        # 1) Delete entries with same wallet_hint but different user_id
        old_entries = conn.execute(
            "SELECT user_id FROM users WHERE wallet_hint = ? AND user_id != ?",
            (wallet_hint, user_id)
        ).fetchall()
        # 2) Also find entries created under old hash(username:wallet_hint) scheme
        #    Check common old usernames that may have created stale entries
        old_style_ids = set()
        for old_name in ["anonymous", username.lower().strip()]:
            old_raw = f"{old_name}:{wallet_hint}"
            old_hash = hashlib.sha256(old_raw.encode()).hexdigest()[:16]
            if old_hash != user_id:
                old_style_ids.add(old_hash)
        if old_style_ids:
            placeholders = ",".join("?" * len(old_style_ids))
            old_entries += conn.execute(
                f"SELECT user_id FROM users WHERE user_id IN ({placeholders})",
                list(old_style_ids)
            ).fetchall()
        # Delete all found duplicates
        for old in old_entries:
            conn.execute("DELETE FROM pnl_history WHERE user_id = ?", (old["user_id"],))
            conn.execute("DELETE FROM users WHERE user_id = ?", (old["user_id"],))

    # Check if user exists
    existing = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    if existing:
        conn.execute("""
            UPDATE users SET
                username = ?,
                wallet_hint = ?,
                total_pnl = ?,
                win_rate = ?,
                wins = ?,
                losses = ?,
                total_trades = ?,
                roi = ?,
                pnl_24h = ?,
                pnl_7d = ?,
                last_report = ?,
                is_active = 1
            WHERE user_id = ?
        """, (
            username,
            wallet_hint,
            data.get("total_pnl", 0),
            data.get("win_rate", 0),
            data.get("wins", 0),
            data.get("losses", 0),
            data.get("total_trades", 0),
            data.get("roi", 0),
            data.get("pnl_24h", 0),
            data.get("pnl_7d", 0),
            now,
            user_id
        ))
    else:
        conn.execute("""
            INSERT INTO users (user_id, username, wallet_hint, total_pnl, win_rate, wins, losses,
                             total_trades, roi, pnl_24h, pnl_7d, last_report, first_seen, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            user_id, username, wallet_hint,
            data.get("total_pnl", 0),
            data.get("win_rate", 0),
            data.get("wins", 0),
            data.get("losses", 0),
            data.get("total_trades", 0),
            data.get("roi", 0),
            data.get("pnl_24h", 0),
            data.get("pnl_7d", 0),
            now, now
        ))

    # Record PnL history point
    conn.execute("""
        INSERT INTO pnl_history (user_id, total_pnl, recorded_at)
        VALUES (?, ?, ?)
    """, (user_id, data.get("total_pnl", 0), now))

    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "user_id": user_id})


@app.route('/api/leaderboard')
def get_leaderboard():
    """Return leaderboard data sorted by total P&L."""
    conn = get_db()

    users = conn.execute("""
        SELECT username, total_pnl, win_rate, wins, losses, total_trades,
               roi, pnl_24h, pnl_7d, last_report, first_seen, is_active
        FROM users
        ORDER BY total_pnl DESC
    """).fetchall()

    conn.close()

    leaderboard = []
    for i, u in enumerate(users):
        leaderboard.append({
            "rank": i + 1,
            "username": u["username"],
            "total_pnl": round(u["total_pnl"], 2),
            "win_rate": round(u["win_rate"], 1),
            "wins": u["wins"],
            "losses": u["losses"],
            "total_trades": u["total_trades"],
            "roi": round(u["roi"], 2),
            "pnl_24h": round(u["pnl_24h"], 2),
            "pnl_7d": round(u["pnl_7d"], 2),
            "is_active": bool(u["is_active"]),
            "member_since": u["first_seen"][:10] if u["first_seen"] else ""
        })

    return jsonify(leaderboard)


@app.route('/api/stats')
def get_stats():
    """Return aggregate stats across all users."""
    conn = get_db()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_users,
            SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_users,
            SUM(total_pnl) as total_pnl,
            SUM(total_trades) as total_trades,
            AVG(win_rate) as avg_win_rate
        FROM users
    """).fetchone()
    conn.close()

    return jsonify({
        "total_users": row["total_users"] or 0,
        "active_users": row["active_users"] or 0,
        "total_pnl": round(row["total_pnl"] or 0, 2),
        "total_trades": row["total_trades"] or 0,
        "avg_win_rate": round(row["avg_win_rate"] or 0, 1)
    })


@app.route('/')
def leaderboard_page():
    """Serve the leaderboard HTML page."""
    return render_template_string(LEADERBOARD_HTML)


LEADERBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>90CENT | Leaderboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0c0e14;
            --card-bg: rgba(26, 29, 41, 0.7);
            --text-primary: #ffffff;
            --text-secondary: #8b949e;
            --accent-blue: #3d8aff;
            --accent-green: #00e676;
            --accent-red: #ff5252;
            --accent-gold: #ffd700;
            --accent-silver: #c0c0c0;
            --accent-bronze: #cd7f32;
            --border-color: rgba(255, 255, 255, 0.08);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
        }
        .container { max-width: 900px; margin: 0 auto; padding: 2rem; }
        header {
            text-align: center;
            margin-bottom: 2rem;
        }
        header h1 {
            font-size: 2rem;
            font-weight: 800;
            background: linear-gradient(90deg, #fff, var(--accent-blue));
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        header p { color: var(--text-secondary); margin-top: 4px; }
        .stats-row {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
            margin-bottom: 2rem;
        }
        .stat-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1rem;
            text-align: center;
        }
        .stat-card .label { color: var(--text-secondary); font-size: 0.75rem; text-transform: uppercase; }
        .stat-card .value { font-size: 1.5rem; font-weight: 700; margin-top: 4px; }
        .leaderboard-table {
            width: 100%;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            overflow: hidden;
        }
        .leaderboard-table table { width: 100%; border-collapse: collapse; }
        .leaderboard-table th {
            text-align: left;
            padding: 0.75rem 1rem;
            color: var(--text-secondary);
            font-size: 0.8rem;
            border-bottom: 1px solid var(--border-color);
            text-transform: uppercase;
        }
        .leaderboard-table td {
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.9rem;
        }
        .leaderboard-table tr:last-child td { border-bottom: none; }
        .leaderboard-table tr:hover { background: rgba(61, 138, 255, 0.05); }
        .rank { font-weight: 700; width: 50px; }
        .rank-1 { color: var(--accent-gold); }
        .rank-2 { color: var(--accent-silver); }
        .rank-3 { color: var(--accent-bronze); }
        .username { font-weight: 600; }
        .active-dot {
            display: inline-block;
            width: 8px; height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .active-dot.online { background: var(--accent-green); box-shadow: 0 0 6px var(--accent-green); }
        .active-dot.offline { background: var(--text-secondary); opacity: 0.4; }
        .positive { color: var(--accent-green); }
        .negative { color: var(--accent-red); }
        .pnl-val { font-weight: 700; }
        @media (max-width: 600px) {
            .stats-row { grid-template-columns: repeat(2, 1fr); }
            .leaderboard-table { font-size: 0.8rem; }
            .leaderboard-table th, .leaderboard-table td { padding: 0.5rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>90CENT LEADERBOARD</h1>
            <p>Live rankings across all bot users</p>
        </header>

        <div class="stats-row">
            <div class="stat-card">
                <div class="label">Total Users</div>
                <div class="value" id="total-users">0</div>
            </div>
            <div class="stat-card">
                <div class="label">Active Now</div>
                <div class="value positive" id="active-users">0</div>
            </div>
            <div class="stat-card">
                <div class="label">Combined P&L</div>
                <div class="value" id="total-pnl">$0</div>
            </div>
            <div class="stat-card">
                <div class="label">Total Trades</div>
                <div class="value" id="total-trades">0</div>
            </div>
        </div>

        <div class="leaderboard-table">
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>User</th>
                        <th>Total P&L</th>
                        <th>24H</th>
                        <th>Win Rate</th>
                        <th>W/L</th>
                        <th>ROI</th>
                        <th>Trades</th>
                    </tr>
                </thead>
                <tbody id="leaderboard-body">
                    <tr><td colspan="8" style="text-align:center;color:#8b949e;padding:2rem;">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function loadLeaderboard() {
            try {
                const [lbRes, statsRes] = await Promise.all([
                    fetch('/api/leaderboard'),
                    fetch('/api/stats')
                ]);
                const lb = await lbRes.json();
                const stats = await statsRes.json();

                document.getElementById('total-users').innerText = stats.total_users;
                document.getElementById('active-users').innerText = stats.active_users;
                const tpEl = document.getElementById('total-pnl');
                tpEl.innerText = `$${stats.total_pnl.toFixed(2)}`;
                tpEl.className = `value ${stats.total_pnl >= 0 ? 'positive' : 'negative'}`;
                document.getElementById('total-trades').innerText = stats.total_trades;

                const tbody = document.getElementById('leaderboard-body');
                if (lb.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#8b949e;padding:2rem;">No users yet</td></tr>';
                    return;
                }

                tbody.innerHTML = lb.map(u => {
                    const rankClass = u.rank <= 3 ? `rank-${u.rank}` : '';
                    const medal = u.rank === 1 ? ' &#x1F947;' : u.rank === 2 ? ' &#x1F948;' : u.rank === 3 ? ' &#x1F949;' : '';
                    const pnlClass = u.total_pnl >= 0 ? 'positive' : 'negative';
                    const pnl24Class = u.pnl_24h >= 0 ? 'positive' : 'negative';
                    const dotClass = u.is_active ? 'online' : 'offline';

                    return `<tr>
                        <td class="rank ${rankClass}">${u.rank}${medal}</td>
                        <td class="username"><span class="active-dot ${dotClass}"></span>${u.username}</td>
                        <td class="pnl-val ${pnlClass}">${u.total_pnl >= 0 ? '+' : ''}$${u.total_pnl.toFixed(2)}</td>
                        <td class="${pnl24Class}">${u.pnl_24h >= 0 ? '+' : ''}$${u.pnl_24h.toFixed(2)}</td>
                        <td style="color:${u.win_rate >= 50 ? '#00e676' : '#ff5252'}">${u.win_rate}%</td>
                        <td>${u.wins}/${u.losses}</td>
                        <td class="${u.roi >= 0 ? 'positive' : 'negative'}">${u.roi}%</td>
                        <td>${u.total_trades}</td>
                    </tr>`;
                }).join('');
            } catch(e) {
                console.error('Leaderboard load error:', e);
            }
        }

        loadLeaderboard();
        setInterval(loadLeaderboard, 30000);
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5053))
    print(f"90CENT Leaderboard Server running on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
