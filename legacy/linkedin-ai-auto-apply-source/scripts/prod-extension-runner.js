const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');
require('dotenv').config({ path: path.join(__dirname, '..', 'config.env') });

const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());

const ROOT = path.resolve(__dirname, '..');
const SEARCH_CONFIG_DIR = process.env.SEARCH_CONFIG_DIR ||
    '/Users/lovepreet/Documents/apps/jobfarm/legacy/master/Auto_job_applier_linkedIn_it';
const CHROME_EXECUTABLE_PATH = process.env.CHROME_EXECUTABLE_PATH ||
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const CHROME_USER_DATA_DIR = process.env.CHROME_USER_DATA_DIR ||
    path.join(ROOT, 'chrome-profile-linkedin');
const CHROME_PROFILE = process.env.CHROME_PROFILE || 'Default';
const USE_NSTBROWSER = process.env.USE_NSTBROWSER !== 'false';
const DIRECT_JOB_URL = process.env.LINKEDIN_DIRECT_JOB_URL || '';
const QUEUE_RESULT_FILE = process.env.JOB_QUEUE_RESULT_FILE || '';
const NST_API_BASE = (process.env.NST_API_BASE || 'http://localhost:8848/api/v2').replace(/\/$/, '');
const NST_API_KEY = process.env.NST_API_KEY || '';
const NST_PROFILE_ID = process.env.NST_PROFILE_ID || '2a7e05fd-7545-4b26-b2c9-293dcd8d495b';
const SUPERVISED_TARGET = Number(process.env.SUPERVISED_TARGET || 3);
const PROD_MAX_APPLICATIONS_PER_RUN = Number(process.env.PROD_MAX_APPLICATIONS_PER_RUN || 15);
const TERM_TIMEOUT_MS = Number(process.env.TERM_TIMEOUT_MS || 45 * 60 * 1000);
const DIRECT_TIMEOUT_MS = Number(process.env.LINKEDIN_EXTENSION_DIRECT_TIMEOUT_MS || 20 * 60 * 1000);
const START_INDEX = Number(process.env.SEARCH_START_INDEX || 0);
const MAX_TERMS = process.env.MAX_TERMS ? Number(process.env.MAX_TERMS) : null;
const LOAD_UNPACKED_EXTENSION = process.env.LOAD_UNPACKED_EXTENSION !== 'false';
const RUN_PROCESSING_SPEED = Number(process.env.RUN_PROCESSING_SPEED || 0.15);
const MAX_ALLOWED_CONSECUTIVE_FAILURES = Number(process.env.MAX_ALLOWED_CONSECUTIVE_FAILURES || 3);
const LINKEDIN_WORKPLACE_FILTER = process.env.LINKEDIN_WORKPLACE_FILTER || '1,3'; // On-site + Hybrid

const runStamp = new Date().toISOString().replace(/[:.]/g, '-');
const OUT_DIR = path.join(ROOT, 'logs', 'prod-runs', runStamp);
fs.mkdirSync(OUT_DIR, { recursive: true });

const summary = {
    startedAt: new Date().toISOString(),
    searchConfigDir: SEARCH_CONFIG_DIR,
    termsProcessed: [],
    applicationsSubmitted: 0,
    successfulSubmissions: 0,
    failedSubmissions: 0,
    skippedJobs: 0,
    supervisedPassed: false,
    productionReadinessStatus: 'not_ready',
    evidence: [],
    failure: null
};

const failurePatterns = [
    /Backend health check failed after 3 attempts/i,
    /Backend offline/i,
    /Security challenge detected/i,
    /Stop threshold reached/i,
];

function writeJson(file, data) {
    fs.writeFileSync(path.join(OUT_DIR, file), JSON.stringify(data, null, 2));
}

function loadSearchConfig() {
    const py = `
import json, sys
sys.path.insert(0, ${JSON.stringify(SEARCH_CONFIG_DIR)})
from config.search import search_terms, search_location, easy_apply_only, date_posted, experience_level
try:
  from config.search import search_locations
except ImportError:
  search_locations = [search_location]
print(json.dumps({
  "search_terms": search_terms,
  "search_location": search_location,
  "search_locations": search_locations,
  "easy_apply_only": easy_apply_only,
  "date_posted": date_posted,
  "experience_level": experience_level,
}))
`;
    const res = spawnSync('python3', ['-c', py], { encoding: 'utf8' });
    if (res.status !== 0) {
        throw new Error(`Failed to load search config: ${res.stderr || res.stdout}`);
    }
    const cfg = JSON.parse(res.stdout);
    let terms = cfg.search_terms.slice(START_INDEX);
    if (MAX_TERMS !== null && Number.isFinite(MAX_TERMS)) terms = terms.slice(0, MAX_TERMS);
    return { ...cfg, search_terms: terms };
}

function linkedInUrl(term, location, cfg) {
    const params = new URLSearchParams();
    params.set('keywords', term);
    params.set('location', location || cfg.search_location || '');
    if (cfg.easy_apply_only) params.set('f_AL', 'true');
    if ((cfg.date_posted || '').toLowerCase() === 'past week') params.set('f_TPR', 'r604800');
    if ((cfg.date_posted || '').toLowerCase() === 'past 24 hours') params.set('f_TPR', 'r86400');
    const expCodes = {
        'Internship': '1',
        'Entry level': '2',
        'Associate': '3',
        'Mid-Senior level': '4',
        'Director': '5',
        'Executive': '6'
    };
    const exp = (cfg.experience_level || []).map(x => expCodes[x]).filter(Boolean);
    if (exp.length) params.set('f_E', exp.join(','));
    if (LINKEDIN_WORKPLACE_FILTER) params.set('f_WT', LINKEDIN_WORKPLACE_FILTER);
    return `https://www.linkedin.com/jobs/search/?${params.toString()}`;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function nstApiRequest(endpoint, options = {}) {
    const url = `${NST_API_BASE}${endpoint}`;
    const headers = {
        'Content-Type': 'application/json',
        ...(NST_API_KEY ? { 'x-api-key': NST_API_KEY } : {}),
        ...(options.headers || {})
    };
    const response = await fetch(url, { ...options, headers });
    const text = await response.text();
    let data = null;
    try {
        data = text ? JSON.parse(text) : null;
    } catch (_) {
        data = text;
    }

    if (!response.ok) {
        throw new Error(`NSTBrowser API ${response.status} ${response.statusText}: ${text}`);
    }
    if (data && typeof data === 'object' && data.err === true) {
        throw new Error(`NSTBrowser API error: ${data.msg || JSON.stringify(data)}`);
    }
    return data;
}

async function connectNstBrowser() {
    const profileId = encodeURIComponent(NST_PROFILE_ID);
    console.log(`Starting NSTBrowser profile ${NST_PROFILE_ID} via ${NST_API_BASE}`);
    const startResult = await nstApiRequest(`/browsers/${profileId}`, { method: 'POST' });
    const browserData = startResult?.data || startResult;
    const browserWSEndpoint = browserData?.webSocketDebuggerUrl || browserData?.wsEndpoint || browserData?.websocketDebuggerUrl;

    if (!browserWSEndpoint) {
        throw new Error(`NSTBrowser did not return webSocketDebuggerUrl: ${JSON.stringify(startResult)}`);
    }

    console.log(`Connected NSTBrowser debugger for profile ${NST_PROFILE_ID}`);
    const browser = await puppeteer.connect({
        browserWSEndpoint,
        defaultViewport: null
    });

    if (LOAD_UNPACKED_EXTENSION) {
        await loadUnpackedExtension(browser);
    }

    return browser;
}

async function loadUnpackedExtension(browser) {
    const session = await browser.target().createCDPSession();
    try {
        await session.send('Extensions.enable').catch(() => {});
        const result = await session.send('Extensions.loadUnpacked', { path: ROOT });
        console.log(`Loaded unpacked extension into browser: ${result.id || JSON.stringify(result)}`);
    } catch (err) {
        console.warn(`Unable to load unpacked extension through CDP: ${err.message}`);
        console.warn('If the runner is not ready, load this folder manually in the NSTBrowser profile via chrome://extensions.');
    } finally {
        await session.detach().catch(() => {});
    }
}

async function launchLocalChrome() {
    const chromeLocks = ['SingletonLock', 'SingletonSocket', 'SingletonCookie'];
    for (const file of chromeLocks) {
        try { fs.rmSync(path.join(CHROME_USER_DATA_DIR, file), { force: true }); } catch (_) {}
    }

    return await puppeteer.launch({
        headless: false,
        executablePath: CHROME_EXECUTABLE_PATH,
        args: [
            `--user-data-dir=${CHROME_USER_DATA_DIR}`,
            `--profile-directory=${CHROME_PROFILE}`,
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--window-size=1365,900',
            '--enable-unsafe-extension-debugging',
            '--disable-features=DisableLoadExtensionCommandLineSwitch',
            `--disable-extensions-except=${ROOT}`,
            `--load-extension=${ROOT}`
        ],
        ignoreDefaultArgs: ['--disable-extensions'],
        defaultViewport: null
    });
}

function randomHumanPacingMs(min = 30000, max = 60000) {
    const lower = Math.min(min, max);
    const upper = Math.max(min, max);
    let u1 = Math.random();
    let u2 = Math.random();
    if (u1 <= Number.EPSILON) u1 = Number.EPSILON;

    const z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
    const mean = lower + (upper - lower) * 0.58;
    const stdDev = (upper - lower) / 6;
    const sample = mean + z0 * stdDev;
    return Math.floor(Math.max(lower, Math.min(upper, sample)));
}

function randomBetween(min, max) {
    return randomHumanPacingMs(min, max);
}

async function humanPacingDelay(label = 'between orchestration actions', minMs = 7000, maxMs = 14000) {
    const delay = randomHumanPacingMs(minMs, maxMs);
    console.log(`Gaussian-adjusted human pacing delay (${label}): ${Math.round(delay / 1000)}s`);
    await sleep(delay);
}

async function interTermPacingDelay(termIndex, totalTerms) {
    // Longer Gaussian-distributed pause between search terms to mimic a human
    // browsing between searches (30–90s, occasionally up to 2 min)
    const minMs = 30_000;
    const maxMs = 90_000;
    const delay = randomHumanPacingMs(minMs, maxMs);
    const mins = (delay / 60000).toFixed(1);
    console.log(`⏱️ Inter-term human pacing (term ${termIndex}/${totalTerms}): ${mins} min`);
    await sleep(delay);
}

async function activeLinkedInPage(browser, fallbackPage = null) {
    const pages = await browser.pages();
    const preferredPattern = fallbackPage && /\/jobs\/view\//i.test(fallbackPage.url())
        ? /https:\/\/www\.linkedin\.com\/jobs\/view\//i
        : /https:\/\/www\.linkedin\.com\/jobs\/search/i;
    const linkedInPage = pages.find(p => preferredPattern.test(p.url()));
    return linkedInPage || fallbackPage || pages[0];
}

async function evaluateRunnerCommand(page, command, options, timeoutMs) {
    return await page.evaluate(async ({ command, options, timeoutMs }) => {
        if (window.WebFormMonitor) {
            const commandPromise = window.WebFormMonitor.startOrStatus({ command, options });
            const timeoutPromise = new Promise((_, reject) => {
                setTimeout(() => reject(new Error(`Timed out waiting for runner command ${command}`)), timeoutMs);
            });
            return await Promise.race([commandPromise, timeoutPromise]);
        }
        return 'bridge_not_initialized';
    }, { command, options, timeoutMs });
}

async function runnerCommand(page, browser, command, options = {}, timeoutMs = 15000) {
    let commandPage = await activeLinkedInPage(browser, page);
    let monitorResult;
    try {
        monitorResult = await evaluateRunnerCommand(commandPage, command, options, timeoutMs);
    } catch (err) {
        const message = err?.message || String(err);
        if (!/detached Frame|Execution context was destroyed|Cannot find context|Target closed|Session closed/i.test(message)) {
            throw err;
        }
        console.warn(`Runner command ${command} hit stale page/frame; reacquiring page and retrying once: ${message}`);
        await sleep(1500);
        commandPage = await activeLinkedInPage(browser, page);
        monitorResult = await evaluateRunnerCommand(commandPage, command, options, timeoutMs);
    }

    console.log("Orchestration Sync Result:", monitorResult);

    if (monitorResult === 'bridge_not_initialized') {
        return { ok: false, error: 'bridge_not_initialized' };
    }
    if (monitorResult && typeof monitorResult === 'object' && 'ok' in monitorResult) {
        return monitorResult;
    }
    return { ok: true, result: monitorResult };
}

async function waitForRunner(page, browser) {
    for (let i = 0; i < 30; i++) {
        try {
            const resp = await runnerCommand(page, browser, 'getProgress', {}, 2000);
            if (resp && resp.ok) return true;
        } catch (_) {
            await sleep(1000);
        }
    }
    for (let i = 0; i < 15; i++) {
        try {
            const resp = await runnerCommand(page, browser, 'getProgress', {}, 2000);
            if (resp && resp.ok) return true;
        } catch (_) {
            await sleep(1000);
        }
    }
    return false;
}

async function clearStaleRunnerState(page, browser) {
    await sleep(3000);
    await page.evaluate(() => {
        [
            'ljm_processing_applications',
            'ljm_currentIndex',
            'ljm_totalJobs',
            'ljm_isPaused',
            'ljm_aborted'
        ].forEach(key => sessionStorage.removeItem(key));
    }).catch(() => {});

    await runnerCommand(page, browser, 'abortApplying', {}, 3000).catch(() => {});
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
}

async function applyDirectJob(page, browser, url) {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await sleep(8000);
    if ((await page.title()).toLowerCase().includes('sign in') || /\/login|checkpoint/i.test(page.url())) {
        return { status: 'failed', result_url: page.url(), reason: `LinkedIn login/checkpoint required at ${page.url()}` };
    }

    if (!await waitForRunner(page, browser)) {
        return { status: 'failed', result_url: page.url(), reason: 'Extension runner did not become ready for the direct job' };
    }

    const configured = await runnerCommand(page, browser, 'setRunOptions', {
        maxApplicationsPerRun: 1,
        processingSpeed: RUN_PROCESSING_SPEED,
        resetSubmissionCount: true
    });
    if (!configured.ok) {
        return { status: 'failed', result_url: page.url(), reason: configured.error || 'Unable to configure direct job run' };
    }

    const started = await runnerCommand(page, browser, 'applyCurrentJob', {}, 20000);
    if (!started.ok || !started.result?.started) {
        return {
            status: 'failed',
            result_url: page.url(),
            reason: started.error || started.result?.message || 'Direct job application did not start'
        };
    }

    const deadline = Date.now() + DIRECT_TIMEOUT_MS;
    while (Date.now() < deadline) {
        const progress = await runnerCommand(page, browser, 'getProgress', {}, 5000);
        if (progress.ok && progress.result?.directJobResult) {
            const outcome = progress.result.directJobResult;
            return {
                status: outcome.status === 'skipped' ? 'manual_review' : outcome.status,
                result_url: page.url(),
                reason: outcome.reason || ''
            };
        }
        if (progress.ok && progress.result?.aborted) {
            return { status: 'failed', result_url: page.url(), reason: 'Direct job runner aborted' };
        }
        await sleep(2000);
    }
    return { status: 'failed', result_url: page.url(), reason: 'Timed out waiting for direct job outcome' };
}

function newTermEvidence(term, url, beforeCount) {
    return {
        term,
        url,
        startedAt: new Date().toISOString(),
        beforeAppliedJobs: beforeCount,
        afterAppliedJobs: beforeCount,
        submittedThisTerm: 0,
        healthCheckPassed: false,
        jobIdsCaptured: [],
        qaAnswersReturned: 0,
        policyAnswersReturned: 0,
        locationAutocompleteSelected: false,
        submitVerificationSucceeded: 0,
        successModalOrPostApplyDetected: 0,
        noSaveDiscardModalRemains: true,
        linkedinMarkedApplied: false,
        skippedAlreadyApplied: 0,
        consoleLog: null,
        status: 'running'
    };
}

async function main() {
    const cfg = DIRECT_JOB_URL ? null : loadSearchConfig();
    if (cfg) writeJson('search-config.json', cfg);

    const allConsole = [];
    const allRejections = [];
    let currentTermLog = [];
    let failureSignal = null;

    const browser = USE_NSTBROWSER ? await connectNstBrowser() : await launchLocalChrome();

    let isCleaningUp = false;
    async function gracefulExit(code = 0) {
        if (isCleaningUp) return;
        isCleaningUp = true;
        console.log('\nGracefully shutting down runner...');
        try {
            fs.writeFileSync(path.join(OUT_DIR, 'browser-console.log'), allConsole.map(e => `[${e.ts}] [${e.source}] [${e.type}] ${e.text}`).join('\n'));
            if (allRejections.length > 0) {
                writeJson('rejections.json', allRejections);
            }
            writeJson('summary.json', summary);
            console.log(JSON.stringify({ outDir: OUT_DIR, summary }, null, 2));
        } catch (err) {
            console.error('Error writing final logs during shutdown:', err);
        }
        try {
            await browser.close().catch(() => {});
        } catch (_) {}
        process.exit(code);
    }

    process.on('SIGINT', () => gracefulExit(0));
    process.on('SIGTERM', () => gracefulExit(0));

    const activePages = new WeakSet();
    function setupPageListeners(p) {
        if (!p || activePages.has(p)) return;
        activePages.add(p);

        p.on('dialog', async dialog => {
            const entry = { ts: new Date().toISOString(), source: 'dialog', type: dialog.type(), text: dialog.message() };
            allConsole.push(entry);
            currentTermLog.push(entry);
            await dialog.accept().catch(() => {});
        });
        p.on('console', msg => {
            const text = msg.text();
            const entry = { ts: new Date().toISOString(), source: 'page', type: msg.type(), text };
            allConsole.push(entry);
            currentTermLog.push(entry);
            if (!failureSignal && failurePatterns.some(re => re.test(text))) {
                failureSignal = { reason: text, ts: entry.ts, url: p.url() };
            }
        });
        p.on('pageerror', err => {
            const entry = { ts: new Date().toISOString(), source: 'pageerror', type: 'error', text: err.message || String(err) };
            allConsole.push(entry);
            currentTermLog.push(entry);
        });
    }

    browser.on('targetcreated', async target => {
        if (target.type() === 'service_worker') {
            try {
                const session = await target.createCDPSession();
                await session.send('Runtime.enable');
                session.on('Runtime.consoleAPICalled', event => {
                    const text = event.args.map(arg => arg.value || arg.description || '').join(' ');
                    const entry = { ts: new Date().toISOString(), source: 'service_worker', type: event.type, text };
                    allConsole.push(entry);
                    currentTermLog.push(entry);
                });
            } catch (_) {}
        } else if (target.type() === 'page') {
            try {
                const newPage = await target.page().catch(() => null);
                if (newPage) setupPageListeners(newPage);
            } catch (_) {}
        }
    });

    // Attach to existing pages
    const initialPages = await browser.pages().catch(() => []);
    for (const p of initialPages) {
        setupPageListeners(p);
    }

    let page = await activeLinkedInPage(browser, initialPages[0]);

    if (DIRECT_JOB_URL) {
        let result = {
            status: 'failed',
            result_url: DIRECT_JOB_URL,
            reason: 'LinkedIn direct application did not complete'
        };
        try {
            result = await applyDirectJob(page, browser, DIRECT_JOB_URL);
        } catch (err) {
            result.reason = `${err.name}: ${err.message}`;
        }
        if (QUEUE_RESULT_FILE) fs.writeFileSync(QUEUE_RESULT_FILE, JSON.stringify(result));
        summary.applicationsSubmitted = result.status === 'applied' ? 1 : 0;
        summary.successfulSubmissions = summary.applicationsSubmitted;
        summary.failedSubmissions = result.status === 'applied' ? 0 : 1;
        summary.productionReadinessStatus = result.status === 'applied' ? 'ready' : 'not_ready_direct_outcome';
        summary.evidence.push({ mode: 'direct', url: DIRECT_JOB_URL, ...result });
        await gracefulExit(result.status === 'applied' ? 0 : 2);
        return;
    }

    try {
        let totalSuccesses = 0;
        let supervisedMode = true;

        const locations = cfg.search_locations || [cfg.search_location];
        const totalTerms = cfg.search_terms.length * locations.length;
        let termsProcessedCount = 0;

        for (const location of locations) {
            for (const term of cfg.search_terms) {
                termsProcessedCount++;
                failureSignal = null;
                currentTermLog = [];
                const url = linkedInUrl(term, location, cfg);
                summary.termsProcessed.push(`${term} (${location})`);
                page = await activeLinkedInPage(browser, page);

                const beforeResp = await runnerCommand(page, browser, 'getAppliedJobs', {}, 2000).catch(() => ({ ok: true, result: [] }));
                const beforeJobs = Array.isArray(beforeResp.result) ? beforeResp.result : [];
                const evidence = newTermEvidence(term, url, beforeJobs.length);
                evidence.location = location;
                summary.evidence.push(evidence);

                await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
                await clearStaleRunnerState(page, browser);
                page = await activeLinkedInPage(browser, page);
                await humanPacingDelay('after search page load');

                if ((await page.title()).toLowerCase().includes('sign in') || /\/login|checkpoint/i.test(page.url())) {
                    throw new Error(`LinkedIn login/checkpoint required at ${page.url()}`);
                }

                const ready = await waitForRunner(page, browser);
                if (!ready) throw new Error(`Extension runner did not become ready for term "${term}"`);

                await runnerCommand(page, browser, 'setRunOptions', {
                    maxApplicationsPerRun: supervisedMode ? SUPERVISED_TARGET : PROD_MAX_APPLICATIONS_PER_RUN,
                    processingSpeed: RUN_PROCESSING_SPEED,
                    resetSubmissionCount: true
                });

                await humanPacingDelay('before scanning jobs');
                const scan = await runnerCommand(page, browser, 'performScan');
                if (!scan.ok) throw new Error(`Scan failed for term "${term}": ${scan.error}`);

                await humanPacingDelay('before starting applications');
                const start = await runnerCommand(page, browser, 'startApplying', {}, 20000);
                if (!start.ok || !start.result?.started) {
                    evidence.status = 'skipped_no_jobs';
                    evidence.reason = start.error || start.result?.message || 'No jobs started';
                    evidence.consoleLog = saveTermLog(term, currentTermLog);
                    continue;
                }

                const deadline = Date.now() + TERM_TIMEOUT_MS;
                let lastProgress = null;
                while (Date.now() < deadline) {
                    if (failureSignal) throw new Error(`Failure while processing "${term}": ${failureSignal.reason}`);
                    const progressResp = await runnerCommand(page, browser, 'getProgress', {}, 5000);
                    if (progressResp.ok) {
                        lastProgress = progressResp.result;
                        if (lastProgress.rejections && Array.isArray(lastProgress.rejections)) {
                            let newRejCount = 0;
                            for (const rej of lastProgress.rejections) {
                                if (!allRejections.some(r => r.jobId === rej.jobId)) {
                                    allRejections.push({
                                        term,
                                        location,
                                        ...rej
                                    });
                                    newRejCount++;
                                }
                            }
                            if (newRejCount > 0) {
                                writeJson('rejections.json', allRejections);
                            }
                        }
                        if (lastProgress.applicationsSubmittedThisRun > evidence.submittedThisTerm) {
                            evidence.submittedThisTerm = lastProgress.applicationsSubmittedThisRun;
                            evidence.afterAppliedJobs = beforeJobs.length + evidence.submittedThisTerm;
                        }
                        if (lastProgress.aborted ||
                            lastProgress.consecutiveFailures >= MAX_ALLOWED_CONSECUTIVE_FAILURES) {
                            throw new Error(`Runner aborted or recorded failures for "${term}": ${JSON.stringify(lastProgress)}`);
                        }
                        if (lastProgress.finished || lastProgress.current >= lastProgress.total ||
                            lastProgress.applicationsSubmittedThisRun >= (supervisedMode ? SUPERVISED_TARGET : PROD_MAX_APPLICATIONS_PER_RUN)) {
                            break;
                        }
                    }
                    await sleep(5000);
                }
                if (Date.now() >= deadline) throw new Error(`Timed out processing term "${term}"`);

                const afterResp = await runnerCommand(page, browser, 'getAppliedJobs', {}, 10000);
                const afterJobs = Array.isArray(afterResp.result) ? afterResp.result : [];
                const newJobs = afterJobs.slice(beforeJobs.length);
                evidence.afterAppliedJobs = afterJobs.length;
                evidence.submittedThisTerm = newJobs.length;
                evidence.jobIdsCaptured = newJobs.map(job => String(job.id)).filter(Boolean);
                evidence.healthCheckPassed = currentTermLog.some(e => /Backend health check passed/i.test(e.text));
                evidence.qaAnswersReturned = currentTermLog.filter(e => /Received QA answer from Python backend|QA backend answered/i.test(e.text)).length;
                evidence.policyAnswersReturned = currentTermLog.filter(e => /Policy\/Backend answered/i.test(e.text)).length;
                evidence.locationAutocompleteSelected = currentTermLog.some(e => /Found matching suggestion/i.test(e.text));
                evidence.submitVerificationSucceeded = currentTermLog.filter(e => /Submit verified via/i.test(e.text)).length;
                evidence.successModalOrPostApplyDetected = currentTermLog.filter(e => /postApplyJobId|success phrase|Done button|Found success modal done button/i.test(e.text)).length;
                evidence.noSaveDiscardModalRemains = !currentTermLog.some(e => /discard application|save this application/i.test(e.text));
                evidence.linkedinMarkedApplied = currentTermLog.some(e => /Skipping already-applied|ljm-applied|Application submitted and saved/i.test(e.text));
                evidence.skippedAlreadyApplied = currentTermLog.filter(e => /Skipping already-applied job/i.test(e.text)).length;
                evidence.consoleLog = saveTermLog(term, currentTermLog);
                evidence.status = 'completed';

                totalSuccesses += newJobs.length;
                summary.applicationsSubmitted += newJobs.length;
                summary.successfulSubmissions += newJobs.length;
                summary.skippedJobs += evidence.skippedAlreadyApplied;

                if (supervisedMode && totalSuccesses >= SUPERVISED_TARGET) {
                    summary.supervisedPassed = true;
                    supervisedMode = false;
                }

                // Human-like pause between search terms
                if (termsProcessedCount < totalTerms) {
                    await interTermPacingDelay(termsProcessedCount, totalTerms);
                }
            }
        }

        summary.productionReadinessStatus = summary.supervisedPassed ? 'ready' : 'not_ready_no_supervised_submissions';
    } catch (err) {
        summary.failedSubmissions += 1;
        summary.productionReadinessStatus = 'not_ready_failure_stopped';
        summary.failure = {
            reason: err.message,
            url: page.url(),
            at: new Date().toISOString()
        };
        try { await runnerCommand(page, browser, 'abortApplying', {}, 5000); } catch (_) {}
        await page.screenshot({ path: path.join(OUT_DIR, 'failure-page.png'), fullPage: true }).catch(() => {});
    } finally {
        await gracefulExit(0);
    }
}

function saveTermLog(term, entries) {
    const safe = term.replace(/[^a-z0-9]+/gi, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'term';
    const file = `term-${summary.evidence.length}-${safe}.log`;
    fs.writeFileSync(path.join(OUT_DIR, file), entries.map(e => `[${e.ts}] [${e.source}] [${e.type}] ${e.text}`).join('\n'));
    return path.join(OUT_DIR, file);
}

main().catch(err => {
    summary.productionReadinessStatus = 'not_ready_runner_crashed';
    summary.failure = { reason: err.message, at: new Date().toISOString() };
    writeJson('summary.json', summary);
    console.error(err);
    process.exit(1);
});
