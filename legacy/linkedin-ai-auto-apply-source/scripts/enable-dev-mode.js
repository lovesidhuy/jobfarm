const fs = require('fs');
const path = require('path');

const prefPath = '/Users/lovepreet/Documents/apps/jobfarm/legacy/linkedin-ai-auto-apply-source/chrome-profile-linkedin/Default/Preferences';
if (!fs.existsSync(prefPath)) {
    console.error('Preferences file not found.');
    process.exit(1);
}

try {
    const prefs = JSON.parse(fs.readFileSync(prefPath, 'utf8'));
    if (!prefs.extensions) {
        prefs.extensions = {};
    }
    if (!prefs.extensions.ui) {
        prefs.extensions.ui = {};
    }
    prefs.extensions.ui.developer_mode = true;

    fs.writeFileSync(prefPath, JSON.stringify(prefs, null, 2), 'utf8');
    console.log('Successfully set extensions.ui.developer_mode = true in Preferences');
} catch (e) {
    console.error('Error modifying Preferences:', e);
}
