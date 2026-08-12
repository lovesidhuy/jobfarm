// =============================================
// content/backend-api.js — Local bridge communication
// =============================================
// Handles communication between page content scripts
// and background automation runtime.
// =============================================

const BACKEND_URL = (typeof AUTOMATION_CONFIG !== 'undefined' ? AUTOMATION_CONFIG.BACKEND_URL : 'http://127.0.0.1:5001');

/**
 * Retrieve local automation settings from extension storage.
 */
function getExtensionAuth() {
    return new Promise((resolve) => {
        browserAPI.storage.local.get(['authToken', 'userPlan', 'aiEnabled']).then(res => {
            resolve({
                token: res.authToken || 'local_token',
                plan: res.userPlan || 'unlimited',
                aiEnabled: typeof res.aiEnabled === 'boolean' ? res.aiEnabled : true
            });
        }).catch(() => resolve({ token: 'local_token', plan: 'unlimited', aiEnabled: true }));
    });
}

function sanitizeToken(raw) {
    if (!raw) return null;
    return raw.startsWith('Bearer ') ? raw.slice(7) : raw;
}

// Register current tab as active LinkedIn tab
function registerAsLinkedInTab() {
    browserAPI.runtime.sendMessage({ type: 'SET_LINKEDIN_TAB' }).then(response => {
        console.log('✅ LinkedIn tab registered:', response);
    }).catch(e => console.error('❌ Failed to register LinkedIn tab:', e));
}

// Update application state in background runner
function updateApplicationState(isRunning, progress = null) {
    const message = {
        type: 'SET_APPLICATION_STATE',
        isRunning: isRunning
    };
    if (progress) {
        message.progress = progress;
    }

    browserAPI.runtime.sendMessage(message).then(response => {
        console.log('📊 Automation status updated:', response);
    }).catch(e => console.error('❌ Failed to update automation state:', e));
}

// Play notification sound when manual intervention is needed
function playNotificationSound() {
    browserAPI.runtime.sendMessage({ type: 'PLAY_NOTIFICATION_SOUND' }).then(response => {
        console.log('🔊 Notification triggered:', response);
    }).catch(e => console.error('❌ Failed to trigger notification sound:', e));
}

// Sync profile data to local background storage
async function syncProfile(fields) {
    console.log('CONTENT: Syncing candidate fields to local background:', Object.keys(fields));

    const filteredFields = filterSensitiveData(fields, 'syncProfile');
    const processedFields = {};
    Object.entries(filteredFields).forEach(([key, value]) => {
        processedFields[key] = extractExactValue(value, key);
    });

    try {
        const response = await browserAPI.runtime.sendMessage({
            type: 'SYNC_PROFILE',
            fields: processedFields
        });
        console.log('CONTENT: Profile sync acknowledged:', response);
    } catch (error) {
        console.error('CONTENT: Profile sync error:', error);
    }
}

// Request answers from local background heuristics / solver
async function fillFromServer(questions) {
    if (!questions || questions.length === 0) {
        return {};
    }

    const filteredQuestions = filterSensitiveQuestions(questions, 'fillFromServer');
    if (filteredQuestions.length === 0) {
        console.log('CONTENT: Sensitive questions skipped');
        return {};
    }

    console.log(`CONTENT: Resolving ${filteredQuestions.length} form questions locally`);

    try {
        const result = await browserAPI.runtime.sendMessage({
            type: 'FILL_FROM_SERVER',
            questions: filteredQuestions
        });

        if (result && !result.error) {
            if (result.compressedProfile && Object.keys(result.compressedProfile).length > 0) {
                const extensionFields = {};
                Object.entries(result.compressedProfile).forEach(([key, value]) => {
                    extensionFields[`field_${key}`] = value;
                });

                if (Object.keys(extensionFields).length > 0) {
                    await browserAPI.storage.local.set(extensionFields);
                    console.log('CONTENT: Profile saved in local storage');
                }
            }

            const answers = result.answers || {};
            console.log(`CONTENT: Resolved ${Object.keys(answers).length} answers`);
            return answers;
        } else {
            const errMsg = result ? result.error : 'Unknown response';
            console.warn('⚠️ Local solver error:', errMsg);
            return {};
        }
    } catch (error) {
        console.warn('⚠️ Communication error with local background runner:', error.message);
        return {};
    }
}
