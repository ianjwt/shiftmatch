// ---- State ----
let allScoredShifts = [];
let currentPreferences = {};
let savedCredentials = {};
let _prefDebounceTimer = null;

// ---- Helpers ----
function getSelectedValues(containerId) {
    const checkboxes = document.querySelectorAll(`#${containerId} input[type="checkbox"]:checked`);
    return Array.from(checkboxes).map(cb => cb.value);
}

function getCommitteeRanking() {
    const items = document.querySelectorAll('#committee-list .rank-item:not(.excluded)');
    return Array.from(items).map(item => item.dataset.committee);
}

function getExcludedCommittees() {
    const items = document.querySelectorAll('#committee-list .rank-item.excluded');
    return Array.from(items).map(item => item.dataset.committee);
}

function gatherPreferences() {
    return {
        days: getSelectedValues('day-pills'),
        times: getSelectedValues('time-pills'),
        committees: getCommitteeRanking(),
        excludedCommittees: getExcludedCommittees(),
    };
}

function scoreBadgeHTML(score) {
    if (score >= 90) return `<span class="score-badge score-fire">&#x2764;&#xFE0F; ${score}%</span>`;
    if (score >= 75) return `<span class="score-badge score-purple">&#x1F495; ${score}%</span>`;
    return `<span class="score-badge score-star">&#x1F497; ${score}%</span>`;
}

function scoreBadgeClass(score) {
    if (score >= 90) return 'score-fire';
    if (score >= 75) return 'score-purple';
    return 'score-star';
}

function breakdownIcon(text) {
    if (text.includes('+')) return '&#x2705;';
    if (text.includes('-')) return '&#x274C;';
    if (text.includes('preferred') || text.includes('Top choice')) return '&#x2705;';
    if (text.includes('not preferred') || text.includes('Not in') || text.includes('Late')) return '&#x274C;';
    return '&#x2796;';
}

// ---- Debounced preference re-scoring ----
function onPreferencesChanged() {
    if (!allScoredShifts.length) return;
    clearTimeout(_prefDebounceTimer);
    _prefDebounceTimer = setTimeout(async () => {
        currentPreferences = gatherPreferences();
        const rawShifts = allScoredShifts.map(s => s.shift);
        try {
            const resp = await fetch('/api/shifts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ shifts: rawShifts, preferences: currentPreferences }),
            });
            const data = await resp.json();
            allScoredShifts = data.scored_shifts || [];
            renderGrid(allScoredShifts);
        } catch (err) {
            // Silently fail on re-score — user can still view current results
        }
    }, 500);
}

// ---- Email opt-in toggle ----
document.getElementById('email-optin').addEventListener('change', function() {
    document.getElementById('email-field').classList.toggle('show', this.checked);
});

// ---- Drag and drop for committee ranking (touch + mouse) ----
(function setupDragDrop() {
    const list = document.getElementById('committee-list');
    let dragItem = null;
    let placeholder = null;
    let offsetY = 0;

    function getY(e) {
        if (e.touches && e.touches.length) return e.touches[0].clientY;
        return e.clientY;
    }

    function onStart(e) {
        if (e.target.closest('.exclude-btn')) return;
        const item = e.target.closest('.rank-item');
        if (!item) return;

        dragItem = item;
        const rect = item.getBoundingClientRect();
        offsetY = getY(e) - rect.top;

        placeholder = item.cloneNode(true);
        placeholder.classList.add('drag-placeholder');
        placeholder.style.height = rect.height + 'px';
        placeholder.innerHTML = '';
        item.parentNode.insertBefore(placeholder, item);

        item.classList.add('dragging');
        item.style.position = 'fixed';
        item.style.left = rect.left + 'px';
        item.style.top = rect.top + 'px';
        item.style.width = rect.width + 'px';
        item.style.zIndex = '50';
        item.style.pointerEvents = 'none';
        item.style.transition = 'transform 0.1s, box-shadow 0.1s';

        if (e.type === 'touchstart') e.preventDefault();
    }

    function onMove(e) {
        if (!dragItem) return;
        e.preventDefault();

        const y = getY(e);
        dragItem.style.top = (y - offsetY) + 'px';

        const siblings = [...list.querySelectorAll('.rank-item:not(.dragging)')];
        let inserted = false;
        for (const sibling of siblings) {
            if (sibling === placeholder) continue;
            const box = sibling.getBoundingClientRect();
            if (y < box.top + box.height / 2) {
                list.insertBefore(placeholder, sibling);
                inserted = true;
                break;
            }
        }
        if (!inserted) list.appendChild(placeholder);
    }

    function onEnd() {
        if (!dragItem) return;

        dragItem.classList.remove('dragging');
        dragItem.style.position = '';
        dragItem.style.left = '';
        dragItem.style.top = '';
        dragItem.style.width = '';
        dragItem.style.zIndex = '';
        dragItem.style.pointerEvents = '';
        dragItem.style.transition = '';

        if (placeholder && placeholder.parentNode) {
            placeholder.parentNode.insertBefore(dragItem, placeholder);
            placeholder.remove();
        }

        dragItem = null;
        placeholder = null;
    }

    list.addEventListener('touchstart', onStart, { passive: false });
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onEnd);
    list.addEventListener('mousedown', onStart);
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onEnd);
})();

// ---- Exclude button click handler ----
document.getElementById('committee-list').addEventListener('click', function(e) {
    const btn = e.target.closest('.exclude-btn');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const item = btn.closest('.rank-item');
    item.classList.toggle('excluded');
    btn.classList.toggle('active');
});

// ---- Modal controls ----
function showLoginModal() {
    currentPreferences = gatherPreferences();
    document.getElementById('login-modal').classList.add('show');
    document.getElementById('member-number').focus();
}

function closeLoginModal() {
    document.getElementById('login-modal').classList.remove('show');
    document.getElementById('login-error').classList.remove('show');
}

function showLoading() {
    document.getElementById('loading').classList.add('show');
}
function hideLoading() {
    document.getElementById('loading').classList.remove('show');
}

// ---- Core: Discover Matches ----
async function discoverMatches() {
    const memberNumber = document.getElementById('member-number').value.trim();
    const password = document.getElementById('member-password').value.trim();

    if (!memberNumber || !password) {
        const err = document.getElementById('login-error');
        err.textContent = 'Please enter both your member number and password.';
        err.classList.add('show');
        return;
    }

    savedCredentials = { member_number: memberNumber, password: password };
    closeLoginModal();
    showLoading();

    try {
        const resp = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                member_number: memberNumber,
                password: password,
                preferences: currentPreferences,
            }),
        });
        const data = await resp.json();

        if (!data.success) {
            hideLoading();
            const err = document.getElementById('login-error');
            let errHTML = `<strong>${data.message || 'Login failed.'}</strong>`;
            if (data.debug && data.debug.length > 0) {
                errHTML += `<div style="margin-top:8px;font-size:0.8rem;color:rgba(255,255,255,0.5);font-family:monospace;text-align:left;">`;
                data.debug.forEach(d => { errHTML += d + '<br>'; });
                errHTML += `</div>`;
            }
            errHTML += `<div style="margin-top:10px;font-size:0.82rem;color:rgba(255,255,255,0.6);">` +
                `Try logging in at <a href="https://members.foodcoop.com/services/login/" target="_blank" ` +
                `style="color:var(--red);font-weight:600;">members.foodcoop.com</a> first to verify your credentials.</div>`;
            err.innerHTML = errHTML;
            err.classList.add('show');
            document.getElementById('login-modal').classList.add('show');
            return;
        }

        allScoredShifts = data.scored_shifts || [];
        const source = data.source || 'live';

        // Sign up for daily emails if opted in
        if (document.getElementById('email-optin').checked) {
            const email = document.getElementById('email-input').value.trim();
            if (email) {
                fetch('/api/signup-daily-email', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: email,
                        member_number: memberNumber,
                        password: password,
                        preferences: currentPreferences,
                    }),
                });
            }
        }

        hideLoading();
        showMatchesScreen(source, data.message);

    } catch (err) {
        hideLoading();
        try {
            const mockResp = await fetch('/api/mock-shifts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preferences: currentPreferences }),
            });
            const mockData = await mockResp.json();
            allScoredShifts = mockData.scored_shifts || [];
            showMatchesScreen('mock', 'Connection failed — showing sample data.');
        } catch (e2) {
            alert('Could not connect to the server. Is it running?');
        }
    }
}

// ---- Render matches screen ----
function showMatchesScreen(source, message) {
    document.getElementById('profile-screen').style.display = 'none';
    document.getElementById('matches-screen').style.display = 'block';

    const chipsContainer = document.getElementById('filter-chips');
    chipsContainer.innerHTML = '';
    currentPreferences.days.forEach(d => {
        chipsContainer.innerHTML += `<span class="filter-chip">${d}</span> `;
    });
    currentPreferences.times.forEach(t => {
        chipsContainer.innerHTML += `<span class="filter-chip">${t}</span> `;
    });
    if (currentPreferences.committees.length > 0) {
        chipsContainer.innerHTML += `<span class="filter-chip">#1 ${currentPreferences.committees[0]}</span> `;
    }

    const status = document.getElementById('results-status');
    const sourceLabel = source === 'live' ? 'Live data from PSFC' : 'Sample data (demo mode)';
    status.innerHTML = `Found ${allScoredShifts.length} matches &middot; ${sourceLabel}` +
        (message ? ` &middot; ${message}` : '');

    renderGrid(allScoredShifts);
}

function renderGrid(scored) {
    const heroContainer = document.getElementById('hero-container');
    const grid = document.getElementById('shift-grid');
    const secondaryHeader = document.getElementById('secondary-header');
    heroContainer.innerHTML = '';
    grid.innerHTML = '';

    const top5 = scored.slice(0, 5);
    if (top5.length === 0) return;

    // ---- Hero card (top match) ----
    const hero = top5[0];
    const hShift = hero.shift;
    const hScore = hero.score;
    const hDay = hShift.date ? hShift.day + ' ' + hShift.date : hShift.day;

    let whyHTML = '';
    for (const [key, val] of Object.entries(hero.breakdown)) {
        whyHTML += `<li><span class="bd-icon">${breakdownIcon(val)}</span> ${val}</li>`;
    }

    const claimBtn = hShift.signup_url
        ? `<a href="${hShift.signup_url}" target="_blank" rel="noopener" class="btn-hero-claim" onclick="event.stopPropagation()">Claim This Shift &#x2192;</a>`
        : `<button class="btn-hero-claim" disabled style="opacity:0.5;cursor:default;">No signup link available</button>`;

    const heroEl = document.createElement('div');
    heroEl.className = 'hero-card';
    heroEl.onclick = () => showDetail(hero);
    heroEl.innerHTML = `
        <div class="hero-badge">&#x1F525; #1 BEST MATCH</div>
        <div class="hero-score">${hScore}<span>%</span></div>
        <div class="hero-committee">${hShift.committee}</div>
        <div class="hero-date">${hDay}</div>
        <div class="hero-time">${hShift.time_raw || hShift.time_slot}</div>
        <div class="hero-slots">${hShift.slots} slots available</div>
        <div class="hero-why">
            <div class="hero-why-title">Why It's Perfect</div>
            <ul>${whyHTML}</ul>
        </div>
        ${claimBtn}
    `;
    heroContainer.appendChild(heroEl);

    // ---- Secondary cards (2-5) ----
    const rest = top5.slice(1);
    if (rest.length > 0) {
        secondaryHeader.style.display = 'block';
        rest.forEach((item, idx) => {
            const shift = item.shift;
            const score = item.score;
            const dayLabel = shift.date ? shift.day + ' ' + shift.date : shift.day;
            const descHTML = shift.description ? `<div class="card-desc">${shift.description}</div>` : '';
            const card = document.createElement('div');
            card.className = 'shift-card top-match';
            card.onclick = () => showDetail(item);
            card.innerHTML = `
                ${scoreBadgeHTML(score)}
                <div class="card-committee">${shift.committee}</div>
                <div class="card-meta">
                    <span class="card-tag">&#x1F4C5; ${dayLabel}</span>
                    <span class="card-tag">&#x1F552; ${shift.time_raw || shift.time_slot}</span>
                    <span class="card-tag">&#x1F465; ${shift.slots} slots</span>
                </div>
                ${descHTML}
                <button class="btn-details" onclick="event.stopPropagation(); showDetail(allScoredShifts[${idx + 1}])">
                    View Details
                </button>
            `;
            grid.appendChild(card);
        });
    } else {
        secondaryHeader.style.display = 'none';
    }
}

// ---- Detail modal ----
function showDetail(item) {
    const shift = item.shift;
    const score = item.score;
    const breakdown = item.breakdown;
    const badgeClass = scoreBadgeClass(score);
    const modal = document.getElementById('detail-modal');
    const content = document.getElementById('detail-content');

    let breakdownHTML = '';
    for (const [key, val] of Object.entries(breakdown)) {
        breakdownHTML += `<li><span class="bd-icon">${breakdownIcon(val)}</span> ${val}</li>`;
    }

    const detailDay = shift.date ? shift.day + ' ' + shift.date : shift.day;
    const detailDescHTML = shift.description ? `<div class="detail-desc">${shift.description}</div>` : '';
    content.innerHTML = `
        <button class="detail-close" onclick="closeDetail()">&times;</button>
        <span class="detail-score ${badgeClass}">${score}% Match</span>
        <h3>${shift.committee}</h3>
        <div class="detail-time">${detailDay} &middot; ${shift.time_raw || shift.time_slot} &middot; ${shift.slots} slots</div>
        ${detailDescHTML}
        <div class="breakdown-title">Match Breakdown</div>
        <ul class="breakdown-list">${breakdownHTML}</ul>
        ${shift.signup_url
            ? `<a href="${shift.signup_url}" target="_blank" rel="noopener" class="btn-claim">Claim on PSFC Portal</a>`
            : `<button class="btn-claim" disabled style="opacity:0.5;cursor:default;">No signup link available</button>`
        }
    `;
    modal.classList.add('show');
}

function closeDetail() {
    document.getElementById('detail-modal').classList.remove('show');
}

// ---- Top 5 modal ----
function showTop5Modal() {
    const top5 = allScoredShifts.slice(0, 5);
    const list = document.getElementById('top5-list');
    list.innerHTML = '';
    top5.forEach((item, idx) => {
        const shift = item.shift;
        const score = item.score;
        const badgeClass = scoreBadgeClass(score);
        list.innerHTML += `
            <div class="top5-item" style="cursor:pointer" onclick="closeTop5(); showDetail(allScoredShifts[${idx}])">
                <span class="top5-rank">${idx + 1}</span>
                <div class="top5-info">
                    <div class="t5-committee">${shift.committee}</div>
                    <div class="t5-when">${shift.day} &middot; ${shift.time_raw || shift.time_slot}</div>
                </div>
                <span class="top5-score ${badgeClass}" style="color:#fff;font-size:0.85rem;">${score}%</span>
            </div>
        `;
    });
    document.getElementById('top5-modal').classList.add('show');
}

function closeTop5() {
    document.getElementById('top5-modal').classList.remove('show');
}

// ---- Edit Profile ----
function editProfile() {
    document.getElementById('matches-screen').style.display = 'none';
    document.getElementById('profile-screen').style.display = 'block';
}

// ---- Close modals on overlay click (consolidated) ----
['login-modal', 'detail-modal', 'top5-modal'].forEach(id => {
    document.getElementById(id).addEventListener('click', function(e) {
        if (e.target !== this) return;
        if (id === 'login-modal') closeLoginModal();
        else if (id === 'detail-modal') closeDetail();
        else closeTop5();
    });
});

// ---- Keyboard: Esc closes modals ----
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeLoginModal();
        closeDetail();
        closeTop5();
    }
});
