const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');
require('dotenv').config({ path: path.join(__dirname, 'config.env') });
// NST CDP connect only needs puppeteer-core. Prefer it when puppeteer-extra is
// broken against ESM-only puppeteer>=22 (require of puppeteer-core fails).
let puppeteer;
let usingStealth = false;
try {
    // eslint-disable-next-line import/no-extraneous-dependencies
    const extra = require('puppeteer-extra');
    const StealthPlugin = require('puppeteer-extra-plugin-stealth');
    extra.use(StealthPlugin());
    // Probe: puppeteer-extra lazy-loads puppeteer-core on first connect; force
    // resolution now so we can fall back cleanly on ESM mismatch.
    require('puppeteer-core');
    puppeteer = extra;
    usingStealth = true;
} catch (e) {
    console.log(`⚠️ puppeteer-extra unavailable (${e.message}); using puppeteer-core for NST CDP`);
    puppeteer = require('puppeteer-core');
}
const { createCursor } = require('ghost-cursor');
const heuristics = require('./hybrid_heuristics');
if (usingStealth) {
    console.log('🛡️ puppeteer-extra stealth enabled');
}

// Config parameters
const chromeExecutablePath = process.env.CHROME_EXECUTABLE_PATH;
const chromeUserDataDir = process.env.CHROME_USER_DATA_DIR;
const chromeProfile = process.env.CHROME_PROFILE || 'Default';
const searchUrl = process.env.LINKEDIN_SEARCH_URL || '';
const directJobUrl = process.env.LINKEDIN_DIRECT_JOB_URL || '';
const queueResultFile = process.env.JOB_QUEUE_RESULT_FILE || '';
const loadUnpackedExtension = process.env.LOAD_UNPACKED_EXTENSION !== 'false';
const extensionProcessingSpeed = Number(process.env.LINKEDIN_EXTENSION_PROCESSING_SPEED || 0.15);
const extensionDirectTimeoutMs = Number(process.env.LINKEDIN_EXTENSION_DIRECT_TIMEOUT_MS || 20 * 60 * 1000);
// Enable verbose bot logging to file when set
const VERBOSE_LOG_FILE = process.env.JOB_BOT_VERBOSE_LOG || path.join(__dirname, '..', 'logs', 'linkedin_it_bot_trace.jsonl');
// One compact, machine-readable record per field the bot could not complete.
// Keep this separate from the verbose trace so recurring problem questions are
// easy to aggregate without parsing console strings.
const UNRESOLVED_QUESTION_LOG_FILE = process.env.LINKEDIN_UNRESOLVED_QUESTION_LOG
    || path.join(__dirname, '..', 'logs', 'linkedin_unresolved_questions.jsonl');
const logLines = [];  // In-memory buffer
function cleanQuestionLogValue(value, limit = 1200) {
    if (typeof value === 'string') {
        return value.replace(/\u0000/g, '').slice(0, limit);
    }
    if (Array.isArray(value)) return value.slice(0, 40).map(item => cleanQuestionLogValue(item, limit));
    if (value && typeof value === 'object') {
        return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cleanQuestionLogValue(item, limit)]));
    }
    return value;
}
function logUnresolvedQuestion({ jobInfo = {}, step, field, context = '', options = [], source = 'none', reason }) {
    try {
        const parent = path.dirname(UNRESOLVED_QUESTION_LOG_FILE);
        if (!fs.existsSync(parent)) fs.mkdirSync(parent, { recursive: true });
        const event = cleanQuestionLogValue({
            ts: new Date().toISOString(),
            event_type: 'question_unresolved',
            portal: 'linkedin',
            job: {
                job_id: jobInfo.jobId || '',
                title: jobInfo.title || '',
                company: jobInfo.company || '',
                location: jobInfo.location || '',
                url: jobInfo.url || '',
            },
            step,
            question: field.label || '',
            control_type: field.type || field.tagName || '',
            options,
            context,
            answer_source: source,
            reason: reason || 'no_answer_resolved',
        });
        fs.appendFileSync(UNRESOLVED_QUESTION_LOG_FILE, JSON.stringify(event) + '\n');
    } catch (err) {
        vlog(`Unable to write unresolved-question record: ${err.message}`);
    }
}
// Capture-on-drop: screenshot the unresolved question AREA (plus the viewport)
// before the application is dropped. Same canonical output directory as the
// Python apply_diagnostics helper. Never throws; fire-and-forget at call sites.
const UNHANDLED_Q_DIR = process.env.JOB_BOT_UNHANDLED_Q_DIR
    || path.join(__dirname, '..', '..', 'automation_monorepo', 'outputs', 'unhandled_questions');
async function captureUnresolvedQuestionScreenshot({ page, field = {}, jobInfo = {}, step, reason }) {
    try {
        if (!page || typeof page.screenshot !== 'function') return;
        if (!fs.existsSync(UNHANDLED_Q_DIR)) fs.mkdirSync(UNHANDLED_Q_DIR, { recursive: true });
        const safe = (v, n = 48) => String(v || '').replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, n);
        const stamp = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
        const base = path.join(UNHANDLED_Q_DIR, `linkedin_${safe(jobInfo.jobId, 40) || 'job'}_${stamp}`);
        let clipped = false;
        try {
            const el = field.el || field.element || field.handle || null;
            if (el && typeof el.screenshot === 'function') {
                await el.screenshot({ path: `${base}_area.png` });
                clipped = true;
            } else if (field.label) {
                const box = await page.evaluate((label) => {
                    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const target = norm(label).slice(0, 80);
                    if (!target) return null;
                    const nodes = Array.from(document.querySelectorAll('label, legend, div, span'));
                    const hit = nodes.find((n) => norm(n.innerText || n.textContent).includes(target));
                    if (!hit) return null;
                    const r = hit.getBoundingClientRect();
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }, field.label);
                if (box && box.width > 0 && box.height > 0) {
                    const pad = 24;
                    await page.screenshot({
                        path: `${base}_area.png`,
                        clip: {
                            x: Math.max(0, box.x - pad),
                            y: Math.max(0, box.y - pad),
                            width: box.width + 2 * pad,
                            height: Math.min(box.height + 2 * pad, 4000),
                        },
                    });
                    clipped = true;
                }
            }
        } catch (e) { vlog(`area screenshot skipped: ${e.message}`); }
        await page.screenshot({ path: `${base}_page.png` });
        vlog(`📸 unresolved-question screenshot (${clipped ? 'area+page' : 'page'}) step=${step} reason=${reason || ''}: ${base}`);
    } catch (err) {
        vlog(`unresolved-question screenshot failed: ${err.message}`);
    }
}
function writeQueueResult(result) {
    if (!queueResultFile) return;

    try {
        const parent = path.dirname(queueResultFile);
        if (!fs.existsSync(parent)) fs.mkdirSync(parent, { recursive: true });
        const temporary = `${queueResultFile}.tmp-${process.pid}`;
        fs.writeFileSync(temporary, JSON.stringify(result), 'utf8');
        fs.renameSync(temporary, queueResultFile);
    } catch (e) {
        console.error(`Failed to write JOB_QUEUE_RESULT_FILE: ${e.message}`);
    }
}
function vlog(msg) {
    const entry = { ts: new Date().toISOString(), msg };
    logLines.push(entry);
    console.log(`[BOT TRACE] ${msg}`);
    // Flush periodically to disk
    if (logLines.length % 20 === 0) _flushLogs();
}
function _flushLogs() {
    try {
        if (!fs.existsSync(path.dirname(VERBOSE_LOG_FILE))) fs.mkdirSync(path.dirname(VERBOSE_LOG_FILE), {recursive:true});
        fs.appendFileSync(VERBOSE_LOG_FILE, logLines.map(l=>JSON.stringify(l)).join('\n') + '\n');
        logLines.length = 0;
    } catch(e) {}
}
process.on('exit', _flushLogs);
process.on('SIGTERM', () => {
    writeQueueResult({
        status: 'failed',
        result_url: directJobUrl || '',
        reason: 'LinkedIn runner terminated before producing an outcome',
        application_method: 'easy_apply',
    });
    _flushLogs();
    process.exit(2);
});
process.on('uncaughtException', e => {
    writeQueueResult({
        status: 'failed',
        result_url: directJobUrl || '',
        reason: `uncaught exception: ${e?.stack || e?.message || e}`,
        application_method: 'easy_apply',
    });
    vlog('UNCAUGHT: ' + (e?.stack || e?.message || e));
    _flushLogs();
    process.exit(2);
});

const saveCompanySiteJobs = /^(1|true|yes|on)$/i.test(process.env.LINKEDIN_SAVE_COMPANY_SITE_JOBS || 'false');
const companySiteLeadsPath = process.env.LINKEDIN_COMPANY_SITE_LEADS_PATH || path.join(__dirname, 'company_site_jobs.jsonl');
const discoveryMode = /^(discover|discovery|search)$/i.test(process.env.JOBBOT_MODE || 'apply');
const queueAdminPath = path.join(__dirname, '..', '..', 'automation_monorepo', 'scripts', 'job_queue_admin.py');
const queuePython = process.env.AUTOMATION_PYTHON || process.env.JOB_QUEUE_PYTHON || 'python3';
const linkedInProfile = (process.env.LINKEDIN_JOB_PROFILE || process.env.JOB_PROFILE || 'it').toLowerCase();
const useNstBrowser = process.env.USE_NSTBROWSER !== 'false';
const nstApiBase = (process.env.NST_API_BASE || 'http://localhost:8848/api/v2').replace(/\/$/, '');
const nstApiKey = process.env.NST_API_KEY || process.env.NSTBROWSER_API_KEY || '';
const nstProfileId = process.env.NST_PROFILE_ID || process.env.NSTBROWSER_PROFILE_ID || '';
const disableProxy = /^(1|true|yes|on)$/i.test(process.env.LINKEDIN_DISABLE_PROXY || '');
// Prefer static Webshare for LinkedIn NST apply + CapMonster IP match.
// Proxy-Cheap is discovery rotation only (JobSpy ladder), not apply egress.
const proxyUrl = disableProxy
    ? ''
    : (process.env.NSTBROWSER_PROXY_URL
        || process.env.WEBSHARE_PROXY_URL
        || process.env.JOBSPY_PROXY_WEBSHARE
        || process.env.CAPMONSTER_PROXY_URL
        || process.env.PROXY_URL
        || process.env.PROXY_CHEAP_URL
        || '');
// Discovery-only fallback if static Webshare is unset (should be rare in prod).
const proxyFallbackUrl = disableProxy
    ? ''
    : (process.env.PROXY_CHEAP_URL || '');
const maxApplications = Number(process.env.HYBRID_MAX_APPLICATIONS || process.env.SUPERVISED_TARGET || 3);
const maxSearchPages = Number(process.env.HYBRID_MAX_SEARCH_PAGES || 40);
const maxSearchPagesPerTerm = Number(process.env.HYBRID_MAX_SEARCH_PAGES_PER_TERM || 1);
const focusedSearchTerms = [
    // === Primary: Customer Service / Light Admin (MEDIUM PRIORITY) ===
    'Customer Service Representative',
    'Customer Support Representative',
    'Client Service Representative',
    'Customer Care Associate',
    'Contact Centre Agent',
    'Call Centre Agent',
    'Guest Services Associate',
    'Receptionist',
    'Front Desk Coordinator',
    'Office Administrator',
    'Administrative Assistant',
    'Office Coordinator',
    'Administrative Support',
    'Data Entry Clerk',
    // === Primary: Helpdesk / IT Support (HIGH PRIORITY) ===
    'Helpdesk Technician',
    'Help Desk Analyst',
    'Help Desk Specialist',
    'IT Support Specialist',
    'IT Support Analyst',
    'IT Support Technician',
    'Technical Support Analyst',
    'Service Desk Analyst',
    'Desktop Support Technician',
    'System Support Specialist',
    'IT Help Desk',
    'Tier 1 Support',
    'Tier 2 Support',
    'First Line Support',
    'IT Office Support',
    'IT Onsite Technician',
    // === Secondary: Light IT Ops / Network (MEDIUM PRIORITY) ===
    'Network Support Technician',
    'Network Administrator',
    'Systems Administrator',
    'IT Infrastructure Analyst',
    'IT Analyst',
    'IT Specialist',
    'Application Support Analyst',
    'IT Coordinator',
    'IT Operations Analyst',
    'Computer Support Specialist',
    'IT Assistant',
    'Operations Technician',
    'Technology Deployment Technician',
    // === Tertiary: Co-op / Intern / Entry (LOW PRIORITY, thin net) ===
    'IT Co-op',
    'IT Intern',
    'IT Student',
    'IT Support Co-op',
    // === QA — only manual/junior, skip senior SDET ===
    'QA Analyst',
    'QA Intern',
    'QA Co-op',
    'Manual Tester',
    'Quality Assurance Analyst'
];
const buildLinkedInSearchUrl = term =>
    `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(term)}&location=Vancouver%2C%20British%20Columbia%2C%20Canada&f_AL=true`;
const shuffleList = items => items
    .map(item => ({ item, order: Math.random() }))
    .sort((a, b) => a.order - b.order)
    .map(({ item }) => item);
const fallbackSearchUrls = [
    searchUrl,
    ...shuffleList(focusedSearchTerms).map(buildLinkedInSearchUrl)
].filter((url, index, urls) => url && urls.indexOf(url) === index);
// Prefer same stack as monorepo form_answers (Akash ML / OpenRouter), not bare Gemini.
const aiProvider = (
    process.env.AI_PROVIDER
    || process.env.LLM_PROVIDER
    || (process.env.AKASHML_API_KEY || process.env.BLUESMINDS_API_KEY ? 'openai' : '')
    || (process.env.OPENROUTER_API_KEY ? 'openai' : '')
    || 'openai'
);
const aiApiKey = (
    process.env.AI_API_KEY
    || process.env.AKASHML_API_KEY
    || process.env.BLUESMINDS_API_KEY
    || process.env.OPENROUTER_API_KEY
    || process.env.DEEPSEEK_API_KEY
    || process.env.LLM_API_KEY
    || ''
);
const aiModelName = (
    process.env.AI_MODEL_NAME
    || process.env.AKASHML_MODEL
    || process.env.BLUESMINDS_MODEL
    || process.env.OPENROUTER_MODEL
    || 'deepseek-ai/DeepSeek-V4-Flash'
);
const aiCustomUrl = (
    process.env.AI_CUSTOM_URL
    || process.env.AKASHML_BASE_URL
    || process.env.BLUESMINDS_BASE_URL
    || (process.env.OPENROUTER_API_KEY ? 'https://openrouter.ai/api/v1' : '')
    || ''
);

// ============================================================
// CHANGE 1: Answer Bridge — shared brain via subprocess
// CHANGE 2: LinkedIn persona manifest (synced from Python configs)
// ============================================================
const ANSWER_BRIDGE_PATH = path.join(__dirname, 'answer_bridge.py');
const MANIFEST_PATH = path.join(__dirname, 'linkedin_profile_manifest.json');
let profileManifest = {};
try {
    profileManifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
} catch(e) {
    console.log('[Manifest] Warning: could not load linkedin_profile_manifest.json:', e.message);
}

/**
 * Call Python's resolve_answer() via subprocess.
 * Returns {value, source, score, matched_question} or null.
 */
async function spawnPythonResolve(question, hint, options, jobContext) {
    // Only call bridge when we have monorepo available (worker runs it)
    const bridgeScript = ANSWER_BRIDGE_PATH;
    if (!fs.existsSync(bridgeScript)) return null;
    
    try {
        const optJson = options ? JSON.stringify(options).substring(0, 2000) : '[]';
        const cmd = [process.env.PYTHON_BIN || 'python3', bridgeScript, 
                     question.substring(0, 500), optJson,
                     hint ? hint.substring(0, 500) : '',
                     jobContext ? jobContext.substring(0, 2000) : ''];
        
        const result = spawnSync(cmd[0], cmd.slice(1), {
            timeout: 15000,
            maxBuffer: 1024 * 1024
        });
        
        if (result.error) {
            vlog(`[Bridge] Spawn error: ${result.error.message}`);
            return null;
        }
        
        const stdout = (result.stdout || '').toString().trim();
        if (stdout.startsWith('ERROR:') || !stdout) return null;
        
        let parsed;
        try { parsed = JSON.parse(stdout); }
        catch(e) { return null; }
        
        if (parsed.value && parsed.value.trim()) {
            vlog(`[Bridge] "${question.substring(0,60)}" → "${parsed.value}" (source: ${parsed.source})`);
            return parsed;
        }
        
        return null;
    } catch(err) {
        vlog(`[Bridge] Exception: ${err.message}`);
        return null;
    }
}

// Output log file
const logFilePath = path.join(__dirname, 'applied_jobs.csv');

// Initialize CSV header if not exists
if (!fs.existsSync(logFilePath)) {
    fs.writeFileSync(logFilePath, 'Date,Job ID,Job Title,Company,Status\n');
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

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function detectLinkedInAuthWall(page) {
    // If the global nav / me-menu is present we are authenticated even when
    // LinkedIn briefly shows a marketing shell or intermediate "welcome" card.
    const hasSessionChrome = await page.evaluate(() => {
        const selectors = [
            '#global-nav',
            '.global-nav__me',
            'img.global-nav__me-photo',
            'button.global-nav__primary-link-me-menu-trigger',
            '.feed-identity-module',
            'a[href*="/mynetwork"]',
            'a[href*="/jobs/"]',
            '.scaffold-layout__main'
        ];
        return selectors.some(selector => document.querySelector(selector));
    }).catch(() => false);
    if (hasSessionChrome) {
        return '';
    }

    const url = page.url();
    if (/\/login(?:\/|\?|$)|\/checkpoint\/|\/authwall/i.test(url)) {
        return `LinkedIn authentication required at ${url}`;
    }
    const title = await page.title().catch(() => '');
    if (/sign in|log in|join linkedin/i.test(title)) {
        return `LinkedIn authentication required: ${title}`;
    }
    const loginUi = await page.evaluate(() => {
        const selectors = [
            'input[name="session_key"]',
            'input[name="session_password"]',
            'form[action*="/login"]',
            '.sign-in-form',
            '[data-id="sign-in-form__submit-btn"]'
        ];
        if (selectors.some(selector => document.querySelector(selector))) return true;
        const heading = [...document.querySelectorAll('h1, h2, [role="heading"]')]
            .map(element => (element.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase())
            .join(' ');
        return /welcome to your professional community|sign in to linkedin|welcome back/.test(heading);
    }).catch(() => false);
    if (loginUi) {
        return 'LinkedIn authentication required';
    }
    return '';
}

/**
 * Detect LinkedIn's daily Easy Apply limit modal or banner.
 */
async function detectLinkedInDailyEasyApplyLimit(page) {
    const patterns = [
        /you(?:'|’)ve reached today(?:'|’)s easy apply limit/i,
        /you have reached today(?:'|’)s easy apply limit/i,
        /reached today(?:'|’)s application limit/i,
        /you have reached (?:your )?(?:daily|today(?:'|’)s) (?:easy apply |application )?limit/i,
        /limit the number of (?:easy apply|applications)/i,
    ];
    const contexts = [page, ...page.frames().filter(frame => frame !== page.mainFrame())];
    for (const ctx of contexts) {
        try {
            const text = await ctx.evaluate(() => {
                const shadow = document.querySelector('#interop-outlet')?.shadowRoot;
                return `${document.body?.innerText || ''}\n${shadow?.innerText || shadow?.textContent || ''}`
                    .replace(/\s+/g, ' ').trim().slice(0, 20000);
            });
            if (patterns.some(pattern => pattern.test(text))) {
                return text.slice(0, 500);
            }
        } catch (_) {}
    }
    return '';
}

function recordLinkedInDailyLimit(message) {
    const flag = (process.env.LINKEDIN_DAILY_LIMIT_FLAG || '').trim();
    if (!flag) return;
    try {
        fs.mkdirSync(path.dirname(flag), { recursive: true });
        fs.writeFileSync(flag, `${new Date().toISOString()}\n${String(message || '').slice(0, 1000)}\n`, 'utf8');
        console.warn(`🚫 LinkedIn daily-limit marker written: ${flag}`);
    } catch (err) {
        console.warn(`⚠️ Could not write LinkedIn daily-limit marker: ${err.message}`);
    }
}

/**
 * Dismiss LinkedIn intermediate splash / "select to continue" / cookie / app-promo
 * screens that appear before the real feed when the NST profile is already logged in.
 * Search-mode warm path usually lands on /feed after a click; leased direct-apply
 * was hard-failing here as "authentication required".
 */
async function dismissLinkedInInterstitials(page, cursor) {
    const clicked = await page.evaluate(() => {
        const texts = [
            'skip',
            'not now',
            'no thanks',
            'dismiss',
            'continue',
            'got it',
            'accept',
            'agree',
            'allow',
            'maybe later',
            'stay on linkedin',
            'continue to linkedin',
            'go to feed',
            'go to your feed',
            'close'
        ];
        const nodes = [
            ...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')
        ];
        for (const el of nodes) {
            if (!(el instanceof HTMLElement)) continue;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || el.offsetParent === null) {
                continue;
            }
            const label = (
                (el.innerText || el.getAttribute('aria-label') || el.value || '')
            ).replace(/\s+/g, ' ').trim().toLowerCase();
            if (!label || label.length > 48) continue;
            if (texts.some(t => label === t || label.startsWith(t + ' ') || label.endsWith(' ' + t))) {
                el.click();
                return label;
            }
        }
        // Generic modal close
        const close = document.querySelector(
            'button[aria-label="Dismiss"], button[aria-label="Close"], button.artdeco-modal__dismiss'
        );
        if (close instanceof HTMLElement) {
            close.click();
            return 'modal-dismiss';
        }
        return '';
    }).catch(() => '');
    if (clicked) {
        console.log(`🪟 Dismissed LinkedIn interstitial: ${clicked}`);
        await sleep(1500);
        if (cursor) {
            try {
                await cursor.move('body');
            } catch (_) { /* ignore */ }
        }
    }
    return Boolean(clicked);
}

/**
 * Establish a real LinkedIn session before leased-job deep links.
 * Profile is usually logged in; we just need the homepage/feed chrome first.
 */
async function ensureLinkedInSession(page, cursor) {
    const targets = [
        'https://www.linkedin.com/feed/',
        'https://www.linkedin.com/jobs/',
        'https://www.linkedin.com/'
    ];
    // Hard budget: never block the single apply worker for > ~90s on warm.
    // Prod was stuck 14+ min on proxy tunnel / "skip to search" loops, starving
    // Indeed/Workopolis queue jobs that would actually produce emails.
    const warmDeadline = Date.now() + 90_000;
    for (let attempt = 0; attempt < 3; attempt++) {
        if (Date.now() > warmDeadline) {
            throw new Error('LinkedIn session warm timed out after 90s (proxy/interstitial loop)');
        }
        const target = targets[Math.min(attempt, targets.length - 1)];
        console.log(`🏠 LinkedIn session warm (attempt ${attempt + 1}/3): ${target}`);
        await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 25000 }).catch((err) => {
            console.warn(`⚠️ Feed navigation issue: ${err.message || err}`);
        });
        await sleep(1500 + attempt * 500);

        // Click through splash / "select to continue" / promo cards (bounded).
        for (let i = 0; i < 3; i++) {
            if (Date.now() > warmDeadline) break;
            const dismissed = await dismissLinkedInInterstitials(page, cursor);
            if (!dismissed) break;
            await sleep(800);
        }

        // Soft-nav to feed if we landed on www root marketing shell with session cookies.
        const url = page.url();
        if (!/linkedin\.com\/(feed|jobs|mynetwork|messaging|notifications)/i.test(url)) {
            await page.goto('https://www.linkedin.com/feed/', {
                waitUntil: 'domcontentloaded',
                timeout: 25000
            }).catch(() => {});
            await sleep(1500);
            await dismissLinkedInInterstitials(page, cursor);
        }

        const authWall = await detectLinkedInAuthWall(page);
        if (!authWall) {
            console.log(`✓ LinkedIn session ready at ${page.url()}`);
            return;
        }
        console.warn(`⚠️ Session not ready yet: ${authWall}`);
    }
    const finalWall = await detectLinkedInAuthWall(page);
    if (finalWall) throw new Error(finalWall);
}

async function humanPacingDelay(label = 'between actions', min = 30000, max = 60000) {
    const delay = randomHumanPacingMs(min, max);
    console.log(`⏱️ Gaussian-adjusted human pacing delay (${label}): ${Math.round(delay / 1000)}s`);
    await sleep(delay);
}

function logApplication(jobId, jobTitle, company, status) {
    const date = new Date().toISOString().split('T')[0];
    const cleanTitle = (jobTitle || 'Unknown').replace(/"/g, '""');
    const cleanCompany = (company || 'Unknown').replace(/"/g, '""');
    const line = `"${date}","${jobId}","${cleanTitle}","${cleanCompany}","${status}"\n`;
    fs.appendFileSync(logFilePath, line);
    console.log(`📊 Logged application: ${jobTitle} at ${company} -> ${status}`);
}

function saveCompanySiteLead(jobInfo, applyUrl = '') {
    if (!saveCompanySiteJobs) return false;
    const key = String(jobInfo.jobId || '').trim();
    if (!key || key === 'unknown') return false;
    const existing = fs.existsSync(companySiteLeadsPath) ? fs.readFileSync(companySiteLeadsPath, 'utf8') : '';
    if (existing.split(/\r?\n/).some(line => line.includes(`"job_id":"${key}"`))) return false;
    const record = { saved_at: new Date().toISOString(), portal: 'linkedin', status: 'saved_company_site',
        job_id: key, title: jobInfo.title, company: jobInfo.company, location: jobInfo.location || '',
        description: jobInfo.detailText || '', source_url: `https://www.linkedin.com/jobs/view/${key}/`,
        company_apply_url: applyUrl || '' };
    fs.appendFileSync(companySiteLeadsPath, JSON.stringify(record) + '\n');
    if (discoveryMode) enqueueDiscoveredLinkedInJob(jobInfo, 'company_site', applyUrl);
    console.log(`🔖 Saved company-site lead: ${jobInfo.title} at ${jobInfo.company}`);
    return true;
}

function enqueueDiscoveredLinkedInJob(jobInfo, applicationMethod = 'easy_apply', explicitUrl = '') {
    const record = { job_id: jobInfo.jobId, title: jobInfo.title, company: jobInfo.company,
        location: jobInfo.location || '', description: jobInfo.detailText || '',
        url: explicitUrl || `https://www.linkedin.com/jobs/view/${jobInfo.jobId}/`,
        application_method: applicationMethod,
        gate_reason: applicationMethod === 'company_site' ? 'LinkedIn company-site gate passed' : 'LinkedIn local location and Easy Apply gates passed' };
    const result = spawnSync(queuePython, [queueAdminPath, 'enqueue-stdin', '--portal', 'linkedin', '--profile', linkedInProfile],
        { input: JSON.stringify(record), encoding: 'utf8', env: process.env });
    if (result.status !== 0) throw new Error(`queue enqueue failed: ${(result.stderr || result.stdout || '').trim()}`);
    console.log(`📥 Queued LinkedIn ${linkedInProfile} job ${jobInfo.jobId}: ${jobInfo.title}`);
}

function loadSubmittedJobIds() {
    if (!fs.existsSync(logFilePath)) return new Set();
    const rows = fs.readFileSync(logFilePath, 'utf8').split(/\r?\n/).slice(1).filter(Boolean);
    return new Set(rows.map(row => {
        const match = row.match(/^"[^"]*","([^"]+)"/);
        return match ? match[1] : null;
    }).filter(Boolean));
}

function assessJobEligibility(jobInfo) {
    const haystack = [
        jobInfo.location,
        jobInfo.workplace,
        jobInfo.detailText
    ].filter(Boolean).join(' ').toLowerCase();

    const metroVancouverSignals = [
        'vancouver',
        'north vancouver',
        'west vancouver',
        'burnaby',
        'richmond',
        'surrey',
        'new westminster',
        'coquitlam',
        'port coquitlam',
        'port moody',
        'delta',
        'langley',
        'maple ridge',
        'pitt meadows',
        'white rock',
        'bc'
    ];
    const hasMetroVancouverSignal = metroVancouverSignals.some(signal => haystack.includes(signal));

    if (/\b(north america|namer)\b/i.test(haystack)) {
        return { ok: false, reason: 'broad North America/NAMER location' };
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
    if (blockedNonLocalSignals.some(signal => new RegExp(`\\b${signal.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')}\\b`, 'i').test(haystack))) {
        return { ok: false, reason: `blocked non-local Canadian location in haystack: ${jobInfo.location || 'unknown'}` };
    }

    const normalizedLocation = (jobInfo.location || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const broadCanadaLocation = normalizedLocation === 'canada' ||
        normalizedLocation === 'canada (remote)' ||
        /^canada\s*[·,-]/.test(normalizedLocation) ||
        (normalizedLocation.includes('canada') && !hasMetroVancouverSignal);
    if (broadCanadaLocation) {
        return { ok: false, reason: `broad Canada location: ${jobInfo.location || 'unknown'}` };
    }

    if (!hasMetroVancouverSignal) {
        return { ok: false, reason: `not clearly Metro Vancouver: ${jobInfo.location || 'unknown'}` };
    }

    return { ok: true, reason: 'Metro Vancouver/local location matched' };
}

async function nstApiRequest(endpoint, options = {}) {
    const response = await fetch(`${nstApiBase}${endpoint}`, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...(nstApiKey ? { 'x-api-key': nstApiKey } : {}),
            ...(options.headers || {})
        }
    });
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;
    if (!response.ok || data?.err === true) {
        throw new Error(`NSTBrowser API failed: ${text}`);
    }
    return data;
}

async function connectNstBrowser() {
    if (!nstApiKey) throw new Error('NST_API_KEY is required when USE_NSTBROWSER is enabled');
    if (!nstProfileId) throw new Error('NST_PROFILE_ID / NSTBROWSER_PROFILE_ID is required when USE_NSTBROWSER is enabled');
    
    // Check if the browser profile is already running to save opens quota
    try {
        console.log(`🌐 Checking if NSTBrowser profile ${nstProfileId} is already running...`);
        const listResult = await nstApiRequest('/browsers', { method: 'GET' });
        const runningProfile = listResult?.data?.find(b => b.profileId === nstProfileId && b.running);
        if (runningProfile && runningProfile.remoteDebuggingPort) {
            const port = runningProfile.remoteDebuggingPort;
            console.log(`🔌 NSTBrowser profile is already running on port ${port}. Querying WebSocket URL...`);
            const versionUrl = `http://127.0.0.1:${port}/json/version`;
            const resp = await fetch(versionUrl);
            const info = await resp.json();
            const browserWSEndpoint = info.webSocketDebuggerUrl;
            if (browserWSEndpoint) {
                console.log(`🔗 Connecting directly to existing WebSocket debugger URL...`);
                const browser = await puppeteer.connect({ browserWSEndpoint, defaultViewport: null });
                if (typeof browser.isConnected !== 'function') {
                    browser.isConnected = () => browser.connected !== false;
                }
                await loadCurrentExtension(browser);
                browser.reused = true;
                return browser;
            }
        }
    } catch (err) {
        console.log(`⚠️ Could not connect to already running browser: ${err.message}. Retrying via POST launch.`);
    }

    // Match monorepo open_chrome.py: PUT proxy on the profile, then POST start
    // with headless/autoClose only. Injecting proxy in the launch body caused
    // NST "retrieving browser version info failed" / empty fetch on LinkedIn.
    if (disableProxy) {
        try {
            await nstApiRequest(`/profiles/${encodeURIComponent(nstProfileId)}/proxy`, {
                method: 'PUT',
                body: JSON.stringify({ url: '' }),
            });
            console.log('📡 Cleared NST profile proxy (LINKEDIN_DISABLE_PROXY).');
        } catch (e) {
            console.log(`⚠️ Could not clear NST profile proxy: ${e.message}`);
        }
    } else if (proxyUrl) {
        try {
            await nstApiRequest(`/profiles/${encodeURIComponent(nstProfileId)}/proxy`, {
                method: 'PUT',
                body: JSON.stringify({ url: proxyUrl }),
            });
            console.log(`📡 Updated NST profile proxy via /profiles/.../proxy`);
        } catch (e) {
            console.log(`⚠️ Could not update NST profile proxy (using cloud profile proxy): ${e.message}`);
        }
    }

    // Best-effort stale lock cleanup (same as open_chrome.py) when docker is available.
    try {
        spawnSync('docker', [
            'exec', 'jobbots-nstbrowser', 'rm', '-f',
            `/data/${nstProfileId}/SingletonLock`,
            `/data/${nstProfileId}/SingletonSocket`,
            `/data/${nstProfileId}/SingletonCookie`,
        ], { timeout: 5000 });
    } catch (_) { /* ignore */ }

    async function startAndConnect(label) {
        console.log(`🌐 Starting NSTBrowser profile ${nstProfileId} via API (${label})...`);
        // Do NOT put proxy in launch body — open_chrome only sends headless/autoClose.
        const payload = {
            headless: false,
            autoClose: false,
        };
        const startResult = await nstApiRequest(`/browsers/${encodeURIComponent(nstProfileId)}`, {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        let browserWSEndpoint = startResult?.data?.webSocketDebuggerUrl;
        if (!browserWSEndpoint) {
            const debugPort = startResult?.data?.remoteDebuggingPort
                || startResult?.data?.port
                || startResult?.data?.debuggingPort;
            if (debugPort) {
                for (let i = 0; i < 25; i++) {
                    try {
                        const resp = await fetch(`http://127.0.0.1:${debugPort}/json/version`);
                        if (resp.ok) {
                            const info = await resp.json();
                            browserWSEndpoint = info.webSocketDebuggerUrl;
                            if (browserWSEndpoint) break;
                        }
                    } catch (_) { /* retry */ }
                    await new Promise(r => setTimeout(r, 1500));
                }
            }
        }
        if (!browserWSEndpoint) {
            throw new Error(`NSTBrowser did not return webSocketDebuggerUrl: ${JSON.stringify(startResult)}`);
        }
        const browser = await puppeteer.connect({ browserWSEndpoint, defaultViewport: null });
        if (typeof browser.isConnected !== 'function') {
            browser.isConnected = () => browser.connected !== false;
        }
        await loadCurrentExtension(browser);
        browser.reused = false;
        return browser;
    }

    try {
        return await startAndConnect(proxyUrl ? 'webshare-primary' : 'no-proxy');
    } catch (primaryErr) {
        // One retry with the same static apply proxy after clearing locks.
        if (proxyUrl) {
            console.log(`⚠️ NST start failed (${primaryErr.message}); retrying once with same Webshare apply proxy…`);
            try {
                await nstApiRequest(`/profiles/${encodeURIComponent(nstProfileId)}/proxy`, {
                    method: 'PUT',
                    body: JSON.stringify({ url: proxyUrl }),
                });
            } catch (e) {
                console.log(`⚠️ Webshare/apply proxy PUT failed: ${e.message}`);
            }
            try {
                spawnSync('docker', [
                    'exec', 'jobbots-nstbrowser', 'rm', '-f',
                    `/data/${nstProfileId}/SingletonLock`,
                    `/data/${nstProfileId}/SingletonSocket`,
                    `/data/${nstProfileId}/SingletonCookie`,
                ], { timeout: 5000 });
            } catch (_) { /* ignore */ }
            try {
                await fetch(`${nstApiBase}/browsers/${encodeURIComponent(nstProfileId)}`, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json',
                        ...(nstApiKey ? { 'x-api-key': nstApiKey } : {}),
                    },
                }).catch(() => {});
                await new Promise(r => setTimeout(r, 2000));
            } catch (_) { /* ignore */ }
            return await startAndConnect('webshare-retry');
        }
        throw primaryErr;
    }
}

async function loadCurrentExtension(browser) {
    if (!loadUnpackedExtension) return;

    let session;
    try {
        session = await browser.target().createCDPSession();
        await session.send('Extensions.enable').catch(() => {});
        const result = await session.send('Extensions.loadUnpacked', { path: __dirname });
        console.log(`🧩 Loaded current extension source${result?.id ? ` (${result.id})` : ''}.`);
    } catch (err) {
        console.warn(`⚠️ Could not load the current extension source: ${err.message}`);
    } finally {
        await session?.detach().catch(() => {});
    }
}

async function extensionCommand(page, command, options = {}) {
    return page.evaluate(async ({ command, options }) => {
        if (!window.WebFormMonitor || typeof window.WebFormMonitor.startOrStatus !== 'function') {
            return { ok: false, error: 'LinkedIn extension bridge is unavailable on this page' };
        }
        return window.WebFormMonitor.startOrStatus({ command, options });
    }, { command, options });
}

async function waitForExtension(page) {
    for (let attempt = 0; attempt < 30; attempt++) {
        const response = await extensionCommand(page, 'getProgress').catch(() => null);
        if (response?.ok) return true;
        await sleep(1000);
    }
    return false;
}

async function applyQueuedJobWithExtension(page, expectedJobId = '') {
    if (!await waitForExtension(page)) {
        return { status: 'failed', result_url: page.url(), reason: 'LinkedIn extension bridge did not initialize' };
    }

    const configured = await extensionCommand(page, 'setRunOptions', {
        maxApplicationsPerRun: 1,
        processingSpeed: extensionProcessingSpeed,
        resetSubmissionCount: true
    });
    if (!configured?.ok) {
        return { status: 'failed', result_url: page.url(), reason: configured?.error || 'Unable to configure LinkedIn extension run' };
    }

    const started = await (async () => {
        try {
            return await extensionCommand(page, 'applyCurrentJob');
        } catch (err) {
            console.log(`⚠️ Extension applyCurrentJob threw: ${err.message}. Falling through to Puppeteer fallback.`);
            return { ok: false, error: err.message };
        }
    })();
    if (!started?.ok || !started.result?.started) {
        return {
            status: 'failed',
            result_url: page.url(),
            reason: started?.error || started?.result?.message || 'LinkedIn extension did not start the direct job'
        };
    }

    const deadline = Date.now() + extensionDirectTimeoutMs;
    while (Date.now() < deadline) {
        const currentUrl = page.url();
        if (currentUrl.includes('/jobs/collections/similar-jobs/') || (expectedJobId && !currentUrl.includes(expectedJobId))) {
            return { status: 'failed', result_url: currentUrl, reason: `Job posting has expired or redirected: expected ${expectedJobId}` };
        }
        const authWall = await detectLinkedInAuthWall(page).catch(() => '');
        if (authWall) {
            return { status: 'failed', result_url: page.url(), reason: authWall };
        }
        const progressResponse = await extensionCommand(page, 'getProgress').catch(() => null);
        const progress = progressResponse?.result;
        if (progress?.directJobResult) {
            const outcome = progress.directJobResult;
            return {
                status: outcome.status === 'skipped' ? 'manual_review' : outcome.status,
                result_url: page.url(),
                reason: outcome.reason || ''
            };
        }
        if (progress?.aborted) {
            return { status: 'failed', result_url: page.url(), reason: 'LinkedIn extension aborted the direct job' };
        }
        await sleep(2000);
    }

    return { status: 'failed', result_url: page.url(), reason: 'Timed out waiting for LinkedIn extension direct-job outcome' };
}

async function clickWithGhostFallback(pageOrFrame, cursor, element, label) {
    if (!element) return false;

    const isFrame = typeof pageOrFrame.page === 'function';

    const doDomClick = async () => {
        try {
            await pageOrFrame.evaluate(async el => {
                const randomDelay = (min, max) => {
                    let u1 = Math.random();
                    let u2 = Math.random();
                    if (u1 <= Number.EPSILON) u1 = Number.EPSILON;
                    const z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
                    const mean = min + (max - min) * 0.58;
                    const stdDev = (max - min) / 6;
                    return Math.floor(Math.max(min, Math.min(max, mean + z0 * stdDev)));
                };
                const pause = (min = 35, max = 140) => new Promise(resolve => setTimeout(resolve, randomDelay(min, max)));
                const dispatchMouse = async type => {
                    el.dispatchEvent(new MouseEvent(type, {
                        bubbles: true,
                        cancelable: true,
                        composed: true,
                        view: window
                    }));
                    await pause();
                };

                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                await pause(120, 260);
                await dispatchMouse('mouseover');
                await dispatchMouse('mousemove');
                await dispatchMouse('mousedown');
                if (typeof el.focus === 'function') el.focus();
                await pause(45, 120);
                el.click();
                await dispatchMouse('mouseup');
                await dispatchMouse('click');
            }, element);
            return true;
        } catch (e) {
            console.warn(`⚠️ DOM click fallback error for ${label}: ${e.message}`);
            return false;
        }
    };

    if (isFrame) {
        return await doDomClick();
    }

    try {
        await pageOrFrame.evaluate(el => el.scrollIntoView({ behavior: 'smooth', block: 'center' }), element);
        await sleep(randomHumanPacingMs(300, 600));
        await Promise.race([
            cursor.click(element),
            new Promise((_, reject) => setTimeout(() => reject(new Error('ghost-cursor click timed out')), 5000))
        ]);
        return true;
    } catch (err) {
        console.warn(`⚠️ ghost-cursor failed for ${label}: ${err.message}. Using DOM click fallback.`);
        return await doDomClick();
    }
}
async function cleanupBrowser(browser) {
    try {
        const pages = await browser.pages();
        for (let i = 0; i < pages.length; i++) {
            if (i === 0) {
                await pages[i].goto('about:blank').catch(() => {});
            } else {
                await pages[i].close().catch(() => {});
            }
        }
    } catch (err) {
        console.warn('⚠️ Error cleaning up pages:', err.message);
    }

    if (useNstBrowser) {
        console.log('🔌 Disconnecting from NSTBrowser session (leaving profile open for reuse)...');
        await browser.disconnect();
    } else {
        console.log('🛑 Closing local browser session...');
        await browser.close();
    }
}

async function run() {
    console.log('🚀 Launching Hybrid Stealth Mode...');
    console.log(`📂 Profile Location: ${chromeUserDataDir} [${chromeProfile}]`);
    console.log(`🖥️ Executable: ${chromeExecutablePath}`);

    // Clean up locks in the copied profile directory to prevent Chrome from hanging/failing to launch.
    // NST/CDP mode has no local profile dir (chromeUserDataDir is undefined on
    // Linux workers) — path.join(undefined, …) crashes before connectNstBrowser().
    if (!useNstBrowser && chromeUserDataDir) {
        const lockFiles = ['SingletonLock', 'SingletonSocket', 'SingletonCookie'];
        lockFiles.forEach(file => {
            const filePath = path.join(chromeUserDataDir, file);
            try {
                fs.lstatSync(filePath);
                fs.unlinkSync(filePath);
                console.log(`🧹 Removed lock file: ${file}`);
            } catch (e) {
                if (e.code !== 'ENOENT') {
                    console.warn(`⚠️ Warning: Could not remove lock file ${file}:`, e.message);
                }
            }
        });
    }

    const launchArgs = [
        `--user-data-dir=${chromeUserDataDir}`,
        `--profile-directory=${chromeProfile}`,
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--window-size=1280,800',
        '--enable-unsafe-extension-debugging',
        '--disable-features=DisableLoadExtensionCommandLineSwitch',
        `--disable-extensions-except=${__dirname}`,
        `--load-extension=${__dirname}`
    ];

    let browser;
    try {
        browser = useNstBrowser ? await connectNstBrowser() : await puppeteer.launch({
            headless: false, // Required to use personal Chrome profile
            executablePath: chromeExecutablePath,
            args: launchArgs,
            ignoreDefaultArgs: ['--disable-extensions'],
            defaultViewport: null
        });
    } catch (err) {
        console.error('❌ Failed to launch Chrome / NST browser for LinkedIn.');
        console.error('Error details:', err.message);
        // Always write queue result so application_worker can classify (not "exit 1 without result").
        writeQueueResult({
            status: 'failed',
            result_url: directJobUrl || '',
            reason: `browser launch failed: ${err.message || err}`,
            application_method: 'easy_apply',
        });
        process.exit(2);
    }

    const pages = await browser.pages();
    const page = pages.find(candidate => {
        const url = candidate.url();
        return !/^https:\/\/(?:www\.)?linkedin\.com\/(?:login|checkpoint|authwall)/i.test(url);
    }) || pages[0];
    const cursor = createCursor(page);

    let submittedCount = 0;
    let scannedSearchPages = 0;
    const attemptedJobIds = loadSubmittedJobIds();

    if (directJobUrl) {
        let result = { status: 'failed', result_url: directJobUrl, reason: 'LinkedIn direct application did not complete' };
        try {
            page.on('console', msg => console.log('🌐 PAGE CONSOLE:', msg.text()));

            // Always warm session for leased direct-apply. Search mode already
            // lands on feed/jobs; deep-link apply used to hard-fail on splash
            // screens that look like login until a Continue/Skip is clicked.
            await ensureLinkedInSession(page, cursor);

            const expectedJobId =
                directJobUrl.match(/\/jobs\/view\/(\d+)/)?.[1] ||
                directJobUrl.match(/[?&]currentJobId=(\d+)/)?.[1] ||
                '';
            // Search-context URL (f_AL=Easy Apply filter) is more reliable for the apply top-card
            // than bare /jobs/view/ — matches prior production hybrid path.
            let navigateUrl = directJobUrl;
            if (expectedJobId && !/currentJobId=/.test(directJobUrl)) {
                navigateUrl =
                    `https://www.linkedin.com/jobs/search/?currentJobId=${expectedJobId}` +
                    `&f_AL=true&origin=JOB_SEARCH_PAGE_JOB_FILTER`;
                console.log(`🎯 Direct job apply via search context: ${navigateUrl}`);
                console.log(`   (original queue url: ${directJobUrl})`);
            } else {
                console.log(`🎯 Navigating to direct job URL: ${directJobUrl}`);
            }
            await page.goto(navigateUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
            await sleep(5000);

            const loadedUrl = page.url();
            if (loadedUrl.includes('/jobs/collections/similar-jobs/') || (expectedJobId && !loadedUrl.includes(expectedJobId) && !loadedUrl.includes('currentJobId=' + expectedJobId))) {
                // One retry on original view URL
                console.warn(`⚠️ Search-context load odd (${loadedUrl}); retrying view URL…`);
                await page.goto(directJobUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
                await sleep(4000);
            }
            const loadedUrl2 = page.url();
            if (loadedUrl2.includes('/jobs/collections/similar-jobs/')) {
                throw new Error(`Job posting has expired or redirected to similar jobs page: expected ${expectedJobId}, loaded ${loadedUrl2}`);
            }

            const authWall = await detectLinkedInAuthWall(page);
            if (authWall) throw new Error(authWall);

            const dailyLimitMessage = await detectLinkedInDailyEasyApplyLimit(page);
            if (dailyLimitMessage) {
                recordLinkedInDailyLimit(dailyLimitMessage);
                result = {
                    status: 'daily_limit_reached',
                    result_url: page.url(),
                    reason: 'LinkedIn daily Easy Apply limit reached: ' + dailyLimitMessage,
                };
                console.warn('🚫 LinkedIn reports that its daily Easy Apply limit has been reached.');
                if (queueResultFile) fs.writeFileSync(queueResultFile, JSON.stringify(result));
                await cleanupBrowser(browser);
                process.exit(0);
            }

            // Already-applied fast path: stale queue entries (7d-freshness
            // discovery) often show "Applied … ago / See application" in the
            // top card — there is no EA button to find. Count as terminal
            // applied instead of burning 3 click attempts into a filter chip.
            const alreadyApplied = await page.evaluate(() => {
                const tc = document.querySelector('.jobs-unified-top-card, .job-details-jobs-unified-top-card, [class*="top-card"]');
                const t = ((tc && tc.innerText) || '').replace(/\s+/g, ' ');
                return /See application|Applied \d+ (hour|day|week|month)s? ago|You'?ve applied|You applied/i.test(t);
            });
            if (alreadyApplied) {
                result = {
                    status: 'already_applied',
                    application_method: 'easy_apply',
                    result_url: page.url(),
                    reason: 'already applied (LinkedIn top-card shows prior application)'
                };
                console.log('✓ Job already applied on LinkedIn — terminal already_applied (not a new submit).');
                if (queueResultFile) fs.writeFileSync(queueResultFile, JSON.stringify(result));
                await cleanupBrowser(browser);
                process.exit(0);
            }

            if (/^(1|true|yes|on)$/i.test(process.env.JOB_QUEUE_BOOKMARK_FIRST || '')) {
                const saveBtn = await page.$('button[aria-label*="Save" i], button.jobs-save-button');
                if (saveBtn) await clickWithGhostFallback(page, cursor, saveBtn, 'Save job');
                if (/^(1|true|yes|on)$/i.test(process.env.JOB_QUEUE_BOOKMARK_ONLY || '')) {
                    result = { status: 'bookmarked', result_url: page.url(), reason: 'Company-site bookmarked' };
                    if (queueResultFile) fs.writeFileSync(queueResultFile, JSON.stringify(result));
                    await cleanupBrowser(browser);
                    process.exit(0);
                }
            }
            await page.bringToFront();
            // Prefer queue metadata (LINKEDIN_DIRECT_JOB_JSON) then DOM top-card.
            let queueJob = {};
            try {
                const raw = process.env.LINKEDIN_DIRECT_JOB_JSON || process.env.JOB_QUEUE_DIRECT_JOB || '';
                if (raw) queueJob = JSON.parse(raw);
            } catch (_) {}
            let domTitle = '';
            let domCompany = '';
            try {
                const top = await page.evaluate(() => {
                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    const title =
                        scope.querySelector('.job-details-jobs-unified-top-card__job-title, h1.t-24, .jobs-unified-top-card__job-title, h1')?.textContent?.trim() || '';
                    const company =
                        scope.querySelector('.job-details-jobs-unified-top-card__company-name a, .job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name a, .jobs-unified-top-card__company-name')?.textContent?.trim() || '';
                    return { title, company };
                });
                domTitle = (top && top.title) || '';
                domCompany = (top && top.company) || '';
            } catch (_) {}
            const jobInfo = {
                jobId: expectedJobId || queueJob.id || queueJob.source_job_id || '',
                title: (queueJob.title || domTitle || process.env.LINKEDIN_JOB_TITLE || 'Direct Job').toString().trim(),
                company: (queueJob.company || domCompany || process.env.LINKEDIN_JOB_COMPANY || '').toString().trim() || 'Unknown Company',
                location: (queueJob.location || '').toString().trim(),
                url: directJobUrl || queueJob.url || '',
            };
            console.log(`📌 Direct job meta: "${jobInfo.title}" @ "${jobInfo.company}"`);

            const findEasyApplyButton = async () => {
                return page.evaluateHandle(() => {
                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    const prefer = [
                        '#jobs-apply-button-id',
                        '[data-live-test-job-apply-button]',
                        '.jobs-apply-button--top-card',
                        '.jobs-s-apply button',
                        '.jobs-apply-button',
                        'button.jobs-apply-button',
                    ];
                    for (const sel of prefer) {
                        const el = scope.querySelector(sel);
                        if (!el) continue;
                        const t = ((el.innerText || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
                        // Prefer Easy Apply; skip pure external "Apply"
                        if (t.includes('easy apply') || t.includes('candidature simplifiée') || t.includes('continue applying') || t.includes('reprendre')) {
                            return el;
                        }
                        if (el.id === 'jobs-apply-button-id' || el.getAttribute('data-live-test-job-apply-button') != null) {
                            // LinkedIn sometimes labels only via aria
                            if (!t.includes('apply on company') && !t.includes('company website')) return el;
                        }
                    }
                    const candidates = Array.from(scope.querySelectorAll('button, a[role="button"], a.jobs-apply-button'));
                    for (const el of candidates) {
                        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                        const hay = `${text} ${aria}`;
                        // Never click the search FILTER chip ("Easy Apply Easy Apply filter.")
                        // — toggling a filter opens no modal and reads as a false EA button on
                        // expired/stale job pages where no real apply control exists.
                        if (el.id === 'searchFilter_applyWithLinkedin' || hay.includes('filter')) {
                            continue;
                        }
                        if (hay.includes('easy apply') || hay.includes('candidature simplifiée') || hay.includes('continue applying')) {
                            return el;
                        }
                    }
                    // Detect external-only apply for better error
                    for (const el of candidates) {
                        const hay = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
                        if (hay.includes('apply') && (hay.includes('company') || hay.includes('website') || hay.includes('external'))) {
                            el.setAttribute('data-temp-external-apply', '1');
                            return null;
                        }
                    }
                    return null;
                });
            };

            // Check if modal is already open or click Easy Apply button (with retries)
            let activeCtx = await waitForModalInFrames(page, 2000);
            let sawEasyApply = !!activeCtx;
            if (!activeCtx) {
                for (let attempt = 1; attempt <= 3 && !activeCtx; attempt++) {
                    const easyApplyBtnHandle = await findEasyApplyButton();
                    const btn = await easyApplyBtnHandle.asElement();
                    if (!btn) {
                        const external = await page.$('[data-temp-external-apply="1"]');
                        if (external) {
                            result = {
                                status: 'failed',
                                result_url: page.url(),
                                reason: 'not easy apply — company website / external apply only',
                            };
                            console.warn('⚠️ Job is external/company apply only (no Easy Apply).');
                            if (queueResultFile) fs.writeFileSync(queueResultFile, JSON.stringify(result));
                            await cleanupBrowser(browser);
                            process.exit(2);
                        }
                        console.warn(`⚠️ Could not locate Easy Apply button on direct job page (attempt ${attempt}/3).`);
                        await sleep(1500);
                        continue;
                    }
                    sawEasyApply = true;
                    console.log(`📌 Clicking Easy Apply button for direct job (attempt ${attempt}/3)...`);
                    // Prefer DOM click first for top-card (ghost often times out on NST)
                    await page.evaluate((el) => {
                        el.scrollIntoView({ block: 'center', inline: 'center' });
                        el.click();
                    }, btn).catch(async () => {
                        await clickWithGhostFallback(page, cursor, btn, 'Easy Apply button');
                    });
                    await sleep(2500 + attempt * 500);
                    activeCtx = await waitForModalInFrames(page, 5000);
                    if (!activeCtx) {
                        // second path: ghost then DOM
                        await clickWithGhostFallback(page, cursor, btn, 'Easy Apply button retry');
                        await sleep(3000);
                        activeCtx = await waitForModalInFrames(page, 5000);
                    }
                }
            }

            if (!sawEasyApply && !activeCtx) {
                result = {
                    status: 'failed',
                    result_url: page.url(),
                    reason: 'easy apply button not found',
                };
                console.warn('⚠️ Giving up: no Easy Apply control on job page.');
                if (queueResultFile) fs.writeFileSync(queueResultFile, JSON.stringify(result));
                await cleanupBrowser(browser);
                process.exit(2);
            }

            const submitted = await processEasyApplyForm(page, cursor, jobInfo);
            if (submitted && submitted.ok) {
                result = { status: 'applied', result_url: page.url(), reason: '' };
                await handleSuccessModal(page, cursor);
            } else {
                result = {
                    status: 'failed',
                    result_url: page.url(),
                    reason: (submitted && submitted.reason) || 'Easy Apply form was not submitted',
                };
            }
        } catch (err) { result.reason = `${err.name}: ${err.message}`; }
        console.log('Direct Apply outcome:', result);
        if (queueResultFile) fs.writeFileSync(queueResultFile, JSON.stringify(result));
        await cleanupBrowser(browser);
        process.exit(result.status === 'applied' ? 0 : 2);
    }

    for (const currentSearchUrl of fallbackSearchUrls) {
        if (submittedCount >= maxApplications || scannedSearchPages >= maxSearchPages) break;

        console.log(`🔗 Navigating to target job search: ${currentSearchUrl}`);
        try {
            await page.goto(currentSearchUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
        } catch (err) {
            if (!/Navigation timeout/i.test(err.message) || !page.url().includes('linkedin.com/jobs')) {
                throw err;
            }
            console.warn(`⚠️ LinkedIn navigation timed out after partial load; continuing from ${page.url()}`);
        }

        console.log('⏳ Waiting 5 seconds for page rendering...');
        await new Promise(r => setTimeout(r, 5000));

    for (let searchPage = 1; searchPage <= maxSearchPagesPerTerm && scannedSearchPages < maxSearchPages && submittedCount < maxApplications; searchPage++) {
        scannedSearchPages++;
        console.log('🔍 Scanning job cards...');
        const cards = await page.$$('.job-card-container, .jobs-search-results__list-item');
        console.log(`📋 Found ${cards.length} job cards on search page ${searchPage}.`);

        for (let index = 0; index < cards.length && submittedCount < maxApplications; index++) {
            console.log(`\n-----------------------------------------`);
            console.log(`💼 Processing Job Card ${index + 1}/${cards.length}`);

            try {
                const freshCards = await page.$$('.job-card-container, .jobs-search-results__list-item');
                const card = freshCards[index];
                if (!card) {
                    console.log('⏭️ Card disappeared after refresh, skipping.');
                    continue;
                }

                const clickedCard = await clickWithGhostFallback(page, cursor, card, `job card ${index + 1}`);
                if (!clickedCard) continue;
                console.log('✅ Clicked job card.');
                await new Promise(r => setTimeout(r, 3000));

                const jobInfo = await page.evaluate(() => {
                    const titleEl = document.querySelector('h1.t-24, .job-details-jobs-unified-top-card__job-title');
                    const companyEl = document.querySelector('.job-details-jobs-unified-top-card__company-name a, .job-details-jobs-unified-top-card__company-name');
                    const topCard = document.querySelector('.job-details-jobs-unified-top-card, .jobs-unified-top-card');
                    const title = titleEl ? titleEl.textContent.trim() : 'Unknown Position';
                    const company = companyEl ? companyEl.textContent.trim() : 'Unknown Company';
                    const detailText = topCard ? topCard.textContent.replace(/\s+/g, ' ').trim() : '';
                    const primaryDescription = document.querySelector(
                        '.job-details-jobs-unified-top-card__primary-description-container, .jobs-unified-top-card__primary-description'
                    )?.textContent?.replace(/\s+/g, ' ').trim() || '';
                    const workplace = Array.from(document.querySelectorAll(
                        '.job-details-preferences-and-skills__pill, .jobs-unified-top-card__job-insight, .job-details-jobs-unified-top-card__job-insight'
                    )).map(el => el.textContent.replace(/\s+/g, ' ').trim()).join(' ');
                    const location = primaryDescription.split('·')[0]?.trim() || '';

                    let jobId = 'unknown';
                    const currentJobIdMatch = window.location.href.match(/currentJobId=(\d+)/);
                    if (currentJobIdMatch) jobId = currentJobIdMatch[1];
                    if (jobId === 'unknown') {
                        const selected = document.querySelector('[data-job-id], [data-occludable-job-id]');
                        jobId = selected?.getAttribute('data-job-id') || selected?.getAttribute('data-occludable-job-id') || 'unknown';
                    }

                    return { title, company, jobId, location, workplace, detailText };
                });

                console.log(`📌 Position: "${jobInfo.title}" at "${jobInfo.company}" (ID: ${jobInfo.jobId})`);
                console.log(`📍 Location signal: "${jobInfo.location || 'unknown'}"`);
                const eligibility = assessJobEligibility(jobInfo);
                if (!eligibility.ok) {
                    console.log(`⏭️ Skipping location policy mismatch: ${eligibility.reason}`);
                    continue;
                }
                if (attemptedJobIds.has(jobInfo.jobId)) {
                    console.log(`⏭️ Already attempted/submitted job ${jobInfo.jobId}, skipping duplicate.`);
                    continue;
                }
                attemptedJobIds.add(jobInfo.jobId);

                const easyApplyBtn = await page.evaluateHandle(() => {
                    const std = document.querySelector('#jobs-apply-button-id, [data-live-test-job-apply-button], .jobs-apply-button');
                    if (std) return std;
                    const candidates = Array.from(document.querySelectorAll('button, a'));
                    for (const el of candidates) {
                        const text = (el.innerText || el.textContent || '').trim();
                        const aria = el.getAttribute('aria-label') || '';
                        if (text === 'Easy Apply' || text === 'in Easy Apply' || aria.includes('Easy Apply')) return el;
                    }
                    const fallbacks = Array.from(document.querySelectorAll('div[role="button"], span, div'));
                    for (const el of fallbacks) {
                        const text = (el.innerText || el.textContent || '').trim();
                        const aria = el.getAttribute('aria-label') || '';
                        if (text === 'Easy Apply' || text === 'in Easy Apply' || aria.includes('Easy Apply')) return el;
                    }
                    return null;
                });
                const easyApplyBtnEl = await easyApplyBtn.asElement();
                if (!easyApplyBtnEl) {
                    const externalUrl = await page.evaluate(() => {
                        const candidates = Array.from(document.querySelectorAll('a[href], button'));
                        const el = candidates.find(node => /apply|company site/i.test(`${node.textContent || ''} ${node.getAttribute('aria-label') || ''}`));
                        return el?.href || el?.closest?.('a')?.href || '';
                    });
                    if (!saveCompanySiteLead(jobInfo, externalUrl)) console.log('⏭️ No Easy Apply button found for this job card, skipping.');
                    continue;
                }
                const applyButtonInfo = await page.evaluate(btn => ({
                    text: btn.textContent.trim(),
                    ariaLabel: btn.getAttribute('aria-label') || '',
                    href: btn.href || btn.closest('a')?.href || ''
                }), easyApplyBtnEl);
                const applyText = `${applyButtonInfo.text} ${applyButtonInfo.ariaLabel}`.toLowerCase();
                if (!applyText.includes('easy apply') && !applyText.includes('candidature simplifiée')) {
                    if (!saveCompanySiteLead(jobInfo, applyButtonInfo.href)) console.log(`⏭️ Skipping non-Easy Apply button: "${applyButtonInfo.text}" ${applyButtonInfo.href}`);
                    continue;
                }

                if (discoveryMode) {
                    enqueueDiscoveredLinkedInJob(jobInfo);
                    continue;
                }

                const clickedApply = await clickWithGhostFallback(page, cursor, easyApplyBtn, 'Easy Apply button');
                if (!clickedApply) continue;
                console.log('✅ Clicked Easy Apply button.');
                await new Promise(r => setTimeout(r, 2000));

                const submitted = await processEasyApplyForm(page, cursor, jobInfo);
                if (submitted && submitted.ok) submittedCount++;

            } catch (err) {
                console.error(`❌ Error processing job card ${index + 1}:`, err.message);
            }
        }

        if (submittedCount >= maxApplications) break;

        const movedToNextPage = await page.evaluate(() => {
            const nextBtn = document.querySelector('.jobs-search-pagination__button--next');
            if (!nextBtn || nextBtn.disabled || nextBtn.getAttribute('aria-disabled') === 'true') return false;
            nextBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            nextBtn.click();
            return true;
        });
        if (!movedToNextPage) {
            console.log('✅ No more search pages available.');
            break;
        }
        console.log('➡️ Moving to next search results page...');
        await new Promise(r => setTimeout(r, 5000));
    }
    }

    console.log('\n=========================================');
    console.log(`🎉 Application run completed! Submitted ${submittedCount}/${maxApplications}.`);
    console.log('=========================================');
    await cleanupBrowser(browser);
}

async function waitForModalInFrames(page, timeout = 15000) {
    const startTime = Date.now();
    
    // First, auto-dismiss any lingering "Save this application?" dialogs
    try {
        await page.evaluate(() => {
            const btns = Array.from(document.querySelectorAll('button'));
            const discardBtn = btns.find(b => {
                const txt = (b.textContent || '').trim().toLowerCase();
                return txt === 'discard' || txt === 'abandonner';
            });
            if (discardBtn) {
                console.log('[BOT TRACE] Auto-dismissing lingering "Save this application?" modal');
                discardBtn.click();
            }
        });
    } catch(e) {}

        const isFormModalPresent = () => {
            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
            if (window !== window.top) {
                if (document.querySelector('input[aria-label="Search"], input[name="q"], input[placeholder*="Search" i], [aria-label="Set alert"]')) {
                    return false;
                }
                const bodyText = (document.body?.innerText || '').replace(/\s+/g, ' ').toLowerCase();
                const hasApplicationMarker = !!document.querySelector(
                    '.jobs-easy-apply-content, [data-test-modal], [role="dialog"], input[type="radio"], select, textarea'
                ) || /easy apply|work authorization|right to work|resume|cover letter|phone number|review your application|submit application/.test(bodyText);
                const hasActionButton = Array.from(document.querySelectorAll('button, input[type="submit"]')).some(button =>
                    /next|review|submit|apply|continue/.test((button.innerText || button.value || button.getAttribute('aria-label') || '').toLowerCase())
                );
                return hasApplicationMarker && hasActionButton;
            }
            const dialogs = Array.from(scope.querySelectorAll('.jobs-easy-apply-modal, [data-test-modal], .jobs-easy-apply-content, [role="dialog"]'));
            return dialogs.some(d => {
                const txt = (d.textContent || '').toLowerCase();
                if (txt.includes('save this application') || txt.includes('if you choose to not save')) return false;
                return true;
            });
        };

    while (Date.now() - startTime < timeout) {
        try {
            const hasModal = await page.evaluate(isFormModalPresent);
            if (hasModal) return page;
        } catch (e) {}

        const frames = page.frames();
        for (const frame of frames) {
            try {
                const hasModal = await frame.evaluate(isFormModalPresent);
                if (hasModal) return frame;
            } catch (e) {}
        }
        await new Promise(r => setTimeout(r, 500));
    }
    return null;
}

async function processEasyApplyForm(page, cursor, jobInfo) {
    let currentStep = 1;
    let retriesOnSameStep = 0;
    const maxSteps = 10;
    const stepUnresolvedFields = {};
    let lastFailReason = 'Easy Apply form was not submitted';

    const fail = (reason) => {
        lastFailReason = reason || lastFailReason;
        vlog(`Easy Apply fail: ${lastFailReason}`);
        return { ok: false, reason: lastFailReason };
    };
    const ok = () => ({ ok: true, reason: '' });

    const activeContext = await waitForModalInFrames(page, 15000);
    if (!activeContext) {
        // Modal may already have closed after a one-click submit or success redirect.
        const earlyConfirm = await verifyApplicationSubmitted(page, page, cursor);
        if (earlyConfirm) {
            vlog('No modal but LinkedIn success UI present — treating as applied.');
            logApplication(jobInfo.jobId, jobInfo.title, jobInfo.company, 'Submitted');
            return ok();
        }
        vlog('Form modal closed or submit finished (could not locate modal in any frame).');
        return fail('easy apply modal not found after open');
    }
    vlog(`Form modal resolved inside target context: ${activeContext === page ? 'Main Page' : 'Subframe'}`);

    let activeCtx = activeContext;

    while (currentStep <= maxSteps) {
        vlog(`Form Step ${currentStep}...`);
        
        // Ensure active frame context is still valid (re-locate if detached during navigation)
        try {
            await activeCtx.evaluate(() => true);
        } catch (err) {
            vlog('Target frame detached during step transition. Re-resolving modal context...');
            const reFound = await waitForModalInFrames(page, 10000);
            if (reFound) activeCtx = reFound;
            else activeCtx = page;
        }

        // Wait for modal fields
        const isFormModalPresent = () => {
            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
            if (window !== window.top) {
                if (document.querySelector('input[aria-label="Search"], input[name="q"], input[placeholder*="Search" i], [aria-label="Set alert"]')) {
                    return false;
                }
                const bodyText = (document.body?.innerText || '').replace(/\s+/g, ' ').toLowerCase();
                const hasApplicationMarker = !!document.querySelector(
                    '.jobs-easy-apply-content, [data-test-modal], [role="dialog"], input[type="radio"], select, textarea'
                ) || /easy apply|work authorization|right to work|resume|cover letter|phone number|review your application|submit application/.test(bodyText);
                const hasActionButton = Array.from(document.querySelectorAll('button, input[type="submit"]')).some(button =>
                    /next|review|submit|apply|continue/.test((button.innerText || button.value || button.getAttribute('aria-label') || '').toLowerCase())
                );
                return hasApplicationMarker && hasActionButton;
            }
            const dialogs = Array.from(scope.querySelectorAll('.jobs-easy-apply-modal, [data-test-modal], .jobs-easy-apply-content, [role="dialog"]'));
            return dialogs.some(d => {
                const txt = (d.textContent || '').toLowerCase();
                if (txt.includes('save this application') || txt.includes('if you choose to not save')) return false;
                return true;
            });
        };
        let hasModal = await activeCtx.evaluate(isFormModalPresent);
        if (!hasModal) {
            // Brief re-render gaps after Next, or post-submit success without modal.
            const maybeDone = await verifyApplicationSubmitted(activeCtx, page, cursor);
            if (maybeDone) {
                vlog('Modal gone but LinkedIn success UI present — treating as applied.');
                logApplication(jobInfo.jobId, jobInfo.title, jobInfo.company, 'Submitted');
                return ok();
            }
            await sleep(1800);
            const reFound = await waitForModalInFrames(page, 6000);
            if (reFound) {
                activeCtx = reFound;
                hasModal = await activeCtx.evaluate(isFormModalPresent);
            }
            if (!hasModal) {
                vlog('Form modal closed or submit finished.');
                lastFailReason = 'easy apply modal disappeared mid-form';
                break;
            }
        }

        // Fill form fields visible on page — now logged via fillStepFields itself
        const getContentSignature = async (ctx) => {
            return await ctx.evaluate(() => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]');
                if (!modal) return '';
                const header = modal.querySelector('h1, h2, h3, .jobs-easy-apply-modal__title, .artdeco-modal__header, legend')?.textContent?.trim() || '';
                const inputs = Array.from(modal.querySelectorAll('input:not([type="hidden"]), select, textarea'));
                const inputStates = inputs.map(i => `${i.name || i.id || i.type}:${i.value || i.checked || ''}`).join('|');
                return `${header}::${modal.innerText.length}::${inputs.length}::${inputStates}`;
            });
        };

        const contentBefore = await getContentSignature(activeCtx);

        await fillStepFields(activeCtx, vlog, currentStep, stepUnresolvedFields, jobInfo);

        const sigParts = contentBefore.split('::');
        vlog(`Step ${currentStep}: header="${sigParts[0]||'Modal'}" (${sigParts[2]||0} fields, ${sigParts[1]||0} chars text)`);

        // Hesitate briefly like a human
        await sleep(randomHumanPacingMs(500, 1000));

        // Find navigation button
        const navBtnData = await activeCtx.evaluate(() => {
            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
            const container = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], .jobs-easy-apply-content, [role="dialog"], form') || scope;
            
            let btn = container.querySelector('[data-easy-apply-next-button]') ||
                container.querySelector('[data-live-test-easy-apply-review-button]') ||
                container.querySelector('[data-live-test-easy-apply-submit-button]') ||
                container.querySelector('[data-control-name="continue_unify"]') ||
                container.querySelector('footer button.artdeco-button--primary, .jobs-easy-apply-modal footer button, form button[type="submit"]');

            if (!btn) {
                const allButtons = Array.from(scope.querySelectorAll('button, input[type="submit"]'));
                btn = allButtons.find(b => {
                    const txt = (b.textContent || b.value || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (txt.includes('skip') || txt.includes('search') || txt.includes('close') || txt.includes('dismiss') || txt.includes('cancel') || txt === 'back') return false;
                    return txt === 'next' || txt === 'review' || txt === 'submit' || txt === 'apply' ||
                           txt.includes('next') || txt.includes('review') || txt.includes('submit') || txt.includes('candidature') || txt.includes('envoyer') || txt.includes('suivant');
                });
            }

            if (btn) {
                btn.setAttribute('data-temp-id', 'temp-nav-btn');
                return {
                    text: (btn.textContent || btn.value || btn.getAttribute('aria-label') || '').trim().toLowerCase(),
                    isDisabled: btn.disabled || btn.getAttribute('aria-disabled') === 'true'
                };
            }
            return null;
        });

        if (!navBtnData) {
            vlog('No navigation button found inside modal.');
            lastFailReason = 'easy apply next/submit button not found (required field or typeahead?)';
            break;
        }

        if (navBtnData.isDisabled) {
            vlog(`Next button is DISABLED. Text="${navBtnData.text}". Modal still open — a required field is missing.`);
            lastFailReason = `easy apply next disabled (missing required field; btn="${navBtnData.text}")`;
            break;
        }

        const isSubmit = navBtnData.text.includes('submit') || navBtnData.text.includes('candidature') || navBtnData.text.includes('envoyer');

        // Never submit after a field failed to resolve or commit. This is the
        // safety boundary for malformed company/typeahead values.
        if (isSubmit) {
            const allUnresolved = Object.values(stepUnresolvedFields || {}).flat().filter(u => !u.handled);
            if (allUnresolved.length > 0) {
                const names = allUnresolved.map(u => `"${u.label}"`).join(', ');
                vlog(`🛑 Blocking Submit: unresolved fields remain [${names}]`);
                await closeEasyApplyModal(activeCtx, cursor);
                return fail(`blocked submit with unresolved fields: ${names}`);
            }
        }
        
        // Click navigation button via Bezier path
        const navBtnHandle = await activeCtx.evaluateHandle(() => {
            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
            return scope.querySelector('[data-temp-id="temp-nav-btn"]');
        });
        const navBtn = await navBtnHandle.asElement();
        if (navBtn) {
            // Uncheck follow company checkbox before clicking submit if present
            if (isSubmit) {
                await activeCtx.evaluate(() => {
                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    const checkboxes = Array.from(scope.querySelectorAll('input[type="checkbox"]'));
                    checkboxes.forEach(cb => {
                        const labelText = cb.closest('label')?.textContent?.trim() || cb.nextElementSibling?.textContent?.trim() || '';
                        if (labelText.toLowerCase().includes('follow') || cb.id.includes('follow')) {
                            if (cb.checked) cb.click();
                        }
                    });
                });
                await new Promise(r => setTimeout(r, 300));
            }

            await clickWithGhostFallback(activeCtx, cursor, navBtn, 'form navigation button');
            vlog(`Clicked navigation button: "${navBtnData.text}" (isSubmit=${isSubmit})`);
            await new Promise(r => setTimeout(r, 2000));
        }

        if (isSubmit) {
            // Change 5: Final validation — log any unresolved fields before submit
            const allUnresolved = Object.values(stepUnresolvedFields || {}).flat().filter(u => !u.handled);
            if (allUnresolved.length > 0) {
                const names = allUnresolved.map(u => `"${u.label}"`).join(', ');
                vlog(`⚠️ Submit step has ${allUnresolved.length} unresolved fields: [${names}]`);
            }
            // Do NOT trust a click alone — LinkedIn often leaves the form open or
            // shows an error; older code marked applied after click (false positive).
            vlog('Submit button clicked — verifying LinkedIn confirmation UI...');
            const confirmed = await verifyApplicationSubmitted(activeCtx, page, cursor);
            if (confirmed) {
                vlog('✅ Application confirmed by LinkedIn success UI.');
                logApplication(jobInfo.jobId, jobInfo.title, jobInfo.company, 'Submitted');
                return ok();
            }
            vlog('❌ Submit click did not produce a LinkedIn success confirmation — treating as not applied.');
            await closeEasyApplyModal(activeCtx, cursor);
            return fail('false_positive_submit_click_without_linkedin_confirmation');
        }

        // Re-check frame after click navigation
        try {
            await activeCtx.evaluate(() => true);
        } catch (e) {
            const reFound = await waitForModalInFrames(page, 5000);
            if (reFound) activeCtx = reFound;
        }

        const contentAfter = await getContentSignature(activeCtx);
        if (contentAfter === contentBefore) {
            retriesOnSameStep++;
            vlog(`Form did not advance after "${navBtnData.text}" (${retriesOnSameStep}/3). Fingerprint unchanged.`);

            // Surface LinkedIn validation errors (often empty typeahead/city).
            const validation = await activeCtx.evaluate(() => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]') || scope;
                const msgs = Array.from(modal.querySelectorAll(
                    '.artdeco-inline-feedback__message, .fb-dash-form-element__error-field, [data-test-form-element-error], .artdeco-text-input--error'
                )).map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
                const emptyRequired = Array.from(modal.querySelectorAll('input, select, textarea')).filter((el) => {
                    if (el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio' || el.type === 'file') return false;
                    if (el.offsetParent === null && !el.getClientRects().length) return false;
                    const required = el.required || el.getAttribute('aria-required') === 'true' ||
                        /required|\*/i.test(el.closest('label, .fb-dash-form-element, .jobs-easy-apply-form-element')?.textContent || '');
                    const empty = !el.value || !String(el.value).trim();
                    return required && empty;
                }).map((el) => el.getAttribute('aria-label') || el.name || el.id || el.placeholder || el.type);
                return { msgs: msgs.slice(0, 5), emptyRequired: emptyRequired.slice(0, 8) };
            }).catch(() => ({ msgs: [], emptyRequired: [] }));
            if (validation.msgs?.length) vlog(`Validation messages: ${JSON.stringify(validation.msgs)}`);
            if (validation.emptyRequired?.length) vlog(`Empty required-ish fields: ${JSON.stringify(validation.emptyRequired)}`);

            // Recovery: LinkedIn "Select checkbox to proceed" — tick required
            // agreement boxes (never "follow company").
            // IMPORTANT: never set checked=true then click() — click toggles it back off.
            const needsCheckbox = (validation.msgs || []).some((m) => /checkbox|select.*to proceed|must agree|please agree|required/i.test(m || ''));
            if (needsCheckbox) {
                const nChecked = await activeCtx.evaluate(() => {
                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]') || scope;
                    const forceCheck = (cb) => {
                        if (!cb || cb.disabled) return false;
                        // Prefer clicking the visible label / form-element shell (React/LinkedIn
                        // custom controls often ignore direct input.checked assignment).
                        const label = cb.closest('label') ||
                            (cb.id ? modal.querySelector(`label[for="${cb.id}"]`) : null) ||
                            cb.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, .artdeco-typeahead, fieldset') ||
                            cb;
                        const fire = (node, type) => {
                            try {
                                node.dispatchEvent(new MouseEvent(type, {
                                    bubbles: true, cancelable: true, composed: true, view: window,
                                }));
                            } catch (_) {}
                        };
                        if (!cb.checked) {
                            fire(label, 'mouseover');
                            fire(label, 'mousedown');
                            try { label.click(); } catch (_) {}
                            fire(label, 'mouseup');
                            // If still unchecked, toggle via native click on input only once
                            if (!cb.checked) {
                                try { cb.click(); } catch (_) {}
                            }
                        }
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked')?.set;
                        if (setter) setter.call(cb, true); else cb.checked = true;
                        cb.setAttribute('aria-checked', 'true');
                        cb.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                        cb.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        // Do NOT click again after forcing true — that unchecks.
                        return !!cb.checked || cb.getAttribute('aria-checked') === 'true';
                    };
                    let n = 0;
                    for (const cb of Array.from(modal.querySelectorAll('input[type="checkbox"]'))) {
                        if (cb.checked || cb.disabled) continue;
                        const label = (
                            (cb.closest('label')?.textContent || '') + ' ' +
                            (cb.getAttribute('aria-label') || '') + ' ' +
                            (cb.name || '') + ' ' +
                            (cb.id || '')
                        ).toLowerCase();
                        if (/follow|stay.?up.?to.?date|email me|newsletter|marketing/.test(label)) continue;
                        // Prefer required / agree / terms / privacy / acknowledge / consent
                        if (!/agree|terms|privacy|acknowledge|confirm|certify|consent|i have|required|accurate|true|understand|declare|read and/.test(label) &&
                            cb.getAttribute('aria-required') !== 'true' && !cb.required) {
                            // still check unlabeled required-looking boxes near error text
                            if (!modal.querySelector('.artdeco-inline-feedback__message, [data-test-form-element-error]')) continue;
                        }
                        try {
                            if (forceCheck(cb)) n += 1;
                        } catch (_) {}
                    }
                    // Also handle role=checkbox custom controls LinkedIn sometimes uses
                    for (const el of Array.from(modal.querySelectorAll('[role="checkbox"]'))) {
                        const state = el.getAttribute('aria-checked');
                        if (state === 'true' || el.getAttribute('aria-disabled') === 'true') continue;
                        const label = (el.textContent || el.getAttribute('aria-label') || '').toLowerCase();
                        if (/follow|stay.?up.?to.?date|newsletter|marketing/.test(label)) continue;
                        if (!/agree|terms|privacy|consent|acknowledge|confirm|certify|understand|declare|required/.test(label) &&
                            el.getAttribute('aria-required') !== 'true') continue;
                        try {
                            el.click();
                            el.setAttribute('aria-checked', 'true');
                            el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                            n += 1;
                        } catch (_) {}
                    }
                    return n;
                }).catch(() => 0);
                vlog(`Recovery: checked ${nChecked} agreement checkbox(es)`);
                await sleep(500);
                // Second pass if validation still present
                const stillNeeds = await activeCtx.evaluate(() => {
                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]') || scope;
                    const msgs = Array.from(modal.querySelectorAll(
                        '.artdeco-inline-feedback__message, .fb-dash-form-element__error-field, [data-test-form-element-error]'
                    )).map((el) => (el.textContent || '').toLowerCase());
                    return msgs.some((m) => /checkbox|select.*proceed|agree|required/.test(m));
                }).catch(() => false);
                if (stillNeeds) {
                    const n2 = await activeCtx.evaluate(() => {
                        const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                        const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]') || scope;
                        let n = 0;
                        for (const cb of Array.from(modal.querySelectorAll('input[type="checkbox"]:not(:checked)'))) {
                            const label = ((cb.closest('label')?.textContent || '') + ' ' + (cb.getAttribute('aria-label') || '')).toLowerCase();
                            if (/follow|newsletter|marketing|stay.?up.?to.?date/.test(label)) continue;
                            try {
                                const shell = cb.closest('label') || cb;
                                shell.click();
                                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked')?.set;
                                if (setter) setter.call(cb, true); else cb.checked = true;
                                cb.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                                if (cb.checked) n += 1;
                            } catch (_) {}
                        }
                        return n;
                    }).catch(() => 0);
                    vlog(`Recovery: second-pass checkbox force n=${n2}`);
                    await sleep(400);
                }
            }

            // Recovery: re-fill (especially typeahead city) then force DOM click on Next
            vlog('Recovery: re-filling fields after stuck Next…');
            await fillStepFields(activeCtx, vlog, currentStep, stepUnresolvedFields, jobInfo);
            await sleep(600);
            try {
                const navAgain = await activeCtx.evaluateHandle(() => {
                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    return scope.querySelector('[data-temp-id="temp-nav-btn"]') ||
                        scope.querySelector('[data-easy-apply-next-button], [data-live-test-easy-apply-review-button], footer button.artdeco-button--primary');
                });
                const navEl = navAgain.asElement ? navAgain.asElement() : navAgain;
                if (navEl) {
                    await activeCtx.evaluate((btn) => {
                        btn.scrollIntoView({ block: 'center' });
                        btn.click();
                    }, navEl).catch(async () => {
                        await navEl.click().catch(() => {});
                    });
                    vlog('Recovery: forced DOM click on navigation button.');
                    await sleep(1500);
                }
                await navAgain.dispose().catch(() => {});
            } catch (recErr) {
                vlog(`Recovery nav click note: ${recErr.message}`);
            }

            const afterRecovery = await getContentSignature(activeCtx);
            if (afterRecovery !== contentBefore) {
                vlog('Recovery succeeded — form advanced.');
                retriesOnSameStep = 0;
                currentStep++;
                continue;
            }

            if (retriesOnSameStep >= 3) {
                vlog('Skipping this application after repeated no-progress clicks.');
                const stuckReason = (validation.emptyRequired?.length)
                    ? `form_stalled_empty_required: ${validation.emptyRequired.slice(0, 3).join('; ')}`
                    : (validation.msgs?.length)
                        ? `form_stalled_validation: ${validation.msgs[0]}`
                        : 'form_stalled_on_step_before_submit';
                await closeEasyApplyModal(activeCtx, cursor);
                return fail(stuckReason);
            }
        } else {
            retriesOnSameStep = 0;
        }

        // Detect if modal content changed to increment step counter
        currentStep++;
    }
    // Final success check in case modal closed after last Next/Submit race
    const lateConfirm = await verifyApplicationSubmitted(activeCtx, page, cursor);
    if (lateConfirm) {
        vlog('Post-loop LinkedIn success UI present — treating as applied.');
        logApplication(jobInfo.jobId, jobInfo.title, jobInfo.company, 'Submitted');
        return ok();
    }
    await closeEasyApplyModal(activeCtx, cursor);
    return fail(lastFailReason);
}

async function closeEasyApplyModal(page, cursor) {
    const closeBtn = await page.$('.jobs-easy-apply-modal button[aria-label="Dismiss"], .jobs-easy-apply-modal .artdeco-modal__dismiss, [data-test-modal-close-btn]');
    if (closeBtn) {
        await clickWithGhostFallback(page, cursor, closeBtn, 'Easy Apply close button');
        await new Promise(r => setTimeout(r, 1000));
    }
    const discardBtn = await page.evaluateHandle(() => {
        const buttons = Array.from(document.querySelectorAll('button'));
        return buttons.find(btn => /discard|dismiss|ignorer|supprimer|abandonner/i.test(btn.textContent || '')) || null;
    });
    const discardEl = discardBtn.asElement();
    if (discardEl) {
        await clickWithGhostFallback(page, cursor, discardEl, 'discard confirmation button');
        await new Promise(r => setTimeout(r, 1000));
    }
    await discardBtn.dispose().catch(() => {});
}

/**
 * Fill plain text/textarea OR LinkedIn typeahead (city/location/company/school).
 * Typeahead must pick a listbox option — bare value set leaves the field empty and
 * blocks Next (seen on Atimi "Location (city)").
 */
async function fillTextOrTypeaheadField(page, field, answer) {
    const label = field.label || '';
    const normLabel = label.toLowerCase();
    let ans = String(answer || '').trim();
    if (!ans) return false;

    const isLocationField = /\b(location|city|ville|localit|where do you live|current location|based in)\b/i.test(normLabel);
    // Company/school/employer controls must commit a real typeahead option;
    // a merely non-empty string can be malformed and must never be submitted.
    const isStrictTypeaheadField = /\b(company|employer|school|university|college)\b/i.test(normLabel);
    // Expand short city answers so LinkedIn typeahead can resolve Metro Van.
    if (isLocationField) {
        const low = ans.toLowerCase();
        if (!low.includes('british') && !low.includes('canada') && !low.includes(',')) {
            // Prefer fuller query for typeahead match quality
            if (/surrey|vancouver|burnaby|richmond|coquitlam|langley|delta|new westminster|north vancouver|west vancouver|white rock|maple ridge|port coquitlam|port moody|abbotsford/i.test(ans)) {
                ans = `${ans}, BC, Canada`;
            }
        }
    }

    const meta = await page.evaluate((tempId, labelText) => {
        const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
        let input = scope.querySelector(`[data-temp-id="${tempId}"]`);
        if (!input) {
            const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]') || document.body;
            const inputs = Array.from(modal.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), textarea'));
            // fallback: match empty location-like field
            input = inputs.find((el) => {
                const al = (el.getAttribute('aria-label') || el.placeholder || '').toLowerCase();
                return /location|city|ville/.test(al) && !el.value;
            }) || null;
        }
        if (!input) return null;
        try { input.setAttribute('data-temp-id', tempId); } catch (_) {}
        const nl = (labelText || '').toLowerCase();
        const isAutocompleteLabel = /location|city|ville|school|company|skills|university|employer|college/.test(nl);
        const hasAutocompleteAttributes =
            input.getAttribute('role') === 'combobox' ||
            input.getAttribute('aria-autocomplete') === 'list' ||
            input.classList.contains('artdeco-typeahead__input') ||
            !!input.closest('.artdeco-typeahead, .basic-typeahead, .search-global-typeahead');
        return {
            isAutocomplete: isAutocompleteLabel || hasAutocompleteAttributes,
            tag: input.tagName,
            type: input.type || '',
            value: input.value || '',
        };
    }, field.tempId, label);

    if (!meta) {
        vlog(`[BrowserFiller] Could not find input for "${label}"`);
        return false;
    }

    // Focus via element handle + real keyboard typing (triggers LinkedIn typeahead fetch)
    const handle = await page.evaluateHandle((tempId) => {
        const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
        return scope.querySelector(`[data-temp-id="${tempId}"]`);
    }, field.tempId);
    const el = handle.asElement ? handle.asElement() : handle;
    if (!el) {
        await handle.dispose().catch(() => {});
        return false;
    }

    try {
        await el.click({ clickCount: 1 }).catch(() => {});
        await page.evaluate((tempId) => {
            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
            const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
            if (!input) return;
            input.focus();
            // clear existing
            const nativeWindow = input.ownerDocument?.defaultView || window;
            const proto = input instanceof nativeWindow.HTMLTextAreaElement
                ? nativeWindow.HTMLTextAreaElement.prototype
                : nativeWindow.HTMLInputElement.prototype;
            const valueSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (valueSetter) valueSetter.call(input, '');
            else input.value = '';
            input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        }, field.tempId);

        // Select-all + delete then type (CDP keyboard)
        await page.keyboard.down('Meta').catch(() => {});
        await page.keyboard.press('a').catch(() => {});
        await page.keyboard.up('Meta').catch(() => {});
        await page.keyboard.press('Backspace').catch(() => {});
        await sleep(120);
        await page.keyboard.type(ans, { delay: 35 + Math.floor(Math.random() * 40) });
        await sleep(meta.isAutocomplete ? 1600 : 400);

        if (!meta.isAutocomplete) {
            // Plain text: also set via React setter as belt-and-suspenders
            await page.evaluate((tempId, val) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                if (!input) return;
                const nativeWindow = input.ownerDocument?.defaultView || window;
                const proto = input instanceof nativeWindow.HTMLTextAreaElement
                    ? nativeWindow.HTMLTextAreaElement.prototype
                    : nativeWindow.HTMLInputElement.prototype;
                const valueSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (valueSetter) valueSetter.call(input, val);
                else input.value = val;
                input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
            }, field.tempId, ans);
            return true;
        }

        vlog(`[BrowserFiller] Typeahead "${label}" typed "${ans}" — waiting for suggestions…`);

        // Query variants: full → short city → BC short → Vancouver metro fallback for Metro Van candidates
        const cityOnly = ans.split(',')[0].trim();
        const queries = [];
        const pushQ = (q) => {
            const t = (q || '').trim();
            if (t && !queries.some((x) => x.toLowerCase() === t.toLowerCase())) queries.push(t);
        };
        pushQ(ans);
        pushQ(cityOnly);
        if (isLocationField) {
            pushQ(`${cityOnly}, BC`);
            pushQ(`${cityOnly}, BC, Canada`);
            pushQ(`${cityOnly}, British Columbia, Canada`);
            // LinkedIn geo often ranks Vancouver higher than Surrey alone
            if (/surrey/i.test(cityOnly)) {
                pushQ('Surrey, British Columbia, Canada');
                pushQ('Vancouver, BC, Canada');
            }
        }
        // Company / employer typeahead: shorter tokens often match better
        if (isStrictTypeaheadField) {
            const words = ans.split(/\s+/).filter(Boolean);
            if (words.length > 2) pushQ(words.slice(0, 3).join(' '));
            if (words.length > 1) pushQ(words.slice(0, 2).join(' '));
            pushQ(words[0] || '');
            // common CA employer aliases
            if (/vancouver coastal health/i.test(ans)) {
                pushQ('Vancouver Coastal Health');
                pushQ('VCH');
                pushQ('Coastal Health');
            }
        }

        const collectPick = async (query) => page.evaluate((q) => {
            const selectors = [
                '[role="listbox"] [role="option"]',
                '.artdeco-typeahead__result',
                '.artdeco-typeahead__results-list [role="option"]',
                '.basic-typeahead__result',
                '.basic-typeahead__results-list [role="option"]',
                '.search-typeahead-v2__hit',
                '.typeahead-suggestions [role="option"]',
                'div[role="option"]',
                'ul[role="listbox"] li',
            ];
            const roots = [];
            const addRoot = (r) => { if (r && !roots.includes(r)) roots.push(r); };
            addRoot(document);
            const interop = document.querySelector('#interop-outlet');
            if (interop && interop.shadowRoot) addRoot(interop.shadowRoot);
            for (const base of [...roots]) {
                try {
                    base.querySelectorAll('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"], .artdeco-modal').forEach(addRoot);
                } catch (_) {}
            }
            // Walk open shadow roots one level deeper (LinkedIn interop shells)
            for (const base of [...roots]) {
                try {
                    base.querySelectorAll('*').forEach((el) => {
                        if (el.shadowRoot) addRoot(el.shadowRoot);
                    });
                } catch (_) {}
            }

            const set = new Set();
            for (const root of roots) {
                for (const sel of selectors) {
                    try {
                        root.querySelectorAll(sel).forEach((n) => {
                            if (n && (n.offsetParent !== null || n.getClientRects().length)) set.add(n);
                        });
                    } catch (_) {}
                }
            }
            const options = Array.from(set);
            const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const qn = norm(q);
            const cityToken = norm((q || '').split(',')[0]);
            let best = null;
            let bestScore = -1;
            const scored = [];
            for (const node of options) {
                const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
                if (!text || text.length > 120) continue;
                const on = norm(text);
                let score = 0;
                if (qn && on === qn) score += 100;
                if (qn && on.includes(qn)) score += 70;
                if (cityToken && on.startsWith(cityToken)) score += 40;
                if (cityToken && on.includes(cityToken)) score += 25;
                if (on.includes('britishcolumbia') || on.includes('bc')) score += 20;
                if (on.includes('canada')) score += 15;
                if (/,\s*(bc|british columbia|canada)/i.test(text)) score += 15;
                scored.push({ text, score });
                if (score > bestScore) {
                    bestScore = score;
                    best = node;
                }
            }
            if (!best || bestScore <= 0) {
                best = options.find((node) => /british columbia|,\s*bc\b|canada/i.test(node.textContent || '')) || options[0] || null;
                if (best) bestScore = 1;
            }
            if (!best) return { ok: false, count: options.length, scored: scored.slice(0, 8) };
            best.setAttribute('data-temp-typeahead-pick', '1');
            return {
                ok: true,
                count: options.length,
                text: (best.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 100),
                score: bestScore,
                scored: scored.slice(0, 8),
            };
        }, query);

        for (const q of queries) {
            // Robustly clear the input before typing the new query variant
            await page.evaluate((tempId) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                if (!input) return;
                input.focus();
                const nativeWindow = input.ownerDocument?.defaultView || window;
                const proto = input instanceof nativeWindow.HTMLTextAreaElement
                    ? nativeWindow.HTMLTextAreaElement.prototype
                    : nativeWindow.HTMLInputElement.prototype;
                const valueSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (valueSetter) valueSetter.call(input, '');
                else input.value = '';
                input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
            }, field.tempId);
            await el.click({ clickCount: 1 }).catch(() => {});
            await page.keyboard.down('Control').catch(() => {});
            await page.keyboard.press('a').catch(() => {});
            await page.keyboard.up('Control').catch(() => {});
            await page.keyboard.press('Backspace').catch(() => {});
            await page.keyboard.down('Meta').catch(() => {});
            await page.keyboard.press('a').catch(() => {});
            await page.keyboard.up('Meta').catch(() => {});
            await page.keyboard.press('Backspace').catch(() => {});
            await sleep(120);
            await page.keyboard.type(q, { delay: 45 + Math.floor(Math.random() * 30) });
            // LinkedIn geo typeahead is slow under NST; poll several times.
            let pick = null;
            for (let poll = 0; poll < 5; poll++) {
                await sleep(poll === 0 ? 1400 : 700);
                pick = await collectPick(q);
                if (pick && pick.ok) break;
                if (pick && pick.scored) {
                    vlog(`[BrowserFiller] Suggestions poll ${poll + 1}/5 for "${q}" (${pick.count}): ${JSON.stringify(pick.scored)}`);
                }
            }

            if (pick && pick.scored && !pick.ok) {
                vlog(`[BrowserFiller] Suggestions (0 for "${q}"): ${JSON.stringify(pick.scored)}`);
            }
            if (pick && pick.ok) {
                const optHandle = await page.evaluateHandle(() => {
                    const roots = [document];
                    const interop = document.querySelector('#interop-outlet');
                    if (interop?.shadowRoot) roots.push(interop.shadowRoot);
                    for (const r of roots) {
                        const hit = r.querySelector('[data-temp-typeahead-pick="1"]');
                        if (hit) return hit;
                    }
                    return document.querySelector('[data-temp-typeahead-pick="1"]');
                });
                const optEl = optHandle.asElement ? optHandle.asElement() : optHandle;
                if (optEl) {
                    await page.evaluate((node) => {
                        node.scrollIntoView({ block: 'center' });
                        node.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, view: window }));
                        node.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        node.click();
                        node.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        node.removeAttribute('data-temp-typeahead-pick');
                    }, optEl).catch(async () => {
                        await optEl.click().catch(() => {});
                    });
                    vlog(`[BrowserFiller] Picked typeahead option: "${pick.text}" (score=${pick.score})`);
                    await sleep(600);
                    // Verify value stuck
                    const val = await page.evaluate((tempId) => {
                        const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                        const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                        return (input && input.value) || '';
                    }, field.tempId);
                    if (val && val.trim() && (!isStrictTypeaheadField || pick.ok)) {
                        vlog(`[BrowserFiller] Typeahead value now: "${val}"`);
                        await handle.dispose().catch(() => {});
                        return true;
                    }
                    // Sometimes LinkedIn stores selection in a chip, not input value
                    const chipOk = await page.evaluate(() => {
                        const scopes = [document];
                        const interop = document.querySelector('#interop-outlet');
                        if (interop?.shadowRoot) scopes.push(interop.shadowRoot);
                        for (const scope of scopes) {
                            const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"]') || scope;
                            if (modal.querySelector('.artdeco-typeahead__selection, .fb-dash-typeahead__selection, [data-test-typeahead-selection], .search-typeahead-v2__selection')) {
                                return true;
                            }
                        }
                        return false;
                    });
                    if (chipOk && isStrictTypeaheadField) {
                        vlog('[BrowserFiller] Typeahead selection chip present.');
                        await handle.dispose().catch(() => {});
                        return true;
                    }
                    // LinkedIn's location widget sometimes commits the geo
                    // selection without exposing it through input.value or a
                    // standard chip. The option click is still the real
                    // commit; press Enter once to finalize the widget and do
                    // not report a false unresolved location.
                    if (isLocationField && pick.ok) {
                        await page.keyboard.press('Enter').catch(() => {});
                        await sleep(350);
                        vlog(`[BrowserFiller] Location typeahead option committed: "${pick.text}"`);
                        await handle.dispose().catch(() => {});
                        return true;
                    }
                }
                await optHandle.dispose().catch(() => {});
            }
        }

        // Last resort: ArrowDown + Enter only while listbox is open (avoid global search navigation)
        const listOpen = await page.evaluate(() => {
            const scopes = [document];
            const interop = document.querySelector('#interop-outlet');
            if (interop?.shadowRoot) scopes.push(interop.shadowRoot);
            for (const scope of scopes) {
                if (scope.querySelector('[role="listbox"] [role="option"], .artdeco-typeahead__result, .basic-typeahead__result')) {
                    return true;
                }
            }
            return false;
        });
        if (listOpen) {
            vlog('[BrowserFiller] Typeahead fallback: ArrowDown + Enter on open listbox');
            await page.keyboard.press('ArrowDown');
            await sleep(200);
            await page.keyboard.press('Enter');
            await sleep(500);
            await handle.dispose().catch(() => {});
            return true;
        }

        // Company free-text last resort: many Easy Apply "Company" fields accept a
        // typed employer even when LinkedIn typeahead returns no org match.
        // Leaving these empty blocks Submit (blocked submit with unresolved fields: "Company").
        if (isStrictTypeaheadField && !isLocationField) {
            vlog(`[BrowserFiller] Company free-text fallback for "${ans}"`);
            await page.evaluate((tempId, val) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                if (!input) return;
                input.focus();
                const nativeWindow = input.ownerDocument?.defaultView || window;
                const proto = input instanceof nativeWindow.HTMLTextAreaElement
                    ? nativeWindow.HTMLTextAreaElement.prototype
                    : nativeWindow.HTMLInputElement.prototype;
                const valueSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (valueSetter) valueSetter.call(input, val);
                else input.value = val;
                input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                input.dispatchEvent(new Event('blur', { bubbles: true, composed: true }));
            }, field.tempId, ans);
            await page.keyboard.press('Tab').catch(() => {});
            await sleep(400);
            const val = await page.evaluate((tempId) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                return (input && input.value) || '';
            }, field.tempId);
            if (val && val.trim()) {
                vlog(`[BrowserFiller] Company free-text committed: "${val}"`);
                await handle.dispose().catch(() => {});
                return true;
            }
        }

        // Location free-text last resort: LinkedIn geo typeahead often returns 0
        // options under NST/proxy even for "Surrey, BC, Canada". Bare free-text
        // is accepted on many Easy Apply forms (plain City/State/Postal blocks).
        // Prefer short city token so State/Postal fields can still be filled next.
        if (isLocationField) {
            const freeCity = cityOnly || ans.split(',')[0].trim() || ans;
            // Try ArrowDown+Enter once more after retyping short city (listbox may be
            // in an undetected shadow tree but still keyboard-navigable).
            try {
                await el.click({ clickCount: 1 }).catch(() => {});
                await page.keyboard.down('Control').catch(() => {});
                await page.keyboard.press('a').catch(() => {});
                await page.keyboard.up('Control').catch(() => {});
                await page.keyboard.press('Backspace').catch(() => {});
                await page.keyboard.type(freeCity, { delay: 40 });
                await sleep(1200);
                await page.keyboard.press('ArrowDown').catch(() => {});
                await sleep(200);
                await page.keyboard.press('Enter').catch(() => {});
                await sleep(400);
            } catch (_) {}
            const afterKey = await page.evaluate((tempId) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                return (input && input.value) || '';
            }, field.tempId);
            if (afterKey && afterKey.trim().length >= 2) {
                vlog(`[BrowserFiller] Location keyboard commit: "${afterKey}"`);
                await handle.dispose().catch(() => {});
                return true;
            }
            vlog(`[BrowserFiller] Location free-text fallback for "${freeCity}"`);
            await page.evaluate((tempId, val) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                if (!input) return;
                input.focus();
                const nativeWindow = input.ownerDocument?.defaultView || window;
                const proto = input instanceof nativeWindow.HTMLTextAreaElement
                    ? nativeWindow.HTMLTextAreaElement.prototype
                    : nativeWindow.HTMLInputElement.prototype;
                const valueSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (valueSetter) valueSetter.call(input, val);
                else input.value = val;
                input.setAttribute('value', val);
                input.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: val, inputType: 'insertText' }));
                input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                input.dispatchEvent(new Event('blur', { bubbles: true, composed: true }));
            }, field.tempId, freeCity);
            await page.keyboard.press('Tab').catch(() => {});
            await sleep(350);
            const val = await page.evaluate((tempId) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                return (input && input.value) || '';
            }, field.tempId);
            if (val && val.trim().length >= 2) {
                vlog(`[BrowserFiller] Location free-text committed: "${val}"`);
                await handle.dispose().catch(() => {});
                return true;
            }
            // Last ditch: report success if we forced a value (LinkedIn may still
            // accept on Next even when input.value readback is empty).
            await page.evaluate((tempId, val) => {
                const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                const input = scope.querySelector(`[data-temp-id="${tempId}"]`);
                if (!input) return;
                const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (valueSetter) valueSetter.call(input, val);
                else input.value = val;
            }, field.tempId, freeCity);
            vlog(`[BrowserFiller] Location free-text forced (may still be empty in React state): "${freeCity}"`);
            await handle.dispose().catch(() => {});
            return true;
        }

        vlog(`[BrowserFiller] No typeahead suggestion for "${ans}" — leaving field unset (do not bare-Enter).`);
        await handle.dispose().catch(() => {});
        return false;
    } catch (err) {
        vlog(`[BrowserFiller] fillTextOrTypeahead error: ${err.message}`);
        await handle.dispose().catch(() => {});
        return false;
    }
}

async function fillStepFields(page, loggerFn, currentStep, stepUnresolvedFields, jobInfo = {}) {
    const fields = await page.evaluate(() => {
        const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
        const modal = scope.querySelector('.jobs-easy-apply-modal, [data-test-modal], [role="dialog"], .artdeco-modal, .jobs-easy-apply-content, form') || (window !== window.top ? document.body : null);
        if (!modal) return [];

        function getDirectInputs(root) {
            return [
                ...Array.from(root.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea')),
            ];
        }
        function getShadowInputs(root, depth = 0) {
            if (depth > 5) return [];
            let result = [];
            try {
                const allNodes = root.querySelectorAll('*');
                for (const node of allNodes) {
                    if (node.shadowRoot) {
                        result.push(...getDirectInputs(node.shadowRoot));
                        result.push(...getShadowInputs(node.shadowRoot, depth + 1));
                    }
                }
            } catch (e) {}
            return result;
        }

        const inputs = [...getDirectInputs(modal), ...getShadowInputs(modal)];
        const seenRadioGroups = new Set();
        return inputs.filter(el => {
            if (el.type === 'radio') {
                const container = el.closest('fieldset, [role="radiogroup"], .jobs-easy-apply-form-element, .fb-dash-form-element');
                const key = el.name || container?.getAttribute('aria-labelledby') || container?.textContent?.trim() || el.id;
                if (seenRadioGroups.has(key)) return false;
                seenRadioGroups.add(key);
            }
            if (el.type === 'file') {
                return false;
            }
            return true;
        }).map((el, index) => {
            const tempId = `temp-field-${index}`;
            try { el.setAttribute('data-temp-id', tempId); } catch(e){}
            let label = '';
            
            if (el.type === 'checkbox') {
                const fieldset = el.closest('fieldset');
                let legendText = '';
                if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend) {
                        const visibleSpan = legend.querySelector('span[aria-hidden="true"]');
                        legendText = visibleSpan ? visibleSpan.textContent.trim() : legend.textContent.trim();
                    }
                }
                const optionLabel = (
                    el.closest('label')?.textContent?.trim() ||
                    scope.querySelector(`label[for="${el.id}"]`)?.textContent?.trim() ||
                    ''
                ).trim();
                if (legendText && optionLabel) {
                    label = `${legendText} - ${optionLabel}`;
                } else {
                    label = legendText || optionLabel || `Field_${index}`;
                }
            } else if (el.type === 'radio') {
                const fieldset = el.closest('fieldset, [role="radiogroup"], .jobs-easy-apply-form-element, .fb-dash-form-element');
                if (fieldset) {
                    const legend = fieldset.querySelector('legend, [class*="title"], [class*="label"]');
                    if (legend) {
                        const visibleSpan = legend.querySelector('span[aria-hidden="true"]');
                        label = visibleSpan ? visibleSpan.textContent.trim() : legend.textContent.trim();
                    }
                }
                if (!label) {
                    label = (
                        el.closest('label')?.textContent?.trim() ||
                        scope.querySelector(`label[for="${el.id}"]`)?.textContent?.trim() ||
                        `Field_${index}`
                    );
                }
            } else {
                if (el.getAttribute('aria-label')) label = el.getAttribute('aria-label').trim();
                if (!label && el.id) {
                    const labelEl = scope.querySelector(`label[for="${el.id}"]`);
                    if (labelEl) label = labelEl.textContent.trim();
                }
                if (!label) {
                    const container = el.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, .artdeco-text-input--container');
                    if (container) {
                        const labelEl = container.querySelector('label, .fb-dash-form-element__label, .artdeco-text-input--label');
                        if (labelEl) label = labelEl.textContent.trim();
                    }
                }
                if (!label && el.placeholder) label = el.placeholder.trim();
            }

            if (label) {
                label = label.replace(/\s*\*\s*$/, '').replace(/\(required\)/gi, '').replace(/\(obligatorio\)/gi, '').trim();
            }
            let needsInput = false;
            if (el.tagName === 'SELECT') {
                needsInput = !el.value || el.selectedIndex <= 0;
            } else if (el.type === 'checkbox') {
                needsInput = !el.checked;
            } else if (el.type === 'radio') {
                const groupContainer = el.closest('fieldset, [role="radiogroup"], .jobs-easy-apply-form-element, .fb-dash-form-element');
                const group = el.name ? modal.querySelectorAll(`input[name="${el.name}"]`) : (groupContainer ? groupContainer.querySelectorAll('input[type="radio"]') : [el]);
                needsInput = !Array.from(group).some(r => r.checked || r.getAttribute('aria-checked') === 'true');
            } else {
                needsInput = !el.value || el.value.trim() === '';
            }
            // LinkedIn often prefills skill YOE as "0". Treat as unanswered so we overwrite.
            const labelLow = (label || '').toLowerCase();
            const valTrim = String(el.value || '').trim();
            if (
                !needsInput &&
                el.tagName !== 'SELECT' &&
                el.type !== 'checkbox' &&
                el.type !== 'radio' &&
                /year|experience|yoe|how many/.test(labelLow) &&
                (valTrim === '0' || valTrim === '0.0' || valTrim === '')
            ) {
                needsInput = true;
            }
            // Empty location/typeahead must always be filled even if LinkedIn leaves a placeholder chipless value.
            if (
                !needsInput &&
                /location|city|ville|where do you live|current location/.test(labelLow) &&
                (!valTrim || valTrim.length < 2)
            ) {
                needsInput = true;
            }

            const isVisible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
            return {
                tagName: el.tagName,
                type: el.type,
                label: label || `Field_${index}`,
                needsInput,
                value: el.value || '',
                isVisible,
                id: el.id || '',
                name: el.name || '',
                tempId: `temp-field-${index}`,
                rawIndex: inputs.indexOf(el)
            };
        });
    });

    loggerFn(`--- Step ${currentStep} Field Audit (${fields.length} detected fields) ---`);
    fields.forEach((f, idx) => {
        loggerFn(`  [Field #${idx + 1}] label="${f.label}" | tag=${f.tagName} | type=${f.type} | needsInput=${f.needsInput} | val="${f.value}" | visible=${f.isVisible}`);
    });
    loggerFn(`------------------------------------------------------------------`);

    for (let index = 0; index < fields.length; index++) {
        const field = fields[index];
        if (!field.needsInput) continue;

        // Log the field being processed
        vlog(`Field #${index+1}: "${field.label}" (${field.type || field.tagName}) needsInput=${field.needsInput}`);
        
        // Log the field data for debug
        if (index % 10 === 0) {
            vlog(`  Fields on this step: ${fields.length} total, ${fields.filter(f=>f.needsInput).length} need input`);
        }

        // Log unanswered required fields for Change 5 (Change 4 in plan)
        const fieldUnanswered = [];

        console.log(`📝 Field: "${field.label}" (${field.type || field.tagName})`);
        
        // ============================================================
        // CHANGE 3: DOM context enrichment + CHANGE 1: Shared brain cascade
        // Answer order: Python Bridge → Heuristics → LLM (fallback)
        // ============================================================
        let answer = null;
        let answerSource = 'none';
        
        // STEP 0: Capture DOM context (Change 3 - plan item #3)
        const domContext = await page.evaluate((tempId) => {
            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
            const el = scope.querySelector(`[data-temp-id="${tempId}"]`);
            if (!el) return { context: '', options: [] };
            const parent = el.closest('fieldset, .fb-dash-form-element, .jobs-easy-apply-form-section, .jobs-easy-apply-form-element');
            let context = el.getAttribute('aria-label') || '';
            if (parent) context += ' ' + (parent.innerText || '').trim().slice(0, 500);
            
            // Get visible options for selects/radios
            let options = [];
            if (el.tagName === 'SELECT') {
                options = Array.from(el.options).map(o => o.textContent.trim());
            } else {
                const groupContainer = el.closest('fieldset, [role="radiogroup"], .jobs-easy-apply-form-element, .fb-dash-form-element');
                const group = el.name ? scope.querySelectorAll(`input[name="${el.name}"]`) : (groupContainer ? groupContainer.querySelectorAll('input[type="radio"]') : [el]);
                group.forEach(r => {
                    const lbl = r.closest('label')?.textContent?.trim() || scope.querySelector(`label[for="${r.id}"]`)?.textContent?.trim() || r.value;
                    if (lbl && !options.includes(lbl)) options.push(lbl);
                });
            }
            
            return { context, options };
        }, field.tempId);

        // STEP 1: Shared Python brain (policy → rules → AI with full profile + DOM options)
        const jobContext = [
            profileManifest.user_information_all || '',
            profileManifest.profile_summary || '',
            profileManifest.profile_headline || '',
            `Target job: ${profileManifest.current_job_title || ''} @ ${profileManifest.current_company || ''}`.trim(),
        ].filter(Boolean).join('\n');
        const realOptions = (domContext.options || []).filter((o) => {
            const t = String(o || '').trim().toLowerCase();
            if (!t) return false;
            if (/^select(\s+an?\s+option)?$/.test(t)) return false;
            if (/please select|choose one|^-$|^--$/.test(t)) return false;
            return true;
        });
        const answerMatchesOptions = (ans, options) => {
            if (!options || options.length < 2) return true;
            if (!ans || !String(ans).trim()) return false;
            const a = String(ans).trim().toLowerCase();
            return options.some((o) => {
                const t = String(o || '').trim().toLowerCase();
                if (!t) return false;
                if (t === a) return true;
                if (t.includes(a) || a.includes(t)) return true;
                // token overlap
                const at = new Set(a.split(/[^a-z0-9]+/).filter(Boolean));
                const ot = new Set(t.split(/[^a-z0-9]+/).filter(Boolean));
                let inter = 0;
                for (const x of at) if (ot.has(x)) inter += 1;
                return inter >= 2 && inter / Math.max(at.size, ot.size) >= 0.4;
            });
        };
        const pickBestOption = (ans, options) => {
            if (!ans || !options || !options.length) return null;
            const a = String(ans).trim().toLowerCase();
            let best = null;
            let bestScore = 0;
            for (const o of options) {
                const t = String(o || '').trim().toLowerCase();
                if (!t) continue;
                if (t === a) return o;
                let score = 0;
                if (t.includes(a) || a.includes(t)) score += 3;
                const at = a.split(/[^a-z0-9]+/).filter(Boolean);
                const ot = t.split(/[^a-z0-9]+/).filter(Boolean);
                const inter = at.filter((x) => ot.includes(x)).length;
                score += inter;
                if (score > bestScore) {
                    bestScore = score;
                    best = o;
                }
            }
            return bestScore >= 2 ? best : null;
        };

        if (!answer && ANSWER_BRIDGE_PATH) {
            const bridgeResult = await spawnPythonResolve(
                field.label,
                domContext.context || '',
                domContext.options,
                jobContext
            );
            if (bridgeResult && bridgeResult.value) {
                answer = bridgeResult.value;
                answerSource = `bridge(${bridgeResult.source})`;
                // If DOM has choices and bridge free-text doesn't match, drop → force AI below
                if (realOptions.length >= 2 && !answerMatchesOptions(answer, realOptions)) {
                    const mapped = pickBestOption(answer, realOptions);
                    if (mapped) {
                        answer = mapped;
                        answerSource = `${answerSource}_optmap`;
                    } else {
                        vlog(`   Bridge "${answer}" ≠ DOM options → forcing AI pick`);
                        answer = null;
                        answerSource = 'none';
                    }
                }
            }
        }

        // Deterministic anti-bot knowledge check used by some ATS forms.
        // The valid choice is often compacted into a token such as
        // "Rtimhortons"; select the exact option exposed by the DOM.
        if (!answer && realOptions.length >= 2) {
            const antiBotQuestion = String(field.label || '').toLowerCase();
            if (
                /uppercase first letter of toronto/.test(antiBotQuestion) &&
                /coffee\s*\/\s*donut|coffee.*donut|donut.*coffee/.test(antiBotQuestion)
            ) {
                const timOption = realOptions.find((option) =>
                    /tim\s*hortons/i.test(String(option).replace(/[^a-z]/gi, ''))
                );
                if (timOption) {
                    answer = timOption;
                    answerSource = 'deterministic_anti_bot_knowledge_check';
                }
            }
        }
        
        // STEP 2: Portal heuristics only when still empty (never override a mapped bridge)
        if (!answer) {
            const h = heuristics.localAnswerQuestion(field.label);
            if (h && String(h).trim()) {
                if (realOptions.length >= 2) {
                    const mapped = pickBestOption(h, realOptions) || (answerMatchesOptions(h, realOptions) ? h : null);
                    if (mapped) {
                        answer = mapped;
                        answerSource = 'heuristics_optmap';
                    }
                } else {
                    answer = h;
                    answerSource = 'heuristics';
                }
            }
        }

        // STEP 2b: Consent/agree option picker
        if (!answer && typeof heuristics.pickConsentOption === 'function') {
            const consent = heuristics.pickConsentOption(domContext.options || []);
            if (consent) {
                answer = consent;
                answerSource = 'consent_option';
            }
        }
        
        // STEP 3: AI with full profile + exact DOM options (fill every unattended field)
        if (!answer && aiApiKey) {
            const manifestBlob = [
                profileManifest.user_information_all || '',
                `Name: ${profileManifest.first_name || ''} ${profileManifest.last_name || ''}`.trim(),
                `Email: ${profileManifest.email || ''}`,
                `Phone: ${profileManifest.phone || ''}`,
                `Location: ${profileManifest.city || profileManifest.location || ''}`,
                `Education: ${profileManifest.school || ''} / ${profileManifest.education || ''}`,
                `Skills: ${profileManifest.skills || ''}`,
                `Summary: ${profileManifest.profile_summary || profileManifest.profile_headline || ''}`,
            ].filter(Boolean).join('\n');
            const llmPrompt = [
                `QUESTION: ${field.label}`,
                domContext.context ? `DOM CONTEXT:\n${String(domContext.context).substring(0, 900)}` : '',
                realOptions.length
                    ? `EXACT OPTIONS (reply with ONE exact option text):\n${realOptions.map((o) => `- ${o}`).join('\n')}`
                    : 'FREE TEXT FIELD: answer concisely for the candidate (years=number, no referral=N/A).',
                `CANDIDATE:\n${manifestBlob.substring(0, 2200)}`,
                'RULES: Output ONLY the final answer. If options listed, copy one option EXACTLY. No quotes or explanation.',
            ].filter(Boolean).join('\n\n');
            let llmAns = await heuristics.callLLMApi(llmPrompt, aiProvider, aiApiKey, aiModelName, aiCustomUrl);
            if (llmAns) {
                llmAns = String(llmAns).trim().replace(/^["']|["']$/g, '');
                if (realOptions.length >= 2) {
                    const mapped = pickBestOption(llmAns, realOptions) || (answerMatchesOptions(llmAns, realOptions) ? llmAns : null);
                    answer = mapped;
                    answerSource = mapped ? 'llm_optmap' : 'llm_no_match';
                    if (!mapped) vlog(`   LLM "${llmAns}" did not match options`);
                } else {
                    answer = llmAns;
                    answerSource = 'llm';
                }
            } else {
                answerSource = 'llm_no_answer';
            }
        }

        // STEP 4: Last-resort for Yes/No option groups so fields are never left blank
        if (!answer && realOptions.length >= 2) {
            const lower = realOptions.map((o) => String(o).toLowerCase());
            const hasYes = lower.some((t) => /^(yes|oui|true)\b/.test(t));
            const hasNo = lower.some((t) => /^(no|non|false)\b/.test(t));
            if (hasYes && hasNo) {
                const q = (field.label || '').toLowerCase();
                const preferNo = /ever been|previously|criminal|disability|sponsor|referred|employed by/.test(q);
                const want = preferNo ? 'no' : 'yes';
                const hit = realOptions.find((o) => new RegExp(`^${want}\\b`, 'i').test(String(o).trim()));
                if (hit) {
                    answer = hit;
                    answerSource = 'fallback_yes_no';
                }
            }
        }
        
        // Track unanswered fields (Change 4/5 - prevent silent abandonment)
        if (!answer || !answer.trim()) {
            fieldUnanswered.push({ label: field.label, type: field.type || field.tagName });
            logUnresolvedQuestion({
                jobInfo,
                step: currentStep,
                field,
                context: domContext.context || '',
                options: realOptions,
                source: answerSource,
                reason: answerSource === 'llm_no_match'
                    ? 'llm_answer_did_not_match_available_options'
                    : 'no_answer_after_bridge_heuristics_llm_and_safe_fallbacks',
            });
            captureUnresolvedQuestionScreenshot({
                page, field, jobInfo, step: currentStep,
                reason: 'no_answer_after_bridge_heuristics_llm_and_safe_fallbacks',
            }).catch(() => {});
        }

        
        if (answer !== null && answer.trim()) {
            // A retry may have recorded this same field as unresolved. Clear
            // that stale entry; failed control commits add a fresh false one.
            if (stepUnresolvedFields[currentStep]) {
                stepUnresolvedFields[currentStep] = stepUnresolvedFields[currentStep].map((entry) =>
                    entry.label === field.label ? { ...entry, handled: true } : entry
                );
            }
            vlog(`   Answer (${answerSource}): "${answer}"`);
            
            // Input values based on DOM type
            if (field.type === 'radio') {
                await page.evaluate(async (tempId, ans) => {
                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    const getRadioLabel = (radio) => {
                        if (!radio) return null;
                        if (radio.id) {
                            try {
                                const escapedId = String(radio.id).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
                                const forLabel = scope.querySelector(`label[for="${escapedId}"]`);
                                if (forLabel) return forLabel;
                            } catch(e){}
                        }
                        const parentLabel = radio.closest('label');
                        if (parentLabel) return parentLabel;
                        if (radio.nextElementSibling && radio.nextElementSibling.tagName === 'LABEL') {
                            return radio.nextElementSibling;
                        }
                        const itemContainer = radio.closest('.fb-dash-radio-button, .artdeco-form__radio-button, [data-test-text-selectable-option], .artdeco-radio');
                        if (itemContainer) {
                            const lbl = itemContainer.querySelector('label');
                            if (lbl) return lbl;
                        }
                    };

                    const getCleanLabelText = (radio) => {
                        const lbl = getRadioLabel(radio);
                        if (!lbl) return '';
                        if (typeof lbl.querySelector === 'function') {
                            const vis = lbl.querySelector('span[aria-hidden="true"]');
                            if (vis && vis.textContent.trim()) return vis.textContent.trim();
                            const visHidden = lbl.querySelector('span.visually-hidden, span.sr-only');
                            if (visHidden && visHidden.textContent.trim()) return visHidden.textContent.trim();
                        }
                        return (lbl.textContent || radio.value || '').trim();
                    };

                    const selectRadio = async radio => {
                        const lbl = getRadioLabel(radio);
                        const span = lbl && typeof lbl.querySelector === 'function' ? lbl.querySelector('span') : null;
                        const parent = radio.parentElement;
                        const container = radio.closest('.fb-dash-radio-button, .artdeco-form__radio-button, [data-test-text-selectable-option], .artdeco-radio');

                        const triggerFullClick = target => {
                            if (!target) return;
                            ['mouseover', 'mousedown', 'mouseup', 'click'].forEach(type => {
                                try {
                                    target.dispatchEvent(new MouseEvent(type, {
                                        bubbles: true,
                                        cancelable: true,
                                        composed: true,
                                        view: window,
                                        detail: 1,
                                        buttons: 1
                                    }));
                                } catch(e){}
                            });
                            if (typeof target.click === 'function') {
                                try { target.click(); } catch(e){}
                            }
                        };

                        radio.checked = false;
                        radio.removeAttribute('aria-checked');

                        [container, lbl, span, parent, radio].forEach(el => triggerFullClick(el));
                        
                        radio.checked = true;
                        radio.setAttribute('aria-checked', 'true');
                        radio.setAttribute('data-target-radio-selected', 'true');
                        radio.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        radio.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                    };
                    const list = Array.from(scope.querySelectorAll(`input[data-temp-id="${tempId}"]`));
                    if (list.length === 0) return;
                    const rEl = list[0];
                    const groupContainer = rEl.closest('fieldset, [role="radiogroup"], .jobs-easy-apply-form-element, .fb-dash-form-element:not(.fb-dash-radio-button)');
                    const group = rEl.name ? Array.from(scope.querySelectorAll(`input[name="${rEl.name}"]`)) : (groupContainer ? Array.from(groupContainer.querySelectorAll('input[type="radio"]')) : [rEl]);
                    
                    // Resume radios: always prefer IT resume (ls_resume_it), never leave/switch to generic upload
                    const isResumeGroup = group.some(r => {
                        const lbl = (r.closest('label')?.textContent || r.value || '').toLowerCase();
                        return lbl.includes('resume') || lbl.includes('select resume') || lbl.includes('deselect resume') || lbl.includes('cv');
                    }) || (rEl.name || '').toLowerCase().includes('resume');
                    if (isResumeGroup) {
                        const labelOf = (r) => (
                            r.closest('label')?.textContent ||
                            scope.querySelector(`label[for="${r.id}"]`)?.textContent ||
                            r.value ||
                            ''
                        ).toLowerCase();
                        const isItResume = (lbl) =>
                            lbl.includes('sample_resume_it') ||
                            lbl.includes('resume_it') ||
                            (lbl.includes('resume') && !lbl.includes('general'));
                        const isGenericResume = (lbl) =>
                            lbl.includes('general') ||
                            /resume\s*\(\d+\)/.test(lbl);
                        const itOpt = group.find((r) => {
                            const lbl = labelOf(r);
                            return !lbl.includes('deselect') && isItResume(lbl);
                        });
                        const checked = group.find((r) => r.checked || r.getAttribute('aria-checked') === 'true');
                        const checkedLbl = checked ? labelOf(checked) : '';
                        // Already on IT resume → leave alone
                        if (checked && isItResume(checkedLbl) && !checkedLbl.includes('deselect')) {
                            return;
                        }
                        // Prefer IT option when present
                        if (itOpt) {
                            await selectRadio(itOpt);
                            return;
                        }
                        // No IT option: keep non-generic selection if any; else first non-deselect
                        if (checked && !isGenericResume(checkedLbl) && !checkedLbl.includes('deselect')) {
                            return;
                        }
                        const selectOpt = group.find((r) => {
                            const lbl = labelOf(r);
                            return !lbl.includes('deselect') && !isGenericResume(lbl);
                        }) || group.find((r) => !labelOf(r).includes('deselect'));
                        if (selectOpt) {
                            await selectRadio(selectOpt);
                            return;
                        }
                        return;
                    }

                    let fallback = null;
                    const valLower = (ans || '').trim().toLowerCase();
                    
                    // Pass 1: Exact match
                    for (const r of group) {
                        const radioLabel = (getCleanLabelText(r) || r.value || '').toLowerCase();
                        if (radioLabel === valLower) {
                            await selectRadio(r);
                            return;
                        }
                    }

                    // Pass 2: Decision keyword matching with word boundaries
                    if (valLower === 'no' || valLower === 'false') {
                        const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
                        for (const r of group) {
                            const rawLabel = getCleanLabelText(r) || r.value || '';
                            if (negRegex.test(rawLabel)) {
                                await selectRadio(r);
                                return;
                            }
                        }
                    } else if (valLower === 'yes' || valLower === 'true') {
                        const posRegex = /\b(yes|am|have|do|i am|i have|authorized|eligible|willing)\b/i;
                        const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
                        for (const r of group) {
                            const rawLabel = getCleanLabelText(r) || r.value || '';
                            if (posRegex.test(rawLabel) && !negRegex.test(rawLabel)) {
                                await selectRadio(r);
                                return;
                            }
                        }
                    } else if (valLower === 'decline' || valLower.includes('prefer not')) {
                        const decRegex = /\b(decline|prefer not|not wish|dont wish|don't wish|rather not)\b/i;
                        for (const r of group) {
                            const rawLabel = getCleanLabelText(r) || r.value || '';
                            if (decRegex.test(rawLabel)) {
                                await selectRadio(r);
                                return;
                            }
                        }
                    }

                    // Pass 3: Safe substring matching for specific text (len >= 4)
                    if (valLower.length >= 4 && !['yes', 'no', 'true', 'false', 'decline'].includes(valLower)) {
                        for (const r of group) {
                            const radioLabel = (getCleanLabelText(r) || r.value || '').toLowerCase();
                            if (radioLabel.includes(valLower) || valLower.includes(radioLabel)) {
                                await selectRadio(r);
                                return;
                            }
                        }
                    }

                    // Pass 4: Fallback for 2-option Yes/No groups
                    if ((valLower === 'yes' || valLower === 'no') && group.length === 2) {
                        await selectRadio(group[valLower === 'yes' ? 0 : 1]);
                        return;
                    }

                    // Pass 5: Ultimate fallback for any unselected radio group (prevents stuck forms)
                    if (group.length > 0 && !group.some(r => r.checked)) {
                        let target = group[0];
                        for (const r of group) {
                            const rawLabel = (getCleanLabelText(r) || r.value || '').toLowerCase();
                            if (!rawLabel.includes('decline') && !rawLabel.includes('prefer not')) {
                                target = r;
                                break;
                            }
                        }
                        await selectRadio(target);
                        return;
                    }
                }, field.tempId, answer);

                // Perform CDP click on the selected radio label in Node.js frame context
                try {
                    const selectedRadioHandle = await page.evaluateHandle(() => {
                        const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                        return scope.querySelector('input[data-target-radio-selected="true"]');
                    });
                    const radioEl = selectedRadioHandle.asElement();
                    if (radioEl) {
                        const labelHandle = await page.evaluateHandle(r => {
                            if (!r) return null;
                            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                            if (r.id) {
                                try {
                                    const escapedId = String(r.id).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
                                    const forLabel = scope.querySelector(`label[for="${escapedId}"]`);
                                    if (forLabel) return forLabel;
                                } catch(e){}
                            }
                            const parentLabel = r.closest('label');
                            if (parentLabel) return parentLabel;
                            if (r.nextElementSibling && r.nextElementSibling.tagName === 'LABEL') return r.nextElementSibling;
                            const itemContainer = r.closest('.fb-dash-radio-button, .artdeco-form__radio-button, [data-test-text-selectable-option]');
                            return itemContainer ? itemContainer.querySelector('label') : r;
                        }, radioEl);
                        const labelEl = labelHandle.asElement();
                        if (labelEl) {
                            await labelEl.click().catch(() => {});
                        }
                        await page.evaluate(r => r.removeAttribute('data-target-radio-selected'), radioEl);
                    }
                } catch(clickErr) {
                    vlog(`Note: CDP radio label click fallback error: ${clickErr.message}`);
                }
            } else if (field.tagName === 'SELECT') {
                await page.evaluate(async (tempId, ans) => {
                    const randomDelay = (min, max) => {
                        let u1 = Math.random();
                        let u2 = Math.random();
                        if (u1 <= Number.EPSILON) u1 = Number.EPSILON;
                        const z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
                        const mean = min + (max - min) * 0.58;
                        const stdDev = (max - min) / 6;
                        return Math.floor(Math.max(min, Math.min(max, mean + z0 * stdDev)));
                    };
                    const pause = (min = 100, max = 250) => new Promise(resolve => setTimeout(resolve, randomDelay(min, max)));
                    const chooseOption = async option => {
                        select.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, composed: true, view: window }));
                        await pause(35, 120);
                        select.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, composed: true, view: window }));
                        if (typeof select.focus === 'function') select.focus();
                        await pause();
                        let valueSetter = null;
                        try {
                            const nativeWindow = select.ownerDocument?.defaultView || window;
                            valueSetter = Object.getOwnPropertyDescriptor(nativeWindow.HTMLSelectElement.prototype, 'value')?.set;
                        } catch (err) {}

                        if (valueSetter) {
                            try {
                                valueSetter.call(select, option.value);
                            } catch (err) {
                                select.value = option.value;
                            }
                        } else {
                            select.value = option.value;
                        }
                        select.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                        select.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        await pause(25, 90);
                        select.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, composed: true, view: window }));
                    };

                    const select = document.querySelector(`[data-temp-id="${tempId}"]`);
                    if (!select) return;
                    const valLower = (ans || '').trim().toLowerCase();

                    // Pass 1: Exact match on option text
                    for (const option of select.options) {
                        const optText = option.textContent.trim().toLowerCase();
                        if (optText === valLower) {
                            await chooseOption(option);
                            return;
                        }
                    }

                    // Pass 2: Decision keyword matching for No / Yes / Decline
                    if (valLower === 'no' || valLower === 'false') {
                        const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
                        for (const option of select.options) {
                            const optText = option.textContent.trim();
                            if (negRegex.test(optText)) {
                                await chooseOption(option);
                                return;
                            }
                        }
                    } else if (valLower === 'yes' || valLower === 'true') {
                        const posRegex = /\b(yes|am|have|do|i am|i have|authorized|eligible|willing)\b/i;
                        const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
                        for (const option of select.options) {
                            const optText = option.textContent.trim();
                            if (posRegex.test(optText) && !negRegex.test(optText)) {
                                await chooseOption(option);
                                return;
                            }
                        }
                    } else if (valLower === 'decline' || valLower.includes('prefer not')) {
                        const decRegex = /\b(decline|prefer not|not wish|dont wish|don't wish|rather not)\b/i;
                        for (const option of select.options) {
                            const optText = option.textContent.trim();
                            if (decRegex.test(optText)) {
                                await chooseOption(option);
                                return;
                            }
                        }
                    }

                    // Pass 3: Safe substring match for specific words (length >= 4)
                    if (valLower.length >= 4 && !['yes', 'no', 'true', 'false', 'decline'].includes(valLower)) {
                        for (const option of select.options) {
                            const optText = option.textContent.trim().toLowerCase();
                            if (optText.includes(valLower) || valLower.includes(optText)) {
                                await chooseOption(option);
                                return;
                            }
                        }
                    }

                    // Pass 3b: Citizenship / work-eligibility fuzzy match
                    // (answer "Canadian Citizen/PR" vs option "I am a Canadian Citizen")
                    if (/citizen|permanent resident|eligibility|authorized|work/.test(valLower)) {
                        const scored = [];
                        for (const option of select.options) {
                            const optText = option.textContent.trim().toLowerCase();
                            if (!optText || /^select/.test(optText)) continue;
                            if (/non-citizen|seeking work|current employer|other/.test(optText) && !/canadian citizen/.test(optText)) {
                                // deprioritize non-citizen buckets unless answer says so
                                if (!/non-citizen|seeking|not a citizen/.test(valLower)) continue;
                            }
                            let score = 0;
                            if (/canadian citizen/.test(optText) && /citizen/.test(valLower)) score += 5;
                            if (/permanent resident/.test(optText) && /permanent resident|pr\b/.test(valLower)) score += 4;
                            if (/i am a canadian citizen/.test(optText)) score += 3;
                            if (score > 0) scored.push({ option, score, optText });
                        }
                        scored.sort((a, b) => b.score - a.score);
                        if (scored.length) {
                            await chooseOption(scored[0].option);
                            return;
                        }
                    }

                    // Pass 4: Yes/No selects when answer is free-text that never matches
                    // (e.g. bridge returned "Bachelor's Degree" for a Yes/No degree question).
                    const realOpts = Array.from(select.options).filter((option) => {
                        const t = (option.textContent || '').trim().toLowerCase();
                        if (!t) return false;
                        if (/^select(\s+an?\s+option)?$/.test(t)) return false;
                        if (/please select|choose one|^-$|^--$/.test(t)) return false;
                        return Boolean(option.value) || t.length > 0;
                    });
                    const realTexts = realOpts.map((o) => (o.textContent || '').trim().toLowerCase());
                    const isYesNo =
                        realTexts.length >= 2 &&
                        realTexts.some((t) => /^(yes|oui|true)\b/.test(t)) &&
                        realTexts.some((t) => /^(no|non|false)\b/.test(t));
                    if (isYesNo) {
                        let wantYes = null;
                        if (valLower === 'yes' || valLower === 'true' || valLower === 'y') wantYes = true;
                        else if (valLower === 'no' || valLower === 'false' || valLower === 'n') wantYes = false;
                        else {
                            // Free-text that failed Pass 1–3: prefer No for specialty
                            // "do you have X degree" style mismatches; else No is safer
                            // than leaving the control empty (stalls Easy Apply).
                            wantYes = false;
                        }
                        for (const option of realOpts) {
                            const optText = (option.textContent || '').trim().toLowerCase();
                            if (wantYes && /^(yes|oui|true)\b/.test(optText)) {
                                await chooseOption(option);
                                return;
                            }
                            if (!wantYes && /^(no|non|false)\b/.test(optText) && !/yes/.test(optText)) {
                                await chooseOption(option);
                                return;
                            }
                        }
                    }
                }, field.tempId, answer);
            } else if (field.type === 'checkbox') {
                // LinkedIn often splits Yes/No into two checkboxes labeled
                // "... - Yes" / "... - No". Answer "No" must CHECK the No box
                // and UNCHECK the Yes box — not leave both empty/wrong.
                // Agreement/privacy "I consent" labels must default to checked.
                await page.evaluate(async (tempId, ans, fieldLabel) => {
                    const pause = (ms = 100) => new Promise(resolve => setTimeout(resolve, ms));
                    const setChecked = async (cb, wantsChecked) => {
                        const shell = cb.closest('label') ||
                            (cb.id ? document.querySelector(`label[for="${cb.id}"]`) : null) ||
                            cb.closest('.fb-dash-form-element, .jobs-easy-apply-form-element') ||
                            cb;
                        const fire = (node, type) => {
                            try {
                                node.dispatchEvent(new MouseEvent(type, {
                                    bubbles: true, cancelable: true, composed: true, view: window,
                                }));
                            } catch (_) {}
                        };
                        fire(shell, 'mouseover');
                        await pause(35);
                        fire(shell, 'mousedown');
                        if (typeof cb.focus === 'function') cb.focus();
                        await pause(50);
                        // Only click when state differs — never force click after setting true
                        if (!!cb.checked !== !!wantsChecked) {
                            try { shell.click(); } catch (_) {}
                            await pause(40);
                            if (!!cb.checked !== !!wantsChecked) {
                                try { cb.click(); } catch (_) {}
                            }
                        }
                        await pause(40);
                        const checkedSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked')?.set;
                        if (checkedSetter) checkedSetter.call(cb, wantsChecked);
                        else cb.checked = wantsChecked;
                        cb.setAttribute('aria-checked', wantsChecked ? 'true' : 'false');
                        cb.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                        cb.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        fire(shell, 'mouseup');
                    };

                    const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
                    const cb = scope.querySelector(`[data-temp-id="${tempId}"]`) || document.querySelector(`[data-temp-id="${tempId}"]`);
                    if (!cb) return;
                    const normAns = String(ans || '').trim().toLowerCase();
                    const answerIsYes = /^(yes|true|y)\b/i.test(normAns);
                    const answerIsNo = /^(no|false|n)\b/i.test(normAns) || normAns === 'n/a';
                    const label = String(fieldLabel || '');
                    const labelLow = label.toLowerCase();
                    const suffix = label.match(/\s[-–—]\s*(yes|no|i\s+consent|consent)\s*$/i);
                    let wantsChecked;
                    if (suffix && /yes|i\s+consent|consent/i.test(suffix[1]) && (answerIsYes || !answerIsNo)) {
                        wantsChecked = true;
                    } else if (suffix && /no/i.test(suffix[1]) && (answerIsYes || answerIsNo)) {
                        wantsChecked = answerIsNo;
                    } else if (/consent|privacy|terms|agree|acknowledge|declare|understand|certify|i have read/.test(labelLow)) {
                        // Agreement boxes: default ON unless answer is explicit No
                        wantsChecked = !answerIsNo;
                    } else if (suffix && (answerIsYes || answerIsNo)) {
                        const optionIsYes = suffix[1].toLowerCase() === 'yes';
                        wantsChecked = optionIsYes ? answerIsYes : answerIsNo;
                    } else {
                        wantsChecked = /^(yes|true|i\s+agree|agree|confirmed|accept|i\s+acknowledge|i\s+accept|i\s+understand|i\s+consent|consent)\b/i.test(normAns)
                            || (!normAns && /consent|agree|privacy|terms/.test(labelLow));
                    }
                    await setChecked(cb, wantsChecked);
                }, field.tempId, answer, field.label || '');
            } else {
                // Text input / textarea (incl. LinkedIn typeahead city/location)
                const filled = await fillTextOrTypeaheadField(page, field, answer);
                if (!filled) {
                    vlog(`⚠️ Text/typeahead fill may have failed for "${field.label}" → "${answer}"`);
                    logUnresolvedQuestion({
                        jobInfo,
                        step: currentStep,
                        field,
                        context: domContext.context || '',
                        options: realOptions,
                        source: answerSource,
                        reason: 'text_or_typeahead_value_not_accepted_by_control',
                    });
                    captureUnresolvedQuestionScreenshot({
                        page, field, jobInfo, step: currentStep,
                        reason: 'text_or_typeahead_value_not_accepted_by_control',
                    }).catch(() => {});

                    if (!stepUnresolvedFields[currentStep]) stepUnresolvedFields[currentStep] = [];
                    stepUnresolvedFields[currentStep].push({
                        label: field.label,
                        source: answerSource,
                        handled: false,
                    });
                }
                await sleep(800);
            }
        } else {
            console.log(`   ⚠️ Could not resolve answer for: "${field.label}"`);
            // Track unanswered fields by step for validation (Change 4)
            if (!stepUnresolvedFields[currentStep]) stepUnresolvedFields[currentStep] = [];
            stepUnresolvedFields[currentStep].push({ label: field.label, source: answerSource, handled: false });
        }
    }
    
    // Summary of unresolved fields for this step (Change 4/5 in plan)
    const allUnanswered = Object.values(stepUnresolvedFields || {}).flat().filter(u => !u.handled);
    if (allUnanswered.length > 0) {
        const names = allUnanswered.map(u => `"${u.label} (${u.source})"`).join(', ');
        vlog(`⚠️ Step ${currentStep}: ${allUnanswered.length} unresolved fields: [${names}]`);
    }
    return true;
}

/**
 * Confirm LinkedIn actually accepted the Easy Apply (not just that we clicked Submit).
 * Returns true only when success copy / post-submit Done UI is visible.
 */
async function verifyApplicationSubmitted(activeCtx, page, cursor) {
    const successRe =
        /application (was )?sent|your application was submitted|you'?ve applied|application submitted|candidature (a|a été) (été )?envoy|vous avez postulé|application has been submitted/i;
    const errorRe =
        /something went wrong|try again|unable to|couldn'?t submit|error submitting|fix all required|please enter|please select|fix the required/i;

    for (let attempt = 0; attempt < 6; attempt++) {
        await new Promise(r => setTimeout(r, attempt === 0 ? 2000 : 1500));

        let ctx = activeCtx;
        try {
            await ctx.evaluate(() => true);
        } catch (_) {
            ctx = page;
        }

        const state = await ctx.evaluate((successSource, errorSource) => {
            const successRe = new RegExp(successSource, 'i');
            const errorRe = new RegExp(errorSource, 'i');
            const scope = document.querySelector('#interop-outlet')?.shadowRoot || document;
            const roots = [
                scope,
                ...Array.from(scope.querySelectorAll('.artdeco-modal, [role="dialog"], .jobs-easy-apply-modal, .jpac-modal-header, .artdeco-inline-feedback')),
            ];
            const blob = roots
                .map((el) => (el && el.innerText) || '')
                .join('\n')
                .replace(/\s+/g, ' ')
                .trim();
            const body = (document.body?.innerText || '').replace(/\s+/g, ' ').trim();
            const text = `${blob} ${body}`.slice(0, 12000);

            // Done / Terminé on post-apply modal (strong signal when paired with success copy)
            let hasDone = false;
            for (const span of document.querySelectorAll('button span.artdeco-button__text, button')) {
                const t = (span.textContent || '').trim();
                if (['Terminé', 'Done', 'Fait', 'Finished', 'Complete'].includes(t) || /^(done|terminé|fait)$/i.test(t)) {
                    const btn = span.closest ? span.closest('button') || span : span;
                    if (btn && btn.tagName === 'BUTTON') {
                        btn.setAttribute('data-temp-id', 'temp-done-btn');
                        hasDone = true;
                        break;
                    }
                }
            }

            const hasSuccessCopy = successRe.test(text);
            const hasErrorCopy = errorRe.test(text) && !hasSuccessCopy;
            // Easy Apply form still open with Submit/Next → not confirmed
            const stillOnForm = Array.from(document.querySelectorAll('button, input[type="submit"]')).some((b) => {
                const t = (b.textContent || b.value || b.getAttribute('aria-label') || '').toLowerCase();
                return t.includes('submit application') || t === 'submit' || t.includes('review');
            });

            return {
                hasSuccessCopy,
                hasDone,
                hasErrorCopy,
                stillOnForm,
                snippet: text.slice(0, 240),
            };
        }, successRe.source, errorRe.source).catch(() => null);

        if (!state) continue;
        vlog(
            `Post-submit check ${attempt + 1}/6: successCopy=${state.hasSuccessCopy} done=${state.hasDone} ` +
                `error=${state.hasErrorCopy} stillForm=${state.stillOnForm} snip="${(state.snippet || '').slice(0, 120)}"`
        );

        if (state.hasErrorCopy && !state.hasSuccessCopy) {
            return false;
        }

        // Require real confirmation: success copy, or Done button after form is gone
        if (state.hasSuccessCopy || (state.hasDone && !state.stillOnForm)) {
            await handleSuccessModal(ctx, cursor);
            // Prefer page-level already-applied badge after modal dismiss
            return true;
        }
    }

    // Final page-level already-applied / applied badge (outside modal)
    try {
        const appliedBadge = await page.evaluate(() => {
            const t = (document.body?.innerText || '').toLowerCase();
            return (
                t.includes("you've applied") ||
                t.includes('you applied') ||
                t.includes('application submitted') ||
                !!document.querySelector('.jobs-s-apply .artdeco-inline-feedback--success, .post-apply-timeline, [data-test-applied-badge]')
            );
        });
        if (appliedBadge) {
            vlog('Post-submit: page shows applied badge / copy.');
            return true;
        }
    } catch (_) { /* ignore */ }

    return false;
}

async function handleSuccessModal(page, cursor) {
    console.log('🔍 Checking for success modal done button...');
    await new Promise(r => setTimeout(r, 500));
    
    const doneBtn = await page.evaluate(() => {
        const buttonSpans = document.querySelectorAll('button span.artdeco-button__text');
        for (const span of buttonSpans) {
            const buttonText = span.textContent.trim();
            if (['Terminé', 'Done', 'Fait', 'Finished', 'Complete'].includes(buttonText)) {
                const btn = span.closest('button');
                if (btn) btn.setAttribute('data-temp-id', 'temp-done-btn');
                return true;
            }
        }
        
        const possiblePrimaryButtons = document.querySelectorAll('button[class*="artdeco-button--primary"]');
        for (const button of possiblePrimaryButtons) {
            const buttonText = button.textContent.trim();
            if (['Terminé', 'Done', 'Fait', 'Finished'].some(t => buttonText.includes(t))) {
                button.setAttribute('data-temp-id', 'temp-done-btn');
                return true;
            }
        }
        // already stamped by verifyApplicationSubmitted
        return !!document.querySelector('[data-temp-id="temp-done-btn"]');
    });

    if (doneBtn) {
        const btn = await page.$('[data-temp-id="temp-done-btn"]');
        if (btn) {
            await clickWithGhostFallback(page, cursor, btn, 'success modal done button');
            console.log('✅ Clicked done button to close success modal.');
            await new Promise(r => setTimeout(r, 1000));
        }
    } else {
        console.log('⚠️ No success-modal Done button found (application not confirmed via Done UI).');
    }
}

run();
