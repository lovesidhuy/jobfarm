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

// Local rule-based heuristic QA solver
function localAnswerQuestion(question) {
    const label = (question || "").toLowerCase();

    const testMatch = (words) => {
        return words.some(w => {
            const regex = new RegExp(`\\b${w}\\b`);
            return regex.test(label);
        });
    };

    if (label.includes("comfortable commuting") || label.includes("commute") || label.includes("commuting")) {
        return "Yes";
    }
    if (label.includes("10 years of residency") || label.includes("residency in canada")) {
        return "Yes";
    }
    if (label.includes("onsite") || label.includes("on-site") || label.includes("work in ")) {
        if (label.includes("saint-bruno") || label.includes("montarville") || label.includes("qc") || label.includes("quebec")) {
            return "No";
        }
        return "Yes";
    }
    // Prefer IT resume file name when LinkedIn shows uploaded-file radios
    if (label.includes("select resume") || label.includes("resume") || label.includes("cv")) {
        if (label.includes("sample_resume") || label.includes("resume_it")) {
            return label.includes("deselect") ? "No" : "Yes";
        }
        if (label.includes("general") || /resume\s*\(\d+\)/.test(label)) {
            // Do not select generic upload when IT resume is available
            return label.includes("deselect") ? "Yes" : "No";
        }
        return "Yes";
    }
    if (label.includes("how did you hear")) {
        if (label.includes(" - ")) {
            const option = label.split(" - ").pop().toLowerCase();
            if (option.includes("linkedin")) return "Yes";
            return "No";
        }
        return "LinkedIn";
    }
    if (label.includes("highest level of education") || label.includes("education completed")) {
        return "Bachelor's degree";
    }
    if (label.includes("college") || label.includes("university") || label.includes("school")) {
        return "Kwantlen Polytechnic University";
    }
    if (label.includes("other") && label.includes("highest level of education")) {
        return "";
    }
    if (label.includes("earliest start") || label.includes("start date") || label.includes("available to start") || label.includes("availability date")) {
        return "Immediately";
    }
    if (label.includes("currently employed") || label.includes("ever been employed")) {
        return "No";
    }
    if (label.includes("certification") || label.includes("licenses")) {
        return "AWS Certified Solutions Architect - Associate; AWS Certified Cloud Practitioner";
    }
    if (label === "your title" || label.includes("your title")) {
        return "Porter";
    }
    if (label === "company" || label.includes("company")) {
        return "Vancouver Coastal Health";
    }
    if (label.includes("currently work here")) {
        return "Yes";
    }
    if (label.includes("month of from")) {
        return "October";
    }
    if (label.includes("year of from")) {
        return "2022";
    }
    if (label.includes("month of to") || label.includes("year of to")) {
        return "";
    }
    if (label === "description" || label === "describe your duties" || label === "job description summary") {
        return "Support hospital operations through reliable patient transport, clear communication, and careful coordination with clinical teams in a fast-paced environment.";
    }
    // Work Authorization — compound "authorized WITHOUT sponsorship" is YES
    // (Canadian citizen). Must run before bare "sponsorship" → No.
    if (
        /without.{0,40}(visa\s+)?sponsorship/i.test(label) &&
        testMatch(["authorized", "eligible", "legally", "right to work", "work authorization", "able to work"])
    ) {
        return "Yes";
    }
    if (testMatch(["authorized", "eligible", "legally", "work in canada", "work in british columbia", "right to work"])) {
        return "Yes";
    }
    // Visa sponsorship needed? → No (do not match "without … sponsorship")
    if (testMatch(["sponsorship", "visa", "sposorship"]) && !/without.{0,40}sponsorship/i.test(label)) {
        return "No";
    }
    // Yes/No capability questions that mention experience should not receive a numeric answer.
    if (/^(do|did|have|are|can|will)\b/.test(label) && testMatch(["experience", "experienced", "familiar", "comfortable", "able"])) {
        return "Yes";
    }
    // Citizenship / work eligibility — use exact-ish labels LinkedIn forms show
    if (testMatch(["citizenship", "citizen", "nationality", "employment eligibility", "work eligibility"])) {
        return "I am a Canadian Citizen";
    }
    // Referral name free-text — never put applicant name
    if (testMatch(["referred", "referral", "referrer"]) && testMatch(["name", "share", "employee who"])) {
        return "N/A";
    }
    // Phone
    if (testMatch(["phone", "mobile", "tel", "telephone"])) {
        return "555-0199";
    }
    // Email
    if (testMatch(["email", "e-mail", "mail"])) {
        return "user@example.com";
    }
    // Name
    if (label.includes("first name")) {
        return "Jane";
    }
    if (label.includes("last name") || label.includes("surname")) {
        return "Doe";
    }
    if (label.includes("middle name")) {
        return "";
    }
    if (testMatch(["full name", "signature", "legal name"]) || (testMatch(["name"]) && !label.includes("employer") && !label.includes("company"))) {
        return "Jane Doe";
    }
    // Location / City / State / Country
    if (testMatch(["city", "location", "address", "live in"])) {
        if (testMatch(["country"])) return "Canada";
        if (testMatch(["state", "province"])) return "British Columbia";
        return "Vancouver";
    }
    if (testMatch(["zip", "postal"])) {
        return "V6B 1A1";
    }
    if (testMatch(["country"])) {
        return "Canada";
    }
    if (testMatch(["state", "province"])) {
        return "British Columbia";
    }
    // Years of Experience — overall and skill-specific (LinkedIn often prefills 0)
    if (
        label === "years of experience" ||
        label === "total years of experience" ||
        label === "overall years of experience" ||
        label === "total years of work experience" ||
        /how many years/.test(label) ||
        (/years?/.test(label) && /experience|exp\b|yoe/.test(label))
    ) {
        // Skill-specific: map known IT skills to realistic YOE; default 2 (not 0).
        const skillDefaults = [
            [/manual testing|test cases|regression|qa|quality assurance|software test/i, "2"],
            [/selenium|cypress|playwright|automation testing|test automation/i, "1"],
            [/python|java|javascript|typescript|react|node/i, "2"],
            [/linux|ubuntu|centos|windows server|active directory|group policy/i, "2"],
            [/network|cisco|vlan|tcp\/ip|routing|switching|firewall/i, "2"],
            [/aws|azure|cloud|terraform|docker|kubernetes|devops/i, "1"],
            [/sql|mysql|mongodb|database/i, "2"],
            [/help desk|service desk|desktop support|ticketing|customer support/i, "2"],
            [/cyber|security|siem|splunk|incident/i, "1"],
        ];
        for (const [re, years] of skillDefaults) {
            if (re.test(label)) return years;
        }
        return "3";
    }
    // Notice Period
    if (testMatch(["notice"])) {
        if (testMatch(["month", "months"])) return "1";
        if (testMatch(["week", "weeks"])) return "3";
        return "30";
    }
    // Expected Salary / desired CTC
    if (testMatch(["salary", "compensation", "ctc", "pay", "expect"])) {
        if (testMatch(["current", "present"])) {
            if (testMatch(["month", "months"])) return "5000";
            return "60000";
        }
        if (testMatch(["month", "months"])) return "10000";
        return "70000";
    }
    // Links / Socials
    if (testMatch(["linkedin"])) {
        return "https://www.linkedin.com/in/example-user/";
    }
    if (testMatch(["github"])) {
        return "https://github.com/example-user";
    }
    if (testMatch(["portfolio", "website", "blog", "link"])) {
        return "https://example.com/portfolio";
    }
    // Diversity / Demographic
    if (testMatch(["gender", "sex"])) {
        return "Male";
    }
    if (testMatch(["disability", "handicapped"])) {
        return "No";
    }
    if (testMatch(["veteran", "protected"])) {
        return "No";
    }
    // Consent / agree dropdowns (LinkedIn Easy Apply often stalls on these)
    if (testMatch(["i agree", "agree", "consent", "terms", "privacy", "acknowledge"])) {
        return "I Agree";
    }
    if (label.includes("select an option") && label.includes("agree")) {
        return "I Agree";
    }

    // Scale of 1-10
    if (label.includes("scale of 1-10") || testMatch(["confidence"])) {
        return "8";
    }
    // Summary / Headline / Cover Letter
    if (testMatch(["headline"])) {
        return "IT Student | Network Administration & Security | AWS Certified | Cloud & Security-Focused Infrastructure";
    }
    if (testMatch(["summary"]) || label.includes("about you")) {
        return "IT student specializing in Network Administration and Security. AWS Certified Solutions Architect with hands-on experience in enterprise networking, systems security, cloud infrastructure, and clear technical communication. Skilled at translating technical work into clear communication for both engineers and non-technical stakeholders.";
    }
    if (label.includes("cover letter") || label.includes("coverletter")) {
        return "Cover Letter";
    }
    // Employer
    if (testMatch(["employer"]) || label.includes("company you worked")) {
        return "Vancouver Coastal Health";
    }

    return null;
}

/**
 * If the field only offers agree/consent-style options, pick that without LLM.
 * @param {string[]} options
 * @returns {string|null}
 */
function pickConsentOption(options) {
    if (!Array.isArray(options) || !options.length) return null;
    const cleaned = options.map((o) => String(o || '').trim()).filter(Boolean);
    const exact = cleaned.find((o) => /^i\s+agree\.?$/i.test(o));
    if (exact) return exact;
    const agreeish = cleaned.find((o) =>
        /^(yes|i\s+agree|agree|i\s+understand|accept|i\s+accept|confirmed|i\s+acknowledge)\b/i.test(o)
        && !/disagree|do not agree|don't agree/i.test(o)
    );
    return agreeish || null;
}

// Query LLM API using Node fetch
async function callLLMApi(question, provider, apiKey, model, customUrl) {
    const cleanProvider = (provider || "").toLowerCase();
    console.log(`[LLM] Querying ${provider} (model: ${model || "default"}) for question: "${question}"`);

    try {
        if (cleanProvider === 'gemini') {
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
            // OpenAI compatible completions
            let url = '';
            let headers = { 'Content-Type': 'application/json' };
            let resolvedModel = model;

            if (cleanProvider === 'openai') {
                url = customUrl ? (customUrl.endsWith('/') ? customUrl.slice(0, -1) : customUrl) + '/chat/completions' : 'https://api.openai.com/v1/chat/completions';
                headers.Authorization = `Bearer ${apiKey}`;
                if (!resolvedModel) resolvedModel = 'gpt-4o-mini';
            } else if (cleanProvider === 'deepseek') {
                url = customUrl ? (customUrl.endsWith('/') ? customUrl.slice(0, -1) : customUrl) + '/chat/completions' : 'https://api.deepseek.com/chat/completions';
                headers.Authorization = `Bearer ${apiKey}`;
                if (!resolvedModel) resolvedModel = 'deepseek-chat';
            } else if (cleanProvider === 'ollama') {
                const baseUrl = customUrl || 'http://localhost:11434/v1';
                url = (baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl) + '/chat/completions';
                if (!resolvedModel) resolvedModel = 'llama3';
            } else {
                throw new Error(`Unsupported AI Provider: ${provider}`);
            }

            const body = {
                model: resolvedModel,
                messages: [
                    { role: 'system', content: systemInstruction },
                    { role: 'user', content: question }
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
    } catch (err) {
        console.error(`[LLM Error] Failed to resolve via ${provider}:`, err.message);
        return null;
    }
}

module.exports = {
    localAnswerQuestion,
    pickConsentOption,
    callLLMApi
};
