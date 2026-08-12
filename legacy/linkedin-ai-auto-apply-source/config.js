// =============================================
// Local configuration for LinkedIn Automation Engine
// =============================================

const DEV_MODE = false;

const _BACKEND = 'http://127.0.0.1:5001';
const _FRONTEND = 'http://localhost:3000';
const _DOMAIN = 'localhost';
const _PATTERN = '*://localhost/*';

const AUTOMATION_CONFIG = Object.freeze({
    DEV_MODE,

    // Local QA / Form Answer Backend API
    BACKEND_URL: _BACKEND,

    // Local Dashboard / Monitor
    DASHBOARD_BASE_URL: _FRONTEND,
    DASHBOARD_BILLING_URL: `${_FRONTEND}/status`,
    DASHBOARD_LOGIN_URL: `${_FRONTEND}/status`,
    DASHBOARD_REGISTER_URL: `${_FRONTEND}/status`,

    // Domain matching
    DASHBOARD_DOMAIN: _DOMAIN,
    DASHBOARD_TAB_PATTERN: _PATTERN,

    VERSION: '1.0.0',
});

// Backward compatibility alias for internal content scripts
const UFH_CONFIG = AUTOMATION_CONFIG;
