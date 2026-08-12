const fs = require('fs');
const path = require('path');

const prefPath = '/Users/lovepreet/Documents/apps/jobfarm/legacy/linkedin-ai-auto-apply-source/chrome-profile-linkedin/Default/Preferences';
if (!fs.existsSync(prefPath)) {
    console.error('Preferences file not found at:', prefPath);
    process.exit(1);
}

try {
    const prefs = JSON.parse(fs.readFileSync(prefPath, 'utf8'));
    console.log('Extensions preference keys:');
    const ext = prefs.extensions || {};
    console.log(JSON.stringify(ext, null, 2));

    console.log('\nChecking other keys:');
    const findKeys = (obj, search, currentPath = '') => {
        for (const key in obj) {
            const path = currentPath ? `${currentPath}.${key}` : key;
            if (key.toLowerCase().includes(search.toLowerCase())) {
                console.log(`Found: ${path} =`, typeof obj[key] === 'object' ? '[Object]' : obj[key]);
            }
            if (obj[key] && typeof obj[key] === 'object') {
                findKeys(obj[key], search, path);
            }
        }
    };
    findKeys(prefs, 'developer');
    findKeys(prefs, 'extension');
} catch (e) {
    console.error('Error reading/parsing preferences:', e);
}
