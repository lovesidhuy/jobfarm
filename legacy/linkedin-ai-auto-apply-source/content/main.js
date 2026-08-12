// =============================================
// content/main.js — Core automation logic
// =============================================
// Page refresh detection, comm bridge, scanning,
// job iteration, form processing, and public API.
// Depends on: utils.js, sensitive-data.js,
//             form-fields.js, backend-api.js
// =============================================

// Guard: if utils.js detected a duplicate injection, stop here
if (!window.__UFH_CONTENT_LOADED__) {
    // This shouldn't happen since utils.js runs first
    console.error('content/main.js loaded without utils.js');
}

// Page refresh detection and reset
function saveRunnerStateToSession() {
    sessionStorage.setItem('ljm_currentIndex', appState.currentIndex);
    sessionStorage.setItem('ljm_totalJobs', appState.totalJobs);
    sessionStorage.setItem('ljm_isPaused', appState.isPaused);
    sessionStorage.setItem('ljm_aborted', appState.aborted);
}

function clearRunnerStateFromSession() {
    sessionStorage.removeItem('ufh_processing_applications');
    sessionStorage.removeItem('ljm_currentIndex');
    sessionStorage.removeItem('ljm_totalJobs');
    sessionStorage.removeItem('ljm_isPaused');
    sessionStorage.removeItem('ljm_aborted');
    sessionStorage.removeItem('ljm_attemptedJobIds');
}

// Page refresh detection and restore/reset state
function setupPageRefreshDetection() {
    console.log('🔄 Setting up page refresh detection...');

    // Load appliedJobs from storage to ensure it's populated on startup/refresh
    browserAPI.storage.local.get(['appliedJobs']).then(res => {
        appState.appliedJobs = res.appliedJobs || [];
        console.log(`📋 Loaded ${appState.appliedJobs.length} applied jobs from storage on startup.`);

        const wasProcessingApplications = sessionStorage.getItem('ufh_processing_applications');

        if (wasProcessingApplications === 'true') {
            console.log('🔄 Detected page refresh during application process - checking runner state');

            const storedIdx = parseInt(sessionStorage.getItem('ljm_currentIndex'));
            const storedTotal = parseInt(sessionStorage.getItem('ljm_totalJobs'));
            const storedPaused = sessionStorage.getItem('ljm_isPaused') === 'true';
            const storedAborted = sessionStorage.getItem('ljm_aborted') === 'true';

            if (!isNaN(storedIdx) && !isNaN(storedTotal) && !storedAborted) {
                appState.currentIndex = storedIdx;
                appState.totalJobs = storedTotal;
                appState.isPaused = storedPaused;
                appState.aborted = false;

                console.log(`✅ Restored state: index ${appState.currentIndex}/${appState.totalJobs}, paused=${appState.isPaused}`);

                // Resume application flow after page rendering finishes
                scheduleNext(() => {
                    if (!appState.isPaused && !appState.aborted) {
                        clickNextJob();
                    }
                }, 3000);
                return;
            }

            // Fallback: reset state if unrecoverable
            appState.isPaused = false;
            appState.currentIndex = 0;
            appState.totalJobs = 0;
            appState.aborted = true;

            pendingTimeouts.forEach(id => clearTimeout(id));
            pendingTimeouts.length = 0;

            clearRunnerStateFromSession();

            updateApplicationState(false, {
                current: 0,
                total: 0,
                isPaused: false,
                finished: true
            });

            document.querySelectorAll('.job-card-container.ufh-applied, .jobs-search-results__list-item.ufh-applied').forEach(el => {
                el.classList.remove('ufh-applied');
                clearJobCardStyle(el);
            });

            console.log('✅ Extension state reset after unrecoverable page refresh');
        }
    }).catch(err => {
        console.error('Error loading appliedJobs on page refresh detection:', err);
    });
}

setupPageRefreshDetection();

/* =============================
 * Web Dashboard Communication  🛰️
 * ============================= */
(function setupPageCommunication() {
    console.log('📡 [UFH] content/main.js: setupPageCommunication start');
    if (window.__UFH_COMM_BRIDGE__) {
        console.log('📡 [UFH] content/main.js: Bridge already exists, skipping');
        return;
    }
    window.__UFH_COMM_BRIDGE__ = true;

    console.log('📡 [UFH] content/main.js: Installing communication bridge');

    // We inject a script tag because Firefox Manifest V3 content scripts run in isolated worlds
    // and setting window.WebFormProvider directly in the content script doesn't expose it to the webpage.
    const injectionScript = document.createElement('script');
    injectionScript.textContent = `
        console.log("🛠️ [UFH] Bridge: Script executing...");
        window.WebFormProvider = {
            version: '1.1',
            ping: () => {
                console.log("🛠️ [UFH] Bridge: ping() called");
                window.postMessage({ type: 'UFH_EXT_PONG', ts: Date.now(), version: '1.1' }, '*');
            },
            sendEvent: (event, payload = {}) => {
                console.log("🛠️ [UFH] Bridge: sendEvent()", event, payload);
                window.postMessage({ type: 'UFH_EXT_EVENT', event, payload }, '*');
            }
        };
        console.log("🛠️ [UFH] Bridge: window.WebFormProvider set, sending UFH_EXT_READY");
        window.postMessage({ type: 'UFH_EXT_READY', ts: Date.now(), version: '1.1' }, '*');
    `;
    console.log('📡 [UFH] content/main.js: Injecting bridge script tag');
    (document.head || document.documentElement).appendChild(injectionScript);
    injectionScript.remove(); // Clean up after execution

    // Helper to send messages safely across Firefox isolated world
    function sendToPage(msg) {
        if (typeof cloneInto !== 'undefined' && window.wrappedJSObject) {
            console.log('📡 [UFH] content/main.js: Sending to page via cloneInto Firefox-style', msg.type);
            window.wrappedJSObject.postMessage(cloneInto(msg, window.wrappedJSObject), '*');
        } else {
            console.log('📡 [UFH] content/main.js: Sending to page via postMessage', msg.type);
            window.postMessage(msg, '*');
        }
    }

    window.addEventListener('message', (evt) => {
        if (evt.source !== window || !evt.data) return;
        const { type } = evt.data;
        if (type === 'UFH_EXT_PING') {
            console.log('📡 [UFH] content/main.js: Received UFH_EXT_PING, replying with PONG');
            sendToPage({ type: 'UFH_EXT_PONG', ts: Date.now(), version: '1.1' });
        }
        else if (type === 'UFH_DASH_GET_PREFS') {
            browserAPI.storage.local.get(['processingSpeed', 'uncheckFinalPageCheckboxes', 'aiEnabled']).then(prefs => {
                sendToPage({ type: 'UFH_EXT_PREFS', prefs });
            });
        }
        else if (type === 'UFH_DASH_SET_PREFS') {
            const prefs = evt.data.prefs || {};
            browserAPI.storage.local.set(prefs).then(() => {
                sendToPage({ type: 'UFH_EXT_PREFS_UPDATED', prefs });
            });
        }
        else if (type === 'UFH_DASH_GET_APPLIED_JOBS') {
            browserAPI.storage.local.get(['appliedJobs']).then(res => {
                const jobs = (res.appliedJobs || []).map(job => ({
                    ...job,
                    id: job.id || Date.now(),
                    jobTitle: job.jobTitle || 'Unknown Position',
                    company: job.company || 'Unknown Company',
                    appliedAt: job.appliedAt || new Date().toISOString(),
                    status: job.status || 'unknown',
                    questionAnswers: job.questionAnswers || {},
                    jobUrl: job.jobUrl || (job.id ? `https://www.linkedin.com/jobs/view/${job.id}` : undefined)
                }));
                sendToPage({ type: 'UFH_EXT_APPLIED_JOBS', jobs });
            });
        }
        else if (type === 'UFH_DASH_GET_PROFILE') {
            browserAPI.storage.local.get(null).then(all => {
                const profile = {};
                Object.keys(all).forEach(k => {
                    if (k.startsWith('field_')) {
                        const fieldName = k.replace('field_', '');
                        profile[fieldName] = all[k];
                    }
                });
                sendToPage({ type: 'UFH_EXT_PROFILE', profile });
            });
        }
        else if (type === 'UFH_DASH_SET_PROFILE_FIELD') {
            const { field, value } = evt.data;
            if (field) {
                const key = `field_${field}`;
                browserAPI.storage.local.set({ [key]: value }).then(() => {
                    sendToPage({ type: 'UFH_EXT_PROFILE_UPDATED', profile: { [field]: value } });
                });
            }
        }
        else if (type === 'UFH_DASH_DELETE_PROFILE_FIELD') {
            const { field } = evt.data;
            if (field) {
                const key = `field_${field}`;
                browserAPI.storage.local.remove([key]).then(() => {
                    sendToPage({ type: 'UFH_EXT_PROFILE_UPDATED', profile: { [field]: undefined } });
                });
            }
        }
        else if (type === 'UFH_RUNNER_COMMAND') {
            const { command, requestId } = evt.data;
            const reply = (payload) => sendToPage({
                type: 'UFH_RUNNER_RESPONSE',
                requestId,
                command,
                ...payload
            });

            (async () => {
                try {
                    if (command === 'performScan') {
                        reply({ ok: true, result: performScan() });
                    } else if (command === 'startApplying') {
                        reply({ ok: true, result: await startApplying() });
                    } else if (command === 'applyCurrentJob') {
                        reply({ ok: true, result: await applyCurrentJob() });
                    } else if (command === 'getProgress') {
                        reply({ ok: true, result: getProgress() });
                    } else if (command === 'getAppliedJobs') {
                        reply({ ok: true, result: await getAppliedJobs() });
                    } else if (command === 'abortApplying') {
                        reply({ ok: true, result: abortApplying() });
                    } else if (command === 'setRunOptions') {
                        const options = evt.data.options || {};
                        if (Number.isFinite(Number(options.maxApplicationsPerRun))) {
                            appState.maxApplicationsPerRun = Math.max(1, Number(options.maxApplicationsPerRun));
                        }
                        if (options.resetSubmissionCount) {
                            appState.applicationsSubmittedThisRun = 0;
                            appState.consecutiveFailures = 0;
                        }
                        if (Number.isFinite(Number(options.processingSpeed))) {
                            delayMultiplier = Math.max(0, Number(options.processingSpeed));
                            await browserAPI.storage.local.set({ processingSpeed: delayMultiplier });
                        }
                        reply({ ok: true, result: getProgress() });
                    } else {
                        reply({ ok: false, error: `Unknown runner command: ${command}` });
                    }
                } catch (err) {
                    reply({ ok: false, error: err?.message || String(err) });
                }
            })();
        }
        else if (type === 'UFH_EXT_GET_TOKEN') {
            try {
                const token = window.localStorage?.getItem('token');
                sendToPage({ type: 'UFH_EXT_TOKEN_RESPONSE', token, requestId: evt.data.requestId });
            } catch (e) {
                sendToPage({ type: 'UFH_EXT_TOKEN_RESPONSE', token: null, error: e.message, requestId: evt.data.requestId });
            }
        }
    });

    // Listen for storage changes (e.g., user toggles in extension popup)
    browserAPI.storage.onChanged.addListener((changes, area) => {
        if (area !== 'local') return;
        const prefsUpdate = {};
        ['processingSpeed', 'uncheckFinalPageCheckboxes', 'aiEnabled'].forEach(key => {
            if (changes[key]) prefsUpdate[key] = changes[key].newValue;
        });
        if (Object.keys(prefsUpdate).length > 0) {
            sendToPage({ type: 'UFH_EXT_PREFS_UPDATED', prefs: prefsUpdate });
        }

        if (changes['appliedJobs']) {
            browserAPI.storage.local.get(['appliedJobs']).then(res => {
                sendToPage({ type: 'UFH_EXT_APPLIED_JOBS_UPDATED', jobs: res.appliedJobs || [] });
            });
        }

        Object.keys(changes).forEach(k => {
            if (k.startsWith('field_')) {
                const field = k.replace('field_', '');
                const value = changes[k].newValue;
                sendToPage({ type: 'UFH_EXT_PROFILE_UPDATED', profile: { [field]: value } });
            }
        });

        if (changes.processingSpeed) {
            const newVal = parseFloat(changes.processingSpeed.newValue);
            if (!isNaN(newVal) && newVal >= 0) {
                delayMultiplier = newVal;
                console.log('⏱️ delayMultiplier updated via storage change →', delayMultiplier);
            }
        }
    });

    console.log('📡 [UFH] content/main.js: Sending initial UFH_EXT_READY');
    sendToPage({ type: 'UFH_EXT_READY', ts: Date.now(), version: '1.1' });
})();

// =============================================
// Storage helpers
// =============================================

function saveJobData(jobData) {
    return new Promise((resolve) => {
        browserAPI.storage.local.get(['appliedJobs']).then((result) => {
            const appliedJobs = result.appliedJobs || [];
            appliedJobs.push(jobData);

            const jobKey = `jobApplication_${jobData.id}`;

            browserAPI.storage.local.set({
                appliedJobs: appliedJobs,
                [jobKey]: jobData
            }).then(() => {
                console.log('✅ Job data saved:', jobData);
                appState.appliedJobs = appliedJobs; // sync cache in memory
                resolve();
            });
        });
    });
}

function getAppliedJobs() {
    return new Promise((resolve) => {
        browserAPI.storage.local.get(['appliedJobs']).then((result) => {
            resolve(result.appliedJobs || []);
        });
    });
}

// =============================================
// Visual indicator
// =============================================

function addExtensionIndicator() {
    if (window.location.href.includes(UFH_CONFIG.DASHBOARD_DOMAIN)) {
        return;
    }

    if (window.__UFH_SECURE_UI_HOST__) return;

    const uiHostContainer = document.createElement('div');
    uiHostContainer.id = 'app-runtime-container-' + Math.random().toString(36).substring(2, 15);
    document.body.appendChild(uiHostContainer);

    const secureShadowRoot = uiHostContainer.attachShadow({ mode: 'closed' });
    const uiWrapper = document.createElement('div');
    uiWrapper.innerHTML = `
        <style>
            .monitor-panel {
                position: fixed;
                top: 0;
                right: 0;
                background: #0a66c2;
                color: #fff;
                padding: 5px 10px;
                font-weight: 700;
                z-index: 999999;
                font-size: 12px;
                line-height: 1.4;
                border-radius: 0 0 0 8px;
                font-family: Arial, sans-serif;
            }
        </style>
        <div class="monitor-panel">Form Helper Active - Click to activate scan</div>
    `;
    secureShadowRoot.appendChild(uiWrapper);
    window.__UFH_SECURE_UI_HOST__ = uiHostContainer;
}

// =============================================
// Job card styling
// =============================================

function setJobCardStyle(card, status, reason = null) {
    if (!card) return;

    const styles = {
        pending: {
            border: '5px solid #dc3545',
            boxShadow: '0 0 15px rgba(220, 53, 69, 0.7)'
        },
        processing: {
            border: '5px solid #ffc107',
            boxShadow: '0 0 15px rgba(255, 193, 7, 0.7)'
        },
        success: {
            border: '5px solid #28a745',
            boxShadow: '0 0 15px rgba(40, 167, 69, 0.7)'
        },
        error: {
            border: '5px solid #dc3545',
            boxShadow: '0 0 15px rgba(220, 53, 69, 0.7)'
        },
        skipped: {
            border: '5px solid #dc3545',
            boxShadow: '0 0 15px rgba(220, 53, 69, 0.7)'
        }
    };

    const style = styles[status] || styles.pending;
    try {
        card.style.border = style.border;
        card.style.boxShadow = style.boxShadow;
        card.setAttribute('data-job-status', status);

        if (reason) {
            let badge = card.querySelector('.ufh-status-badge');
            if (!badge) {
                badge = document.createElement('div');
                badge.className = 'ufh-status-badge';
                badge.style.cssText = 'background: #dc3545; color: white; padding: 4px 8px; font-size: 11px; font-weight: bold; border-radius: 4px; margin-top: 8px; display: inline-block; word-break: break-word;';
                card.appendChild(badge);
            }
            badge.textContent = `Status: ${status.toUpperCase()} (${reason})`;
            if (status === 'success') {
                badge.style.background = '#28a745';
                badge.style.color = 'white';
            } else if (status === 'processing') {
                badge.style.background = '#ffc107';
                badge.style.color = 'black';
            } else {
                badge.style.background = '#dc3545';
                badge.style.color = 'white';
            }
        }
    } catch (e) {
        console.warn('Failed to apply job card styling:', e);
    }
}

function clearJobCardStyle(card) {
    if (!card) return;
    try {
        card.style.border = '';
        card.style.boxShadow = '';
        card.removeAttribute('data-job-status');
    } catch (e) {
        console.warn('Failed to clear job card styling:', e);
    }
}

// =============================================
// Scanning
// =============================================

function performScan() {
    console.log('🔍 Performing scan...');

    const totalJobsElement = document.querySelector('.results-context-header__job-count');
    const totalJobs = totalJobsElement ? parseInt(totalJobsElement.textContent.replace(/[^\d]/g, '')) || 0 : 0;

    const jobCards = document.querySelectorAll('.job-card-container, .jobs-search-results__list-item');
    const currentPageJobs = jobCards.length;

    const jobDescriptionPresent = document.querySelector('.jobs-description') !== null;

    return {
        totalJobs: totalJobs,
        currentPageJobs: currentPageJobs,
        jobDescriptionPresent: jobDescriptionPresent,
        lastUpdated: Date.now()
    };
}

function getJobIdFromCard(card) {
    if (!card) return null;
    const dataJobId = card.getAttribute('data-job-id');
    if (dataJobId) return dataJobId;
    const cardLink = card.querySelector('a[href*="/jobs/view/"]');
    if (cardLink) {
        const m = cardLink.href.match(/\/jobs\/view\/(\d+)/);
        if (m) return m[1];
    }
    const anyLink = card.querySelector('a');
    if (anyLink && anyLink.href) {
        const m = anyLink.href.match(/\/jobs\/view\/(\d+)/) || anyLink.href.match(/[?&]currentJobId=(\d+)/);
        if (m) return m[1];
    }
    return null;
}

function getAttemptedJobIds() {
    try {
        const stored = sessionStorage.getItem('ljm_attemptedJobIds');
        return stored ? JSON.parse(stored) : [];
    } catch (_) {
        return [];
    }
}

function addAttemptedJobId(jobId) {
    if (!jobId) return;
    try {
        const attempted = getAttemptedJobIds();
        if (!attempted.includes(String(jobId))) {
            attempted.push(String(jobId));
            sessionStorage.setItem('ljm_attemptedJobIds', JSON.stringify(attempted));
            console.log(`💾 Saved attempted job ID to session storage: ${jobId}`);
        }
    } catch (err) {
        console.error('Error saving attempted job ID to session storage:', err);
    }
}

function isJobAlreadyApplied(card) {
    if (card.classList.contains('ufh-applied')) {
        return true;
    }

    const cardJobId = getJobIdFromCard(card);
    if (cardJobId) {
        // 1. Check against local storage (submitted/applied jobs)
        if (appState.appliedJobs && appState.appliedJobs.length > 0) {
            const alreadyApplied = appState.appliedJobs.some(job => String(job.id) === String(cardJobId));
            if (alreadyApplied) {
                console.log(`🔍 [isJobAlreadyApplied] Detected already applied job ID: ${cardJobId} from local storage`);
                return true;
            }
        }
        
        // 2. Check against same-session attempted/processed jobs
        const attempted = getAttemptedJobIds();
        if (attempted.includes(String(cardJobId))) {
            console.log(`🔍 [isJobAlreadyApplied] Detected already attempted/processed job ID in same session: ${cardJobId}`);
            return true;
        }
    }

    const stateEl = card.querySelector('.job-card-container__footer-job-state');
    if (stateEl) {
        const txt = stateEl.textContent.trim().toLowerCase();
        const appliedKeywords = [
            'applied',
            'application submitted',
            'candidature envoy',
            'candidatura enviada',
            'solicitud enviada',
            'bewerbung gesendet',
            'postulación enviada',
            'candidature inviata',
        ];
        return appliedKeywords.some(k => txt.includes(k));
    }
    return false;
}

function evaluateMetroVancouverWorkplaceGate(card) {
    const normalize = (text) => String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const cardText = normalize(card?.innerText);
    const detailText = normalize(
        document.querySelector('.job-details-jobs-unified-top-card, .jobs-search__job-details, .jobs-details')?.innerText || ''
    );
    const text = `${cardText} ${detailText}`.trim();

    if (!text) {
        return { allowed: false, reason: 'missing visible location/workplace text' };
    }

    const remoteSignals = [
        /\bremote\b/,
        /\bwork\s+from\s+home\b/,
        /\banywhere\s+in\s+canada\b/,
        /\bcanada\s*\(\s*remote\s*\)/,
        /\bremote\s+canada\b/,
        /\bcanada\s+remote\b/
    ];
    if (remoteSignals.some(pattern => pattern.test(text))) {
        return { allowed: false, reason: 'remote role' };
    }

    const blockedNonLocalSignals = [
        'toronto', 'ontario', 'ottawa', 'mississauga', 'hamilton', 'london', 'kitchener', 'waterloo', 'windsor', 'kingston', 'sudbury',
        'montreal', 'montréal', 'quebec', 'québec', 'laval', 'gatineau', 'sherbrooke',
        'calgary', 'edmonton', 'red deer', 'lethbridge', 'fort mcmurray', 'alberta',
        'winnipeg', 'manitoba',
        'saskatchewan', 'saskatoon', 'regina',
        'halifax', 'nova scotia',
        'new brunswick', 'moncton', 'saint john', 'fredericton',
        'newfoundland', "st. john's",
        'prince edward island', 'charlottetown',
        'yukon', 'whitehorse',
        'northwest territories', 'yellowknife',
        'nunavut', 'iqaluit',
        'victoria', 'kelowna', 'nanaimo', 'kamloops', 'prince george', 'abbotsford', 'chilliwack', 'vernon', 'penticton', 'campbell river'
    ];
    if (blockedNonLocalSignals.some(signal => new RegExp(`\\b${signal.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')}\\b`, 'i').test(text))) {
        return { allowed: false, reason: 'blocked non-local Canadian location' };
    }

    const broadCanadaSignals = [
        /\bcanada\s*(?:·|\||-|,|\(|$)/,
        /(?:^|·|\||-|\()\s*canada\s*(?:·|\||-|\)|$)/,
        /\bca\s*\(\s*hybrid\s*\)/,
        /\bca\s*\(\s*on-site\s*\)/
    ];
    const metroSignals = [
        'vancouver',
        'burnaby',
        'surrey',
        'richmond',
        'coquitlam',
        'port coquitlam',
        'port moody',
        'new westminster',
        'north vancouver',
        'west vancouver',
        'delta',
        'langley',
        'maple ridge',
        'pitt meadows',
        'white rock',
        'tsawwassen',
        'lower mainland',
        'metro vancouver',
        'greater vancouver',
        'vancouver, bc',
        'surrey, bc',
        'burnaby, bc',
        'richmond, bc'
    ];
    const hasMetroSignal = metroSignals.some(signal => text.includes(signal));

    if (!hasMetroSignal) {
        if (broadCanadaSignals.some(pattern => pattern.test(text))) {
            return { allowed: false, reason: 'broad Canada-level location' };
        }
        return { allowed: false, reason: 'not clearly Metro Vancouver' };
    }

    const workplaceSignals = [/\bon-site\b/, /\bonsite\b/, /\bhybrid\b/];
    if (!workplaceSignals.some(pattern => pattern.test(text))) {
        return { allowed: false, reason: 'not clearly on-site or hybrid' };
    }

    return { allowed: true, reason: 'Metro Vancouver on-site/hybrid' };
}

function logRejection(card, reason) {
    if (!card) return;
    const jobId = getJobIdFromCard(card) || '';
    
    let title = '';
    let company = '';
    try {
        title = card.querySelector('.job-card-list__title, .job-card-container__link')?.textContent?.trim() || '';
        company = card.querySelector('.job-card-container__company-name, .job-card-container__primary-description')?.textContent?.trim() || '';
    } catch (_) {}

    if (!title) {
        title = document.querySelector('h1.t-24, .job-details-jobs-unified-top-card__job-title')?.textContent?.trim() || '';
    }
    if (!company) {
        company = document.querySelector('.job-details-jobs-unified-top-card__company-name a, .job-details-jobs-unified-top-card__company-name')?.textContent?.trim() || '';
    }

    if (!appState.rejections) appState.rejections = [];
    
    if (jobId && appState.rejections.some(r => r.jobId === jobId)) return;

    appState.rejections.push({
        jobId,
        title,
        company,
        reason,
        ts: new Date().toISOString()
    });
}

function skipCurrentJob(card, totalCards, reason) {
    console.log(`⏭️ Skipping job at index ${appState.currentIndex + 1}: ${reason}`);
    logRejection(card, reason);
    try {
        setJobCardStyle(card, 'skipped', reason);
    } catch (_) {
        /* no-op if style fails */
    }
    appState.currentIndex++;
    saveRunnerStateToSession();
    updateApplicationState(true, {
        current: appState.currentIndex,
        total: appState.totalJobs,
        isPaused: appState.isPaused,
        finished: false
    });
    scheduleNext(() => {
        if (!appState.isPaused && !appState.aborted && appState.currentIndex <= totalCards) {
            clickNextJob();
        }
    }, randomHumanPacingMs());
}

// =============================================
// Daily limit detection
// =============================================

function checkDailyApplyLimit() {
    // Method 1: Check error feedback messages
    const dailyLimitElements = document.querySelectorAll('.artdeco-inline-feedback--error');

    for (const element of dailyLimitElements) {
        const messageElement = element.querySelector('.artdeco-inline-feedback__message');
        if (messageElement) {
            const messageText = messageElement.textContent.trim().toLowerCase();

            const dailyLimitKeywords = [
                // French
                'limitons le nombre',
                'envois quotidiens',
                'limite de candidatures',
                'atteint la limite',
                'candidatures pour aujourd',
                'postulez demain',
                'enregistrez cette offre',
                // English
                'daily easy apply limit',
                'reached your limit',
                'daily limit',
                'limit the number of',
                'applications today',
                'apply tomorrow',
                'save this job',
                // Spanish
                'límite diario',
                'límite de solicitudes',
                'solicitudes diarias',
                // German
                'tägliches limit',
                'bewerbungslimit',
                'täglichen bewerbungen',
                // Italian
                'limite giornaliero',
                // Generic
                'empêcher les bots',
                'prevent bots'
            ];

            const isDailyLimit = dailyLimitKeywords.some(keyword =>
                messageText.includes(keyword)
            );

            if (isDailyLimit) {
                console.log('🚫 Daily Easy Apply limit detected via error message!');
                devLog('LIMIT', `Matched message: "${messageText.substring(0, 100)}"`);
                return {
                    detected: true,
                    message: messageElement.textContent.trim(),
                    element: element
                };
            }
        }
    }

    // Method 2: Check if apply button is disabled
    const applyBtn = document.getElementById('jobs-apply-button-id') ||
        document.querySelector('[data-live-test-job-apply-button]') ||
        document.querySelector('.jobs-apply-button');

    if (applyBtn && (applyBtn.disabled || applyBtn.classList.contains('artdeco-button--disabled'))) {
        // Look for any nearby error feedback to get the message
        const container = applyBtn.closest('.mt4') || applyBtn.closest('.display-flex')?.parentElement;
        const errorMsg = container?.querySelector('.artdeco-inline-feedback__message');
        const message = errorMsg?.textContent?.trim() || 'Apply button is disabled — daily limit likely reached';

        console.log('🚫 Daily limit detected via disabled apply button!');
        devLog('LIMIT', `Button disabled. Message: "${message.substring(0, 100)}"`);
        return {
            detected: true,
            message: message,
            element: applyBtn
        };
    }

    return { detected: false };
}

function handleDailyLimit(limitInfo) {
    console.log('🚫 Handling daily Easy Apply limit...');

    appState.aborted = true;
    appState.isPaused = false;

    pendingTimeouts.forEach(id => clearTimeout(id));
    pendingTimeouts.length = 0;

    clearRunnerStateFromSession();

    updateApplicationState(false, {
        current: appState.currentIndex,
        total: appState.totalJobs,
        isPaused: false,
        finished: true,
        dailyLimitReached: true
    });

    const shortMessage = limitInfo.message.length > 150 ?
        limitInfo.message.substring(0, 150) + '...' :
        limitInfo.message;

    alert(`🚫 DAILY EASY APPLY LIMIT REACHED\n\n${shortMessage}\n\nThe application process has been stopped. You can continue tomorrow!`);

    appState.isPaused = false;
    appState.currentIndex = 0;
    appState.totalJobs = 0;

    console.log('✅ Daily limit handling completed');
}

async function waitForJobDetailsToLoad(timeoutMs = 15000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        if (getEasyApplyButton()) {
            return true;
        }
        const titleEl = document.querySelector('h1.t-24, .job-details-jobs-unified-top-card__job-title');
        const descEl = document.querySelector('.jobs-description__content, .jobs-description, #job-details');
        if (titleEl && titleEl.textContent.trim().length > 0 && descEl && descEl.textContent.trim().length > 0) {
            const detailsPanel = document.querySelector('.jobs-search__job-details, .job-view-layout, .jobs-details');
            const loader = detailsPanel ? detailsPanel.querySelector('.artdeco-loader, .spinner') : null;
            if (!loader) {
                return true;
            }
        }
        await sleep(500);
    }
    return false;
}

function getEasyApplyButton() {
    const candidates = [
        document.getElementById('jobs-apply-button-id'),
        ...document.querySelectorAll('[data-live-test-job-apply-button], .jobs-apply-button'),
        ...document.querySelectorAll('button, a[role="button"], a')
    ].filter(Boolean);
    const easyApplyPattern = /easy apply|candidature simplifiée|candidati ora|candidatura simplificada/i;
    const currentJobId = window.location.href.match(/\/jobs\/view\/(\d+)/)?.[1] ||
        window.location.href.match(/[?&]currentJobId=(\d+)/)?.[1] || '';
    const scopedCandidates = candidates.filter(button => {
        const href = button.href || button.getAttribute('href') || '';
        const hrefJobId = href.match(/\/jobs\/view\/(\d+)/)?.[1] || '';
        return !hrefJobId || !currentJobId || hrefJobId === currentJobId;
    });
    return scopedCandidates.find(button => easyApplyPattern.test(
        `${button.textContent || ''} ${button.getAttribute('aria-label') || ''}`
    )) || null;
}

function recordDirectJobRejection(reason) {
    const jobId = getActiveJobId() || '';
    if (!appState.rejections) appState.rejections = [];
    if (jobId && appState.rejections.some(rejection => rejection.jobId === jobId)) return;

    appState.rejections.push({
        jobId,
        title: document.querySelector('h1.t-24, .job-details-jobs-unified-top-card__job-title')?.textContent?.trim() || '',
        company: document.querySelector('.job-details-jobs-unified-top-card__company-name a, .job-details-jobs-unified-top-card__company-name')?.textContent?.trim() || '',
        reason,
        ts: new Date().toISOString()
    });
}

function finishDirectJob(status, reason = '') {
    appState.currentIndex = 1;
    appState.isPaused = false;
    appState.directJobResult = {
        status,
        reason,
        jobId: getActiveJobId() || '',
        completedAt: new Date().toISOString()
    };
    clearRunnerStateFromSession();
    updateApplicationState(false, {
        current: appState.currentIndex,
        total: appState.totalJobs,
        isPaused: false,
        finished: true,
        directJobResult: appState.directJobResult
    });
}

async function runCurrentJobApplication() {
    try {
        if (checkChallengeDetected()) {
            finishDirectJob('failed', 'LinkedIn security challenge detected');
            return;
        }

        const detailsLoaded = await waitForJobDetailsToLoad();
        if (!detailsLoaded) {
            recordDirectJobRejection('job details loading timeout');
            finishDirectJob('failed', 'Job details did not load');
            return;
        }

        const limitCheck = checkDailyApplyLimit();
        if (limitCheck.detected) {
            const reason = `LinkedIn daily Easy Apply limit reached: ${limitCheck.message || 'limit detected'}`;
            recordDirectJobRejection(reason);
            finishDirectJob('failed', reason);
            return;
        }

        const existingModal = document.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]');
        if (!existingModal) {
            const easyApplyBtn = getEasyApplyButton();
            if (!easyApplyBtn) {
                recordDirectJobRejection('no Easy Apply button');
                finishDirectJob('skipped', 'No Easy Apply button on this job');
                return;
            }
            if (easyApplyBtn.disabled || easyApplyBtn.classList.contains('artdeco-button--disabled')) {
                recordDirectJobRejection('Easy Apply button disabled');
                finishDirectJob('skipped', 'Easy Apply button is disabled');
                return;
            }

            await clickLikeHuman(easyApplyBtn);
            const modalDeadline = Date.now() + 15000;
            while (Date.now() < modalDeadline && !document.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]')) {
                await new Promise(resolve => setTimeout(resolve, 500));
            }
        }
        await humanPacingDelay('after Easy Apply click');

        const submitted = await processApplicationForm('1/1');
        if (submitted) {
            finishDirectJob('applied');
        } else if (!appState.aborted) {
            recordDirectJobRejection('form filling failed or was aborted');
            finishDirectJob('failed', 'Easy Apply form was not submitted');
        }
    } catch (error) {
        console.error('❌ Direct job application failed:', error);
        recordDirectJobRejection(error.message || String(error));
        finishDirectJob('failed', error.message || String(error));
    }
}

// =============================================
// Core job iteration loop
// =============================================

async function clickNextJob() {
    if (appState.aborted) { console.log('🛑 Aborted, exiting clickNextJob'); return; }

    if (checkChallengeDetected()) {
        console.error("🛑 Security challenge detected! Stopping application process.");
        alert("🛑 CHALLENGE DETECTED\n\nLinkedIn has prompted a verification challenge or CAPTCHA. The runner has been paused to protect your account.");
        abortApplying();
        return;
    }

    try {
        const jobCards = document.querySelectorAll('.job-card-container, .jobs-search-results__list-item');
        const totalCards = jobCards.length;

        // Skip already-applied cards
        while (appState.currentIndex < totalCards && isJobAlreadyApplied(jobCards[appState.currentIndex])) {
            console.log('⏩ Skipping already-applied job at index', appState.currentIndex + 1);
            appState.currentIndex++;
        }
        saveRunnerStateToSession();

        if (appState.currentIndex >= totalCards) {
            console.log('📄 Finished all jobs on current page, checking for next page...');

            const nextPageBtn = document.querySelector('.jobs-search-pagination__button--next');
            if (nextPageBtn && !nextPageBtn.disabled) {
                console.log('📄 Found next page button, moving to next page...');
                await clickLikeHuman(nextPageBtn);

                await humanPacingDelay('after next page click');

                appState.currentIndex = 0;
                saveRunnerStateToSession();

                if (!appState.aborted && !appState.isPaused) {
                    clickNextJob();
                }
                return;
            } else {
                console.log('✅ No more pages available - Finished applying to all jobs!');
                handleApplicationCompletion();
                return;
            }
        }

        if (appState.isPaused) {
            console.log('⏸️ Process paused');
            return;
        }

        const currentCard = jobCards[appState.currentIndex];
        const progress = (appState.currentIndex + 1) + '/' + totalCards;
        console.log('🔍 Processing job', progress);

        if (!currentCard) {
            throw new Error('Job card not found at index ' + appState.currentIndex);
        }

        const cardJobId = getJobIdFromCard(currentCard);
        if (cardJobId) {
            addAttemptedJobId(cardJobId);
        }

        setJobCardStyle(currentCard, 'processing');
        await clickLikeHuman(currentCard);
        console.log('✅ Clicked job card', progress);

        await humanPacingDelay('after job card click');

        const detailsLoaded = await waitForJobDetailsToLoad(15000);
        if (!detailsLoaded) {
            console.log('⚠️ Job details failed to load or got stuck in infinite spinner. Skipping.');
            skipCurrentJob(currentCard, totalCards, 'job details loading timeout');
            return;
        }

        const locationGate = evaluateMetroVancouverWorkplaceGate(currentCard);
        if (!locationGate.allowed) {
            skipCurrentJob(currentCard, totalCards, locationGate.reason);
            return;
        }
        console.log(`✅ Location/workplace gate passed: ${locationGate.reason}`);

        // --- PRESCREENING GATE ---
        const prescreenTitle = document.querySelector('h1.t-24, .job-details-jobs-unified-top-card__job-title')?.textContent?.trim() || '';
        const prescreenCompany = document.querySelector('.job-details-jobs-unified-top-card__company-name a, .job-details-jobs-unified-top-card__company-name')?.textContent?.trim() || '';
        const prescreenDesc = document.querySelector('.jobs-description__content, .jobs-description, #job-details')?.textContent?.trim() || '';
        const prescreenJobId = getActiveJobId() || '';

        console.log(`🤖 Prescreening job "${prescreenTitle}" at "${prescreenCompany}" (Job ID: ${prescreenJobId})...`);
        try {
            const result = await new Promise((resolve, reject) => {
                browserAPI.runtime.sendMessage({
                    type: "JOB_PRESCREEN",
                    body: {
                        title: prescreenTitle,
                        company: prescreenCompany,
                        description: prescreenDesc,
                        job_id: prescreenJobId
                    }
                }).then(res => {
                    if (res && res.success) {
                        resolve(res.result);
                    } else {
                        reject(new Error(res ? res.error : "Unknown error"));
                    }
                }).catch(reject);
            });

            if (result) {
                const { skip, reason } = result;
                if (skip) {
                    console.log(`🚫 Job rejected by prescreening gate: ${reason}`);
                    skipCurrentJob(currentCard, totalCards, `prescreen rejected: ${reason}`);
                    return;
                } else {
                    console.log(`✅ Job passed prescreening gate: ${reason}`);
                }
            } else {
                console.warn("⚠️ Prescreen request failed to return a result.");
            }
        } catch (prescreenErr) {
            console.error("❌ Error running job prescreening gate via background script:", prescreenErr);
        }

        // Check for daily limit
        const initialLimitCheck = checkDailyApplyLimit();
        if (initialLimitCheck.detected) {
            handleDailyLimit(initialLimitCheck);
            return;
        }

        if (appState.isPaused) {
            await waitUntilResumed();
        }

        const dailyLimitCheck = checkDailyApplyLimit();
        if (dailyLimitCheck.detected) {
            handleDailyLimit(dailyLimitCheck);
            return;
        }

        // Look for Easy Apply button
        const easyApplyBtn = document.getElementById("jobs-apply-button-id") ||
            document.querySelector('[data-live-test-job-apply-button]') ||
            document.querySelector('.jobs-apply-button');

        if (easyApplyBtn) {
            const buttonText = easyApplyBtn.textContent.trim();

            const buttonTextLower = buttonText.toLowerCase();
            const isEasyApplyButton =
                easyApplyBtn.id === 'jobs-apply-button-id' ||
                easyApplyBtn.hasAttribute('data-live-test-job-apply-button') ||
                easyApplyBtn.classList.contains('jobs-apply-button') && (
                    buttonTextLower.includes('easy apply') ||
                    buttonTextLower.includes('candidature simplifiée') ||
                    buttonTextLower.includes('candidati ora') ||
                    buttonTextLower.includes('candidatura simplificada')
                );

            if (isEasyApplyButton) {
                // Check if button is disabled (daily limit)
                if (easyApplyBtn.disabled || easyApplyBtn.classList.contains('artdeco-button--disabled')) {
                    console.log('🚫 Apply button is disabled, checking for daily limit...');
                    const limitCheck = checkDailyApplyLimit();
                    if (limitCheck.detected) {
                        handleDailyLimit(limitCheck);
                        return;
                    }
                    // Button disabled for another reason — skip this job
                    skipCurrentJob(currentCard, totalCards, 'apply button disabled (not daily limit)');
                    return;
                }

                if (appState.isPaused) {
                    await waitUntilResumed();
                }

                console.log('✅ Opening application modal...');
                await clickLikeHuman(easyApplyBtn);

                await humanPacingDelay('after Easy Apply click');

                const postClickLimitCheck = checkDailyApplyLimit();
                if (postClickLimitCheck.detected) {
                    handleDailyLimit(postClickLimitCheck);
                    return;
                }

                if (appState.isPaused) {
                    await waitUntilResumed();
                }

                const formResult = await processApplicationForm(progress);

                try {
                    if (formResult) {
                        currentCard.classList.add('ufh-applied');
                        setJobCardStyle(currentCard, 'success', 'applied successfully');
                    } else {
                        logRejection(currentCard, 'form filling failed / aborted');
                        setJobCardStyle(currentCard, 'error', 'form filling failed / aborted');
                    }
                } catch (_) {
                    /* no-op if style fails */
                }

            } else {
                console.log('⚠️ Button found but not recognized as Easy Apply:', buttonText);
                logRejection(currentCard, `not easy apply: ${buttonText}`);
                setJobCardStyle(currentCard, 'skipped', `not easy apply: ${buttonText}`);
            }
        } else {
            console.log('⚠️ No Easy Apply button found for job', progress);
            logRejection(currentCard, 'no easy apply button');
            setJobCardStyle(currentCard, 'skipped', 'no easy apply button');

            const limitCheckNoButton = checkDailyApplyLimit();
            if (limitCheckNoButton.detected) {
                handleDailyLimit(limitCheckNoButton);
                return;
            }
        }

        appState.currentIndex++;
        saveRunnerStateToSession();

        updateApplicationState(true, {
            current: appState.currentIndex,
            total: appState.totalJobs,
            isPaused: appState.isPaused,
            finished: false
        });

        scheduleNext(() => {
            if (!appState.isPaused && !appState.aborted && appState.currentIndex < totalCards) {
                clickNextJob();
            } else if (!appState.isPaused && !appState.aborted && appState.currentIndex >= totalCards) {
                clickNextJob();
            }
        }, randomHumanPacingMs());

    } catch (error) {
        console.log('❌ Error processing job', appState.currentIndex + 1 + '/' + appState.totalJobs, ':', error.message);
        console.error('Full error:', error);

        const jobCards = document.querySelectorAll('.job-card-container, .jobs-search-results__list-item');
        if (appState.currentIndex < jobCards.length) {
            const errReason = error.message || String(error);
            logRejection(jobCards[appState.currentIndex], errReason);
            setJobCardStyle(jobCards[appState.currentIndex], 'error', errReason);
        }

        appState.currentIndex++;
        saveRunnerStateToSession();

        updateApplicationState(true, {
            current: appState.currentIndex,
            total: appState.totalJobs,
            isPaused: appState.isPaused,
            finished: false
        });

        scheduleNext(() => {
            if (!appState.isPaused && !appState.aborted && appState.currentIndex < appState.totalJobs) {
                clickNextJob();
            }
        }, randomHumanPacingMs());
    }
}

// =============================================
// Form filling orchestration
// =============================================

async function fillRequiredFields() {
    console.log('✏️ Checking required fields for user input...');
    devLog('FILL', '--- fillRequiredFields START ---');

    const requiredSelectors = [
        'input[required]',
        'select[required]',
        'textarea[required]',
        '[aria-required="true"]',
        'fieldset input[type="checkbox"]',
        '.fb-dash-form-element input[type="checkbox"]',
        'input[type="checkbox"][aria-required="true"]'
    ];

    const allRequiredInputs = [];
    const processedRadioNames = new Set();
    requiredSelectors.forEach(selector => {
        const found = document.querySelectorAll(selector);
        devLog('FILL', `Selector "${selector}" matched ${found.length} element(s)`);
        found.forEach(input => {
            if (!allRequiredInputs.includes(input)) {
                // Deduplicate radio groups — only keep the first radio per name
                if (input.type === 'radio') {
                    if (processedRadioNames.has(input.name)) return;
                    processedRadioNames.add(input.name);
                    devLog('FILL', `Radio group registered: name="${input.name}"`);
                }
                allRequiredInputs.push(input);
            }
        });
    });

    devLog('FILL', `Total unique required inputs found: ${allRequiredInputs.length}`);

    const inputsNeedingData = [];
    for (const input of allRequiredInputs) {
        if (appState.isPaused) {
            await waitUntilResumed();
        }
        if (input.offsetParent !== null) {
            const needsInput = await checkIfFieldNeedsInput(input);
            devLog('FILL', `checkIfFieldNeedsInput type=${input.type || input.tagName} -> ${needsInput}`);
            if (needsInput) {
                inputsNeedingData.push(input);
            }
        } else {
            devLog('FILL', `Skipping hidden input type=${input.type || input.tagName}`);
        }
    }

    if (inputsNeedingData.length === 0) {
        console.log('✅ No required fields need input.');
        devLog('FILL', '--- fillRequiredFields END (nothing to fill) ---');
        return;
    }

    console.log(`📝 Found ${inputsNeedingData.length} fields needing input`);

    const fieldsToFillManually = [];

    for (const input of inputsNeedingData) {
        if (appState.isPaused) {
            await waitUntilResumed();
        }

        const label = extractCleanLabel(input, 0) || 'Field';
        const fieldType = input.type === 'radio' ? 'radio' : input.tagName === 'SELECT' ? 'select' : input.type || input.tagName.toLowerCase();
        devLog('FILL', `Processing field: "${label}" (type=${fieldType})`);

        // Prioritize: Extension Profile exact match storage first
        const extensionAnswer = await getStoredFieldAnswer(label);
        if (extensionAnswer) {
            devLog('FILL', `  -> Found stored answer: "${String(extensionAnswer).substring(0, 50)}"`);
            const accepted = await applyAndVerifyField(input, extensionAnswer, fieldType, label);
            if (accepted) {
                continue;
            } else {
                console.warn(`⚠️ Field validation failed for stored answer on "${label}". Retrying with backend/heuristics.`);
            }
        }

        // Get options for options-based fields
        const policyOptions = (() => {
            if (input.type === 'radio') {
                return Array.from(document.querySelectorAll(`input[name="${input.name}"]`)).map(getRadioLabel).filter(Boolean);
            }
            if (input.tagName === 'SELECT') {
                return Array.from(input.options).slice(1).map(o => o.textContent.trim()).filter(Boolean);
            }
            return [];
        })();

        // Query the UFHAnswerPolicy (which calls /qa/answer and has local emergency heuristics fallback built-in)
        let policyAnswer = null;
        if (window.UFHAnswerPolicy && window.UFHAnswerPolicy.answerForField) {
            try {
                policyAnswer = await window.UFHAnswerPolicy.answerForField({
                    label,
                    type: fieldType,
                    options: policyOptions
                });
            } catch (err) {
                console.error("❌ Error while invoking answerForField:", err);
            }
        }

        if (policyAnswer && policyAnswer.matched && policyAnswer.answer !== null) {
            console.log(`🧭 Policy/Backend answered "${label}" with "${policyAnswer.answer}" (${policyAnswer.source})`);
            const accepted = await applyAndVerifyField(input, policyAnswer.answer, fieldType, label);
            if (accepted) {
                await saveFieldAnswer(label, policyAnswer.answer);
                await syncProfile({ [label]: policyAnswer.answer });
                continue;
            } else {
                console.warn(`⚠️ Field validation failed for policy answer on "${label}" with value "${policyAnswer.answer}".`);
            }
        }

        // If both failed or unmatched, add to manual prompt list
        fieldsToFillManually.push(input);
    }

    // Step 3: Manual prompt fallback
    if (fieldsToFillManually.length > 0) {
        console.log(`🧑‍💻 Prompting user for ${fieldsToFillManually.length} remaining fields...`);
        const fieldsNotAnswered = [];

        for (const input of fieldsToFillManually) {
            if (appState.isPaused) {
                await waitUntilResumed();
            }

            const label = extractCleanLabel(input, 0) || 'Field';
            const fieldType = input.type === 'radio' ? 'radio' : input.tagName === 'SELECT' ? 'select' : input.type || input.tagName.toLowerCase();

            const userAnswer = await promptUserForField(input, label);
            if (userAnswer) {
                const accepted = await applyAndVerifyField(input, userAnswer, fieldType, label);
                if (accepted) {
                    await saveFieldAnswer(label, userAnswer);
                    await syncProfile({ [label]: userAnswer });
                } else {
                    console.warn(`⚠️ User answer failed verification check for "${label}".`);
                    fieldsNotAnswered.push({ input, label });
                }
            } else {
                fieldsNotAnswered.push({ input, label });
            }
            await sleep(300);
        }

        if (fieldsNotAnswered.length > 0) {
            const fieldLabels = fieldsNotAnswered.map(f => f.label).join(', ');

            if (window.UFH_UNATTENDED_MODE !== false) {
                console.log(`🤖 Unattended mode: required fields not completed (${fieldLabels}); skipping this job without prompting.`);
                throw new Error('Application skipped: Required fields not completed');
            }

            const continueAnyway = confirm(
                `⚠️ APPLY MODE WARNING\n\n` +
                `${fieldsNotAnswered.length} required field(s) were not filled correctly:\n${fieldLabels}\n\n` +
                `Submitting with missing or invalid required fields may cause the application to fail.\n\n` +
                `Do you want to continue anyway?\n\n` +
                `Click OK to continue, Cancel to stop the application process.`
            );

            if (!continueAnyway) {
                throw new Error('Application stopped: Required fields not completed');
            }
        }
    }

    console.log('✅ Required fields processed');
}

// Challenge/Checkpoint detection helper
function checkChallengeDetected() {
    const href = window.location.href;
    if (href.includes('checkpoint/challenge') || href.includes('security/captcha')) {
        console.log('🛑 Challenge URL detected:', href);
        return true;
    }

    const isVisible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 20 &&
            rect.height > 20 &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.opacity !== '0';
    };

    const visibleChallengeDialog = Array.from(document.querySelectorAll(
        '.challenge-dialog, #captcha-dialog, [data-test-captcha], [data-test-challenge]'
    )).find(isVisible);
    if (visibleChallengeDialog) {
        console.log('🛑 Visible challenge dialog detected:', visibleChallengeDialog.outerHTML.substring(0, 300));
        return true;
    }

    const visibleChallengeFrame = Array.from(document.querySelectorAll('iframe')).find(frame => {
        const src = (frame.src || '').toLowerCase();
        return isVisible(frame) && (
            src.includes('captcha') ||
            src.includes('/checkpoint/') ||
            src.includes('challenge')
        );
    });
    if (visibleChallengeFrame) {
        console.log('🛑 Visible challenge iframe detected:', visibleChallengeFrame.src);
        return true;
    }

    return false;
}

// Clean up any remaining Artdeco modals or confirm dialogs
async function cleanUpRemainingModals() {
    console.log("🧹 Running modal cleanup...");
    const modals = document.querySelectorAll('.artdeco-modal, [data-test-modal]');

    // Words that indicate a destructive/discard intent - we ONLY click a button if its
    // text matches one of these. We never blindly click by selector alone (e.g.
    // [data-test-dialog-secondary-btn] could be a "Save" button on some dialogs).
    const DISCARD_KEYWORDS = ['discard', 'supprimer', 'abandonner', 'abandon', 'no', 'löschen', 'ignorer', 'eliminar'];

    for (const modal of modals) {
        // --- Discard / abandon button: text-gated ---
        const discardBtn =
            // Named discard control (unambiguous)
            modal.querySelector('[data-control-name="discard_application_confirm_btn"]') ||
            // Fall back to scanning every button for a discard-intent label
            Array.from(modal.querySelectorAll('button')).find(btn => {
                const text = btn.textContent.trim().toLowerCase();
                return DISCARD_KEYWORDS.some(kw => text.includes(kw));
            });

        if (discardBtn) {
            console.log(`🧹 Clicking discard button: "${discardBtn.textContent.trim()}"`); 
            discardBtn.click();
            await sleep(1000);
        }

        // --- Close / dismiss button: aria-label gated (safe — no page mutation) ---
        const closeBtn =
            modal.querySelector('.artdeco-modal__dismiss') ||
            modal.querySelector('[data-test-modal-close-btn]') ||
            modal.querySelector('button[aria-label*="Close"]') ||
            modal.querySelector('button[aria-label*="Dismiss"]') ||
            modal.querySelector('button[aria-label*="Ignorer"]');

        if (closeBtn) {
            console.log("🧹 Found close button in open modal, clicking it...");
            closeBtn.click();
            await sleep(1000);
        }
    }
}

// Robust Job ID extraction helper
// Priority: active selected card > job details card link > URL parameter.
// URL is last because it can reflect a previously-viewed job when the user
// clicks through cards quickly — the details panel updates before the URL does.
function getActiveJobId() {
    let jobId = null;

    // 1. Active / selected job card (most reliable: reflects what's rendered in the details pane)
    const activeCard = document.querySelector(
        '.jobs-search-results-list__list-item--active, ' +
        '.job-card-container--active, ' +
        '.job-card-list__entity-lockup--active'
    );
    if (activeCard) {
        const cardLink = activeCard.querySelector('a[href*="/jobs/view/"]');
        if (cardLink) {
            const m = cardLink.href.match(/\/jobs\/view\/(\d+)/);
            if (m) {
                jobId = m[1];
                console.log(`🔎 Job ID from active card: ${jobId}`);
            }
        }
    }

    // 2. Job details top-card title link (shown in the details pane header)
    if (!jobId) {
        const topCardLink = document.querySelector(
            '.job-details-jobs-unified-top-card__job-title a'
        );
        if (topCardLink) {
            const m = topCardLink.href.match(/\/jobs\/view\/(\d+)/);
            if (m) {
                jobId = m[1];
                console.log(`🔎 Job ID from details title link: ${jobId}`);
            }
        }
    }

    // 3. URL parameter — fallback only (can lag behind the active card)
    if (!jobId) {
        const urlMatch =
            window.location.href.match(/[?&]currentJobId=(\d+)/) ||
            window.location.href.match(/\/jobs\/view\/(\d+)/);
        if (urlMatch) {
            jobId = urlMatch[1];
            console.log(`🔎 Job ID from URL (fallback): ${jobId}`);
        }
    }

    return jobId;
}

async function verifyApplicationSubmitted(jobId, company) {
    const modalSelector = '.jobs-easy-apply-modal, [data-test-modal], [role="dialog"], .artdeco-modal';
    const modalSuccessPhrases = [
        'application was sent',
        'your application was sent',
        'application submitted',
        'next best action',
        'candidature envoy',
        'candidatura enviada',
        'solicitud enviada',
        'bewerbung gesendet',
        'postulación enviada',
        'candidature inviata',
        'candidatura inviata'
    ];

    // LinkedIn can take several seconds to navigate to post-apply or repaint
    // the success modal, especially in Chrome for Testing with extension logs.
    for (let attempt = 0; attempt < 15; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000));

        // --- 1. Strongest signal: postApplyJobId in URL ---
        if (jobId && window.location.href.includes('postApplyJobId=' + jobId)) {
            console.log('✅ Submit verified via postApplyJobId URL match.');
            return true;
        }
        if (window.location.href.includes('postApplyJobId=')) {
            console.log('✅ Submit verified via postApplyJobId present in URL.');
            return true;
        }

        // --- 2. Modal-scoped success screen (Done button + no active inputs) ---
        // We deliberately do NOT check document.body.textContent because LinkedIn
        // job cards and list items also contain words like "Applied" and would
        // produce false positives.
        const modal = document.querySelector(modalSelector);
        if (modal) {
            const hasInputs = modal.querySelectorAll('input:not([type="hidden"]), select, textarea').length > 0;
            const doneBtn = Array.from(modal.querySelectorAll('button')).find(btn => {
                const text = btn.textContent.trim().toLowerCase();
                return ['done', 'terminé', 'fait', 'fertig', 'finished', 'complete'].includes(text);
            });
            if (!hasInputs && doneBtn) {
                console.log('✅ Submit verified via Done button + no inputs inside modal.');
                return true;
            }

            // Success text strictly scoped to the modal
            const modalText = modal.textContent || '';
            const modalTextLower = modalText.toLowerCase();
            if (modalSuccessPhrases.some(phrase => modalTextLower.includes(phrase))) {
                console.log('✅ Submit verified via success phrase inside modal.');
                return true;
            }
            if (company && modalTextLower.includes(`your application was sent to ${company.toLowerCase()}`)) {
                console.log('✅ Submit verified via company-specific success phrase inside modal.');
                return true;
            }
        }
    }

    // --- 3. Log any visible errors for debugging ---
    const visibleErrors = Array.from(document.querySelectorAll(
        '.jobs-easy-apply-modal .artdeco-inline-feedback--error, ' +
        '[data-test-modal] .artdeco-inline-feedback--error, ' +
        '[role="dialog"] .artdeco-inline-feedback--error, ' +
        '.artdeco-modal .artdeco-inline-feedback--error, ' +
        '.jobs-easy-apply-modal .fb-dash-error, ' +
        '[data-test-modal] .fb-dash-error, ' +
        '[role="dialog"] .fb-dash-error, ' +
        '.artdeco-modal .fb-dash-error, ' +
        '[data-test-modal] [role="alert"]'
    )).map(el => el.textContent.trim()).filter(Boolean);

    if (visibleErrors.length) {
        console.log('⚠️ Submit verification saw errors inside modal:', visibleErrors.join(' | '));
    } else {
        const modal = document.querySelector(modalSelector);
        console.log('⚠️ Submit verification timed out; modal text:', (modal?.textContent || '').trim().slice(0, 500));
    }

    return false;
}

// =============================================
// Multi-step form processing
// =============================================

async function processApplicationForm(progress) {
    console.log('📝 Processing application form for job', progress);

    let allFormData = [];
    let jobTitle = '';
    let company = '';
    let jobUrl = null;
    let jobId = null;
    let submittedSuccess = false;

    try {
        if (checkChallengeDetected()) {
            console.error("🛑 Security challenge detected! Stopping application process.");
            alert("🛑 CHALLENGE DETECTED\n\nLinkedIn has prompted a verification challenge or CAPTCHA. The runner has been paused to protect your account.");
            abortApplying();
            return false;
        }

        jobTitle = document.querySelector('h1.t-24, .job-details-jobs-unified-top-card__job-title')?.textContent?.trim() || 'Unknown Job';
        company = document.querySelector('.job-details-jobs-unified-top-card__company-name a, .job-details-jobs-unified-top-card__company-name')?.textContent?.trim() || 'Unknown Company';

        jobId = getActiveJobId();
        if (!jobId) {
            jobId = Date.now().toString();
            console.warn(`⚠️ Could not extract Job ID. Falling back to timestamp: ${jobId}`);
        }
        jobUrl = `https://www.linkedin.com/jobs/view/${jobId}`;

        let currentStep = 1;
        let maxSteps = 10;
        let retriesOnSamePage = 0;

        while (currentStep <= maxSteps) {
            console.log(`📄 Processing form step ${currentStep}...`);

            if (appState.isPaused) {
                await waitUntilResumed();
            }

            await new Promise(resolve => setTimeout(resolve, 1500));

            const modal = document.querySelector('.jobs-easy-apply-modal, [data-test-modal]');
            if (!modal) {
                console.log('❌ Modal closed unexpectedly');
                break;
            }

            if (appState.isPaused) {
                await waitUntilResumed();
            }

            await fillKnownOptionalFields(modal);

            // Fill required fields — may need multiple passes if filling one
            // field (e.g. radio) reveals new conditional fields
            let fillPasses = 0;
            const maxFillPasses = 3;
            while (fillPasses < maxFillPasses) {
                devLog('FORM', `Fill pass ${fillPasses + 1}/${maxFillPasses}`);
                await fillRequiredFields();
                fillPasses++;

                // Wait for LinkedIn's JS to react to field changes
                await new Promise(resolve => setTimeout(resolve, 800));

                // Check if there are still unfilled required fields
                const stillEmpty = modal.querySelectorAll(
                    'input[required]:not([type="hidden"]), select[required], textarea[required], [aria-required="true"]'
                );
                let hasUnfilled = false;
                for (const el of stillEmpty) {
                    if (el.offsetParent === null) continue; // skip hidden
                    if (el.type === 'radio') {
                        const group = modal.querySelectorAll(`input[name="${el.name}"]`);
                        if (!Array.from(group).some(r => r.checked)) { hasUnfilled = true; break; }
                    } else if (el.tagName === 'SELECT') {
                        if (!el.value || el.selectedIndex <= 0) { hasUnfilled = true; break; }
                    } else {
                        if (!el.value || el.value.trim() === '') { hasUnfilled = true; break; }
                    }
                }
                if (!hasUnfilled) {
                    devLog('FORM', `All visible required fields are filled after pass ${fillPasses}`);
                    break;
                }
                console.log(`🔄 Fill pass ${fillPasses}: still have unfilled fields, retrying...`);
            }

            if (appState.isPaused) {
                await waitUntilResumed();
            }

            const stepFormData = await gatherFormFields(modal);
            allFormData.push(...stepFormData);

            // Find navigation button — scoped to modal footer area
            const modalFooter = modal.querySelector('.jobs-easy-apply-footer, .jobs-easy-apply-modal__content footer, .artdeco-modal__actionbar');
            const searchScope = modalFooter || modal;

            const nextBtn = searchScope.querySelector('[data-easy-apply-next-button]') ||
                searchScope.querySelector('[data-live-test-easy-apply-review-button]') ||
                searchScope.querySelector('[data-control-name="continue_unify"]') ||
                searchScope.querySelector('button.artdeco-button--primary');

            if (nextBtn) {
                const btnText = nextBtn.textContent.trim();
                devLog('FORM', `Button found: text="${btnText}" id="${nextBtn.id || ''}" classes="${nextBtn.className.substring(0, 60)}"`);

                const isReviewButton = btnText.includes('Vérifier') ||
                    btnText.includes('Review') ||
                    nextBtn.hasAttribute('data-live-test-easy-apply-review-button');

                const isSubmitButton = btnText.includes('Envoyer la candidature') ||
                    btnText.includes('Submit') ||
                    btnText.includes('Send application') ||
                    btnText.includes('Envoyer') ||
                    btnText.includes('Soumettre') ||
                    nextBtn.hasAttribute('data-live-test-easy-apply-submit-button');

                const isContinueButton = btnText.includes('Continue') ||
                    btnText.includes('Next') ||
                    btnText.includes('Suivant') ||
                    nextBtn.hasAttribute('data-easy-apply-next-button');

                if (isSubmitButton) {
                    console.log('📋 Final submit page reached');
                    devLog('FORM', 'Button classified as SUBMIT');

                    await fillRequiredFields();
                    await handleFinalPageCheckboxes();

                    const finalFormData = await gatherFormFields(modal);
                    allFormData.push(...finalFormData);

                    const questionAnswers = convertFormDataToQA(allFormData);

                    const jobData = {
                        id: jobId,
                        jobTitle,
                        company,
                        jobUrl,
                        appliedAt: new Date().toISOString(),
                        status: 'submitted',
                        questionAnswers: questionAnswers
                    };

                    await humanPacingDelay('before submitting application');
                    await clickLikeHuman(nextBtn);

                    // Verify the submission BEFORE dismissing the modal or saving
                    const submitted = await verifyApplicationSubmitted(jobId, company);
                    if (!submitted) {
                        console.log('⚠️ Submit click did not verify as successful');
                        break;
                    }

                    // Success! Reset consecutive failures and save job application
                    appState.consecutiveFailures = 0;
                    appState.applicationsSubmittedThisRun++;

                    await saveJobData(jobData);
                    console.log('📊 Application submitted and saved');

                    browserAPI.runtime.sendMessage({ type: 'NOTIFY_APPLICATION_SUCCESS' }).catch(() => { });

                    // Now close the success modal
                    await handleSuccessModal();

                    if (appState.applicationsSubmittedThisRun >= appState.maxApplicationsPerRun) {
                        console.log(`🎉 Reached max applications limit for this run (${appState.maxApplicationsPerRun}). Stopping.`);
                        alert(`🎉 RUN COMPLETED\n\nReached the maximum application limit of ${appState.maxApplicationsPerRun} jobs for this run!`);
                        handleApplicationCompletion();
                    }

                    submittedSuccess = true;
                    break;

                } else if (isReviewButton || isContinueButton) {
                    devLog('FORM', `Button classified as ${isReviewButton ? 'REVIEW' : 'CONTINUE'}`);
                    if (isReviewButton) {
                        const reviewPageFormData = await gatherFormFields(modal);
                        allFormData.push(...reviewPageFormData);
                        await handleFinalPageCheckboxes();
                    }

                    // Snapshot current form content to detect if page actually changed
                    const contentBefore = modal.innerHTML.length;

                    await humanPacingDelay(`before ${isReviewButton ? 'review' : 'continue'} click`);
                    await clickLikeHuman(nextBtn);
                    await humanPacingDelay(`after ${isReviewButton ? 'review' : 'continue'} click`);

                    // Check if page actually changed (validation may have prevented it)
                    const modalAfter = document.querySelector('.jobs-easy-apply-modal, [data-test-modal]');
                    const contentAfter = modalAfter ? modalAfter.innerHTML.length : 0;

                    if (modalAfter && Math.abs(contentAfter - contentBefore) < 50) {
                        retriesOnSamePage++;
                        console.log(`⚠️ Page didn't change after clicking Next (retry ${retriesOnSamePage}/3)`);
                        if (retriesOnSamePage >= 3) {
                            console.log('❌ Stuck on same page after 3 retries, moving on');
                            break;
                        }
                    } else {
                        currentStep++;
                        retriesOnSamePage = 0;
                    }
                } else {
                    console.log('⚠️ Unknown button type:', btnText);
                    break;
                }
            } else {
                console.log('❌ No next/continue button found');
                break;
            }
        }

        console.log('✅ Application form processing completed for job', progress);
        await cleanUpRemainingModals();

        if (!submittedSuccess) {
            console.log('⚠️ Application did not submit successfully (e.g. validation error or stuck page); capturing failure screenshot');
            if (jobId) {
                browserAPI.runtime.sendMessage({ type: 'CAPTURE_SCREENSHOT', jobId: jobId }).catch(() => {});
            }

            appState.consecutiveFailures++;
            if (appState.consecutiveFailures >= appState.maxFailuresBeforeStop) {
                console.error(`🛑 Stop threshold reached: ${appState.consecutiveFailures} consecutive failures. Stopping run.`);
                alert(`🛑 STOP THRESHOLD REACHED\n\nToo many consecutive failures (${appState.consecutiveFailures}). Stopped to prevent spam/safety issues.`);
                abortApplying();
            }
        }

        return submittedSuccess;

    } catch (error) {
        console.log('❌ Error in form processing:', error.message);
        
        if (jobId) {
            browserAPI.runtime.sendMessage({ type: 'CAPTURE_SCREENSHOT', jobId: jobId }).catch(() => {});
        }

        if (error.message.includes('Application stopped: Required fields not completed') ||
            error.message.includes('Application skipped: Required fields not completed')) {
            return false;
        }

        appState.consecutiveFailures++;
        if (appState.consecutiveFailures >= appState.maxFailuresBeforeStop) {
            console.error(`🛑 Stop threshold reached: ${appState.consecutiveFailures} consecutive failures. Stopping run.`);
            alert(`🛑 STOP THRESHOLD REACHED\n\nToo many consecutive failures (${appState.consecutiveFailures}). Stopped to prevent spam/safety issues.`);
            abortApplying();
            return false;
        }

        alert(
            'LinkedIn Auto Apply encountered an unexpected issue while filling the form.\n' +
            'Details: ' + error.message + '\n\n' +
            'You can continue filling the remaining fields manually and submit the application yourself, or close the modal if you prefer.'
        );
        return false;
    }
}

// =============================================
// Checkbox handling, modals, state
// =============================================

async function handleFinalPageCheckboxes() {
    return new Promise((resolve) => {
        browserAPI.storage.local.get(['uncheckFinalPageCheckboxes']).then((result) => {
            const shouldUncheck = result.uncheckFinalPageCheckboxes || false;

            if (shouldUncheck) {
                console.log('🔲 Unchecking final page checkboxes as configured...');

                const checkboxSelectors = [
                    'input[type="checkbox"]',
                    '#follow-company-checkbox',
                    'input[id*="follow"]',
                    'input[id*="newsletter"]',
                    'input[id*="notification"]',
                    'input[class*="follow"]',
                ];

                const allCheckboxes = new Set();
                checkboxSelectors.forEach(selector => {
                    const found = document.querySelectorAll(selector);
                    found.forEach(cb => allCheckboxes.add(cb));
                });

                const checkboxes = Array.from(allCheckboxes);
                let uncheckedCount = 0;

                checkboxes.forEach(checkbox => {
                    const isChecked = checkbox.checked || checkbox.hasAttribute('checked');

                    if (isChecked) {
                        const checkboxId = checkbox.id || 'no-id';
                        let label = '';

                        if (checkbox.id) {
                            const labelElement = document.querySelector(`label[for="${checkbox.id}"]`);
                            if (labelElement) {
                                label = labelElement.textContent.trim();
                            }
                        }

                        if (!label) {
                            const parent = checkbox.closest('label') || checkbox.parentElement;
                            if (parent) {
                                label = parent.textContent.trim().substring(0, 50) + '...';
                            }
                        }

                        if (!label) {
                            label = `Checkbox with id: ${checkboxId}`;
                        }

                        const isRequired = label.toLowerCase().includes('required') ||
                            label.toLowerCase().includes('obligatoire') ||
                            checkbox.hasAttribute('required');

                        if (!isRequired) {
                            checkbox.checked = false;
                            checkbox.removeAttribute('checked');

                            if (checkbox.classList.contains('ember-checkbox')) {
                                Object.defineProperty(checkbox, 'checked', {
                                    value: false,
                                    writable: true,
                                    configurable: true
                                });
                            }

                            const events = ['input', 'change', 'click'];
                            events.forEach(eventType => {
                                const event = new Event(eventType, {
                                    bubbles: true,
                                    cancelable: true,
                                    composed: true
                                });
                                checkbox.dispatchEvent(event);
                            });

                            setTimeout(async () => {
                                if (checkbox.checked) {
                                    if (checkbox.id) {
                                        const label = document.querySelector(`label[for="${checkbox.id}"]`);
                                        if (label) {
                                            await clickLikeHuman(label);
                                        }
                                    }
                                }
                            }, 100);

                            uncheckedCount++;
                            console.log(`✅ Unchecked checkbox: ${label}`);
                        }
                    }
                });

                console.log(`🔲 Total checkboxes unchecked: ${uncheckedCount}`);
            }

            resolve();
        });
    });
}

async function closeApplicationModal() {
    console.log('🔍 Looking for close button to dismiss application...');

    let closeBtn = document.querySelector('[data-test-modal-close-btn]') ||
        document.querySelector('.artdeco-modal__dismiss') ||
        document.querySelector('[aria-label="Ignorer"]') ||
        document.querySelector('[aria-label="Dismiss"]') ||
        document.querySelector('[aria-label="Close"]');

    if (!closeBtn) {
        const possibleCloseButtons = document.querySelectorAll('button[class*="artdeco-button--circle"], button[class*="modal__dismiss"]');
        for (const btn of possibleCloseButtons) {
            if (btn.querySelector('svg[data-test-icon="close-medium"]') ||
                btn.querySelector('use[href="#close-medium"]') ||
                btn.getAttribute('aria-label')?.toLowerCase().includes('close') ||
                btn.getAttribute('aria-label')?.toLowerCase().includes('dismiss') ||
                btn.getAttribute('aria-label')?.toLowerCase().includes('ignorer')) {
                closeBtn = btn;
                break;
            }
        }
    }

    if (closeBtn) {
        await clickLikeHuman(closeBtn);
        await humanPacingDelay('after close modal click');
        await handleConfirmationModal();
    } else {
        console.log('⚠️ Close button not found in application modal');
    }
}

async function handleSuccessModal() {
    await humanPacingDelay('before handling success modal');

    let doneBtn = null;

    // Method 1: Find by button text (Done/Terminé/etc.)
    const buttonSpans = document.querySelectorAll('button span.artdeco-button__text');
    for (const span of buttonSpans) {
        const buttonText = span.textContent.trim();
        if (['Terminé', 'Done', 'Fait', 'Finished', 'Complete'].includes(buttonText)) {
            doneBtn = span.closest('button');
            break;
        }
    }

    // Method 2: Primary buttons with expected text
    if (!doneBtn) {
        const candidateButtons = document.querySelectorAll('button[class*="artdeco-button--primary"]');
        for (const button of candidateButtons) {
            const buttonText = button.textContent.trim();
            if (['Terminé', 'Done', 'Fait', 'Finished'].some(t => buttonText.includes(t))) {
                doneBtn = button;
                break;
            }
        }
    }

    // Method 3: Specific ember structure
    if (!doneBtn) {
        const specificButtons = document.querySelectorAll('button[id^="ember"].artdeco-button.artdeco-button--2.artdeco-button--primary.ember-view');
        for (const button of specificButtons) {
            const span = button.querySelector('span.artdeco-button__text');
            if (span && ['Terminé', 'Done', 'Fait', 'Finished'].includes(span.textContent.trim())) {
                doneBtn = button;
                break;
            }
        }
    }

    if (doneBtn) {
        console.log('✅ Found success modal done button, clicking it...');
        await clickLikeHuman(doneBtn);
        await humanPacingDelay('after success modal done click');
        return;
    }

    // Method 4: No Done button found (Premium upsell modal, etc.)
    // Close via the modal dismiss (X) button
    console.log('⚠️ No Done button found, looking for modal dismiss button...');

    let dismissBtn = document.querySelector('.artdeco-modal__dismiss') ||
        document.querySelector('[data-test-modal-close-btn]') ||
        document.querySelector('button[aria-label="Ignorer"]') ||
        document.querySelector('button[aria-label="Dismiss"]') ||
        document.querySelector('button[aria-label="Close"]');

    // Try finding dismiss by icon
    if (!dismissBtn) {
        const circleButtons = document.querySelectorAll('button.artdeco-button--circle, button[class*="modal__dismiss"]');
        for (const btn of circleButtons) {
            if (btn.querySelector('svg[data-test-icon="close-medium"]') ||
                btn.querySelector('use[href="#close-medium"]') ||
                btn.getAttribute('aria-label')?.toLowerCase().includes('close') ||
                btn.getAttribute('aria-label')?.toLowerCase().includes('dismiss') ||
                btn.getAttribute('aria-label')?.toLowerCase().includes('ignorer')) {
                dismissBtn = btn;
                break;
            }
        }
    }

    if (dismissBtn) {
        console.log('✅ Found modal dismiss button, clicking it...');
        await clickLikeHuman(dismissBtn);
        await humanPacingDelay('after success modal dismiss click');
    } else {
        console.log('⚠️ No dismiss button found either, modal may remain open');
    }
}

async function handleConfirmationModal() {
    await new Promise(resolve => setTimeout(resolve, 1000));

    const DISCARD_KEYWORDS = ['discard', 'supprimer', 'abandonner', 'abandon', 'no', 'loschen', 'löschen', 'ignorer', 'eliminar'];
    const discardBtn = document.querySelector('[data-control-name="discard_application_confirm_btn"]') ||
        Array.from(document.querySelectorAll('button')).find(btn => {
            const text = btn.textContent.trim().toLowerCase();
            return DISCARD_KEYWORDS.some(kw => text.includes(kw));
        });

    if (discardBtn) {
        await humanPacingDelay('before discard confirmation click');
        await clickLikeHuman(discardBtn);
        await humanPacingDelay('after discard confirmation click');
    }
}

// =============================================
// Application lifecycle
// =============================================

// Preflight: verify the local Python backend is reachable before starting a run.
// If it is down, hard identity questions will have no authoritative source and the
// run will stall on manual-skip prompts.  Better to stop loudly before a single
// card is touched.
async function checkBackendHealth() {
    const probeOnce = async (attempt) => {
        const resp = await browserAPI.runtime.sendMessage({ type: 'BACKEND_HEALTH' });
        if (!resp || !resp.success) {
            throw new Error(resp?.error || 'Backend health bridge returned failure');
        }
        // Any HTTP response (including 4xx from bad payload) means the server is up.
        console.log(`✅ Backend health check passed (HTTP ${resp.status}, attempt ${attempt})`);
        return true;
    };

    for (let attempt = 1; attempt <= 3; attempt++) {
        try {
            return await probeOnce(attempt);
        } catch (err) {
            console.warn(`⚠️ Backend health check failed on attempt ${attempt}:`, err);
            if (attempt < 3) {
                await new Promise(resolve => setTimeout(resolve, 1000 * attempt));
            }
        }
    }

    console.error('❌ Backend health check failed after 3 attempts');
    return false;
}

async function fillKnownOptionalFields(modal) {
    const locationAnswer = 'Vancouver, British Columbia, Canada';
    const fields = Array.from(modal.querySelectorAll('input:not([type="hidden"]), textarea, select'));

    for (const field of fields) {
        if (field.offsetParent === null || field.disabled) continue;
        if ((field.value || '').trim()) continue;

        const label = extractCleanLabel(field, 0) || '';
        const normalized = label.toLowerCase();
        if (!normalized.includes('location') && !normalized.includes('city')) continue;

        console.log(`🧭 Filling known optional location field "${label}" with "${locationAnswer}"`);
        try {
            await applyAndVerifyField(
                field,
                locationAnswer,
                field.tagName === 'SELECT' ? 'select' : field.type || field.tagName.toLowerCase(),
                label
            );
        } catch (err) {
            console.warn(`⚠️ Failed to fill known optional field "${label}":`, err);
        }
    }
}

function startWithPreflight(beginRun) {
    return new Promise((resolve) => {
        browserAPI.storage.local.get(['applicationsLeft', 'userPlan']).then(async result => {
            const applicationsLeft = result.applicationsLeft;
            const userPlan = result.userPlan;
            const isProUser = userPlan && userPlan !== 'free' && userPlan !== 'FREE';

            if (applicationsLeft === 0) {
                if (isProUser) {
                    alert('🚫 LINKEDIN DAILY LIMIT REACHED\n\nYou\'ve reached LinkedIn\'s Easy Apply limit for today.\n\nAs a PRO user, you have unlimited applications through our service, but LinkedIn itself limits the number of Easy Apply submissions per day.\n\nPlease try again tomorrow!');

                    resolve({ started: false, totalJobs: 0, message: 'LinkedIn daily limit reached' });
                } else {
                    const shouldUpgrade = confirm('🚫 NO FREE APPLICATIONS LEFT\n\nYou have used all your free applications for today.\nUpgrade to Premium for unlimited applications!\n\nClick OK to open the billing page, or Cancel to continue browsing.');

                    if (shouldUpgrade) {
                        window.open(UFH_CONFIG.DASHBOARD_BILLING_URL, '_blank');
                    }

                    resolve({ started: false, totalJobs: 0, message: 'No free applications left - upgrade required' });
                }
                return;
            }

            // ── Backend preflight ─────────────────────────────────────────────────
            // Without a reachable backend, hard/identity questions have no
            // authoritative answer source and the run will stall.  Halt now with
            // a clear message so the user can start the Python server first.
            const backendUp = await checkBackendHealth();
            if (!backendUp) {
                alert(
                    '🚫 LOCAL PYTHON BACKEND OFFLINE\n\n' +
                    'The extension needs the local Python backend running at ' +
                    'http://127.0.0.1:5001 to answer application questions safely.\n\n' +
                    'Start the backend first:\n' +
                    '  cd master/Auto_job_applier_linkedIn_it && python3 -c "import app; app.app.run(port=5001)"\n\n' +
                    'Then click Apply again.'
                );
                resolve({ started: false, totalJobs: 0, message: 'Backend offline' });
                return;
            }

            beginRun(resolve);

        }).catch(error => {
            console.error('Error checking quota:', error);
            console.log('⚠️ Quota check failed, proceeding with application...');
            beginRun(resolve);
        });
    });
}

function startApplying() {
    console.log('🚀 Starting job application process...');
    return startWithPreflight(_beginApplying);
}

function applyCurrentJob() {
    console.log('🚀 Starting direct job application process...');
    return startWithPreflight(_beginCurrentJob);
}

// Internal helper extracted to avoid code duplication in startApplying
function _beginApplying(resolve) {
    registerAsLinkedInTab();

    sessionStorage.setItem('ufh_processing_applications', 'true');

    browserAPI.storage.local.get(['appliedJobs']).then(result => {
        appState.appliedJobs = result.appliedJobs || [];
        console.log(`📋 Loaded ${appState.appliedJobs.length} applied jobs from storage in _beginApplying.`);

        document.querySelectorAll('.job-card-container.ufh-applied, .jobs-search-results__list-item.ufh-applied').forEach(el => {
            el.classList.remove('ufh-applied');
            clearJobCardStyle(el);
        });

        const jobCards = document.querySelectorAll('.job-card-container, .jobs-search-results__list-item');
        appState.currentIndex = 0;
        appState.totalJobs = jobCards.length;
        appState.isPaused = false;
        appState.aborted = false;
        pendingTimeouts.length = 0;

        saveRunnerStateToSession();

        if (appState.totalJobs > 0) {
            console.log(`✅ Starting application process for ${appState.totalJobs} jobs`);

            updateApplicationState(true, {
                current: appState.currentIndex,
                total: appState.totalJobs,
                isPaused: appState.isPaused,
                finished: false
            });

            clickNextJob();
            resolve({
                started: true,
                totalJobs: appState.totalJobs,
                message: `Started applying to ${appState.totalJobs} jobs`
            });
        } else {
            console.log('❌ No jobs found to apply to');
            resolve({
                started: false,
                totalJobs: 0,
                message: 'No jobs found to apply to'
            });
        }
    }).catch(error => {
        console.error('Error fetching appliedJobs in _beginApplying:', error);
        appState.appliedJobs = [];
        
        document.querySelectorAll('.job-card-container.ufh-applied, .jobs-search-results__list-item.ufh-applied').forEach(el => {
            el.classList.remove('ufh-applied');
            clearJobCardStyle(el);
        });

        const jobCards = document.querySelectorAll('.job-card-container, .jobs-search-results__list-item');
        appState.currentIndex = 0;
        appState.totalJobs = jobCards.length;
        appState.isPaused = false;
        appState.aborted = false;
        pendingTimeouts.length = 0;

        saveRunnerStateToSession();

        if (appState.totalJobs > 0) {
            clickNextJob();
            resolve({
                started: true,
                totalJobs: appState.totalJobs,
                message: `Started applying to ${appState.totalJobs} jobs`
            });
        } else {
            resolve({
                started: false,
                totalJobs: 0,
                message: 'No jobs found to apply to'
            });
        }
    });
}

function _beginCurrentJob(resolve) {
    registerAsLinkedInTab();

    sessionStorage.setItem('ufh_processing_applications', 'true');
    appState.currentIndex = 0;
    appState.totalJobs = 1;
    appState.isPaused = false;
    appState.aborted = false;
    appState.directJobResult = null;
    pendingTimeouts.length = 0;
    saveRunnerStateToSession();

    updateApplicationState(true, {
        current: appState.currentIndex,
        total: appState.totalJobs,
        isPaused: false,
        finished: false
    });

    runCurrentJobApplication();
    resolve({
        started: true,
        totalJobs: 1,
        message: 'Started applying to the current job'
    });
}

function togglePauseState() {
    appState.isPaused = !appState.isPaused;
    console.log(appState.isPaused ? '⏸️ Application process paused' : '▶️ Application process resumed');

    saveRunnerStateToSession();

    updateApplicationState(true, {
        current: appState.currentIndex,
        total: appState.totalJobs,
        isPaused: appState.isPaused,
        finished: false
    });

    if (!appState.isPaused && appState.currentIndex < appState.totalJobs) {
        clickNextJob();
    }

    return appState.isPaused;
}

async function setRunOptions(options = {}) {
    if (Number.isFinite(Number(options.maxApplicationsPerRun))) {
        appState.maxApplicationsPerRun = Math.max(1, Number(options.maxApplicationsPerRun));
    }
    if (options.resetSubmissionCount) {
        appState.applicationsSubmittedThisRun = 0;
        appState.consecutiveFailures = 0;
    }
    if (Number.isFinite(Number(options.processingSpeed))) {
        delayMultiplier = Math.max(0, Number(options.processingSpeed));
        await browserAPI.storage.local.set({ processingSpeed: delayMultiplier });
    }
    return getProgress();
}

function handleApplicationCompletion() {
    console.log('✅ Application process completed');

    clearRunnerStateFromSession();

    updateApplicationState(false, {
        current: appState.currentIndex,
        total: appState.totalJobs,
        isPaused: false,
        finished: true
    });

    appState.isPaused = false;
    appState.currentIndex = 0;
    appState.totalJobs = 0;
}

function getProgress() {
    const progress = {
        current: appState.currentIndex,
        total: appState.totalJobs,
        isPaused: appState.isPaused,
        finished: appState.totalJobs > 0 && appState.currentIndex >= appState.totalJobs,
        aborted: appState.aborted,
        applicationsSubmittedThisRun: appState.applicationsSubmittedThisRun,
        maxApplicationsPerRun: appState.maxApplicationsPerRun,
        consecutiveFailures: appState.consecutiveFailures,
        rejections: appState.rejections || [],
        directJobResult: appState.directJobResult
    };

    updateApplicationState(appState.totalJobs > 0 && !progress.finished, progress);

    return progress;
}

// =============================================
// Public API (exposed to window)
// =============================================

function abortApplying() {
    console.log('🛑 abortApplying() called');
    appState.aborted = true;
    appState.isPaused = false;

    clearRunnerStateFromSession();

    pendingTimeouts.forEach(id => clearTimeout(id));
    pendingTimeouts.length = 0;
    handleApplicationCompletion();
    return true;
}

window.WebFormMonitor = {
    performScan,
    startApplying,
    applyCurrentJob,
    togglePauseState,
    setRunOptions,
    getProgress,
    getAppliedJobs,
    abortApplying
};

// Initialize
addExtensionIndicator();

console.log('✅ LinkedIn AI Auto Apply initialized');
