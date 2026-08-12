console.log("🛠️ [LinkedIn Automation] content/utils.js: Initializing utilities...");
// =============================================
// content/utils.js — Shared utilities & globals
// =============================================
// Loaded first among content scripts. Sets up
// browserAPI, appState, speed control, and helpers.
// =============================================

// Cross-browser API compatibility
const browserAPI = typeof browser !== 'undefined' ? browser : chrome;

// Redirect isolated world logs to the main world so Puppeteer can see them
(function bridgeLogsToMainWorld() {
    const originalLog = console.log;
    const originalWarn = console.warn;
    const originalError = console.error;

    function formatArg(arg) {
        if (arg instanceof Error) return arg.stack || arg.message;
        if (typeof arg === 'object') {
            try {
                return JSON.stringify(arg);
            } catch (_) {
                return String(arg);
            }
        }
        return String(arg);
    }

    console.log = function(...args) {
        originalLog.apply(console, args);
        const text = args.map(formatArg).join(' ');
        window.postMessage({ type: 'UFH_ISOLATED_LOG', level: 'log', text }, '*');
    };

    console.warn = function(...args) {
        originalWarn.apply(console, args);
        const text = args.map(formatArg).join(' ');
        window.postMessage({ type: 'UFH_ISOLATED_LOG', level: 'warn', text }, '*');
    };

    console.error = function(...args) {
        originalError.apply(console, args);
        const text = args.map(formatArg).join(' ');
        window.postMessage({ type: 'UFH_ISOLATED_LOG', level: 'error', text }, '*');
    };
})();

// Prevent multiple injections — all subsequent content/ files check this flag
if (window.__UFH_CONTENT_LOADED__) {
    console.log('⚠️ [UFH] Content script already loaded, skipping...');
}

// Set the guard flag immediately
window.__UFH_CONTENT_LOADED__ = true;

// Simple global state
const appState = {
    isPaused: false,
    currentIndex: 0,
    totalJobs: 0,
    appliedJobs: [],
    aborted: false,
    rejections: [],
    
    // Production Runner settings
    maxApplicationsPerRun: 15,
    maxFailuresBeforeStop: 3,
    consecutiveFailures: 0,
    applicationsSubmittedThisRun: 0,
    directJobResult: null
};

const pendingTimeouts = [];
const HUMAN_ACTION_DELAY_MIN_MS = 30000;
const HUMAN_ACTION_DELAY_MAX_MS = 60000;

function randomHumanPacingMs(min = HUMAN_ACTION_DELAY_MIN_MS, max = HUMAN_ACTION_DELAY_MAX_MS) {
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

function scheduleNext(fn, delay) {
    const id = setTimeout(fn, delay);
    pendingTimeouts.push(id);
    return id;
}

// ==== Speed control (delay multiplier) ====
let delayMultiplier = 1; // 1 = default speed

// Load speed preference from storage
browserAPI.storage.local.get(['processingSpeed']).then(res => {
    const stored = parseFloat(res.processingSpeed);
    if (!isNaN(stored) && stored >= 0) {
        delayMultiplier = stored;
    }
});

// Helper sleep that respects speed multiplier
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms * delayMultiplier));
}

async function humanPacingDelay(label = 'between actions', min = HUMAN_ACTION_DELAY_MIN_MS, max = HUMAN_ACTION_DELAY_MAX_MS) {
    const delay = randomHumanPacingMs(min, max);
    console.log(`⏱️ Gaussian-adjusted human pacing delay (${label}): ${Math.round(delay / 1000)}s`);
    await sleep(delay);
}

// Patch setTimeout globally so all existing calls respect multiplier
(function () {
    const _setTimeout = window.setTimeout.bind(window);
    window.setTimeout = (handler, timeout = 0, ...args) => _setTimeout(handler, timeout * delayMultiplier, ...args);
})();

// Helper to block until user resumes
async function waitUntilResumed() {
    while (appState.isPaused) {
        await sleep(200);
    }
}

// Utility function to wait for element by id
function waitForElementById(id, timeout = 5000) {
    return new Promise((resolve, reject) => {
        const element = document.getElementById(id);
        if (element) {
            resolve(element);
            return;
        }

        const observer = new MutationObserver(() => {
            const element = document.getElementById(id);
            if (element) {
                observer.disconnect();
                resolve(element);
            }
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });

        setTimeout(() => {
            observer.disconnect();
            reject(new Error(`Element with id "${id}" not found within ${timeout}ms`));
        }, timeout);
    });
}

// Utility function to wait for element by selector
function waitForElement(selector, timeout = 5000) {
    return new Promise((resolve, reject) => {
        const element = document.querySelector(selector);
        if (element) {
            resolve(element);
            return;
        }

        const observer = new MutationObserver(() => {
            const element = document.querySelector(selector);
            if (element) {
                observer.disconnect();
                resolve(element);
            }
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });

        setTimeout(() => {
            observer.disconnect();
            reject(new Error(`Element "${selector}" not found within ${timeout}ms`));
        }, timeout);
    });
}

// Function to remove duplicate patterns in text
function removeDuplicatePatterns(text) {
    if (!text) return text;

    // Pattern 1: Handle exact word sequence duplicates like "Email addressEmail address"
    const words = text.split(/\s+/);
    if (words.length % 2 === 0) {
        const halfLength = words.length / 2;
        const firstHalf = words.slice(0, halfLength).join(' ');
        const secondHalf = words.slice(halfLength).join(' ');
        if (firstHalf.toLowerCase() === secondHalf.toLowerCase()) {
            return firstHalf;
        }
    }

    // Pattern 2: Handle character-level exact duplicates like "EmailEmail"
    const textLength = text.length;
    if (textLength % 2 === 0) {
        const halfLength = textLength / 2;
        const firstHalf = text.substring(0, halfLength);
        const secondHalf = text.substring(halfLength);
        if (firstHalf.toLowerCase() === secondHalf.toLowerCase()) {
            return firstHalf;
        }
    }

    // Pattern 3: Handle partial overlaps
    for (let i = 1; i < words.length; i++) {
        const currentWord = words[i].toLowerCase();
        for (let j = 0; j < i; j++) {
            if (words[j].toLowerCase() === currentWord) {
                let matchLength = 0;
                let k = 0;
                while (j + k < i &&
                    i + k < words.length &&
                    words[j + k].toLowerCase() === words[i + k].toLowerCase()) {
                    matchLength++;
                    k++;
                }
                if (matchLength > 0 && matchLength >= Math.min(i - j, words.length - i)) {
                    return words.slice(0, i).join(' ');
                }
            }
        }
    }

    // Pattern 4: Remove consecutive duplicate words only
    const uniqueWords = [];
    let lastWord = '';
    for (const word of words) {
        const cleanWord = word.toLowerCase().replace(/[^\w]/g, '');
        if (cleanWord !== lastWord && cleanWord.length > 0) {
            uniqueWords.push(word);
            lastWord = cleanWord;
        }
    }

    return uniqueWords.join(' ');
}

// Helper to normalize question strings for matching
function normalizeLabel(str) {
    return str.toLowerCase()
        .normalize('NFD').replace(/\p{Diacritic}/gu, '')
        .replace(/[^a-z0-9]+/g, '')
        .trim();
}

// Helper function to extract exact values from ranges (especially salary)
function extractExactValue(answer, fieldKey) {
    if (!answer || typeof answer !== 'string') return answer;

    const isSalaryField = fieldKey.toLowerCase().includes('salario') ||
        fieldKey.toLowerCase().includes('salary') ||
        fieldKey.toLowerCase().includes('compensation') ||
        fieldKey.toLowerCase().includes('sueldo') ||
        fieldKey.toLowerCase().includes('remuneration');

    if (isSalaryField) {
        const rangePattern = /(\d+(?:,\d{3})*(?:\.\d+)?)\s*[-–—]\s*(\d+(?:,\d{3})*(?:\.\d+)?)/;
        const match = answer.match(rangePattern);
        if (match) {
            const lowerBound = match[1].replace(/,/g, '');
            console.log(`💰 Extracted salary lower bound: "${answer}" -> "${lowerBound}"`);
            return lowerBound;
        }

        const kRangePattern = /(\d+(?:\.\d+)?)\s*k\s*[-–—]\s*(\d+(?:\.\d+)?)\s*k/i;
        const kMatch = answer.match(kRangePattern);
        if (kMatch) {
            const lowerBoundK = parseFloat(kMatch[1]) * 1000;
            console.log(`💰 Extracted salary from k notation: "${answer}" -> "${lowerBoundK}"`);
            return lowerBoundK.toString();
        }
    }

    return answer;
}

// Security helper function
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Dev-mode debug logger — only logs when DEV_MODE is true
// Usage: devLog('CATEGORY', 'message', optionalData)
function devLog(tag, ...msg) {
    if (typeof UFH_CONFIG !== 'undefined' && UFH_CONFIG.DEV_MODE) {
        console.log(`[UFH:${tag}]`, ...msg);
    }
}

// Easing and chunk-based human scrolling simulation
async function scrollLikeHuman(element) {
    if (!element) return;
    
    try {
        const rect = element.getBoundingClientRect();
        const targetY = window.scrollY + rect.top - (window.innerHeight / 2);
        const startY = window.scrollY;
        const distance = targetY - startY;
        
        if (Math.abs(distance) < 50) return; // already close enough
        
        const steps = randomHumanPacingMs(5, 10);
        for (let i = 1; i <= steps; i++) {
            const t = i / steps;
            const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
            const currentY = startY + (distance * ease);
            window.scrollTo(0, currentY);
            
            const delay = randomHumanPacingMs(30, 80);
            await new Promise(resolve => setTimeout(resolve, delay * (typeof delayMultiplier !== 'undefined' ? delayMultiplier : 1)));
        }
    } catch (err) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

// Simulated human-like click event sequence
async function clickLikeHuman(element) {
    if (!element) return;

    try {
        await scrollLikeHuman(element);
        await new Promise(resolve => setTimeout(resolve, randomHumanPacingMs(100, 300)));

        const dispatchWithPause = async (type, EventClass = MouseEvent) => {
            element.dispatchEvent(new EventClass(type, {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window
            }));
            await new Promise(resolve => setTimeout(resolve, randomHumanPacingMs(20, 90)));
        };

        await dispatchWithPause('mouseenter');
        await dispatchWithPause('mouseover');
        await dispatchWithPause('mousemove');
        await dispatchWithPause('mousedown');
        if (typeof element.focus === 'function') element.focus();
        await new Promise(resolve => setTimeout(resolve, randomHumanPacingMs(35, 110)));
        element.click();
        await dispatchWithPause('mouseup');
        element.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (err) {
        console.error("clickLikeHuman failed, falling back to direct click:", err);
        element.click();
    }
}

console.log('🚀 LinkedIn AI Auto Apply: utils loaded');
devLog('INIT', `Content scripts loaded. DEV_MODE=${UFH_CONFIG.DEV_MODE}, URL=${window.location.href.substring(0, 60)}`);
