// =============================================
// content/answer-policy.js
// JS port of the bots' core.answer_policy / answer_controls.
// Keep this deterministic: no I/O, no backend, no AI.
// =============================================

(function () {
    const values = Object.freeze({
        gender: 'Male',
        pronouns: 'he/him',
        desiredSalary: '120000',
        yearsExperience: '4',
        city: 'Vancouver',
        province: 'British Columbia',
        country: 'Canada',
        fullLocation: 'Vancouver, British Columbia, Canada',
        postalCode: 'V6B 1A1',
        phone: '555-0199',
        email: 'user@example.com',
        portfolio: 'https://example.com/portfolio',
        linkedin: 'https://www.linkedin.com/in/example-user/',
        github: 'https://github.com/example-user',
        recentEmployer: 'Vancouver Coastal Health',
        noticePeriod: '30',
        currentTitle: 'Porter',
        currentEmployer: 'Vancouver Coastal Health',
        authorizedCanada: true,
        needsCanadaSponsorship: false,
        authorizedUs: false,
        needsUsSponsorship: true,
        isVeteran: false,
        hasDisability: false,
        isIndigenous: false,
        isHispanicLatino: false,
        hasCriminalRecord: false,
        canWorkWeekends: true,
        canWorkEvenings: true,
        canWorkNights: true,
        canWorkOvertime: true,
        canWorkHolidays: true,
        canTravel: true,
        canCommute: true,
        canRelocate: true,
        canWorkOnSite: true,
        canStandLong: true,
        canLift70Lbs: true,
        canFreelyTravelToUs: false,
        hasDriversLicense: true,
        hasReliableVehicle: true,
        speaksEnglishFluent: true,
        speaksFrench: false
    });

    const hardRules = [
        ['auth_us', ['authorized to work in the us', 'authorized to work in the united states', 'eligible to work in the us', 'eligible to work in the united states', 'legally authorized to work in the us', 'work authorization in the us', 'us work authorization', 'u.s. work authorization']],
        ['travel_us', ['travel to the us', 'travel to us', 'travel freely to the us', 'travel freely to us', 'freely travel to the us', 'freely travel to us']],
        // Compound Yes — must precede bare "visa sponsorship" substring matches
        ['auth_without_sponsorship', [
            'without the need for visa sponsorship',
            'without need for visa sponsorship',
            'without the need for sponsorship',
            'without visa sponsorship',
            'without requiring sponsorship',
            'authorized to work without sponsorship',
            'eligible to work without sponsorship',
        ]],
        ['sponsorship_ca', ['require sponsorship', 'need sponsorship', 'visa sponsorship', 'sponsorship to work in canada', 'sponsorship now or in the future', 'require visa', 'need visa', 'work permit sponsorship']],
        ['auth_ca', ['authorized to work in canada', 'eligible to work in canada', 'legally authorized to work in canada', 'legal right to work in canada', 'work in canada', 'authorized to work in british columbia', 'eligible to work in british columbia', 'authorized to work in the country', 'eligible to work in the country', 'legally entitled to work', 'documents légaux', 'documents legaux']],
        ['job_location_eligible', ['eligible to work in the job location', 'eligible to work at the job location', 'authorized to work in the job location']],
        ['msp_experience', ['managed service provider', ' msp']],
        ['it_support_experience', ['experience with it support', 'it support experience']],
        ['ticketing_systems', ['ticketing system', 'ticketing systems', 'service desk ticket', 'help desk ticket']],
        ['gender', ['gender', 'what is your sex', ' sexe']],
        ['pronouns', ['pronoun', 'preferred pronoun']],
        ['veteran', ['veteran', 'armed forces', 'protected veteran', 'military service']],
        ['disability', ['disability', 'disabled', 'differently abled', 'handicap']],
        ['indigenous', ['indigenous', 'aboriginal', 'first nation', 'métis', 'metis', 'inuit']],
        ['hispanic', ['hispanic', 'latino', 'latinx', 'hispanique']],
        ['race', ['visible minority', 'racial', 'racialized', 'ethnicity', 'ethnic origin', 'what is your race']],
        ['lgbtq', ['lgbtq', 'sexual orientation', 'sexual identity']],
        ['criminal', ['convicted', 'felony', 'criminal charge', 'criminal offence', 'criminal offense', 'criminal record', 'criminal history']],
        ['non_compete', ['non-compete', 'non compete', 'restrictive covenant', 'employment bond', 'bonded obligation', 'restrictive obligation', 'conflict of interest']],
        ['availability_weekend', ['weekend', 'weekends']],
        ['availability_evening', ['evening', 'evenings']],
        ['availability_night', ['overnight', 'night shift', 'graveyard']],
        ['availability_overtime', ['overtime']],
        ['availability_holiday', ['holiday', 'holidays', 'public holiday']],
        ['availability_shift', ['shift work', 'rotating shift', 'rotating shifts', 'shifts including']],
        ['availability_full_time', ['40 hours', 'full-time hours', 'full time hours', 'tuesday to saturday', 'monday to friday', 'monday through friday']],
        ['travel', ['willing to travel', 'travel for work', 'travel between locations']],
        ['commute', ['commute', 'commuting', 'reliably commute']],
        ['relocate', ['relocate', 'relocation', 'willing to relocate']],
        ['on_site', ['on-site', 'onsite', 'in-office', 'in office', 'in person', 'in-person', 'come to the office', 'work from office']],
        ['physical_stand', ['stand for long periods', 'standing for long periods', 'long periods of time']],
        ['physical_lift', ['lift up to', 'weighing up to', 'able to lift', 'lifting requirements']],
        ['drivers_license', ['valid driver\'s license', 'valid drivers license', 'valid driving licence', 'valid driver\'s licence', 'valid drivers licence', 'driving licence', 'driving license', 'bc driver', 'bc license', 'bc licence', 'g licence', 'g license', 'class 5']],
        ['vehicle', ['reliable vehicle', 'access to a reliable vehicle', 'own vehicle', 'personal vehicle']],
        ['salary_expected', ['salary expectation', 'salary expectations', 'desired salary', 'desired pay', 'expected salary', 'expected compensation', 'wage expectation', 'wage expectations', 'compensation expectation', 'annually in cad', 'annual salary', 'annual compensation', 'base salary', 'base pay', 'hourly rate', 'what are your wage', 'what is your wage', 'what is your salary', 'what are your salary', 'starting pay', 'pay range', 'pay rate', 'what is your pay', 'what is the pay', 'what is the starting pay']],
        ['years_experience', ['how many years', 'years of experience', 'amount of experience', 'years have you']],
        ['start_date', ['start date', 'desired start', 'available to start', 'availability date', 'date available', 'available date', 'earliest available', 'earliest start']],
        ['interview_availability', ['interview availability', 'available for an interview', 'availability for a call', 'available for a call', 'phone screen', 'screening call', '2-3 dates', 'two to three dates']],
        ['referral', ['referred by', 'referral name', 'recommended by', 'current employee referral', 'referrer']],
        ['english_proficiency', ['speak english', 'fluent in english', 'english proficiency', 'proficient in english', 'english language']],
        ['french_proficiency', ['speak french', 'fluent in french', 'bilingual', 'français', 'francais']],
        ['confirm_truthful', ['i confirm', 'i certify', 'i declare', 'i acknowledge', 'true and complete', 'truthful', 'misrepresentation', 'falsification']],
        ['consent_data', ['consent', 'authorize processing', 'privacy policy', 'personal information', 'data processing']],
        ['background_check', ['background check', 'criminal record check', 'police check', 'record check']],
        ['drug_test', ['drug test', 'substance test']]
    ];

    function norm(text) {
        return String(text || '').toLowerCase().match(/[a-z0-9']+/g)?.join(' ') || '';
    }

    function hasKw(textNorm, kw) {
        const kwNorm = norm(kw);
        return !!kwNorm && textNorm.includes(kwNorm);
    }

    function yesNo(value) {
        return value ? 'yes' : 'no';
    }

    function decision(category, intent, source, value) {
        return { category, intent, value, source, aiAllowed: false, confidence: 'hard', matched: category !== 'unmatched' };
    }

    function resolve(category) {
        switch (category) {
            case 'auth_without_sponsorship': return decision(category, yesNo(values.authorizedCanada && !values.needsCanadaSponsorship), 'policy_auth_without_sponsorship');
            case 'auth_ca': return decision(category, yesNo(values.authorizedCanada), 'policy_auth_ca');
            case 'job_location_eligible': return decision(category, yesNo(values.authorizedCanada), 'policy_job_location_auth');
            case 'msp_experience': return decision(category, 'no', 'policy_msp_no');
            case 'it_support_experience': return decision(category, 'yes', 'policy_it_support_yes');
            case 'ticketing_systems': return decision(category, 'yes', 'policy_ticketing_yes');
            case 'sponsorship_ca': return decision(category, yesNo(values.needsCanadaSponsorship), 'policy_sponsorship_ca');
            case 'auth_us': return decision(category, yesNo(values.authorizedUs), 'policy_auth_us');
            case 'travel_us': return decision(category, yesNo(values.canFreelyTravelToUs), 'policy_travel_us');
            case 'gender': return decision(category, 'text', 'policy_gender', values.gender);
            case 'pronouns': return decision(category, 'text', 'policy_pronouns', values.pronouns);
            case 'veteran': return decision(category, yesNo(values.isVeteran), 'policy_veteran');
            case 'disability': return decision(category, yesNo(values.hasDisability), 'policy_disability');
            case 'indigenous': return decision(category, yesNo(values.isIndigenous), 'policy_indigenous');
            case 'hispanic': return decision(category, 'decline', 'policy_hispanic_decline');
            case 'race': return decision(category, 'decline', 'policy_race_decline');
            case 'lgbtq': return decision(category, 'decline', 'policy_lgbtq_decline');
            case 'criminal': return decision(category, yesNo(values.hasCriminalRecord), 'policy_criminal');
            case 'non_compete': return decision(category, 'no', 'policy_no_non_compete');
            case 'availability_weekend': return decision(category, yesNo(values.canWorkWeekends), 'policy_avail_weekend');
            case 'availability_evening': return decision(category, yesNo(values.canWorkEvenings), 'policy_avail_evening');
            case 'availability_night': return decision(category, yesNo(values.canWorkNights), 'policy_avail_night');
            case 'availability_overtime': return decision(category, yesNo(values.canWorkOvertime), 'policy_avail_overtime');
            case 'availability_holiday': return decision(category, yesNo(values.canWorkHolidays), 'policy_avail_holiday');
            case 'availability_shift':
            case 'availability_full_time': return decision(category, 'yes', 'policy_avail_yes');
            case 'travel': return decision(category, yesNo(values.canTravel), 'policy_travel');
            case 'commute': return decision(category, yesNo(values.canCommute), 'policy_commute');
            case 'relocate': return decision(category, yesNo(values.canRelocate), 'policy_relocate');
            case 'on_site': return decision(category, yesNo(values.canWorkOnSite), 'policy_on_site');
            case 'physical_stand': return decision(category, yesNo(values.canStandLong), 'policy_stand');
            case 'physical_lift': return decision(category, yesNo(values.canLift70Lbs), 'policy_lift');
            case 'drivers_license': return decision(category, yesNo(values.hasDriversLicense), 'policy_dl');
            case 'vehicle': return decision(category, yesNo(values.hasReliableVehicle), 'policy_vehicle');
            case 'salary_expected': return decision(category, 'numeric', 'policy_salary', values.desiredSalary);
            case 'years_experience': return decision(category, 'numeric', 'policy_years', values.yearsExperience);
            case 'referral': return decision(category, 'text', 'policy_no_referral', 'N/A');
            case 'english_proficiency': return decision(category, yesNo(values.speaksEnglishFluent), 'policy_english');
            case 'french_proficiency': return decision(category, yesNo(values.speaksFrench), 'policy_french');
            case 'confirm_truthful': return decision(category, 'yes', 'policy_confirm_yes');
            case 'consent_data': return decision(category, 'yes', 'policy_consent_yes');
            case 'background_check': return decision(category, 'yes', 'policy_bg_check_yes');
            case 'drug_test': return decision(category, 'yes', 'policy_drug_test_yes');
            default: return { category: 'unmatched', aiAllowed: true, confidence: 'soft', matched: false };
        }
    }

    function classify(question, options) {
        const haystack = `${norm(question)} ${norm((options || []).join(' '))}`.trim();
        if (!haystack) return { category: 'unmatched', aiAllowed: true, confidence: 'soft', matched: false };
        for (const [category, keywords] of hardRules) {
            if (keywords.some(kw => hasKw(haystack, kw))) return resolve(category);
        }
        return { category: 'unmatched', aiAllowed: true, confidence: 'soft', matched: false };
    }

    function matchOption(intent, optionLabels) {
        if (!intent || !optionLabels || optionLabels.length === 0) return null;
        
        const intentNorm = norm(intent);
        const opts = optionLabels.map(label => ({ label, norm: norm(label) }));
        
        // Pass 1: Exact normalized match
        const exact = opts.find(opt => opt.norm === intentNorm);
        if (exact) return exact.label;

        // Pass 2: Intent-specific word boundary matching
        if (intentNorm === 'no' || intentNorm === 'false') {
            const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
            const match = opts.find(opt => negRegex.test(opt.label));
            if (match) return match.label;
        } else if (intentNorm === 'yes' || intentNorm === 'true') {
            const posRegex = /\b(yes|am|have|do|i am|i have|authorized|eligible|willing)\b/i;
            const negRegex = /\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b/i;
            const match = opts.find(opt => posRegex.test(opt.label) && !negRegex.test(opt.label));
            if (match) return match.label;
        } else if (intentNorm === 'decline' || intentNorm.includes('prefer not')) {
            const decRegex = /\b(decline|prefer not|not wish|dont wish|don't wish|rather not)\b/i;
            const match = opts.find(opt => decRegex.test(opt.label));
            if (match) return match.label;
        }

        // Pass 3: Safe word-boundary or long substring matching (len >= 4)
        if (intentNorm.length >= 4 && !['yes', 'no', 'true', 'false', 'decline'].includes(intentNorm)) {
            const match = opts.find(opt => opt.norm.includes(intentNorm) || intentNorm.includes(opt.norm));
            if (match) return match.label;
        }

        return null;
    }

    function profileAnswer(label) {
        const q = norm(label);
        if (!q) return null;
        if ((q.includes('location') && q.includes('city')) || q.includes('current location')) return values.fullLocation;
        if (q === 'city' || q.includes('city town') || q.includes('what city')) return values.fullLocation;
        if (q.includes('postal') || q.includes('zip')) return values.postalCode;
        if (q.includes('province') || q.includes('state')) return values.province;
        if (q.includes('country')) return values.country;
        if (q.includes('phone') || q.includes('telephone') || (q.includes('mobile') && !q.includes('experience') && !q.includes('testing') && !q.includes('development') && !q.includes('app')) ) return values.phone;
        if (q.includes('email') || q.includes('e mail')) return values.email;
        if (q.includes('portfolio') || q.includes('website')) return values.portfolio;
        if (q.includes('linkedin')) return values.linkedin;
        if (q.includes('github')) return values.github;
        if (q.includes('recent employer') || q.includes('current employer')) return values.recentEmployer;
        if (q === 'company' || q.includes('company name') || q.includes('employer name')) return values.currentEmployer;
        if (q === 'your title' || q.includes('current job title') || q.includes('job title') || q.includes('position title')) return values.currentTitle;
        if (q.includes('currently work here') || q.includes('i currently work here')) return 'Yes';
        if (q.includes('notice')) return values.noticePeriod;
        if (q.includes('manual testing')) return '1';
        if (q.includes('mobile applications')) return '1';
        if (q.includes('sanity testing')) return '1';
        if (q.includes('mobile testing')) return '1';
        if (q.includes('regression testing')) return '1';
        if (q.includes('due diligence')) return '0';
        if (q.includes('financial transactions')) return '0';
        return null;
    }

    async function answerForField({ label, type, options, jobTitle, company }) {
        const profileValue = profileAnswer(label);
        if (profileValue !== null) {
            return {
                answer: profileValue,
                intent: profileValue === 'Yes' ? 'yes' : 'text',
                source: 'profile_alias',
                confidence: 'hard',
                ai_allowed: false,
                matched: true
            };
        }

        try {
            const body = {
                question: label,
                control_type: type,
                options: options || [],
                field_label: label,
                job_title: jobTitle || document.querySelector('h1.t-24, .job-details-jobs-unified-top-card__job-title')?.textContent?.trim() || '',
                company: company || document.querySelector('.job-details-jobs-unified-top-card__company-name a, .job-details-jobs-unified-top-card__company-name')?.textContent?.trim() || ''
            };

            console.log("📡 Fetching QA answer from Python backend:", body);

            const response = await browserAPI.runtime.sendMessage({
                type: "QA_ANSWER",
                body
            });

            if (response && response.success) {
                const result = response.result;
                console.log("📥 Received QA answer from Python backend:", result);
                if (result && result.matched && result.answer !== null) {
                    return {
                        answer: result.answer,
                        intent: result.intent,
                        source: result.source || 'backend',
                        confidence: result.confidence || 'hard',
                        ai_allowed: result.ai_allowed,
                        matched: true
                    };
                }
            } else {
                console.error("⚠️ Local QA backend returned error:", response?.status, response?.error);
            }
        } catch (err) {
            console.error("❌ Local QA backend is down or unreachable:", err);
            
            // Check if this is a hard identity question
            const localDec = classify(label, options);
            const isHard = !localDec || localDec.confidence === 'hard';
            if (isHard) {
                console.log(`❌ Emergency fallback disallowed: "${label}" is a hard/identity question. Skipping.`);
                return {
                    error: "backend_down",
                    matched: false
                };
            }
        }

        // Local deterministic fallback. This runs when the backend is reachable
        // but has no policy match, and also as an emergency fallback for soft
        // questions if the backend is unreachable.
        const d = classify(label, options);
        if (d.matched) {
            console.log(`🧭 Local policy fallback answered "${label}" with "${d.value || d.intent}"`);
            let raw = d.value || d.intent;
            if (d.intent === 'yes') raw = 'Yes';
            if (d.intent === 'no') raw = 'No';
            return {
                answer: raw,
                intent: d.intent,
                source: d.source || 'local_fallback',
                confidence: d.confidence,
                ai_allowed: d.ai_allowed,
                matched: true
            };
        }

        return {
            matched: false
        };
    }

    window.UFHAnswerPolicy = { values, norm, classify, matchOption, answerForField };
})();
