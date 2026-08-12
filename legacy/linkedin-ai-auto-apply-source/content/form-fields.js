// =============================================
// content/form-fields.js — Form field utilities
// =============================================
// Functions for inspecting, labeling, and
// manipulating LinkedIn form fields.
// =============================================

// Function to extract clean field labels (unified approach)
function extractCleanLabel(input, index) {
    let label = '';

    // Priority 0 (radio/checkbox only): Fieldset legend — the actual question text
    // For radio/checkbox inputs, label[for=id] gives the option text (Yes/No),
    // not the question. The legend holds the real question.
    if (input.type === 'radio' || input.type === 'checkbox') {
        const fieldset = input.closest('fieldset');
        if (fieldset) {
            const legend = fieldset.querySelector('legend');
            if (legend) {
                // Get only the visible text, skip visually-hidden duplicates
                const visibleSpan = legend.querySelector('span[aria-hidden="true"]');
                if (visibleSpan) {
                    label = visibleSpan.textContent.trim().replace(/\s*\*\s*$/, '');
                } else {
                    label = legend.textContent.trim().replace(/\s*\*\s*$/, '');
                }
                devLog('LABEL', `P0 fieldset legend for ${input.type}: "${label.substring(0, 60)}"`);
            }
        }
    }

    // Priority 1: aria-label
    if (!label && input.getAttribute('aria-label')) {
        label = input.getAttribute('aria-label').trim();
    }
    // Priority 2: Associated label element
    if (!label && input.id) {
        const labelEl = document.querySelector(`label[for="${input.id}"]`);
        if (labelEl) {
            label = labelEl.textContent.trim().replace(/\s*\*\s*$/, '');
        }
    }
    // Priority 3: Parent label
    if (!label) {
        const parentLabel = input.closest('label');
        if (parentLabel) {
            label = parentLabel.textContent.trim().replace(/\s*\*\s*$/, '');
        }
    }
    // Priority 4: Legend for fieldsets (non-radio/checkbox fallback)
    if (!label) {
        const fieldset = input.closest('fieldset');
        if (fieldset) {
            const legend = fieldset.querySelector('legend');
            if (legend) {
                label = legend.textContent.trim().replace(/\s*\*\s*$/, '');
            }
        }
    }
    // Priority 5: Previous sibling label text
    if (!label) {
        const container = input.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, .artdeco-text-input--container');
        if (container) {
            const labelEl = container.querySelector('label, .fb-dash-form-element__label, .artdeco-text-input--label');
            if (labelEl) {
                label = labelEl.textContent.trim().replace(/\s*\*\s*$/, '');
            }
        }
    }
    // Priority 6: Placeholder
    if (!label && input.placeholder) {
        label = input.placeholder.trim();
    }

    // Clean up the label
    if (label) {
        // Step 1: Remove "required" indicator text
        label = label.replace(/\s*\*\s*$/, '')
            .replace(/^\s*\*\s*/, '')
            .replace(/\s*\(required\)\s*/gi, '')
            .replace(/\s*\(obligatorio\)\s*/gi, '')
            .replace(/\s*\(requis\)\s*/gi, '');

        // Step 2: Trim whitespace
        label = label.trim();

        // Step 3: Remove duplicate patterns
        label = removeDuplicatePatterns(label);

        // Step 4: Limit length
        if (label.length > 100) {
            label = label.substring(0, 100) + '...';
        }
    }

    // Fallback to a better generic name
    if (!label || label.length < 2) {
        if (input.type === 'email') {
            label = 'Email Address';
        } else if (input.type === 'tel') {
            label = 'Phone Number';
        } else if (input.type === 'text' && input.name?.includes('phone')) {
            label = 'Phone Number';
        } else if (input.type === 'text' && input.name?.includes('name')) {
            label = 'Name';
        } else {
            label = `${input.type || input.tagName.toLowerCase()}_field_${index}`;
        }
    }

    return label;
}

// Function to extract clean field information
function extractFieldInfo(input, index) {
    const label = extractCleanLabel(input, index);
    devLog('FIELDS', `extractFieldInfo[${index}] type=${input.type || input.tagName} label="${label}"`);
    const type = input.type || input.tagName.toLowerCase();
    const value = getFieldValue(input);

    const fieldInfo = {
        label: label,
        type: type,
        value: value,
        required: input.required || input.getAttribute('aria-required') === 'true'
    };

    if (input.type === 'radio') {
        fieldInfo.options = getRadioOptions(input);
    } else if (input.tagName === 'SELECT') {
        fieldInfo.options = getSelectOptions(input);
    } else if (input.type === 'checkbox') {
        fieldInfo.checkboxInfo = getCheckboxInfo(input);
    }

    return fieldInfo;
}

// Function to get field value based on type
function getFieldValue(input) {
    if (input.type === 'checkbox') {
        return input.checked ? 'Yes' : 'No';
    } else if (input.type === 'radio') {
        const radioGroup = getRadioGroup(input);
        const selectedRadio = Array.from(radioGroup).find(radio => radio.checked);
        if (selectedRadio) {
            const radioLabel = getRadioLabel(selectedRadio);
            return radioLabel || selectedRadio.value || 'Selected';
        }
        return 'None selected';
    } else if (input.tagName === 'SELECT') {
        const selectedOption = input.options[input.selectedIndex];
        return selectedOption ? selectedOption.textContent.trim() : '';
    }
    return input.value || '';
}

// Function to get radio button options
function getRadioOptions(input) {
    const radioGroup = getRadioGroup(input);
    return Array.from(radioGroup).map((radio, index) => {
        const label = getRadioLabel(radio) || `Option ${index + 1}`;
        return {
            value: radio.value,
            label: label,
            selected: radio.checked
        };
    });
}

// Function to get radio button label
function getRadioLabel(radio) {
    return (radio.id ? document.querySelector(`label[for="${radio.id}"]`)?.textContent?.trim()?.replace(/\s*\*\s*$/, '') : null) ||
        radio.getAttribute('aria-label') ||
        radio.closest('label')?.textContent?.trim()?.replace(/\s*\*\s*$/, '') ||
        radio.nextElementSibling?.textContent?.trim() ||
        radio.previousElementSibling?.textContent?.trim() ||
        radio.value;
}

function getRadioGroup(input) {
    const fieldsetRadios = input.closest('fieldset')?.querySelectorAll('input[type="radio"]');
    if (fieldsetRadios && fieldsetRadios.length > 0) {
        return fieldsetRadios;
    }

    if (input.name) {
        return document.querySelectorAll(`input[name="${CSS.escape(input.name)}"]`);
    }

    return [input];
}

function getRadioGroupKey(input, index) {
    const fieldset = input.closest('fieldset');
    if (fieldset) {
        return fieldset.id || fieldset.getAttribute('data-test-form-element') || fieldset.textContent?.trim()?.slice(0, 120) || `fieldset-${index}`;
    }
    return input.name || input.id || `radio-${index}`;
}

function localGaussianPacingMs(min, max) {
    const lower = Math.min(min, max);
    const upper = Math.max(min, max);
    let u1 = Math.random();
    let u2 = Math.random();
    if (u1 <= Number.EPSILON) u1 = Number.EPSILON;

    const z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
    const mean = lower + (upper - lower) * 0.58;
    const stdDev = (upper - lower) / 6;
    return Math.floor(Math.max(lower, Math.min(upper, mean + z0 * stdDev)));
}

async function fieldMicroDelay(min = 80, max = 220) {
    const delay = typeof randomHumanPacingMs === 'function'
        ? randomHumanPacingMs(min, max)
        : localGaussianPacingMs(min, max);
    await new Promise(resolve => setTimeout(resolve, delay));
}

function setNativeInputProperty(input, property, value) {
    const nativeWindow = input.ownerDocument?.defaultView || window;
    const prototype = input instanceof nativeWindow.HTMLSelectElement
        ? nativeWindow.HTMLSelectElement.prototype
        : nativeWindow.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, property)?.set;
    if (setter) {
        setter.call(input, value);
    } else {
        input[property] = value;
    }
}

async function dispatchControlPointerSequence(target, options = {}) {
    if (!target) return;
    const includeClick = options.includeClick !== false;
    const dispatchMouse = async type => {
        target.dispatchEvent(new MouseEvent(type, {
            bubbles: true,
            cancelable: true,
            composed: true,
            view: window
        }));
        await fieldMicroDelay(25, 95);
    };

    await dispatchMouse('mouseover');
    await dispatchMouse('mousemove');
    await dispatchMouse('mousedown');
    if (typeof target.focus === 'function') target.focus();
    await fieldMicroDelay(45, 130);
    if (includeClick) target.click();
    await dispatchMouse('mouseup');
}

async function setCheckboxChecked(input, shouldCheck) {
    await dispatchControlPointerSequence(input, { includeClick: input.checked !== shouldCheck });
    setNativeInputProperty(input, 'checked', shouldCheck);
    input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
}

async function setSelectValueWithEvents(input, value) {
    input.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, composed: true, view: window }));
    await fieldMicroDelay(30, 100);
    input.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, composed: true, view: window }));
    if (typeof input.focus === 'function') input.focus();
    await fieldMicroDelay(100, 250);
    setNativeInputProperty(input, 'value', value);
    input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    input.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    await fieldMicroDelay(25, 90);
    input.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, composed: true, view: window }));
}

async function selectRadio(radio) {
    const radioLabelNorm = normalizeLabel(getRadioLabel(radio) || radio.value || '');
    const fieldset = radio.closest('fieldset');
    const matchingFieldsetLabel = Array.from(fieldset?.querySelectorAll('label') || [])
        .find(label => normalizeLabel(label.textContent || '') === radioLabelNorm);
    const visibleLabel = (radio.id ? document.querySelector(`label[for="${radio.id}"]`) : null) ||
        matchingFieldsetLabel;
    const clickTarget = visibleLabel ||
        radio.closest('label') ||
        radio.parentElement ||
        radio;

    console.log(`🎯 Selecting radio "${getRadioLabel(radio)}" via ${clickTarget.tagName}${clickTarget.id ? '#' + clickTarget.id : ''}`);

    await dispatchControlPointerSequence(clickTarget);
    setNativeInputProperty(radio, 'checked', true);
    radio.setAttribute('checked', '');
    radio.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true }));
    radio.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    radio.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
}

// Function to get select options
function getSelectOptions(select) {
    return Array.from(select.options).map(option => ({
        value: option.value,
        label: option.textContent.trim(),
        selected: option.selected
    }));
}

// Function to get checkbox information
function getCheckboxInfo(input) {
    const checkboxLabel = input.closest('label')?.textContent?.trim()?.replace(/\s*\*\s*$/, '') ||
        input.nextElementSibling?.textContent?.trim() ||
        input.getAttribute('aria-label') ||
        'Checkbox option';

    return [{
        value: input.value || 'on',
        label: checkboxLabel,
        checked: input.checked
    }];
}

// Function to gather form fields from current step
function gatherFormFields(step) {
    const fields = [];
    devLog('FIELDS', '--- gatherFormFields START ---');
    devLog('FIELDS', `Searching in element: ${step.tagName}.${step.className?.substring(0, 40) || ''}`);

    const inputs = step.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea');
    const processedRadioGroups = new Set();

    inputs.forEach((input, index) => {
        if (input.offsetParent === null) return; // Skip hidden elements

        // Skip radio buttons we've already processed
        if (input.type === 'radio') {
            const radioGroupKey = getRadioGroupKey(input, index);
            if (processedRadioGroups.has(radioGroupKey)) return;
            processedRadioGroups.add(radioGroupKey);
        }

        const label = extractCleanLabel(input, index);
        if (!shouldExcludeField(label)) {
            fields.push(extractFieldInfo(input, index));
        }
    });

    devLog('FIELDS', `gatherFormFields found ${fields.length} fields:`);
    fields.forEach((f, i) => devLog('FIELDS', `  [${i}] label="${f.label}" type=${f.type} value="${String(f.value).substring(0, 30)}"`));

    return fields;
}

// Function to check if a field should be excluded from form data
function shouldExcludeField(label) {
    if (!label) return true;

    const lowerLabel = label.toLowerCase();
    const excludePatterns = [
        'city', 'ciudad', 'ville', 'stadt',
        'state', 'province', 'estado', 'región',
        'country', 'país', 'pays', 'land',
        'zip', 'postal', 'código postal',
        'resume', 'cv', 'curriculum',
        'cover letter', 'carta de presentación',
        'upload', 'attach', 'file',
        'search', 'buscar'
    ];

    return excludePatterns.some(pattern => lowerLabel.includes(pattern));
}

// Function to check if a field needs input
async function checkIfFieldNeedsInput(input) {
    const isInvalid = input.getAttribute('aria-invalid') === 'true' ||
        (input.validity && input.willValidate && !input.validity.valid);
    if (isInvalid) return true;

    // If the parent container already shows a visible inline error, the field
    // needs to be re-filled even if it has a value (e.g. autocomplete with
    // unrecognised text that LinkedIn marks invalid after a Next click).
    const container = input.closest(
        '.fb-dash-form-element, .jobs-easy-apply-form-element, .artdeco-text-input--container'
    );
    if (container) {
        const errEl = container.querySelector('.artdeco-inline-feedback--error, .fb-dash-error');
        if (errEl && errEl.offsetParent !== null) return true;  // visible error
    }

    if (input.type === 'radio') {
        const radioGroup = getRadioGroup(input);
        return !Array.from(radioGroup).some(radio => radio.checked);
    } else if (input.type === 'checkbox') {
        return !input.checked;
    } else if (input.tagName === 'SELECT') {
        return !input.value || input.selectedIndex <= 0;
    } else {
        return !input.value || input.value.trim() === '';
    }
}

// Function to prompt user based on field type
async function promptUserForField(input, label) {
    if (window.UFH_UNATTENDED_MODE !== false) {
        console.log(`🤖 Unattended mode: skipping manual prompt for "${label}"`);
        return null;
    }

    playNotificationSound();
    const modeText = 'APPLY MODE';

    if (input.type === 'radio') {
        const radioGroup = getRadioGroup(input);
        const radioLabels = Array.from(radioGroup).map((radio, index) => {
            return radio.closest('label')?.textContent?.trim() ||
                radio.nextElementSibling?.textContent?.trim() ||
                document.querySelector(`label[for="${radio.id}"]`)?.textContent?.trim() ||
                radio.value ||
                `Option ${index + 1}`;
        });
        const options = radioLabels.map((lbl, i) => `${i + 1}. ${lbl}`).join('\n');

        const choice = prompt(`🔊 MANUAL INPUT REQUIRED [${modeText}]\n\nField: ${label}\n\n${options}\n\nEnter the number of your choice (or Cancel to skip):`);
        if (choice && !isNaN(choice)) {
            const selectedIndex = parseInt(choice) - 1;
            if (selectedIndex >= 0 && selectedIndex < radioGroup.length) {
                // Return the label text (e.g. "Yes") so the saved answer is human-readable
                return radioLabels[selectedIndex];
            }
        }
        return null;

    } else if (input.tagName === 'SELECT') {
        const options = Array.from(input.options).slice(1).map((option, index) => {
            return `${index + 1}. ${option.textContent.trim()}`;
        }).join('\n');

        if (options) {
            const choice = prompt(`🔊 MANUAL INPUT REQUIRED [${modeText}]\n\nField: ${label}\n\n${options}\n\nEnter the number of your choice (or Cancel to skip):`);
            if (choice && !isNaN(choice)) {
                const selectedIndex = parseInt(choice);
                if (selectedIndex >= 1 && selectedIndex < input.options.length) {
                    return input.options[selectedIndex].value;
                }
            }
        } else {
            return prompt(`🔊 MANUAL INPUT REQUIRED [${modeText}]\n\nField: ${label}\n\nPlease provide a value (or Cancel to skip):\n\n(This will be saved for future applications)`);
        }
        return null;

    } else {
        return prompt(`🔊 MANUAL INPUT REQUIRED [${modeText}]\n\nField: ${label}\n\nPlease provide an answer (or Cancel to skip):\n\n(This will be saved for future applications)`);
    }
}

// Function to set field value based on type
async function setFieldValue(input, value) {
    if (!value) return;

    if (input.type === 'radio') {
        const radioGroup = getRadioGroup(input);
        const isResumeGroup = Array.from(radioGroup).some(r => {
            const lbl = (getRadioLabel(r) || r.value || '').toLowerCase();
            return lbl.includes('resume') || lbl.includes('select resume') || lbl.includes('deselect resume');
        }) || (input.name || '').toLowerCase().includes('resume');
        if (isResumeGroup && Array.from(radioGroup).some(r => r.checked)) {
            console.log("📄 Resume radio group already has a selected resume, skipping.");
            return;
        }
        const valLower = normalizeLabel(value);
        
        // Pass 1: Try exact match
        for (let i = 0; i < radioGroup.length; i++) {
            const radio = radioGroup[i];
            const radioLabel = normalizeLabel(getRadioLabel(radio) || radio.value || '');
            if (radioLabel === valLower) {
                await selectRadio(radio);
                return;
            }
        }

        // Pass 2: Handle Decline / No / Yes decision keywords with word boundaries
        if (valLower === 'no' || valLower === 'false') {
            const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
            for (let i = 0; i < radioGroup.length; i++) {
                const radio = radioGroup[i];
                const rawLabel = getRadioLabel(radio) || radio.value || '';
                if (negRegex.test(rawLabel)) {
                    await selectRadio(radio);
                    return;
                }
            }
        } else if (valLower === 'yes' || valLower === 'true') {
            const posRegex = /\b(yes|am|have|do|i am|i have|authorized|eligible|willing)\b/i;
            const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
            for (let i = 0; i < radioGroup.length; i++) {
                const radio = radioGroup[i];
                const rawLabel = getRadioLabel(radio) || radio.value || '';
                if (posRegex.test(rawLabel) && !negRegex.test(rawLabel)) {
                    await selectRadio(radio);
                    return;
                }
            }
        } else if (valLower === 'decline' || valLower.includes('prefer not')) {
            const decRegex = /\b(decline|prefer not|not wish|dont wish|don't wish|rather not)\b/i;
            for (let i = 0; i < radioGroup.length; i++) {
                const radio = radioGroup[i];
                const rawLabel = getRadioLabel(radio) || radio.value || '';
                if (decRegex.test(rawLabel)) {
                    await selectRadio(radio);
                    return;
                }
            }
        }

        // Pass 3: Safe substring matching for specific text (len >= 4)
        if (valLower.length >= 4 && !['yes', 'no', 'true', 'false', 'decline'].includes(valLower)) {
            for (let i = 0; i < radioGroup.length; i++) {
                const radio = radioGroup[i];
                const radioLabel = normalizeLabel(getRadioLabel(radio) || radio.value || '');
                if (radioLabel.includes(valLower) || valLower.includes(radioLabel)) {
                    await selectRadio(radio);
                    return;
                }
            }
        }

        // Pass 4: Fallback for 2-option Yes/No groups
        if ((valLower === 'yes' || valLower === 'no') && radioGroup.length === 2) {
            await selectRadio(radioGroup[valLower === 'yes' ? 0 : 1]);
            return;
        }

        // Fallback: index
        const selectedIndex = parseInt(value);
        if (!isNaN(selectedIndex) && selectedIndex >= 0 && selectedIndex < radioGroup.length) {
            await selectRadio(radioGroup[selectedIndex]);
            return;
        }

        // Pass 5: Ultimate fallback for any unselected radio group (prevents stuck forms)
        if (radioGroup.length > 0 && !radioGroup.some(r => r.checked)) {
            let target = radioGroup[0];
            for (const r of radioGroup) {
                const rawLabel = (r.closest('label')?.textContent || document.querySelector(`label[for="${r.id}"]`)?.textContent || r.value || '').toLowerCase();
                if (!rawLabel.includes('decline') && !rawLabel.includes('prefer not')) {
                    target = r;
                    break;
                }
            }
            await selectRadio(target);
            return;
        }
    } else if (input.type === 'checkbox') {
        const valLower = String(value).toLowerCase();
        const shouldCheck = !['no', 'false', '0', 'unchecked', 'uncheck', 'decline'].includes(valLower);
        await setCheckboxChecked(input, shouldCheck);
    } else if (input.tagName === 'SELECT') {
        const valLower = value.toLowerCase();
        
        // Pass 1: Exact match on option text
        for (const option of input.options) {
            const optText = option.textContent.trim().toLowerCase();
            if (optText === valLower) {
                await setSelectValueWithEvents(input, option.value);
                return;
            }
        }

        // Pass 2: Decision keyword matching for No / Yes / Decline
        if (valLower === 'no' || valLower === 'false') {
            const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
            for (const option of input.options) {
                const optText = option.textContent.trim();
                if (negRegex.test(optText)) {
                    await setSelectValueWithEvents(input, option.value);
                    return;
                }
            }
        } else if (valLower === 'yes' || valLower === 'true') {
            const posRegex = /\b(yes|am|have|do|i am|i have|authorized|eligible|willing)\b/i;
            const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
            for (const option of input.options) {
                const optText = option.textContent.trim();
                if (posRegex.test(optText) && !negRegex.test(optText)) {
                    await setSelectValueWithEvents(input, option.value);
                    return;
                }
            }
        } else if (valLower === 'decline' || valLower.includes('prefer not')) {
            const decRegex = /\b(decline|prefer not|not wish|dont wish|don't wish|rather not)\b/i;
            for (const option of input.options) {
                const optText = option.textContent.trim();
                if (decRegex.test(optText)) {
                    await setSelectValueWithEvents(input, option.value);
                    return;
                }
            }
        }

        // Pass 3: Safe substring match for specific words (length >= 4)
        if (valLower.length >= 4 && !['yes', 'no', 'true', 'false', 'decline'].includes(valLower)) {
            for (const option of input.options) {
                const optText = option.textContent.trim().toLowerCase();
                if (optText.includes(valLower) || valLower.includes(optText)) {
                    await setSelectValueWithEvents(input, option.value);
                    return;
                }
            }
        }

        // Fallback: set value directly
        await setSelectValueWithEvents(input, value);
    } else {
        await typeLikeHuman(input, value);
    }
}

// Function to get stored field answer
function getStoredFieldAnswer(fieldKey) {
    return new Promise((resolve) => {
        browserAPI.storage.local.get(null).then((result) => {
            const exact = result[`field_${fieldKey}`];
            if (exact) {
                resolve(exact);
                return;
            }

            const fieldNorm = normalizeLabel(fieldKey);
            const aliasGroups = [
                { keys: ['locationcity', 'currentlocation', 'citytown'], storage: ['field_Location', 'field_Current Location', 'field_City'] },
                { keys: ['city'], storage: ['field_City', 'field_Location'] },
                { keys: ['province', 'state'], storage: ['field_State', 'field_Province'] },
                { keys: ['country'], storage: ['field_Country'] },
                { keys: ['postalcode', 'zipcode', 'zip'], storage: ['field_Postal Code', 'field_Zip Code'] },
                { keys: ['phonenumber', 'mobilephonenumber', 'mobilephone'], storage: ['field_Phone Number'] },
                { keys: ['emailaddress', 'email'], storage: ['field_Email Address'] },
                { keys: ['yourtitle', 'currentjobtitle', 'jobtitle', 'positiontitle'], storage: ['field_Your title', 'field_Current Job Title', 'field_Job Title'] },
                { keys: ['company', 'companyname', 'employername', 'currentemployer', 'recentemployer'], storage: ['field_Company', 'field_Current Employer', 'field_Recent Employer'] },
                { keys: ['icurrentlyworkhere', 'currentlyworkhere'], storage: ['field_I currently work here'] }
            ];

            for (const group of aliasGroups) {
                if (group.keys.some(k => fieldNorm.includes(k) || k.includes(fieldNorm))) {
                    for (const key of group.storage) {
                        if (result[key]) {
                            resolve(result[key]);
                            return;
                        }
                    }
                }
            }

            const matchingKey = Object.keys(result).find(key =>
                key.startsWith('field_') &&
                normalizeLabel(key.replace(/^field_/, '')) === fieldNorm
            );
            resolve(matchingKey ? result[matchingKey] : null);
        });
    });
}

// Function to save field answer
function saveFieldAnswer(fieldKey, answer) {
    return new Promise((resolve) => {
        if (isSensitiveField(fieldKey, answer)) {
            console.log(`🔒 saveFieldAnswer: Skipping save of sensitive field "${fieldKey}"`);
            resolve();
            return;
        }

        const processedAnswer = extractExactValue(answer, fieldKey);
        browserAPI.storage.local.set({ [`field_${fieldKey}`]: processedAnswer }).then(resolve);
    });
}

// Function to get stored Q&A answer
function getStoredQAAnswer(question) {
    return new Promise((resolve) => {
        browserAPI.storage.local.get(null).then((result) => {
            // Look for matching Q&A entries
            const qaKeys = Object.keys(result).filter(k => k.startsWith('qa_'));
            for (const key of qaKeys) {
                const qa = result[key];
                if (qa && qa.question && normalizeLabel(qa.question) === normalizeLabel(question)) {
                    resolve(qa.answer);
                    return;
                }
            }
            resolve(null);
        });
    });
}

// Function to set field value from Q&A answer
async function setFieldValueFromQA(input, answer) {
    if (!answer) return;

    if (input.type === 'radio') {
        const radioGroup = getRadioGroup(input);
        for (const radio of radioGroup) {
            const radioLabel = getRadioLabel(radio);
            if (radioLabel && normalizeLabel(radioLabel) === normalizeLabel(answer)) {
                await selectRadio(radio);
                return;
            }
        }
        // Fallback: try by value or index
        const idx = parseInt(answer);
        if (!isNaN(idx) && idx >= 0 && idx < radioGroup.length) {
            await selectRadio(radioGroup[idx]);
        }
    } else if (input.tagName === 'SELECT') {
        // Try matching by label first
        for (const option of input.options) {
            if (normalizeLabel(option.textContent) === normalizeLabel(answer)) {
                await setSelectValueWithEvents(input, option.value);
                return;
            }
        }
        // Fallback: set value directly
        await setSelectValueWithEvents(input, answer);
    } else {
        await typeLikeHuman(input, answer);
    }
}

// Function to convert form data to simple Q&A format
function convertFormDataToQA(formData) {
    const qa = {};

    formData.forEach(field => {
        if (!field.label || !field.value) return;

        // For sensitive fields, store them locally but mark them
        if (isSensitiveField(field.label, field.value)) {
            qa[field.label] = field.value; // Still record for the job application data
            return;
        }

        qa[field.label] = field.value;
    });

    return qa;
}

// Function to get application mode preference (always apply)
function getApplicationMode() {
    return Promise.resolve('apply');
}

// Function to simulate realistic human-like typing in DOM inputs
async function typeLikeHuman(input, value) {
    if (!input || !value) return;

    try {
        input.focus();
        
        // Calculate a random delay between 50ms and 150ms per keypress, scaled by delayMultiplier
        const baseDelay = typeof randomHumanPacingMs === 'function'
            ? randomHumanPacingMs(50, 150)
            : localGaussianPacingMs(50, 150);
        const delay = baseDelay * (typeof delayMultiplier !== 'undefined' ? delayMultiplier : 1);
        
        // Bypass window.userEvent to avoid sandbox "Illegal invocation" exceptions
        if (false && window.userEvent) {
            const user = window.userEvent.setup({ delay });
            if (input.value) {
                await user.clear(input);
            }
            await user.type(input, value);
        } else {
            // Fallback: character-by-character typing with simulated event sequence
            input.value = '';
            for (let i = 0; i < value.length; i++) {
                const char = value[i];
                input.dispatchEvent(new KeyboardEvent('keydown', { key: char, bubbles: true }));
                input.value += char;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new KeyboardEvent('keyup', { key: char, bubbles: true }));
                await new Promise(resolve => setTimeout(resolve, delay));
            }
        }
        
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.blur();
    } catch (err) {
        console.error("typeLikeHuman simulation failed, falling back to direct value injection:", err);
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

// Check if a field requires autocomplete selection (e.g. city, school, skills, company)
function isAutocompleteField(input, label) {
    if (!input || input.type === 'radio' || input.type === 'checkbox' || input.tagName === 'SELECT') {
        return false;
    }

    if (input.tagName === 'INPUT') {
        const inputType = (input.type || 'text').toLowerCase();
        const textLikeTypes = ['text', 'search', 'email', 'tel', 'url'];
        if (!textLikeTypes.includes(inputType)) {
            return false;
        }
    } else if (input.tagName !== 'TEXTAREA') {
        return false;
    }

    const normLabel = (label || '').toLowerCase();
    const isAutocompleteLabel = normLabel.includes('location') ||
        normLabel.includes('city') ||
        normLabel.includes('school') ||
        normLabel.includes('company') ||
        normLabel.includes('skills') ||
        normLabel.includes('university') ||
        normLabel.includes('employer');
        
    const hasAutocompleteAttributes = input.getAttribute('role') === 'combobox' ||
        input.getAttribute('aria-autocomplete') === 'list' ||
        input.classList.contains('artdeco-typeahead__input') ||
        input.closest('.artdeco-typeahead');
        
    return isAutocompleteLabel || hasAutocompleteAttributes;
}

// Special typing and suggestion click handler for autocomplete fields
async function handleAutocomplete(input, value) {
    console.log(`🔍 Handling autocomplete for field with value: "${value}"`);

    const setNativeValue = (element, nextValue) => {
        try {
            const nativeWindow = element.ownerDocument?.defaultView || window;
            const prototype = element instanceof nativeWindow.HTMLTextAreaElement
                ? nativeWindow.HTMLTextAreaElement.prototype
                : nativeWindow.HTMLInputElement.prototype;
            const valueSetter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
            if (valueSetter) {
                valueSetter.call(element, nextValue);
            } else {
                element.value = nextValue;
            }
        } catch (err) {
            element.value = nextValue;
        }
    };

    const optionMatchesValue = (optionText, requestedValue) => {
        const normOption = normalizeLabel(optionText || '');
        const normValue = normalizeLabel(requestedValue || '');
        const cityToken = normalizeLabel(String(requestedValue || '').split(',')[0]);
        return (normValue && (normOption.includes(normValue) || normValue.includes(normOption))) ||
            (cityToken.length >= 3 && normOption.includes(cityToken));
    };

    const scoreSuggestion = (optionText, requestedValue) => {
        const normOption = normalizeLabel(optionText || '');
        const normValue = normalizeLabel(requestedValue || '');
        const cityToken = normalizeLabel(String(requestedValue || '').split(',')[0]);
        let score = 0;
        if (!optionMatchesValue(optionText, requestedValue)) return 0;
        if (normValue && normOption === normValue) score += 100;
        if (normValue && normOption.includes(normValue)) score += 70;
        if (cityToken && normOption.startsWith(cityToken)) score += 35;
        if (cityToken && normOption.includes(cityToken)) score += 20;
        if (normOption.includes('britishcolumbia')) score += 35;
        if (normOption.includes('canada')) score += 25;
        if (normOption.includes('unitedkingdom') || normOption.includes('unitedstates')) score -= 20;
        return score;
    };
    
    // 1. Focus and clear the field
    input.focus();
    setNativeValue(input, '');
    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward', data: null }));
    await sleep(300);
    
    // 2. Insert text char-by-char to ensure keydown/keyup/input events are fired
    // and correctly captured by React/Ember typeahead state handlers.
    setNativeValue(input, '');
    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward', data: null }));
    for (let char of value) {
        input.dispatchEvent(new KeyboardEvent('keydown', { key: char, bubbles: true }));
        setNativeValue(input, input.value + char);
        input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: char }));
        input.dispatchEvent(new KeyboardEvent('keyup', { key: char, bubbles: true }));
        await sleep(typeof randomHumanPacingMs === 'function' ? randomHumanPacingMs(30, 80) : localGaussianPacingMs(30, 80));
    }
    
    // Wait for the suggestion dropdown to fetch results and render
    await sleep(1500);
    
    // 3. Find suggestions. Look for common LinkedIn typeahead/autocomplete elements
    const selectors = [
        '[role="listbox"] [role="option"]',
        '.artdeco-typeahead__result',
        '.artdeco-typeahead__results-list [role="option"]',
        '.typeahead-suggestions [role="option"]',
        '.search-suggest__item',
        '.jobs-search-box__typeahead-suggestion',
        '.basic-typeahead__result',
        '.basic-typeahead__results-list [role="option"]'
    ];
    
    const suggestionSet = new Set();
    let suggestions = [];
    const suggestionRoots = [];
    const controlledIds = [
        input.getAttribute('aria-controls'),
        input.getAttribute('aria-owns')
    ].filter(Boolean).flatMap(ids => ids.split(/\s+/));

    for (const id of controlledIds) {
        const controlled = document.getElementById(id) || document.querySelector(`#${CSS.escape(id)}`);
        if (controlled) suggestionRoots.push(controlled);
    }

    const localContainer = input.closest(
        '.jobs-easy-apply-form-element, .fb-dash-form-element, .artdeco-typeahead, .basic-typeahead'
    );
    if (localContainer) suggestionRoots.push(localContainer);

    suggestionRoots.push(document);

    for (const root of suggestionRoots) {
        for (const selector of selectors) {
            const found = Array.from(root.querySelectorAll(selector))
                .filter(el => el.offsetParent !== null);
            if (found.length > 0) {
                found.forEach(el => suggestionSet.add(el));
            }
        }
    }

    suggestions = Array.from(suggestionSet);
    if (suggestions.length > 0) {
        console.log(`🎯 Found autocomplete suggestions (Count: ${suggestions.length})`);
    }
    
    if (suggestions.length > 0) {
        // 4. Click the best match
        let bestMatch = null;
        let bestScore = 0;
        
        for (const sug of suggestions) {
            const text = (sug.textContent || sug.innerText || '');
            const score = scoreSuggestion(text, value);
            if (score > bestScore) {
                bestScore = score;
                bestMatch = sug;
            }
        }
        
        if (bestMatch) {
            console.log(`✅ Found matching suggestion (${bestScore}): "${bestMatch.textContent.trim()}"`);
            console.log(`👉 Clicking suggestion: "${bestMatch.textContent.trim()}"`);
            if (typeof clickLikeHuman === 'function') {
                await clickLikeHuman(bestMatch);
            } else {
                bestMatch.click();
                bestMatch.dispatchEvent(new Event('click', { bubbles: true }));
            }
            await sleep(1000);
        } else {
            console.warn(`⚠️ Suggestions found, but none matched "${value}". Pressing Enter instead of choosing an unrelated option.`);
            input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            input.dispatchEvent(new KeyboardEvent('keypress', { key: 'Enter', bubbles: true }));
            input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
            await sleep(500);
        }
    } else {
        console.warn(`⚠️ No suggestions found for autocomplete value: "${value}". Pressing Enter...`);
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        input.dispatchEvent(new KeyboardEvent('keypress', { key: 'Enter', bubbles: true }));
        input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
        await sleep(500);
    }
}

// Verify that the field value was accepted and is valid
async function verifyFieldAccepted(input, answer, type, label) {
    // Check if aria-invalid is true
    if (input.getAttribute('aria-invalid') === 'true') {
        console.warn(`❌ Verification failed: aria-invalid is true for "${label}"`);
        return false;
    }

    // Check for visible error messages in parent containers
    const container = input.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, .artdeco-text-input--container');
    if (container) {
        const errorMsg = container.querySelector('.artdeco-inline-feedback--error, .fb-dash-error');
        if (errorMsg && errorMsg.offsetParent !== null) { // visible error
            console.warn(`❌ Verification failed: visible error msg for "${label}": "${errorMsg.textContent.trim()}"`);
            return false;
        }
    }

    // For autocomplete fields, also verify the suggestion dropdown has been
    // dismissed — a still-visible listbox means the user's text was typed but
    // no option was actually selected (the most common LinkedIn location failure).
    if (isAutocompleteField(input, label)) {
        const LISTBOX_SELECTORS = [
            '[role="listbox"]',
            '.artdeco-typeahead__results-list',
            '.basic-typeahead__results-list',
            '.typeahead-suggestions'
        ];
        const listboxVisible = LISTBOX_SELECTORS.some(sel => {
            const el = document.querySelector(sel);
            return el && el.offsetParent !== null;  // visible = open
        });
        if (listboxVisible) {
            console.warn(`❌ Verification failed for autocomplete "${label}": suggestion dropdown is still open (no option was selected)`);
            return false;
        }
    }

    // Check value
    if (type === 'radio') {
        const radioGroup = getRadioGroup(input);
        const checkedRadio = Array.from(radioGroup).find(r => r.checked);
        if (!checkedRadio) {
            console.warn(`❌ Verification failed: no checked radio in group for "${label}"`);
            return false;
        }
        return true;
    } else if (type === 'checkbox') {
        if (!input.checked) {
            console.warn(`❌ Verification failed: checkbox is not checked for "${label}"`);
            return false;
        }
        return true;
    } else if (type === 'select') {
        if (!input.value || input.selectedIndex <= 0) {
            console.warn(`❌ Verification failed: select value is empty or unselected for "${label}"`);
            return false;
        }
        return true;
    } else {
        // Text/textarea/autocomplete
        const val = input.value || '';
        if (!val.trim()) {
            console.warn(`❌ Verification failed: value is empty for "${label}"`);
            return false;
        }
        return true;
    }
}

// Apply value to field and verify acceptance
async function applyAndVerifyField(input, answer, type, label) {
    if (isAutocompleteField(input, label)) {
        await handleAutocomplete(input, answer);
    } else {
        await setFieldValue(input, answer);
        await sleep(500); // Wait for DOM updates
    }
    
    const isAccepted = await verifyFieldAccepted(input, answer, type, label);
    return isAccepted;
}
