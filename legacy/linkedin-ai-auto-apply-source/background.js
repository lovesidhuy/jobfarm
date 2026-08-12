console.log("🛠️ [LinkedIn Automation] background.js: Initializing local automation runner...");

const PYTHON_QA_BACKEND_URL = "http://127.0.0.1:5001";

let linkedInTabId = null;
let isProcessingApplications = false;
let applicationState = {
    isRunning: false,
    progress: {
        current: 0,
        total: 0,
        isPaused: false,
        finished: false
    }
};

// Seed candidate profile on extension startup if not seeded
function seedProfileIfEmpty() {
    browser.storage.local.get(["profileSeeded"]).then((res) => {
        const durableProfileFields = {
            "field_Location": "Vancouver, British Columbia, Canada",
            "field_Current Location": "Vancouver, British Columbia, Canada",
            "field_Province": "British Columbia",
            "field_Postal Code": "V6B 1A1",
            "field_Current Employer": "Company",
            "field_Company": "Company",
            "field_Your title": "Specialist",
            "field_Current Job Title": "Specialist",
            "field_Job Title": "Specialist",
            "field_I currently work here": "Yes"
        };
        browser.storage.local.get(Object.keys(durableProfileFields)).then(existing => {
            const missing = {};
            Object.entries(durableProfileFields).forEach(([key, value]) => {
                if (!existing[key]) missing[key] = value;
            });
            if (Object.keys(missing).length) {
                browser.storage.local.set(missing);
            }
        });

        if (!res.profileSeeded) {
            console.log("BACKGROUND: Seeding candidate profile for Jane Doe...");
            const defaultProfile = {
                "profileSeeded": true,
                "field_First Name": "Jane",
                "field_Last Name": "Doe",
                "field_Full Name": "Jane Doe",
                "field_Email Address": "user@example.com",
                "field_Phone Number": "555-0199",
                "field_City": "Vancouver",
                "field_Location": "Vancouver, British Columbia, Canada",
                "field_Current Location": "Vancouver, British Columbia, Canada",
                "field_State": "British Columbia",
                "field_Province": "British Columbia",
                "field_Country": "Canada",
                "field_Postal Code": "V6B 1A1",
                "field_Portfolio Website": "https://example.com/portfolio",
                "field_LinkedIn Profile": "https://www.linkedin.com/in/example-user/",
                "field_Default Resume": "sample_resume_it.pdf",
                "field_Desired Salary": "120000",
                "field_Current Salary": "80000",
                "field_Years of Experience": "4",
                "field_Notice Period": "30 days",
                "field_Gender": "Male",
                "field_Disability": "No",
                "field_Veteran": "No",
                "field_Recent Employer": "Vancouver Coastal Health",
                "field_Current Employer": "Vancouver Coastal Health",
                "field_Company": "Vancouver Coastal Health",
                "field_Your title": "Porter",
                "field_Current Job Title": "Porter",
                "field_Job Title": "Porter",
                "field_I currently work here": "Yes",
                "field_Work Authorization": "Yes",
                "field_Visa Sponsorship": "No",
                "field_Summary": "Final-year Information Technology student at Kwantlen Polytechnic University specializing in Network Administration & Security, with a strong focus on building secure, scalable systems. AWS Certified Solutions Architect with hands-on experience in cloud infrastructure, network security, and full-stack development. Actively seeking entry-level opportunities in IT support, cloud, networking, or security roles where I can contribute, learn, and grow.",
                "field_Headline": "IT Student | Network Administration & Security | AWS Certified | Cloud & Security-Focused Infrastructure"
            };
            browser.storage.local.set(defaultProfile).then(() => {
                console.log("BACKGROUND: Candidate profile seeded successfully.");
            });
        }
    });
}

// Downgraded legacy QA solver (now a stub - backend is single source of truth)
function localAnswerQuestion(question) {
    console.log("BACKGROUND: localAnswerQuestion stub called. Using Flask backend instead.");
    return null;
}

// Local LLM client endpoint queries
async function callLLMApi(question, provider, apiKey, model, customUrl) {
    const userProfile = `
Name: Jane Doe
Email: user@example.com
Phone: 555-0199
Location: Surrey, BC, Canada
Portfolio: https://usery.github.io/portfoliowebsite
Education: Bachelor of Technology in Information Technology at Kwantlen Polytechnic University, specializing in Network Administration and Security. Expected graduation: Dec 2026.
Certifications: AWS Certified Solutions Architect - Associate, AWS Certified Cloud Practitioner.
Summary: IT student specializing in Network Administration and Security with hands-on experience in enterprise networking, systems security, cloud infrastructure, and clear technical communication.
Target roles: IT support, help desk, service desk, desktop support, technical support, network administration, systems administration, cloud infrastructure, security operations, SOC analyst, junior cybersecurity, QA, automation, DevOps, infrastructure, and related technical roles.
Skills: Cisco IOS, VLANs, VPNs, IPv4/IPv6, OSPF, BGP, firewall policy, 802.1X, WPA3, Wi-Fi 6, RF analysis, Nmap, Wireshark, Splunk, Wazuh, SIEM, Autopsy, FTK, endpoint hardening, OSSEC HIDS, incident response, root-cause analysis, AWS VPC, EC2, S3, IAM, IoT Core, Terraform, CloudFormation, Docker, VMware, Hyper-V, Windows Server, Active Directory Domain Services, Group Policy, Linux Ubuntu/CentOS, Ansible, Python, Bash, Java, PHP, Embedded C++, MQTT, Arduino, SQL, MySQL, MongoDB, REST APIs, Git, Postman, ticketing systems.
Experience: Porter at Vancouver Coastal Health since Oct 2022.
`;

    const systemInstruction = `You are an intelligent AI assistant filling out a job application form. Answer the question for the candidate like a human. 
Respond concisely based on the type of question:
1. If the question asks for years of experience, duration, or numeric value, return ONLY a number (e.g., "2", "5", "10").
2. If the question is a Yes/No question, return ONLY "Yes" or "No".
3. If the question requires a short description, give a single-sentence response.
4. If the question requires a detailed response, provide a well-structured and human-like answer and keep the number of characters under 350.
5. Do NOT repeat the question in your answer.
6. CRITICAL: Provide ONLY the exact final answer text. Do NOT include any explanations, reasoning, commentary, conversational filler, parenthetical notes, or quotation marks around your answer. Your output will be typed directly into a form field.

Candidate Information:
${userProfile}`;

    console.log(`LLM_API: Querying ${provider} with model ${model || "default"} for question: "${question}"`);

    try {
        if (provider === 'gemini') {
            const urlModel = model || 'gemini-1.5-flash';
            const url = `https://generativelanguage.googleapis.com/v1beta/models/${urlModel}:generateContent?key=${apiKey}`;
            const body = {
                contents: [{
                    parts: [{
                        text: `${systemInstruction}\n\nQUESTION: ${question}`
                    }]
                }]
            };

            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            if (!response.ok) {
                const errText = await response.text();
                throw new Error(`Gemini API returned status ${response.status}: ${errText}`);
            }

            const data = await response.json();
            const answer = data.candidates?.[0]?.content?.parts?.[0]?.text;
            if (!answer) throw new Error("Empty response from Gemini API");
            return answer.trim();

        } else {
            // OpenAI compatible: openai, deepseek, ollama
            let url = '';
            let headers = { 'Content-Type': 'application/json' };
            let resolvedModel = model;

            if (provider === 'openai') {
                url = customUrl ? (customUrl.endsWith('/') ? customUrl.slice(0, -1) : customUrl) + '/chat/completions' : 'https://api.openai.com/v1/chat/completions';
                headers.Authorization = `Bearer ${apiKey}`;
                if (!resolvedModel) resolvedModel = 'gpt-4o-mini';
            } else if (provider === 'deepseek') {
                url = customUrl ? (customUrl.endsWith('/') ? customUrl.slice(0, -1) : customUrl) + '/chat/completions' : 'https://api.deepseek.com/chat/completions';
                headers.Authorization = `Bearer ${apiKey}`;
                if (!resolvedModel) resolvedModel = 'deepseek-chat';
            } else if (provider === 'ollama') {
                const baseUrl = customUrl || 'http://localhost:11434/v1';
                url = (baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl) + '/chat/completions';
                if (!resolvedModel) resolvedModel = 'llama3';
            }

            const body = {
                model: resolvedModel,
                messages: [
                    { role: 'system', content: systemInstruction },
                    { role: 'user', content: `QUESTION: ${question}` }
                ],
                temperature: 0.1
            };

            const response = await fetch(url, {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(body)
            });

            if (!response.ok) {
                const errText = await response.text();
                throw new Error(`${provider} API returned status ${response.status}: ${errText}`);
            }

            const data = await response.json();
            const answer = data.choices?.[0]?.message?.content;
            if (!answer) throw new Error(`Empty response from ${provider} API`);
            return answer.trim();
        }
    } catch (error) {
        console.error(`LLM_API error for ${provider}:`, error);
        return null;
    }
}

function setLinkedInTab(tabId) {
    linkedInTabId = tabId;
    console.log("LinkedIn tab set to:", tabId);
}

function clearLinkedInTab() {
    linkedInTabId = null;
    isProcessingApplications = false;
    applicationState.isRunning = false;
    console.log("LinkedIn tab cleared, application process reset");
}

function getLinkedInTabId() {
    return linkedInTabId;
}

function isOnLinkedInTab(tabId) {
    return linkedInTabId === tabId;
}

function clearAuthToken() {
    browser.storage.local.remove(["authToken", "userPlan"]).then(() => {
        console.log("🔒 BACKGROUND: Auth token cleared");
        browser.runtime.sendMessage({ type: "LOGGED_OUT" }).catch(() => {});
    });
}

// Bypassed/Mocked quota functions
async function fetchQuotaAndStore() {
    console.log("BACKGROUND: Quota check requested. Overriding with local unlimited quota.");
    await browser.storage.local.set({ applicationsLeft: 99999 });
}

async function notifyApplicationSuccess() {
    console.log("BACKGROUND: Application success reported. Saved locally only.");
    await browser.storage.local.set({ applicationsLeft: 99999 });
}

async function callPythonBackend(endpoint, body, timeoutMs = 8000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(`${PYTHON_QA_BACKEND_URL}${endpoint}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: controller.signal
        });
        const text = await response.text();
        let data = null;
        if (text) {
            try {
                data = JSON.parse(text);
            } catch (err) {
                console.warn(`BACKGROUND: Backend returned non-JSON body for ${endpoint}:`, text.substring(0, 200));
            }
        }
        return { ok: response.ok, status: response.status, data };
    } finally {
        clearTimeout(timeoutId);
    }
}

function callPythonQaBackend(body, timeoutMs = 8000) {
    return callPythonBackend("/qa/answer", body, timeoutMs);
}

browser.tabs.onRemoved.addListener((tabId, removeInfo) => {
    if (tabId === linkedInTabId) {
        console.log("LinkedIn tab closed, resetting application state");
        clearLinkedInTab();
    }
});

browser.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (tabId === linkedInTabId && changeInfo.url && !changeInfo.url.includes("linkedin.com/jobs")) {
        console.log("LinkedIn tab navigated away from jobs page, resetting application state");
        clearLinkedInTab();
    }
});

browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("📡 [LinkedIn Automation] BACKGROUND: Received message", message.type, "from", sender.tab ? "tab " + sender.tab.id : "worker");

    if (message.type === "JOB_PRESCREEN") {
        (async () => {
            try {
                const result = await callPythonBackend("/job/prescreen", message.body || {}, 10000);
                console.log(`BACKGROUND: Job prescreen request answered via service worker (HTTP ${result.status})`);
                sendResponse({ success: result.ok, status: result.status, result: result.data });
            } catch (err) {
                console.error("BACKGROUND: Job prescreen request failed via service worker:", err);
                sendResponse({ success: false, error: err.message || String(err) });
            }
        })();
        return true;
    }

    if (message.type === "BACKEND_HEALTH") {
        (async () => {
            try {
                const result = await callPythonQaBackend({
                    question: "__health_check__",
                    control_type: "text",
                    options: []
                }, 5000);
                console.log(`BACKGROUND: Backend health check passed via service worker (HTTP ${result.status})`);
                sendResponse({ success: true, status: result.status });
            } catch (err) {
                console.error("BACKGROUND: Backend health check failed via service worker:", err);
                sendResponse({ success: false, error: err.message || String(err) });
            }
        })();
        return true;
    }

    if (message.type === "QA_ANSWER") {
        (async () => {
            try {
                const result = await callPythonQaBackend(message.body || {}, 10000);
                console.log(`BACKGROUND: QA backend answered via service worker (HTTP ${result.status})`);
                sendResponse({ success: result.ok, status: result.status, result: result.data });
            } catch (err) {
                console.error("BACKGROUND: QA backend request failed via service worker:", err);
                sendResponse({ success: false, error: err.message || String(err) });
            }
        })();
        return true;
    }

    if (message.type === "CAPTURE_SCREENSHOT") {
        browser.tabs.captureVisibleTab(null, { format: 'png' }).then(dataUrl => {
            const key = `screenshot_${message.jobId || Date.now()}`;
            browser.storage.local.set({ [key]: dataUrl }).then(() => {
                console.log(`📸 Saved failure screenshot under key: ${key}`);
                sendResponse({ success: true, key });
            });
        }).catch(err => {
            console.error('❌ captureVisibleTab failed:', err);
            sendResponse({ success: false, error: err.toString() });
        });
        return true; // async response
    }

    if (message.type === "FILL_FROM_SERVER") {
        (async () => {
            console.log(`BACKGROUND: Received FILL_FROM_SERVER for ${message.questions.length} questions`);
            
            const settings = await browser.storage.local.get(['aiEnabled', 'aiProvider', 'aiApiKey', 'aiModel', 'aiApiUrl']);
            const aiEnabled = settings.aiEnabled !== false;
            const provider = settings.aiProvider || 'rules';
            const apiKey = settings.aiApiKey || '';
            const model = settings.aiModel || '';
            const customUrl = settings.aiApiUrl || '';

            const answers = {};
            for (const question of message.questions) {
                // Heuristic matches
                let answer = localAnswerQuestion(question);
                if (answer !== null) {
                    console.log(`BACKGROUND: Heuristic match: "${question}" -> "${answer}"`);
                    answers[question] = answer;
                } else if (aiEnabled && provider !== 'rules' && (apiKey || provider === 'ollama')) {
                    // AI calls
                    console.log(`BACKGROUND: Rules missed, calling Local AI provider ${provider} for: "${question}"`);
                    const aiAnswer = await callLLMApi(question, provider, apiKey, model, customUrl);
                    if (aiAnswer) {
                        console.log(`BACKGROUND: AI answer: "${question}" -> "${aiAnswer}"`);
                        answers[question] = aiAnswer;
                    }
                }
            }

            console.log(`BACKGROUND: Answering ${Object.keys(answers).length} questions of ${message.questions.length}`);
            sendResponse({ answers });
        })();
        return true;
    }
    if (message.type === "SET_LINKEDIN_TAB") {
        setLinkedInTab(sender.tab?.id || message.tabId);
        sendResponse({ success: true });
        return false;
    }
    if (message.type === "GET_LINKEDIN_TAB") {
        sendResponse({ tabId: linkedInTabId });
        return false;
    }
    if (message.type === "CLEAR_LINKEDIN_TAB") {
        clearLinkedInTab();
        sendResponse({ success: true });
        return false;
    }
    if (message.type === "CHECK_TAB_STATUS") {
        const tabId = sender.tab?.id || message.currentTabId;
        sendResponse({
            isOnLinkedInTab: isOnLinkedInTab(tabId),
            linkedInTabId: linkedInTabId,
            isProcessingApplications: isProcessingApplications
        });
        return false;
    }
    if (message.type === "SET_APPLICATION_STATE") {
        isProcessingApplications = message.isRunning;
        if (message.progress) {
            applicationState.progress = message.progress;
        }
        applicationState.isRunning = message.isRunning;
        sendResponse({ success: true });
        return false;
    }
    if (message.type === "GET_APPLICATION_STATE") {
        sendResponse({
            isRunning: applicationState.isRunning,
            progress: applicationState.progress,
            linkedInTabId: linkedInTabId
        });
        return false;
    }
    if (message.type === "GO_TO_LINKEDIN_TAB") {
        if (linkedInTabId) {
            browser.tabs.update(linkedInTabId, { active: true }).then(() => {
                sendResponse({ success: true });
            }).catch(() => {
                clearLinkedInTab();
                sendResponse({ success: false, error: "Tab no longer exists" });
            });
        } else {
            sendResponse({ success: false, error: "No LinkedIn tab found" });
        }
        return true;
    }
    if (message.type === "NOTIFY_APPLICATION_SUCCESS") {
        notifyApplicationSuccess().then(() => {
            sendResponse({ success: true });
        });
        return true;
    }
    if (message.type === "GET_QUOTA") {
        fetchQuotaAndStore().then(() => {
            sendResponse({ applicationsLeft: 99999 });
        });
        return true;
    }
});

// Seed default profile immediately
seedProfileIfEmpty();
