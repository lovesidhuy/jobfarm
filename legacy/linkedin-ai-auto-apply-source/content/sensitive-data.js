// =============================================
// content/sensitive-data.js — Privacy filtering
// =============================================
// Detects and filters sensitive personal data
// (emails, phone numbers) before sending to APIs.
// =============================================

// Function to detect if a field contains sensitive personal information
function isSensitiveField(fieldKey, fieldValue) {
    if (!fieldKey) return false;

    // Helper to strip accents for robust matching (e.g., "Teléfono móvil" -> "telefono movil")
    const stripAccents = (str) => {
        return str ? String(str).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase() : '';
    };

    const keyLower = stripAccents(fieldKey);

    const sensitiveKeywords = [
        'email', 'correo', 'e-mail', 'mail', 'courriel', 'correo electronico', 'e-mail-adresse',
        'phone', 'telefono', 'telephone', 'tel', 'celular', 'movil', 'mobile', 'handy', 'mobiltelefon',
        'numero', 'number', 'contact', 'contacto'
    ];

    const isSensitiveKey = sensitiveKeywords.some(keyword => keyLower.includes(keyword));

    // Check if the value looks like an email
    const emailPattern = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b/;
    const isEmailValue = fieldValue ? emailPattern.test(fieldValue) : false;

    // Check if the value looks like a phone number
    const phonePatterns = [
        /^\+?[\d\s\-\(\)]{7,15}$/,
        /^\+?\d{1,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}$/,
        /^\(\d{3}\)\s?\d{3}[\s\-]?\d{4}$/,
        /^\d{3}[\s\-]?\d{3}[\s\-]?\d{4}$/,
        /^\d{10}$/
    ];
    const isPhoneValue = fieldValue ? phonePatterns.some(pattern => pattern.test(String(fieldValue).replace(/\s/g, ''))) : false;

    const result = isSensitiveKey || isEmailValue || isPhoneValue;

    if (result) {
        console.log('🔒 SENSITIVE DATA DETECTED:', {
            fieldKey,
            fieldValue: '[REDACTED]',
            reason: isSensitiveKey ? 'sensitive key' : isEmailValue ? 'email pattern' : 'phone pattern'
        });
    }

    return result;
}

// Function to filter out sensitive data from objects
function filterSensitiveData(data, logContext = 'Unknown') {
    if (!data || typeof data !== 'object') return data;

    const filtered = {};
    let removedCount = 0;

    Object.entries(data).forEach(([key, value]) => {
        if (isSensitiveField(key, value)) {
            console.log(`🔒 ${logContext}: Removing sensitive field "${key}" from data`);
            removedCount++;
        } else {
            filtered[key] = value;
        }
    });

    if (removedCount > 0) {
        console.log(`🔒 ${logContext}: Filtered out ${removedCount} sensitive fields`);
    }

    return filtered;
}

// Function to filter sensitive questions from arrays
function filterSensitiveQuestions(questions, logContext = 'Unknown') {
    if (!Array.isArray(questions)) return questions;

    const filtered = questions.filter(question => {
        if (isSensitiveField(question, '')) {
            console.log(`🔒 ${logContext}: Removing sensitive question "${question}" from list`);
            return false;
        }
        return true;
    });

    const removedCount = questions.length - filtered.length;
    if (removedCount > 0) {
        console.log(`🔒 ${logContext}: Filtered out ${removedCount} sensitive questions`);
    }

    return filtered;
}
