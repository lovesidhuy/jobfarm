// extension/service-worker.js
// This file acts as a wrapper for Chrome's Manifest V3 service worker requirement.
// Firefox continues to use background.scripts in Manifest V3.

try {
    importScripts('browser-polyfill.js', 'config.js', 'background.js');
    console.log('✅ Service worker successfully imported scripts');
} catch (e) {
    console.error('❌ Service worker failed to import scripts:', e);
}
