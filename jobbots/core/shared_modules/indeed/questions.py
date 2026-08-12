from __future__ import annotations

import re

from ._bootstrap import *  # noqa: F403

try:
    from modules.qa_answer_bank import find_answer
except Exception:
    def find_answer(*args, **kwargs):
        return None


def _handle_qual_questions(page) -> None:
    print_lg("    [SmartApply] Answering qualification questions…")
    time.sleep(_T_Q)
    
    from jobbots.core.shared_modules.indeed.smartapply import _extract_page_questions_schema
    from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
    
    questions = _extract_page_questions_schema(page)
    if questions:
        log_job_status_event_from_meta("questions_found", reason=f"Found {len(questions)} qualification questions")

    _handle_indeed_custom_select_lists(page)

    # Use the same name-based grouping as employer questions
    _answer_radios_by_name_group(page)
    # Also handle any fieldset-based groups
    groups = page.query_selector_all("fieldset, [role='radiogroup']")
    for grp in groups:
        radios = grp.query_selector_all("input[type='radio']")
        if not radios:
            continue
        answered = False
        for r in radios:
            rid = r.get_attribute("id") or ""
            lbl = grp.query_selector(f'label[for="{rid}"]') if rid else None
            ltext = (lbl.inner_text().strip() if lbl else (r.get_attribute("value") or ""))
            if ltext.lower() in ("yes", "true", "oui"):
                if not r.is_checked():
                    r.click(force=True)
                answered = True
                break
        if not answered and radios and not radios[0].is_checked():
            radios[0].click(force=True)

    log_job_status_event_from_meta("answers_drafted", reason="Drafted answers for qualification questions")
    log_job_status_event_from_meta("filled", reason="Filled qualification questions")


def _click_yes_on_all_radios(page) -> None:
    radios = page.query_selector_all("input[type='radio']")
    seen: set = set()
    for r in radios:
        name = r.get_attribute("name") or r.get_attribute("id") or ""
        if name in seen:
            continue
        rid = r.get_attribute("id") or ""
        lbl = page.query_selector(f'label[for="{rid}"]') if rid else None
        ltext = (lbl.inner_text().strip() if lbl else (r.get_attribute("value") or "")).lower()
        if ltext in ("yes", "true", "oui"):
            if not r.is_checked():
                r.click(force=True)
            seen.add(name)


# ── Name-based radio group handler (PRIMARY for Indeed employer questions) ──

def _extract_question_text_for_radio_group(page, name: str, options: list) -> str:
    """
    Extract the question text for a radio group identified by name='q_{hash}'.

    Indeed's structure (from HTML dump analysis):
      div[data-testid='input-q_{hash}']         ← question container (question + options)
      div.ia-Questions-item (id='q_N')           ← outer question wrapper

    Strategy: find the container div, get its full text, strip out option labels
    to isolate the question text.
    """
    question_text = ""

    # Strategy 1: query the container div by data-testid containing the hash
    try:
        container = page.query_selector(f"div[data-testid*='{name}']")
        if container:
            full_text = container.inner_text()
            qt = full_text
            for _, opt_text in options:
                if opt_text:
                    qt = qt.replace(opt_text, " ")
            # Also strip asterisks / required markers
            qt = qt.replace("*", " ").replace("Required", " ")
            question_text = " ".join(qt.split()).strip()
    except Exception:
        pass

    # Strategy 2: walk up from first radio to ia-Questions-item
    if not question_text and options:
        try:
            r0 = options[0][0]
            # Go up until we find div.ia-Questions-item or div[id^='q_']
            js = """el => {
                let e = el;
                for (let i = 0; i < 8; i++) {
                    e = e.parentElement;
                    if (!e) return '';
                    if (e.id && e.id.match(/^q_/)) {
                        // get text and strip option labels
                        return e.innerText || '';
                    }
                    let cls = e.className || '';
                    if (cls.includes('ia-Questions-item')) return e.innerText || '';
                }
                return '';
            }"""
            full_text = r0.evaluate(js) or ""
            if full_text:
                qt = full_text
                for _, opt_text in options:
                    if opt_text:
                        qt = qt.replace(opt_text, " ")
                qt = qt.replace("*", " ").replace("Required", " ")
                question_text = " ".join(qt.split()).strip()
        except Exception:
            pass

    # Strategy 3: fallback via _get_question_context
    if not question_text and options:
        try:
            question_text = _get_question_context(page, options[0][0])
        except Exception:
            pass

    return question_text or ""


def _pick_radio_by_rules(question_lower: str, options: list) -> object:
    """
    Rule-based radio option picker for common Indeed employer question patterns.
    Returns the selected radio element, or None if AI fallback is needed.

    options: list of (element, label_text)
    """
    def pick_first(*prefs):
        for pref in prefs:
            for r, lbl in options:
                if pref.lower() in lbl.lower():
                    return r
        return None

    def pick_without(*prefs, avoid=()):
        for pref in prefs:
            for r, lbl in options:
                lbl_l = lbl.lower()
                if pref.lower() in lbl_l and not any(a.lower() in lbl_l for a in avoid):
                    return r
        return None

    def pick_yes_no(value: bool):
        return pick_first("yes", "oui", "true") if value else pick_first("no", "non", "false")

    def configured_gender_prefs():
        value = str(_configured_gender or "").strip().lower()
        if value == "male":
            return ("male", "homme", "man", "masculin")
        if value == "female":
            return ("female", "femme", "woman", "féminin", "feminin")
        if value == "other":
            return ("other", "autre", "non-binary", "non binaire")
        return ("prefer not", "préfère ne pas", "refus", "decline")

    def pronoun_label_is_male(lbl: str) -> bool:
        ln = (lbl or "").lower().replace(" ", "")
        # Explicit she/they must never match male profile
        if any(x in ln for x in ("she", "her", "hers", "elle", "they", "them", "their", "iel")):
            if "he/him" not in ln and not re.search(r"(^|/)he(/|$)", ln):
                return False
        if "he/him" in ln or "him/his" in ln or "he/him/his" in ln:
            return True
        if re.search(r"\bhe\b", (lbl or "").lower()) and re.search(
            r"\b(him|his)\b", (lbl or "").lower()
        ):
            return True
        return False

    def pronoun_label_is_female(lbl: str) -> bool:
        ln = (lbl or "").lower().replace(" ", "")
        if "he/him" in ln and "she" not in ln:
            return False
        return any(x in ln for x in ("she/her", "she/her/hers", "her/hers")) or (
            re.search(r"\bshe\b", (lbl or "").lower())
            and re.search(r"\b(her|hers)\b", (lbl or "").lower())
        )

    def looks_like_pronoun_options(opts: list) -> bool:
        joined = " ".join((lbl or "").lower() for _, lbl in opts)
        compact = joined.replace(" ", "")
        return (
            "he/him" in compact
            or "she/her" in compact
            or ("he" in joined and "him" in joined and "she" in joined)
            or "pronoun" in joined
        )

    q = question_lower
    opt_labels = [lbl for _, lbl in options]

    gender_value = str(_configured_gender or "").strip().lower()
    if any(k in q for k in ("identify as a woman", "identify as female", "are you a woman")):
        return pick_yes_no(gender_value == "female")
    if any(k in q for k in ("identify as a man", "identify as male", "are you a man")):
        return pick_yes_no(gender_value == "male")

    # HARD LOCK: pronouns — never let AI pick She/Her for a male profile
    if (
        any(k in q for k in ("pronoun", "preferred pronoun", "your pronouns"))
        or looks_like_pronoun_options(options)
    ):
        if gender_value == "male":
            for r, lbl in options:
                if pronoun_label_is_male(lbl):
                    return r
        elif gender_value == "female":
            for r, lbl in options:
                if pronoun_label_is_female(lbl):
                    return r
        else:
            for r, lbl in options:
                ln = (lbl or "").lower()
                if any(x in ln for x in ("they", "them", "other", "ask me", "prefer not")):
                    return r

    if (
        any(k in q for k in ("gender", "sex", "sexe"))
        or (not q and _looks_like_gender_option_set(opt_labels))
    ):
        for r, lbl in options:
            if _gender_label_matches_configured(lbl):
                return r

    # ── Hard requirement gates: answer deterministically before AI ─────────
    if (
        any(k in q for k in ("travel to the us", "travel to us", "travel freely to us",
                             "freely travel to the us", "freely travel to us"))
        or (
            "us" in q and "travel" in q
            and any(k in q for k in ("visa", "permit", "permits", "without need"))
        )
    ):
        return pick_yes_no(bool(can_freely_travel_to_us))

    if any(k in q for k in ("16 years or older", "aged 16", "age 16", "16 or older",
                             "16 ans", "seize ans")):
        return pick_yes_no(bool(meets_minimum_work_age))

    if any(k in q for k in ("legal documents to work", "documents légaux",
                             "documents legaux", "legally authorized",
                             "authorized to work", "eligible to work",
                             "legal right to work", "work in canada",
                             "citizen or", "citizen,", "citizen/permanent",
                             "work visa", "work permit", "valid work permit",
                             "permanent resident", "pr status",
                             "status in canada", "immigration status",
                             "autorisation de travailler", "travailler au canada",
                             "autorisation légale", "autorisation legale",
                             "droit légal", "droit legal", "permis de travail")):
        return pick_yes_no(bool(has_legal_work_documents))

    if any(k in q for k in ("vaccinated against covid", "vaccinated against covid-19",
                             "covid-19 vaccine", "covid vaccine", "covid vaccination")):
        return pick_yes_no(bool(is_vaccinated_against_covid))

    if any(k in q for k in ("minimum 1 year", "at least 1 year", "1 year experience",
                             "one year experience")) and any(k in q for k in (
                                 "dental receptionist", "receptionist in a health office",
                                 "health office", "medical office", "moa",
                                 "oral surgery office",
                             )):
        if "dental" in q or "oral surgery" in q:
            return pick_yes_no(bool(has_dental_reception_experience))
        return pick_yes_no(bool(has_health_office_reception_experience))

    if any(k in q for k in ("valid driver's license", "valid drivers license",
                             "valid driving licence", "valid driver's licence",
                             "driver's license", "drivers license", "driving licence")):
        return pick_yes_no(bool(has_valid_drivers_license))

    if any(k in q for k in ("reliable vehicle", "access to a reliable vehicle")):
        return pick_yes_no(bool(has_reliable_vehicle))

    if any(k in q for k in ("stand for long periods", "standing for long periods",
                             "long periods of time")):
        return pick_yes_no(bool(can_stand_for_long_periods))

    if any(k in q for k in ("lift up to 70", "weighing up to 70", "up to 70 lb",
                             "up to 70 lbs", "70 lb", "70 lbs")):
        return pick_yes_no(bool(can_lift_up_to_70_lbs))

    if any(k in q for k in ("tuesday to saturday", "tuesday through saturday",
                             "full-time (40 hours", "full time (40 hours",
                             "40 hours")):
        return pick_yes_no(bool(can_work_full_time_40_hours))

    if any(k in q for k in ("evenings and weekends", "evening and weekend",
                             "work evenings", "work weekends", "available weekends",
                             "available to work weekends", "weekend availability",
                             "work on weekends")):
        if "evening" in q and not can_work_evenings:
            return pick_yes_no(False)
        if "weekend" in q and not can_work_weekends:
            return pick_yes_no(False)
        return pick_yes_no(True)

    if any(k in q for k in ("in-person", "in person", "come to the office",
                             "office for 4 days", "4 days a week", "11:00 am to 7:30 pm",
                             "11am to 7:30pm", "11:00am to 7:30pm")):
        return pick_yes_no(bool(can_work_in_person))

    if any(k in q for k in ("travel to our three locations", "travel to 3 locations",
                             "travel between locations", "travel to multiple locations")):
        return pick_yes_no(bool(can_travel_between_local_locations))

    if any(k in q for k in ("travel up to 1 hour", "travel up to one hour",
                             "commute up to 1 hour", "commute up to one hour")):
        return pick_yes_no(bool(can_commute_up_to_one_hour))

    # ── Truthfulness / legal attestation ──────────────────────────────────
    # These must never fall through to a noisy option match where
    # "No, I do not confirm" contains the word "confirm".
    if (
        _norm_choice(q) in ("i agree", "agree", "i acknowledge", "acknowledge")
        or any(k in q for k in ('i confirm', 'certify', 'declare', 'attest',
                                'true and complete', 'truthful', 'misrepresentation',
                                'falsification', 'omission of information',
                                'verification of the information', 'pre-employment background check',
                                'eligibility to work', 'identity and eligibility'))
        or (
            'confirm' in q
            and any(k in q for k in ('application', 'information provided',
                                     'information will be used', 'reference check'))
        )
    ):
        return pick_without(
            'i agree', 'agree', 'yes, i confirm', 'i confirm', 'yes',
            'i certify', 'i declare', 'acknowledge',
            avoid=('do not', "don't", 'not confirm', 'no,')
        )

    if any(k in q for k in ('consent', 'authorize', 'processing your personal information',
                             'personal information', 'privacy policy', 'data processing',
                             'transmission of the application')):
        return pick_without('yes, i consent', 'i consent', 'consent', 'yes',
                            'authorize', 'agree',
                            avoid=('do not', "don't", 'no,', 'withdraw'))

    # ── Location / residency ───────────────────────────────────────────────
    if any(k in q for k in ("currently located", "currently reside", "où résidez-vous", "ou residez-vous", "where do you reside", "location", "résidez-vous")):
        loc_prefs = []
        if current_city:
            loc_prefs.append(current_city.lower())
        if state:
            loc_prefs.append(state.lower())
            if state.lower() == "bc":
                loc_prefs.extend(("british columbia", "colombie-britannique"))
        loc_prefs.extend(("vancouver", "surrey", "british columbia", "colombie-britannique", "canada"))
        for pref in loc_prefs:
            for r, lbl in options:
                if pref in lbl.lower():
                    return r

    # ── Commute / relocation ───────────────────────────────────────────────
    if any(k in q for k in ('commute', 'reliably commute', 'relocat', 'willing to travel')):
        return pick_first('yes, i can make the commute', 'can make the commute',
                          'yes, i am able to commute', 'commute readily', 'yes', 'true')

    if any(k in q for k in ('convenient location', 'convenient for you to commute',
                             'is this a convenient', 'convenient commute',
                             'location convenient', 'location work for you',
                             'able to commute to this', 'commute to our office',
                             'commute to this location', 'commute to this office',
                             'location for you', 'work location')):
        return pick_first('yes', 'oui', 'true')

    if any(k in q for k in ('based in metro vancouver', 'located in metro vancouver',
                             'live in metro vancouver', 'currently in metro vancouver',
                             'based in vancouver', 'located in vancouver')):
        return pick_first('yes', 'oui', 'true')

    if (
        any(k in q for k in ('cuba', 'iran', 'north korea', 'rth korea', 'syria', 'crimea'))
        and any(k in q for k in ('citizen', 'permanent resident', 'export control'))
    ):
        return pick_first('no', 'non', 'false')

    # ── Work authorization / sponsorship ───────────────────────────────────
    if (
        any(k in q for k in ('sponsorship', 'visa sponsorship', 'require sponsorship'))
        or (
            any(k in q for k in ('require', 'need', 'nécessit', 'besoin'))
            and any(k in q for k in ('work authorization', 'work permit', 'visa'))
        )
    ):
        return pick_first('no', 'do not require', 'not require', 'no sponsorship')

    # ── Referral / current employee recommendation ─────────────────────────
    if any(k in q for k in ('referred', 'referral', 'recommended by',
                             'recommandé', 'recommande', 'référé', 'refere')):
        return pick_first('no', 'non')

    # ── Work eligibility / citizenship ─────────────────────────────────────
    if any(k in q for k in ('eligible', 'éligible', 'admissible',
                             'authorized to work', 'autorisé', 'autorisee',
                             'legal right', 'droit de travailler',
                             'legally authorized', 'work in canada', 'work in the us',
                             'pays où vous postulez', 'pays ou vous postulez',
                             'citizen or', 'citizen,', 'citizen/permanent',
                             'work visa', 'work permit', 'valid work permit',
                             'permanent resident', 'pr status',
                             'status in canada', 'immigration status',
                             'autorisation de travailler', 'travailler au canada',
                             "autorisation légale", "autorisation legale",
                             "droit légal", "droit legal", "permis de travail")):
        return pick_first('yes', 'canadian citizen', 'authorized', 'eligible',
                          'permanent resident', 'oui', 'autorisé', 'autorisée')

    # ── Language ───────────────────────────────────────────────────────────
    if _is_non_english_language_question(q):
        speaks_language = _candidate_speaks_language(q)
        if speaks_language is not None:
            return pick_yes_no(speaks_language)
        return pick_first('no', 'non', 'false')

    if "english" in q or "anglais" in q:
        if not any(k in q for k in ("year", "how many", "experience", "expéri")):
            return pick_first('advanced', 'native', 'fluent', 'mother tongue', 'professional', 'bilingual',
                              'avancé', 'avance', 'maternelle', 'courant', 'bilingue', 'yes', 'oui')

    if any(k in q for k in ('speak english', 'english proficiency', 'proficient in english',
                             'fluent in english', 'english language')):
        if not any(k in q for k in ("year", "how many", "experience", "expéri")):
            return pick_first('yes', 'advanced', 'native', 'fluent', 'mother tongue')

    # ── Background check consent ───────────────────────────────────────────
    if any(k in q for k in ('background check', 'criminal record check', 'screening',
                             'consent to a check')):
        if any(k in q for k in ('completed', 'within the past', 'already have',
                                 'currently have', 'do you have')):
            return pick_first('no', 'not completed', 'do not have')
        return pick_without('yes', 'i consent', 'agree', 'authorize',
                            avoid=('do not', "don't", 'no,'))

    # ── Drug test ──────────────────────────────────────────────────────────
    if any(k in q for k in ('drug test', 'substance test')):
        return pick_first('yes', 'i consent', 'agree')

    # ── Gender ─────────────────────────────────────────────────────────────
    if 'pronoun' in q:
        pronoun_answer = _configured_pronoun_answer().lower()
        pronoun_prefs = [pronoun_answer]
        if str(_configured_gender or "").strip().lower() == "male":
            pronoun_prefs.extend(("he/him", "he / him", "he"))
        elif str(_configured_gender or "").strip().lower() == "female":
            pronoun_prefs.extend(("she/her", "she / her", "she"))
        elif str(_configured_gender or "").strip().lower() == "other":
            pronoun_prefs.extend(("they/them", "they / them", "they"))
        pronoun_prefs.extend(("prefer not", "decline"))
        return pick_first(*pronoun_prefs)

    if 'gender' in q or 'sex' in q or 'sexe' in q:
        for r, lbl in options:
            if _gender_label_matches_configured(lbl):
                return r

    # ── Indigenous / Aboriginal / First Nations ───────────────────────────
    if any(k in q for k in ('indigenous', 'aboriginal', 'first nation',
                             'métis', 'inuit', 'status indian')):
        return pick_first('prefer not to disclose', 'prefer not to say',
                          'prefer not', 'decline', 'no', 'non')

    # ── Disability ──────────────────────────────────────────────────────────
    if any(k in q for k in ('disability', 'disabled', 'handicap', 'differently abled')):
        return pick_first('no', 'non', 'prefer not to say')

    # ── Visible minority / race / ethnicity ────────────────────────────────
    if any(k in q for k in ('visible minority', 'racial', 'racialized',
                             'race', 'ethnicity', 'ethnic origin')):
        return pick_first('south asian', 'asian', 'prefer not to say', 'prefer not to disclose')

    # ── Veteran / military ──────────────────────────────────────────────────
    if any(k in q for k in ('veteran', 'military', 'armed forces', 'protected veteran')):
        return pick_first('not a veterans', 'not a veteran', 'no', 'none', 'prefer not')

    # ── LGBTQ+ / sexual orientation ─────────────────────────────────────────
    if any(k in q for k in ('lgbtq', 'sexual orientation', 'sexual identity')):
        return pick_first('prefer not', 'prefer not to disclose', 'prefer not to say')

    # ── Conviction / criminal background ────────────────────────────────────
    if any(k in q for k in ('convicted', 'felony', 'criminal charge', 'criminal offence',
                             'criminal offense', 'found responsible', 'provincial legislation',
                             'protection of persons in care', 'vulnerable adults')):
        return pick_first('no', 'none')

    # ── Restrictive obligations / conflicts ────────────────────────────────
    if any(k in q for k in ('non-compete', 'non compete', 'restrictive covenant',
                             'bonded obligation', 'employment bond',
                             'contractual obligation', 'conflict of interest')):
        return pick_first('no', 'none')

    # ── Age eligibility ──────────────────────────────────────────────────────
    if any(k in q for k in ('18 or above', 'over 18', 'at least 18', 'minimum age',
                             '18 years of age', 'age 18', 'be 18')):
        return pick_first('yes', 'oui')

    # ── Previous employment at SPECIFIC company (read question carefully) ────
    # "Have you ever worked for CompanyX, CompanyY...?" → always 'No'
    # These are disqualifying rehire checks — employer wants fresh candidates.
    if any(k in q for k in ('ever worked for', 'previously worked for',
                             'have you worked for',
                             'have you worked at', 'ever been employed by',
                             'formerly employed by', 'worked at any of',
                             'been employed, or otherwise engaged',
                             'employed, or otherwise engaged',
                             'previously employed by', 'ever employed by',
                             'work for any of', 'ever been an employee',
                             'been an employee of', 'current employee of',
                             'former employee of', 'employee of',
                             'previously employed with', 'employed with',
                             'worked with our company', 'worked with this company')):
        return pick_first('no', 'non')

    # ── Relatives / relations working at company ──────────────────────────
    if any(k in q for k in ('related to', 'relative', 'family member', 'parent, sibling')):
        return pick_first('no', 'non')

    # ── Availability / schedule ──────────────────────────────────────────────
    if any(k in q for k in ('available to work', 'work evenings', 'work weekends',
                             'work overtime', 'flexible schedule', 'work on weekends',
                             'reliable', 'on-time attendance', 'scheduled shifts')):
        return pick_first('yes', 'oui')

    # ── Management / supervisory experience ───────────────────────────────
    if any(k in q for k in ('people management', 'manage people', 'managed people',
                             'supervisory experience', 'supervisor experience',
                             'team management', 'managed a team', 'managing a team',
                             'direct reports')):
        return pick_first('no', 'non')

    # ── Experience with years / duration ────────────────────────────────────
    if any(k in q for k in ('how many years', 'years of experience',
                             'amount of experience', 'years have you')):
        # Find the option that matches our experience range
        try:
            from config.search import current_experience as _ce
            exp = int(_ce)
        except Exception:
            exp = 3
        # Find the range that includes exp
        import re as _re
        for r, lbl in options:
            nums = [int(x) for x in _re.findall(r'\d+', lbl)]
            if not nums:
                continue
            if len(nums) == 1:
                if nums[0] <= exp:
                    return r
            elif len(nums) >= 2:
                lo, hi = nums[0], nums[-1]
                if lo <= exp <= hi:
                    return r
        # Fallback: pick the lowest range
        return options[0][0] if options else None

    # ── Salary / compensation ranges ────────────────────────────────────────
    if any(k in q for k in ('salary', 'compensation', 'pay expectation',
                             'pay expectations', 'base pay', 'annual pay',
                             'annually in cad', 'wage')):
        try:
            target_salary = int(_ds)
        except Exception:
            target_salary = 80000

        def _salary_nums(label: str) -> list[int]:
            nums = [int(x.replace(',', '')) for x in re.findall(r'\d[\d,]*', label or "")]
            # Ignore tiny incidental numbers from labels like "option 1".
            return [n for n in nums if n >= 1000]

        best = None
        best_distance = None
        for r, lbl in options:
            nums = _salary_nums(lbl)
            if not nums:
                continue
            if len(nums) == 1:
                lo = nums[0]
                hi = float("inf") if "+" in lbl else nums[0]
            else:
                lo, hi = nums[0], nums[-1]
            if lo <= target_salary <= hi:
                return r
            distance = abs(lo - target_salary)
            if best is None or distance < best_distance:
                best = r
                best_distance = distance
        if best is not None:
            return best

    # ── Generic Yes/No fallback ──────────────────────────────────────────────
    # When options are ONLY Yes/No AND no specific rule matched above,
    # return None → let the Ollama AI decide based on question context.
    # If AI cannot choose, the caller leaves it unanswered instead of guessing.
    opt_lowers = [lbl.lower().strip() for _, lbl in options]
    if set(opt_lowers) <= {'yes', 'no', 'oui', 'non', 'true', 'false'}:
        return None   # → AI will answer; if AI unavailable, leave unanswered

    return None   # → AI fallback needed


def _answer_radios_by_name_group(page) -> None:
    """
    PRIMARY handler for Indeed's employer/screener questions.

    Indeed renders radio groups as flat inputs grouped by name='q_{hash}',
    with NO fieldset or role=radiogroup wrapper (confirmed from ghg.html dump).

    For each group:
      1. Extract question text from div[data-testid*='q_{hash}'] container
      2. Try rule-based picker (_pick_radio_by_rules)
      3. Fall back to Ollama AI (_ai_answer) if rules don't match
      4. If AI also fails → leave unanswered + log for review
    """
    from collections import defaultdict

    all_radios = page.query_selector_all("input[type='radio']")
    if not all_radios:
        return

    # Group by name attribute
    groups: dict = defaultdict(list)
    no_name_radios = []
    for r in all_radios:
        name = r.get_attribute("name") or ""
        if name:
            groups[name].append(r)
        else:
            no_name_radios.append(r)

    handled_names: set = set()

    for name, radios in groups.items():
        if name in handled_names:
            continue
        handled_names.add(name)

        # Skip if any radio is already checked
        try:
            if any(r.is_checked() for r in radios):
                continue
        except Exception:
            pass

        # Build options list: (element, label_text)
        options = []
        for r in radios:
            rid = r.get_attribute("id") or ""
            lbl_text = ""
            if rid:
                lbl = page.query_selector(f'label[for="{rid}"]')
                if lbl:
                    try:
                        lbl_text = lbl.inner_text().strip()
                    except Exception:
                        pass
            if not lbl_text:
                lbl_text = r.get_attribute("value") or ""
            options.append((r, lbl_text))

        if not options:
            continue

        opts_texts = [lbl for _, lbl in options]

        # Extract the question text
        question_text = _extract_question_text_for_radio_group(page, name, options)
        q_lower = question_text.lower()

        print_lg(f"    [Questions] Radio: {question_text[:70]!r}  opts={opts_texts}")
        log_training_event(
            "question_detected",
            job=_current_job_meta,
            control_type="radio_group_name",
            question=question_text or name,
            options=opts_texts,
            group_name=name,
            dom=[element_dom_snapshot(page, r, {"option_label": lbl}) for r, lbl in options],
        )

        # ── Rule-based picker ────────────────────────────────────────────
        chosen = _pick_radio_by_rules(q_lower, options)
        decision_source = "rules" if chosen is not None else ""
        # Identity lock: if rules picked pronouns/gender, never fall through to AI
        identity_q = any(
            k in q_lower
            for k in ("pronoun", "gender", "sex", "sexe", "preferred pronoun")
        )
        identity_opts = any(
            any(x in (lbl or "").lower() for x in ("he/him", "she/her", "they/them", "male", "female", "homme", "femme"))
            for lbl in opts_texts
        )
        if chosen is not None and (identity_q or identity_opts):
            decision_source = decision_source or "identity_lock"
        if "add additional" in q_lower and {label.lower() for label in opts_texts} >= {"yes", "no"}:
            additional_count = getattr(_handle_employer_questions, "_additional_count", 0)
            wanted = "yes" if additional_count == 0 else "no"
            chosen = next((r for r, label in options if label.lower() == wanted), None)
            _handle_employer_questions._additional_count = additional_count + 1
            decision_source = "configured_work_history_sequence"

        # Sensitive self-identification questions sometimes have noisy question
        # text extraction, but the option set is reliable. Prefer non-disclosure
        # before any AI fallback can choose Yes/No.
        if chosen is None:
            options_l = " ".join(opts_texts).lower()
            sensitive_l = f"{q_lower} {options_l}"
            if (
                "prefer not to disclose" in options_l
                and any(k in sensitive_l for k in (
                    "aboriginal", "first nation", "metis", "métis", "inuit",
                    "indigenous", "self-identification", "demographic",
                    "equal opportunity", "identify as",
                ))
            ):
                for r, lbl in options:
                    if "prefer not to disclose" in lbl.lower():
                        chosen = r
                        decision_source = "sensitive_prefer_not_disclose"
                        break

        # ── Ollama AI fallback (blocked for gender/pronoun identity) ──────
        if chosen is None and not (identity_q or identity_opts):
            dom_context = ""
            try:
                group_container = page.query_selector(f"div[data-testid*='{name}']")
                if group_container:
                    dom_context = group_container.inner_text()
            except Exception:
                dom_context = ""
            ai_ans = _ai_answer(
                question=question_text or name,
                hint=_radio_ai_hint(question_text or name, opts_texts, dom_context),
                job_context=_current_job_context,
                options=opts_texts,
            )
            if ai_ans:
                selected_label = _choose_by_ai_answer(ai_ans, opts_texts)
                for r, lbl in options:
                    if lbl == selected_label:
                        chosen = r
                        break
                if chosen:
                    print_lg(f"    [Questions] AI chose: {ai_ans!r} → {selected_label!r}")
                    decision_source = "ai"
        elif chosen is None and (identity_q or identity_opts):
            # Second pass identity lock if rules missed
            chosen = _pick_radio_by_rules(q_lower or "pronouns gender", options)
            if chosen:
                decision_source = "identity_lock_retry"
                print_lg("    [Questions] Identity lock (no AI): forced configured gender/pronouns")

        # ── AI forced-choice retry (stronger prompt) ──────────────────────
        if chosen is None and options and _aiClient is not None:
            forced_ans = _ai_forced_choice(
                question_text or name, opts_texts, _current_job_context
            )
            if forced_ans:
                selected_label = _choose_by_ai_answer(forced_ans, opts_texts)
                for r, lbl in options:
                    if lbl == selected_label:
                        chosen = r
                        break
                if chosen:
                    print_lg(f"    [Questions] AI forced-choice: {forced_ans!r} → {selected_label!r}")
                    decision_source = "ai_forced_choice"

        # ── Last resort: heuristic pick (only if AI completely unavailable) ─
        if chosen is None and options:
            chosen = _best_guess_radio_option(question_text or name, options)
            if chosen:
                decision_source = "best_guess_never_skip"
                chosen_label = next((lbl for r, lbl in options if r is chosen), "?")
                print_lg(f"    [Questions] ⚠ Heuristic fallback (AI unavailable): {chosen_label!r}")

        # Click the chosen radio
        if chosen:
            try:
                if not chosen.is_checked():
                    chosen.click(force=True)
                chosen_label = next((lbl for r, lbl in options if r is chosen), "?")
                print_lg(f"    [Questions] ✓ Selected: {chosen_label!r}")
                log_training_event(
                    "question_answered",
                    job=_current_job_meta,
                    control_type="radio_group_name",
                    question=question_text or name,
                    options=opts_texts,
                    selected=chosen_label,
                    decision_source=decision_source or "unknown",
                    dom=element_dom_snapshot(page, chosen, {"option_label": chosen_label}),
                )
            except Exception as e:
                print_lg(f"    [Questions] Failed to click radio: {e}")
                log_training_event(
                    "question_answer_failed",
                    job=_current_job_meta,
                    control_type="radio_group_name",
                    question=question_text or name,
                    options=opts_texts,
                    selected=next((lbl for r, lbl in options if r is chosen), "?"),
                    decision_source=decision_source or "unknown",
                    error=f"{type(e).__name__}: {e}",
                    page=page_dom_snapshot(page, limit=35),
                )


def _safe_date_answer_for_question(hint: str, desired_start_date_str: str, today_str: str) -> str | None:
    """
    Return a date only when the question can be answered safely without
    inventing credentials or check-completion dates.

    Indeed SmartApply often shows a bare ``Date *`` next to Name / Employee ID
    on attestation steps (GEI and similar).  That is today's signature date,
    not a work-history From/Until and not DOB.
    """
    h = (hint or "").lower()
    h_norm = re.sub(r"[\s*:\-_/]+", " ", h).strip()
    # Never invent DOB / credential / check-completion dates.
    if any(k in h for k in (
        "date of birth", "birth date", "birthdate", "dob", "birthday",
        "date de naissance", "né le", "nee le",
    )):
        return None
    if any(k in h for k in ("criminal record", "vulnerable sector", "background check",
                             "police check", "record check")):
        return None
    if any(k in h for k in ("date completed", "completed date", "completion date",
                             "issued date", "expiry date", "expiration date",
                             "license date", "licence date", "certification date")):
        return None
    # Work-history range fields (From / Until / End) — leave blank for history filler.
    # Do NOT treat plain "start date" as history; that is usually availability.
    if any(k in h for k in (
        "until", "end date", "to date", "date from", "date to",
        "employment start", "employment end", "job start", "job end",
        "from date", "work history",
    )) or re.search(r"\bfrom\b", h) and re.search(r"\b(to|until|end)\b", h):
        return None
    if any(k in h for k in ("interview", "phone screen", "screening call",
                             "availability for a call", "available for a call")):
        return today_str
    if any(k in h for k in ("start date", "desired start", "available to start",
                             "availability date", "date available", "available date",
                             "earliest available", "date you can start", "earliest date",
                             "earliest start", "when can you start", "disponibilité",
                             "date de début", "disponible")):
        return desired_start_date_str
    if any(k in h for k in ("today", "date signed", "signature date", "sign date",
                             "date of signature", "dated", "jour", "date d'aujourd",
                             "today's date", "todays date", "current date")):
        return today_str
    # Bare "Date" / "Date *" attestation control (Indeed GEI pattern).
    if h_norm in {"date", "date de", "la date", "the date"} or re.fullmatch(
        r"(the )?date( field| here)?", h_norm
    ):
        return today_str
    # Label is effectively just "date" with id/name noise like input-q_…-date
    if re.search(r"(^|[\s_])date([\s_*]|$)", h) and not any(
        k in h for k in ("birth", "start", "end", "from", "until", "issued", "expir", "completed")
    ):
        return today_str
    return None


def _date_format_variants(iso_date: str, hint: str = "") -> list[str]:
    """Produce YYYY-MM-DD plus locale formats Indeed datepickers sometimes want."""
    iso = (iso_date or "").strip()
    out: list[str] = []
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        y, m, d = iso.split("-")
        out.extend([
            iso,                 # native <input type=date>
            f"{m}/{d}/{y}",      # US
            f"{d}/{m}/{y}",      # CA/EU style
            f"{m}-{d}-{y}",
            f"{d}-{m}-{y}",
        ])
    elif iso:
        out.append(iso)
    h = (hint or "").lower()
    # Prefer placeholder-driven order.
    if "mm/dd" in h or "mm-dd" in h:
        out = sorted(out, key=lambda v: 0 if re.match(r"\d{2}/\d{2}/\d{4}", v) else 1)
    elif "dd/mm" in h:
        out = sorted(out, key=lambda v: 0 if re.match(r"\d{2}/\d{2}/\d{4}", v) and v[0:2] > "12" else 1)
    # Always try ISO first for type=date (browser native).
    if iso in out:
        out = [iso] + [v for v in out if v != iso]
    # de-dupe preserve order
    seen = set()
    uniq = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _fill_date_control(page, element, iso_date: str, hint: str = "") -> bool:
    """
    Set a date control without leaving Chrome's native datepicker open.

    Playwright ``fill()`` on ``input[type=date]`` often opens the OS/Chrome
    calendar widget, which then steals focus and blocks Continue (Indeed GEI).
    Prefer the native value setter + input/change events, then Escape/Tab/blur.
    """
    if not element or not iso_date:
        return False
    variants = _date_format_variants(iso_date, hint)
    typ = ""
    try:
        typ = (element.get_attribute("type") or "").lower()
    except Exception:
        typ = ""
    # Native date inputs only accept YYYY-MM-DD.
    if typ == "date":
        variants = [iso_date] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso_date) else variants

    for value in variants:
        try:
            ok = element.evaluate(
                """
                (el, value) => {
                    const proto = el instanceof HTMLTextAreaElement
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                    try { el.blur(); } catch (e) {}
                    return (el.value || "").trim() === value
                        || (el.value || "").replace(/\\//g, "-").includes(value.slice(0, 4));
                }
                """,
                value,
            )
            if ok:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                try:
                    page.keyboard.press("Tab")
                except Exception:
                    pass
                try:
                    element.evaluate("el => el.blur()")
                except Exception:
                    pass
                # Confirm value stuck.
                try:
                    current = (element.input_value() if hasattr(element, "input_value")
                               else element.get_attribute("value")) or ""
                except Exception:
                    current = ""
                if current.strip():
                    return True
        except Exception:
            continue

    # Last resort: fill() (may open picker) then dismiss.
    try:
        element.fill(iso_date)
        for key in ("Escape", "Tab"):
            try:
                page.keyboard.press(key)
            except Exception:
                pass
        try:
            element.evaluate("el => el.blur()")
        except Exception:
            pass
        try:
            current = (element.input_value() if hasattr(element, "input_value")
                       else element.get_attribute("value")) or ""
        except Exception:
            current = ""
        return bool(str(current).strip())
    except Exception:
        return False


def _is_interview_availability_question(text: str) -> bool:
    h = (text or "").lower()
    if any(k in h for k in (
        "tell me about",
        "tell us about",
        "describe your",
        "describe your experience",
        "your experience in",
        "experience with",
        "experience using",
    )):
        return False
    if any(k in h for k in ("interview", "phone screen", "screening call",
                             "availability for a call", "available for a call")):
        return True
    if "2-3 dates" in h or "two to three dates" in h:
        return True
    if "date" in h and any(k in h for k in ("time range", "time ranges", "interview", "call")):
        return True
    return False


def _is_work_availability_question(text: str) -> bool:
    h = (text or "").lower()
    if _is_interview_availability_question(h):
        return False
    return any(k in h for k in (
        "weekly availability",
        "work availability",
        "what is your availability",
        "availability in terms of days",
        "working hours",
        "hours are you available",
        "available to work",
        "schedule availability",
    ))


def _interview_availability_answer(include_times: bool = False) -> str:
    """
    Give near-future interview availability from the actual run date.
    Avoid stale AI-generated dates by deriving all dates from datetime.now().
    """
    dates = []
    day = datetime.now()
    while len(dates) < 3:
        # Include today when possible, then nearby business days.
        if day.weekday() < 5:
            dates.append(day)
        day += timedelta(days=1)

    formatted_dates = [
        f"{d.strftime('%A, %B')} {d.day}, {d.year}" for d in dates
    ]
    if include_times:
        return (
            "I am available for an interview on "
            f"{formatted_dates[0]}, {formatted_dates[1]}, or {formatted_dates[2]}. "
            "I am flexible during regular business hours."
        )
    return ", ".join(formatted_dates)


def _safe_text_answer_for_question(hint: str, full_name: str,
                                   desired_start_date_str: str,
                                   today_str: str) -> tuple[str | None, str]:
    """
    Deterministic answers for sensitive employer questions. Conservative by
    design: no invented dates, no invented certifications, clear "No" for
    adverse-history questions, and clear consent/ability where the question
    asks whether documentation can be provided if required.

    The IT-specific block at the top is anchored on `all resumes/resumedump.txt`
    plus successful answer patterns from `data/training/it_training_data.json`.
    """
    h = (hint or "").lower()

    gender_value = str(_configured_gender or "").strip().lower()
    if any(k in h for k in ("identify as a woman", "identify as female", "are you a woman")):
        return ("Yes" if gender_value == "female" else "No"), "configured_gender_identity"
    if any(k in h for k in ("identify as a man", "identify as male", "are you a man")):
        return ("Yes" if gender_value == "male" else "No"), "configured_gender_identity"
    if any(k in h for k in ("what is your gender", "gender identity", "your gender")):
        return str(_configured_gender or "Prefer not to say"), "configured_gender"
    if "pronoun" in h:
        return _configured_pronoun_answer(), "configured_pronouns"

    if any(k in h for k in ("what languages", "which languages", "list languages")):
        return _configured_languages_summary(), "configured_languages"

    speaks_language = _candidate_speaks_language(h)
    if speaks_language is not None and any(
        k in h for k in ("speak", "bilingual", "fluent", "proficient", "language")
    ):
        if "scale of 0 to 10" in h and "english" in h:
            return "10", "configured_english_proficiency"
        return ("Yes" if speaks_language else "No"), "configured_language_ability"

    if any(k in h for k in ("if yes", "if applicable", "employee id", "hire date", "who and how are you related", "relation details", "license number", "expiry date", "if you do not", "write 'n/a'")):
        return "N/A", "safe_conditional_na"

    if any(k in h for k in ("recruiter or referral", "referral contact",
                            "referral name", "referrer", "referred by")):
        return "N/A", "no_referral"

    if any(k in h for k in ("how did you hear", "how did you find out",
                            "heard about this role", "heard about this job",
                            "heard about our company", "heard about this opportunity")):
        link = str((_current_job_meta or {}).get("job_link")
                   or (_current_job_meta or {}).get("job_href") or "").lower()
        source = str((_current_job_meta or {}).get("source") or "").lower()
        if "glassdoor" in source or "glassdoor" in link:
            return "Glassdoor", "application_source"
        if "indeed" in source or "indeed" in link:
            return "Indeed", "application_source"
        return "Online job posting", "application_source"

    # ── Numeric "how many years of X" questions → just the YOE number ─────
    # Prevents long-prose IT-specific answers from being typed into a number/text
    # field (e.g. "how many years of active directory experience" must be "3",
    # not a paragraph about RADIUS/EAP-TLS).
    if (
        any(k in h for k in ("how many years", "how many year",
                              "years of experience", "years of relevant",
                              "yrs of experience", "number of years"))
        and not any(k in h for k in ("describe", "tell us about", "tell me about",
                                      "explain", "what experience", "summarise",
                                      "summarize"))
    ):
        try:
            from config.questions import years_of_experience as _yoe_local
        except ImportError:
            _yoe_local = 3

        known_skills = [
            "aws", "python", "sql", "networking", "security", "helpdesk", "help desk",
            "cisco", "windows server", "linux", "docker", "git", "vmware", "active directory",
            "ossec", "splunk", "wazuh", "bell", "vancouver coastal health", "porter",
            "customer service", "tech support", "technical support", "sales",
            "customer-facing", "wifi", "wi-fi", "troubleshooting", "hardware", "software",
            "btech", "degree", "kpu", "systems administration", "network administration",
            "cybersecurity", "information technology", "qa", "quality assurance", "testing",
            "tester", "java", "spring boot", "ansible", "bash", "terraform", "cloud", "ad ds",
            "gpo", "nps", "radius", "eap-tls", "firewall", "nmap", "wireshark", "siem", "hids",
            "autopsy", "ftk", "endpoint", "routing", "switching", "postman", "api", "rest"
        ]
        
        general_experience_words = [
            "work", "job", "employment", "professional", "overall", "industry", "career",
            "total", "relevant", "similar", "customer", "support", "position", "role"
        ]
        
        unmatched_skills = [
            "react", "angular", "vue", "scala", "ruby", "rust", "go", "golang", "c#", "dotnet",
            ".net", "c++", "php", "laravel", "swift", "kotlin", "kubernetes", "k8s", "salesforce",
            "sap", "oracle", "powerbi", "tableau", "power bi", "azure", "gcp", "google cloud"
        ]

        if any(u in h for u in unmatched_skills):
            return "0", "rules_unmatched_skill_zero_experience"

        if any(k in h for k in known_skills) or any(g in h for g in general_experience_words):
            return str(_yoe_local), "rules_years_of_experience"

        return "0", "rules_safe_fallback_zero_experience"

    # ── LinkedIn / professional profile URL ───────────────────────────────
    # Covers textarea path too (e.g. "please copy/paste your linkedin here").
    if "linkedin" in h:
        try:
            from config.questions import professional_profile_url as _ppu, website as _ws
            url = _ppu or _ws or ""
            if url:
                return url, "rules_linkedin_url"
        except ImportError:
            pass

    if any(k in h for k in ("portfolio", "github", "project link", "project links",
                            "work samples", "code samples", "personal website")):
        try:
            from config.questions import website as _ws, professional_profile_url as _ppu
            url = _ws or _ppu or ""
            if url:
                return url, "rules_portfolio_url"
        except ImportError:
            pass

    # ── IT-specific deterministic answers (anchored on resume + training dump) ──
    # These run BEFORE the legacy general-work patterns so IT-relevant questions
    # get the right answer immediately and don't burn LLM tokens.

    def _has_experience_marker(text: str) -> bool:
        return any(k in text for k in (
            "experience", "experiance", "familiar", "worked with", "have you used",
            "have you worked", "describe your", "tell me about your", "tell us about your",
            "background", "knowledge of", "proficien", "skilled in", "skills with",
        ))

    if any(k in h for k in (
        "list your technical skills", "your technical skills",
        "what technical skills", "describe your technical",
        "technical skills do you have", "primary technical skills",
    )):
        return (
            "Networking & Security: Cisco IOS, VLANs, VPNs, OSPF, BGP, 802.1X, WPA3, "
            "Wi-Fi 6, firewall policy, Nmap, Wireshark. Security Operations: Splunk, "
            "Wazuh, OSSEC, SIEM, Autopsy, FTK, endpoint hardening. Cloud & Virtualization: "
            "AWS (VPC, EC2, S3, IAM, CloudFormation), Docker, Terraform, VMware, Hyper-V. "
            "Systems & Automation: Windows Server (AD DS, GPO, NPS), Linux (Ubuntu/CentOS), "
            "Python (Boto3), Bash, Ansible. Dev & Tools: Java, SQL, REST APIs, Git, Postman."
        ), "safe_it_technical_skills"

    if any(k in h for k in (
        "aws certifi", "aws certification", "aws cert ",
        "certified solutions architect", "cloud practitioner",
        "solutions architect associate",
    )):
        return (
            "Yes — AWS Certified Solutions Architect – Associate (Nov 2024) and "
            "AWS Cloud Practitioner (Feb 2024)."
        ), "safe_aws_certifications"

    if any(k in h for k in (
        "certifications you hold", "certifications you have",
        "list your certifications", "what certifications",
        "current certifications", "professional certifications",
        "certifications, affiliations", "professional memberships",
    )):
        return (
            "AWS Certified Solutions Architect – Associate (Nov 2024); "
            "AWS Cloud Practitioner (Feb 2024)."
        ), "safe_certifications_list"

    if any(k in h for k in ("aws", "amazon web services", "ec2", "vpc", "s3 bucket",
                             "cloudformation", "iam")) and _has_experience_marker(h):
        return (
            "AWS Certified Solutions Architect – Associate with hands-on experience in "
            "VPC design (public/private subnets, NAT gateways, bastion hosts), EC2, S3, "
            "IAM, and CloudFormation, including the STaaS Cloud-Based Smart Drive project "
            "(Python/Boto3, Spring Boot, Docker)."
        ), "safe_aws_experience"

    if any(k in h for k in ("cisco", " vlan", "vlans", " vpn", "vpns",
                             "ospf", "bgp", "routing and switching",
                             "firewall polic")) and _has_experience_marker(h):
        return (
            "Hands-on lab experience with Cisco IOS, VLANs, VPNs, OSPF, BGP, 802.1X, "
            "WPA3, and firewall policy from KPU Networking Technologies coursework and "
            "the Cybersecurity & Identity Infrastructure Lab project."
        ), "safe_cisco_networking_experience"

    if any(k in h for k in ("siem", "splunk", "wazuh", "ossec")) and _has_experience_marker(h):
        return (
            "Lab experience with Splunk, Wazuh, and OSSEC HIDS for centralized monitoring, "
            "alerting, file-integrity checking, and rootkit detection."
        ), "safe_siem_experience"

    if any(k in h for k in ("wireshark", "packet analysis", "packet capture",
                             "network forensics")) and _has_experience_marker(h):
        return (
            "Hands-on experience with Wireshark for packet analysis and network "
            "forensics during KPU labs and structured RADIUS/EAP-TLS validation testing."
        ), "safe_wireshark_experience"

    if any(k in h for k in ("linux", "ubuntu", "centos", "rhel",
                             "red hat")) and _has_experience_marker(h):
        return (
            "Daily Linux experience across Ubuntu and CentOS for systems labs, OSSEC "
            "HIDS deployments, Docker containers, and Python/Bash automation."
        ), "safe_linux_experience"

    if any(k in h for k in ("windows server", "active directory", "ad ds",
                             " gpo", " nps", "group policy")) and _has_experience_marker(h):
        return (
            "Hands-on Windows Server experience with Active Directory (AD DS), Group "
            "Policy, and NPS RADIUS authentication, deployed and validated for the KPU "
            "Identity Infrastructure Lab using EAP-TLS certificate-based authentication."
        ), "safe_windows_server_experience"

    if any(k in h for k in ("python ", " python", "boto3", "bash script",
                             "ansible", "scripting language", "automation tools",
                             "infrastructure as code", "terraform")) and _has_experience_marker(h):
        return (
            "Python (Boto3, Spring Boot integration) and Bash scripting for cloud "
            "automation, Ansible playbooks for configuration management, plus Java for "
            "an FIM tool with SHA-256 hashing."
        ), "safe_scripting_experience"

    # Backup / disaster-recovery platforms — be honest. Candidate has AWS S3
    # lifecycle / snapshot lab experience but no production backup-platform
    # deployment (e.g. Veeam, Datto, Cohesity, Acronis). Without this rule the
    # LLM previously hallucinated "AWS, Veracode" (Veracode is SAST, not backup).
    if any(k in h for k in ("backup platform", "backup solution", "backup software",
                             "backup tool", "backup and restore", "backup/restore",
                             "veeam", "datto", "cohesity", "acronis", "rubrik",
                             "commvault", "barracuda backup", "carbonite",
                             "disaster recovery platform")):
        return (
            "Hands-on AWS lab experience with S3 versioning, lifecycle policies, "
            "and EBS/RDS snapshots for cloud-native data protection. No "
            "production deployment of dedicated backup platforms like Veeam, "
            "Datto, Cohesity, or Acronis yet — comfortable learning the team's "
            "preferred tooling on the job."
        ), "safe_backup_platforms_honest"

    if any(k in h for k in ("docker", "container", "kubernetes",
                             "k8s")) and _has_experience_marker(h):
        return (
            "Hands-on Docker experience containerizing a Spring Boot backend integrated "
            "with S3 pre-signed URLs in the STaaS Cloud-Based Smart Drive project."
        ), "safe_docker_experience"

    if any(k in h for k in ("network administration", "network admin",
                             "network engineer", "networking",
                             "enterprise network")) and _has_experience_marker(h):
        return (
            "KPU Bachelor of Technology specialization in Network Administration & "
            "Security with lab experience in Cisco IOS, VLAN/VPN deployment, OSPF/BGP "
            "routing, firewall policy, RADIUS authentication, and Wireshark analysis."
        ), "safe_network_admin_experience"

    if (
        any(k in h for k in ("years of qa", "years of quality assurance",
                              "years of testing", "qa experience"))
        and any(k in h for k in ("how many", "year", "yrs", "years"))
    ):
        return "2", "safe_qa_yoe"

    if (
        any(k in h for k in ("ticketing", "ticket system", "service now",
                              "servicenow", "jira", "zendesk", "freshservice"))
        and _has_experience_marker(h)
    ):
        return (
            "Experience using ticketing systems for resolution logging and escalation "
            "during three years of Bell Canada technical support and during patient-"
            "tracking workflows at Vancouver Coastal Health."
        ), "safe_ticketing_experience"

    if any(k in h for k in ("ios", "iphone", "android device", "mobile device",
                             "wi-fi troubleshoot", "wifi troubleshoot")) and _has_experience_marker(h):
        return (
            "Three years at Bell Canada diagnosing iOS/Android device setup, software "
            "errors, Wi-Fi/Bluetooth connectivity, and network configuration across "
            "20+ daily client interactions."
        ), "safe_mobile_support_experience"

    # ── End IT-specific block ──

    if _is_work_availability_question(h):
        return str(weekly_work_availability), "configured_weekly_work_availability"

    if (
        any(k in h for k in ("travel to the us", "travel to us", "travel freely to us",
                             "freely travel to the us", "freely travel to us"))
        or (
            "us" in h and "travel" in h
            and any(k in h for k in ("visa", "permit", "permits", "without need"))
        )
    ):
        return ("Yes" if can_freely_travel_to_us else "No"), "configured_us_travel"

    if any(k in h for k in ("16 years or older", "aged 16", "age 16", "16 or older",
                             "16 ans", "seize ans")):
        return ("Yes" if meets_minimum_work_age else "No"), "configured_minimum_work_age"

    if any(k in h for k in ("legal documents to work", "documents légaux",
                             "documents legaux", "legally authorized",
                             "authorized to work", "eligible to work",
                             "legal right to work", "work in canada",
                             "citizen or", "citizen,", "citizen/permanent",
                             "work visa", "work permit", "valid work permit",
                             "permanent resident", "pr status",
                             "status in canada", "immigration status")):
        return ("Yes" if has_legal_work_documents else "No"), "configured_work_documents"

    if any(k in h for k in ("vaccinated against covid", "vaccinated against covid-19",
                             "covid-19 vaccine", "covid vaccine", "covid vaccination")):
        return ("Yes" if is_vaccinated_against_covid else "No"), "configured_covid_vaccine"

    if any(k in h for k in ("minimum 1 year", "at least 1 year", "1 year experience",
                             "one year experience")) and any(k in h for k in (
                                 "dental receptionist", "receptionist in a health office",
                                 "health office", "medical office", "moa",
                                 "oral surgery office",
                             )):
        if "dental" in h or "oral surgery" in h:
            return ("Yes" if has_dental_reception_experience else "No"), "configured_dental_reception_experience"
        return ("Yes" if has_health_office_reception_experience else "No"), "configured_health_office_reception_experience"

    if any(k in h for k in ("valid driver's license", "valid drivers license",
                             "valid driving licence", "valid driver's licence",
                             "driver's license", "drivers license", "driving licence")):
        return ("Yes" if has_valid_drivers_license else "No"), "configured_drivers_license"

    if any(k in h for k in ("reliable vehicle", "access to a reliable vehicle")):
        return ("Yes" if has_reliable_vehicle else "No"), "configured_reliable_vehicle"

    if any(k in h for k in ("stand for long periods", "standing for long periods",
                             "long periods of time")):
        return ("Yes" if can_stand_for_long_periods else "No"), "configured_standing"

    if any(k in h for k in ("lift up to 70", "weighing up to 70", "up to 70 lb",
                             "up to 70 lbs", "70 lb", "70 lbs")):
        return ("Yes" if can_lift_up_to_70_lbs else "No"), "configured_lifting"

    if any(k in h for k in ("tuesday to saturday", "tuesday through saturday",
                             "full-time (40 hours", "full time (40 hours",
                             "40 hours")):
        return ("Yes" if can_work_full_time_40_hours else "No"), "configured_full_time_40"

    if any(k in h for k in ("evenings and weekends", "evening and weekend",
                             "work evenings", "work weekends", "available weekends",
                             "available to work weekends", "weekend availability",
                             "work on weekends")):
        answer = bool(can_work_evenings if "evening" in h else True) and bool(can_work_weekends if "weekend" in h else True)
        return ("Yes" if answer else "No"), "configured_evening_weekend_availability"

    if any(k in h for k in ("in-person", "in person", "come to the office",
                             "office for 4 days", "4 days a week", "11:00 am to 7:30 pm",
                             "11am to 7:30pm", "11:00am to 7:30pm")):
        return ("Yes" if can_work_in_person else "No"), "configured_in_person"

    if any(k in h for k in ("travel to our three locations", "travel to 3 locations",
                             "travel between locations", "travel to multiple locations")):
        return ("Yes" if can_travel_between_local_locations else "No"), "configured_local_travel"

    if any(k in h for k in ("travel up to 1 hour", "travel up to one hour",
                             "commute up to 1 hour", "commute up to one hour")):
        return ("Yes" if can_commute_up_to_one_hour else "No"), "configured_one_hour_commute"

    if any(k in h for k in ("school", "institution", "university", "college", "kpu")):
        if any(k in h for k in ("where", "name", "which", "attend", "attended", "study")):
            return KPU_SCHOOL_NAME, "hardcoded_kpu_school"

    if any(k in h for k in ("desired pay", "desired salary", "expected salary",
                             "salary expectation", "salary expectations",
                             "base salary", "base pay", "compensation expectation",
                             "annually in cad", "annual salary", "wage",
                             "enter the amount", "amount", "salary amount", "rate amount")):
        return desired_salary_str, "safe_desired_salary"

    if any(k in h for k in ("where did you study", "what program",
                             "what is your graduation", "graduation date",
                             "are you studying", "education program")):
        return (
            "I am studying Bachelor of Technology in Information Technology at "
            f"{KPU_SCHOOL_NAME}, specializing in Network Administration "
            "and Security, with expected graduation in December 2026."
        ), "safe_education_program"

    if any(k in h for k in ("ai coding assistant", "coding assistants",
                             "github copilot", "claude code")):
        return (
            "I use AI tools thoughtfully for brainstorming, debugging, and documentation, "
            "while verifying code, testing changes, and relying on my own technical judgment."
        ), "safe_ai_tools"

    if any(k in h for k in ("front-end experience", "frontend experience",
                             "react", "javascript developer", "full-stack javascript",
                             "full stack javascript")):
        return (
            "I have academic and project experience with web development, JavaScript, "
            "REST APIs, and debugging, and I am comfortable learning the team's frontend stack."
        ), "safe_frontend_experience"

    if any(k in h for k in ("ios application", "android application", "mobile application",
                             "offline-first")):
        return (
            "I do not have professional mobile app shipping experience, but I have strong "
            "software, systems, and troubleshooting fundamentals and can learn the stack quickly."
        ), "safe_mobile_experience"

    if any(k in h for k in ("commute", "commuting", "in-office", "in office",
                             "on-site", "onsite", "work from office",
                             "downtown vancouver")):
        return "Yes, I am comfortable commuting to the office.", "safe_commute_yes"

    if any(k in h for k in ("relocate", "relocation", "willing to travel")):
        return "Yes, I am open to reasonable travel or relocation for the right role.", "safe_travel_relocation"

    if _is_interview_availability_question(h):
        include_times = any(k in h for k in ("time", "times", "slot", "slots", "hours"))
        return _interview_availability_answer(include_times), "safe_interview_availability"

    if any(k in h for k in ("start date", "desired start", "available to start",
                             "availability date", "date available", "available date",
                             "earliest available")):
        return desired_start_date_str, "safe_start_date"

    if any(k in h for k in ("criminal record", "vulnerable sector", "background check",
                             "police check", "record check", "screening")):
        if any(k in h for k in ("date completed", "completed date", "completion date",
                                 "within the past", "past 12 months", "last 12 months")):
            return "N/A - not completed in the past 12 months", "safe_no_check_date"
        # Consent / "can we run a check" style questions → Yes.
        if any(k in h for k in ("able to obtain", "able to provide", "can provide",
                                 "if offered", "if required", "consent", "authorize",
                                 "can we run", "may we run", "can we conduct",
                                 "may we conduct", "can we perform", "may we perform",
                                 "permit", "allow", "do you agree", "are you willing",
                                 "willing to undergo", "willing to consent",
                                 "willing to complete", "okay with",
                                 "ok with", "comfortable with",
                                 "run a background", "run background",
                                 "conduct a background", "perform a background")):
            return "Yes, I consent to a background check if offered the position.", "safe_consent_background_check"
        # "Do you have a criminal record?" style possession questions → No.
        if any(k in h for k in ("do you have", "have you ever", "have you been",
                                 "ever had", "currently have")):
            return "No", "safe_no_criminal_record"
        # Default for ambiguous "background check" wording: consent rather than refuse.
        return "Yes, I consent to a background check if offered the position.", "safe_consent_background_check"

    if any(k in h for k in ("convicted", "felony", "criminal charge", "criminal offence",
                             "criminal offense", "found responsible",
                             "protection of persons in care", "vulnerable adults")):
        return "No", "safe_no_adverse_history"

    if any(k in h for k in ("non-compete", "non compete", "restrictive covenant",
                             "bonded obligation", "employment bond",
                             "contractual obligation", "conflict of interest")):
        return "No", "safe_no_restrictive_obligation"

    if any(k in h for k in ("immunization", "immunisation", "vaccination", "vaccine",
                             "tb skin test", "clear chest x-ray")):
        if any(k in h for k in ("able to provide", "can provide", "if required",
                                 "proof", "documentation")):
            return "Yes, I can provide required documentation if needed.", "safe_can_provide_immunization"
        return "N/A", "safe_immunization_na"

    if any(k in h for k in ("legally entitled to work", "authorized to work",
                             "legally authorized", "eligible to work",
                             "work in canada", "legal right to work",
                             "citizen or", "citizen,", "citizen/permanent",
                             "work visa", "work permit", "valid work permit",
                             "permanent resident", "pr status",
                             "status in canada", "immigration status",
                             "autorisation de travailler", "travailler au canada",
                             "autorisation légale", "autorisation legale",
                             "droit légal", "droit legal", "permis de travail")):
        return "Yes, I am legally authorized to work in Canada.", "safe_work_authorized"

    if any(k in h for k in ("agreement", "acknowledgement", "acknowledge", "agree")):
        if any(k in h for k in ("sign", "signature", "name")):
            return full_name, "safe_agreement_signature"
        return "I agree", "safe_agreement_agree"

    if any(k in h for k in ("i confirm", "certify", "declare", "attest",
                             "true and complete", "truthful", "misrepresentation")):
        return full_name, "safe_attestation_signature"

    if any(k in h for k in ("date signed", "signature date", "today")):
        return today_str, "safe_signature_date"

    return None, ""


# ── Indeed custom searchable select lists (non-native <select>) ───────────────
# Chrome DevTools recording pattern:
#   trigger: [data-testid='input-q_{hash}-select-list-select-list']
#   option:  [data-testid='input-q_{hash}-select-list-{id}'] > span > span

_STATE_TO_PROVINCE = {
    "bc": "British Columbia",
    "ab": "Alberta",
    "sk": "Saskatchewan",
    "mb": "Manitoba",
    "on": "Ontario",
    "qc": "Quebec",
    "nb": "New Brunswick",
    "ns": "Nova Scotia",
    "pe": "Prince Edward Island",
    "nl": "Newfoundland and Labrador",
    "yt": "Yukon",
    "nt": "Northwest Territories",
    "nu": "Nunavut",
}


def _indeed_custom_select_prefix(trigger) -> str:
    testid = (trigger.get_attribute("data-testid") or "").strip()
    marker = "-select-list-select-list"
    if marker in testid:
        return testid.split(marker, 1)[0]
    return testid.rsplit("-select-list", 1)[0] if "-select-list" in testid else testid


def _indeed_custom_select_question_text(page, trigger, prefix: str) -> str:
    try:
        container = page.query_selector(f"div[data-testid*='{prefix}']")
        if container:
            text = (container.inner_text() or "").strip()
            if text:
                return text.split("\n", 1)[0].strip()
    except Exception:
        pass
    try:
        return (_get_question_context(page, trigger) or "").strip()
    except Exception:
        return ""


def _indeed_custom_select_is_filled(trigger) -> bool:
    try:
        text = re.sub(r"\s+", " ", (trigger.inner_text() or "")).strip().lower()
    except Exception:
        return False
    if not text:
        return False
    placeholders = (
        "select", "select an option", "search to select an option",
        "choose", "choose an option",
    )
    return text not in placeholders and "search to select" not in text


def _choose_custom_select_label(ctx: str, opts_text: list[str]) -> tuple[str, str]:
    ctx_l = (ctx or "").lower()
    if not opts_text:
        return "", ""

    if "province" in ctx_l:
        st = (state or "").strip().lower()
        target = _STATE_TO_PROVINCE.get(st, "")
        if target:
            for opt in opts_text:
                if opt.strip().lower() == target.lower():
                    return opt, "province_state_config"
        for opt in opts_text:
            if st and st in opt.lower().replace(".", ""):
                return opt, "province_abbrev_match"
        if current_city:
            for opt in opts_text:
                if current_city.lower() in opt.lower():
                    return opt, "province_city_match"

    if any(k in ctx_l for k in ("education", "degree", "diploma", "highest level")):
        chosen = _find_bachelors_option(opts_text)
        if chosen:
            return chosen, "education_bachelors"

    if any(k in ctx_l for k in ("how many years", "years of experience", "years have you", "professional experience")):
        try:
            exp = int(current_experience)
        except Exception:
            exp = 3
        for opt in opts_text:
            nums = [int(x) for x in re.findall(r"\d+", opt)]
            if len(nums) >= 2 and nums[0] <= exp <= nums[-1]:
                return opt, "experience_range"
            if len(nums) == 1 and nums[0] <= exp:
                return opt, "experience_range"
        for pref in ("1-3", "1–3", "less than 1", "1 year", "2 year", "3 year"):
            for opt in opts_text:
                if pref in opt.lower():
                    return opt, "experience_fallback"

    if any(k in ctx_l for k in ("country", "pays")) and any("canada" in o.lower() for o in opts_text):
        for opt in opts_text:
            if "canada" in opt.lower():
                return opt, "country_canada"

    guess = _best_guess_dropdown_option(ctx, [{"text": o} for o in opts_text], opts_text)
    if guess:
        return guess, "best_guess"
    return opts_text[0], "first_option"


def _collect_indeed_custom_select_options(page, prefix: str) -> tuple[list[str], dict[str, object]]:
    opts_text: list[str] = []
    option_map: dict[str, object] = {}
    selector = f"[data-testid^='{prefix}-select-list-']"
    for el in page.query_selector_all(selector):
        try:
            testid = (el.get_attribute("data-testid") or "")
            if testid.endswith("-select-list-select-list") or not el.is_visible():
                continue
            label = (el.inner_text() or "").strip()
            inner = el.query_selector("span span")
            if inner:
                inner_text = (inner.inner_text() or "").strip()
                if inner_text:
                    label = inner_text
            if not label or label in opts_text:
                continue
            opts_text.append(label)
            option_map[label] = inner or el
        except Exception:
            continue
    return opts_text, option_map


def _handle_indeed_custom_select_lists(page) -> None:
    """Handle Indeed SmartApply custom dropdowns (data-testid *-select-list-select-list)."""
    triggers = page.query_selector_all("[data-testid*='-select-list-select-list']")
    if not triggers:
        return

    for trigger in triggers:
        ctx = ""
        try:
            if not trigger.is_visible():
                continue
            if _indeed_custom_select_is_filled(trigger):
                continue

            prefix = _indeed_custom_select_prefix(trigger)
            if not prefix:
                continue

            ctx = _indeed_custom_select_question_text(page, trigger, prefix)
            trigger.click(force=True)
            time.sleep(0.45)

            opts_text, option_map = _collect_indeed_custom_select_options(page, prefix)
            if not opts_text:
                time.sleep(0.3)
                opts_text, option_map = _collect_indeed_custom_select_options(page, prefix)
            if not opts_text:
                print_lg(f"      [Questions] Custom select opened but no options for {ctx!r}")
                log_training_event(
                    "question_unresolved",
                    job=_current_job_meta,
                    control_type="custom_select",
                    question=ctx or "custom select",
                    reason="custom_select_has_no_options",
                    dom=element_dom_snapshot(page, trigger),
                )
                continue

            chosen_label, decision_source = _choose_custom_select_label(ctx, opts_text)
            if not chosen_label:
                log_training_event(
                    "question_unresolved",
                    job=_current_job_meta,
                    control_type="custom_select",
                    question=ctx or "custom select",
                    options=[{"text": option} for option in opts_text],
                    reason="no_safe_option_match",
                    dom=element_dom_snapshot(page, trigger),
                )
                continue

            clickable = option_map.get(chosen_label)
            if clickable is None:
                loc = page.get_by_text(chosen_label, exact=True)
                if loc.count() > 0:
                    clickable = loc.first
            if clickable is None:
                log_training_event(
                    "question_unresolved",
                    job=_current_job_meta,
                    control_type="custom_select",
                    question=ctx or "custom select",
                    options=[{"text": option} for option in opts_text],
                    attempted_answer=chosen_label,
                    decision_source=decision_source,
                    reason="selected_option_not_locatable_in_dom",
                    dom=element_dom_snapshot(page, trigger),
                )
                continue

            clickable.click(force=True)
            print_lg(
                f"      [Questions] Custom select {ctx[:80]!r} -> "
                f"{chosen_label!r} ({decision_source})"
            )
            log_training_event(
                "question_answered",
                job=_current_job_meta,
                control_type="custom_select",
                question=ctx,
                options=[{"text": o} for o in opts_text],
                selected=chosen_label,
                decision_source=decision_source,
            )
            time.sleep(0.25)
        except Exception as e:
            log_training_event(
                "question_answer_failed",
                job=_current_job_meta,
                control_type="custom_select",
                question=locals().get("ctx", "custom select"),
                error=f"{type(e).__name__}: {e}",
                page=page_dom_snapshot(page, limit=35),
            )


# ── Employer questions (the big one) ─────────────────────────────────────────

def _handle_employer_questions(page) -> None:
    print_lg("    [SmartApply] Answering employer questions…")
    time.sleep(_T_Q)
    
    from jobbots.core.shared_modules.indeed.smartapply import _extract_page_questions_schema
    from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
    
    questions = _extract_page_questions_schema(page)
    if questions:
        log_job_status_event_from_meta("questions_found", reason=f"Found {len(questions)} employer questions")

    today_str = datetime.now().strftime("%Y-%m-%d")
    desired_start_date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    full_name  = f"{first_name} {last_name}".strip() or "Applicant"
    global _employer_field_counts, _employer_additional_count
    if not _answered_field_keys:
        _handle_employer_questions._field_counts = {}
        _handle_employer_questions._additional_count = 0
    _employer_field_counts = getattr(_handle_employer_questions, "_field_counts", {})
    _employer_additional_count = getattr(_handle_employer_questions, "_additional_count", 0)
    _handle_employer_questions._field_counts = _employer_field_counts

    try:
        from jobbots.core.shared_modules.indeed.employer_history import fill_work_history
        fill_work_history(page)
    except Exception as exc:
        print_lg(f"      [WorkHistory] Could not fill employer history: {exc}")

    # Checkboxes (consent / agree / understand)
    for cb in page.query_selector_all("input[type='checkbox']"):
        try:
            cb_ctx = _get_question_context(page, cb).lower()
            if any(k in cb_ctx for k in (
                "save my answers", "pre-filling", "prefilling",
                "saving my self-identification", "self-identification answers",
            )):
                continue
            if not cb.is_checked():
                cb.click(force=True)
        except Exception:
            pass

    _handle_indeed_custom_select_lists(page)

    # Dropdowns (native <select>)
    for sel_el in page.query_selector_all("select"):
        ctx = ""
        opts_info = []
        try:
            if not sel_el.is_visible():
                continue
            opts_info = sel_el.evaluate(
                "el => Array.from(el.options).filter(o => o.value)"
                ".map(o => ({text: o.text.trim(), value: o.value}))"
            )
            if not opts_info:
                continue
            opts_text = [o['text'] for o in opts_info]
            ctx = _get_question_context(page, sel_el)
            ctx_l = (ctx or "").lower()
            select_dom_snapshot = element_dom_snapshot(page, sel_el)
            log_training_event(
                "question_detected",
                job=_current_job_meta,
                control_type="select",
                question=ctx,
                options=opts_info,
                dom=select_dom_snapshot,
            )

            chosen_label = None
            decision_source = "rules"
            opts_joined_l = " ".join(
                f"{o.get('text', '')} {o.get('value', '')}" for o in opts_info
            ).lower()

            if (
                ("afghanistan (+93)" in opts_joined_l and "canada (+1)" in opts_joined_l)
                or any(k in ctx_l for k in ("phone country", "mobile country", "country code"))
            ):
                for opt in opts_info:
                    opt_text = opt.get("text", "")
                    opt_value = opt.get("value", "")
                    opt_l = f"{opt_text} {opt_value}".lower()
                    if "canada (+1)" in opt_l or opt_value.strip().upper() == "CA +1":
                        chosen_label = opt_text
                        decision_source = "phone_country_canada"
                        break
            elif any(k in ctx_l for k in ("country", "pays")) and "canada" in opts_joined_l:
                for opt in opts_text:
                    if opt.strip().lower() == "canada" or "canada" in opt.lower():
                        chosen_label = opt
                        decision_source = "country_canada"
                        break
            elif any(k in ctx_l for k in ("currency", "devise")) or "cad" in opts_joined_l:
                for opt in opts_text:
                    if "cad" in opt.lower() or "canadian" in opt.lower():
                        chosen_label = opt
                        decision_source = "currency_cad"
                        break
            elif any(k in ctx_l for k in ("currently located", "currently reside", "où résidez-vous", "ou residez-vous", "where do you reside", "location", "résidez-vous")):
                loc_prefs = []
                if current_city:
                    loc_prefs.append(current_city.lower())
                if state:
                    loc_prefs.append(state.lower())
                    if state.lower() == "bc":
                        loc_prefs.extend(("british columbia", "colombie-britannique"))
                loc_prefs.extend(("vancouver", "surrey", "british columbia", "colombie-britannique", "canada"))
                for pref in loc_prefs:
                    for opt in opts_text:
                        if pref in opt.lower():
                            chosen_label = opt
                            decision_source = "location_match"
                            break
                    if chosen_label:
                        break
            elif _looks_like_gender_option_set(opts_text):
                chosen_label = _configured_gender_option_label(opts_text)
                if chosen_label:
                    decision_source = "configured_gender_options"
            elif (
                any(k in ctx_l for k in (
                    "education", "degree", "qualification", "diploma",
                    "highest level", "highest obtained", "highest education",
                    "education obtained",
                ))
                or (
                    any("ged" in opt.lower() for opt in opts_text)
                    and any("bachelor" in opt.lower() or "baccalaureate" in opt.lower() for opt in opts_text)
                )
                or _looks_like_education_options(opts_text)
            ):
                chosen_label = _find_bachelors_option(opts_text)
                if chosen_label:
                    decision_source = "education_bachelors"
            elif _is_non_english_language_question(ctx_l):
                speaks_language = _candidate_speaks_language(ctx_l)
                target_choices = (
                    ("yes", "oui", "true", "fluent", "advanced", "native")
                    if speaks_language
                    else ("no", "non", "false")
                )
                for pref in target_choices:
                    for opt in opts_text:
                        if pref in _norm_choice(opt):
                            chosen_label = opt
                            decision_source = "configured_language_ability"
                            break
                    if chosen_label:
                        break
            elif ("english" in ctx_l or "anglais" in ctx_l) and not any(k in ctx_l for k in ("year", "how many", "experience", "expéri")):
                for pref in ("advanced", "native", "fluent", "mother tongue", "professional", "bilingual",
                              "avancé", "avance", "maternelle", "courant", "bilingue", "yes", "oui"):
                    for opt in opts_text:
                        if pref in opt.lower():
                            chosen_label = opt
                            break
                    if chosen_label:
                        break
                if not chosen_label:
                    chosen_label = opts_text[0]
            elif any(k in ctx_l for k in ("pronoun", "pronouns")):
                pronoun_answer = _configured_pronoun_answer()
                pronoun_prefs = [pronoun_answer]
                if str(_configured_gender or "").strip().lower() == "male":
                    pronoun_prefs.extend(("he/him", "he / him", "he"))
                elif str(_configured_gender or "").strip().lower() == "female":
                    pronoun_prefs.extend(("she/her", "she / her", "she"))
                elif str(_configured_gender or "").strip().lower() == "other":
                    pronoun_prefs.extend(("they/them", "they / them", "they"))
                pronoun_prefs.extend(("prefer not", "do not wish", "decline"))
                for pref in pronoun_prefs:
                    for opt in opts_text:
                        if pref in opt.lower():
                            chosen_label = opt
                            decision_source = "configured_pronouns"
                            break
                    if chosen_label:
                        break
            elif any(k in ctx_l for k in ("gender", "sex", "sexe")):
                chosen_label = _configured_gender_option_label(opts_text)
                if chosen_label:
                    decision_source = "configured_gender"
            elif (
                any(
                    pref in " ".join(opts_text).lower()
                    for pref in (
                        "hispanic", "hispanique", "latino", "racial", "ethnic",
                        "ethnique", "origine", "race",
                    )
                )
                or any(k in ctx_l for k in ("race", "ethnicity", "ethnic", "origine ethnique"))
            ):
                for pref in ("refus", "decline", "prefer not", "do not wish", "not disclose"):
                    for opt in opts_text:
                        if pref in opt.lower():
                            chosen_label = opt
                            decision_source = "demographic_decline"
                            break
                    if chosen_label:
                        break
            else:
                for pref in ("indeed", "glassdoor"):
                    for opt in opts_text:
                        if pref in opt.lower():
                            chosen_label = opt
                            decision_source = "application_source"
                            break
                    if chosen_label:
                        break

            if not chosen_label:
                opt_norms = [_norm_choice(opt) for opt in opts_text]
                if any(k in ctx_l for k in ("sponsorship", "visa", "work permit")):
                    for opt, opt_norm in zip(opts_text, opt_norms):
                        if opt_norm in ("no", "non", "false") or "do not require" in opt_norm:
                            chosen_label = opt
                            decision_source = "sponsorship_no"
                            break
                elif any(k in ctx_l for k in ("authorized to work", "eligible to work",
                                               "legally authorized", "work in canada",
                                               "legal right to work",
                                               "citizen or", "citizen,", "citizen/permanent",
                                               "work visa", "work permit",
                                               "permanent resident", "pr status",
                                               "status in canada", "immigration status",
                                               "autorisation de travailler", "travailler au canada",
                                               "autorisation légale", "autorisation legale",
                                               "droit légal", "droit legal", "permis de travail")):
                    for opt, opt_norm in zip(opts_text, opt_norms):
                        if opt_norm in ("yes", "oui", "true") or any(x in opt_norm for x in ("authorized", "autorisé", "autorisée")):
                            chosen_label = opt
                            decision_source = "work_authorized"
                            break
                elif any(k in ctx_l for k in ("consent", "authorize", "privacy",
                                               "personal information", "data processing")):
                    for opt, opt_norm in zip(opts_text, opt_norms):
                        if any(pref in opt_norm for pref in ("consent", "agree", "authorize", "yes")):
                            chosen_label = opt
                            decision_source = "consent"
                            break
                elif any(k in ctx_l for k in ("referral", "referred", "recommended by")):
                    for opt, opt_norm in zip(opts_text, opt_norms):
                        if opt_norm in ("no", "non", "false"):
                            chosen_label = opt
                            decision_source = "referral_no"
                            break
                elif any(k in ctx_l for k in ("how many years", "years of experience",
                                               "amount of experience", "years have you")):
                    try:
                        exp = int(current_experience)
                    except Exception:
                        exp = 3
                    for opt in opts_text:
                        nums = [int(x) for x in re.findall(r"\d+", opt)]
                        if len(nums) == 1 and nums[0] <= exp:
                            chosen_label = opt
                        elif len(nums) >= 2 and nums[0] <= exp <= nums[-1]:
                            chosen_label = opt
                        if chosen_label:
                            decision_source = "experience_range"
                            break

            if not chosen_label:
                ai_ans = _ai_answer(
                    question=ctx or "dropdown question",
                    hint=_radio_ai_hint(
                        ctx or "dropdown question",
                        opts_text,
                        json.dumps(_compact_dom_for_ai(select_dom_snapshot), ensure_ascii=False, sort_keys=True),
                    ),
                    job_context=_current_job_context,
                    options=opts_text,
                )
                chosen_label = _choose_by_ai_answer(ai_ans, opts_text)
                if chosen_label:
                    decision_source = "ai"

            # AI forced-choice retry with stronger prompt
            if not chosen_label and _aiClient is not None:
                forced_ans = _ai_forced_choice(ctx or "dropdown question", opts_text, _current_job_context)
                if forced_ans:
                    chosen_label = _choose_by_ai_answer(forced_ans, opts_text)
                    if chosen_label:
                        decision_source = "ai_forced_choice"
                        print_lg(f"      [Questions] AI forced-choice dropdown: {chosen_label!r}")

            # Heuristic fallback only if AI completely unavailable
            if not chosen_label:
                chosen_label = _best_guess_dropdown_option(ctx, opts_info, opts_text)
                if chosen_label:
                    decision_source = "best_guess_never_skip"
                    print_lg(f"      [Questions] ⚠ Heuristic dropdown fallback: {chosen_label!r}")

            if chosen_label:
                sel_el.select_option(label=chosen_label)
                log_training_event(
                    "question_answered",
                    job=_current_job_meta,
                    control_type="select",
                    question=ctx,
                    options=opts_info,
                    selected=chosen_label,
                    decision_source=decision_source,
                    dom=select_dom_snapshot,
                )
            else:
                log_training_event(
                    "question_unresolved",
                    job=_current_job_meta,
                    control_type="select",
                    question=ctx or "dropdown question",
                    options=opts_info,
                    reason="no_safe_option_match_after_ai_and_fallbacks",
                    dom=select_dom_snapshot,
                )
        except Exception as e:
            log_training_event(
                "question_answer_failed",
                job=_current_job_meta,
                control_type="select",
                question=locals().get("ctx", "dropdown question"),
                options=locals().get("opts_info", []),
                error=f"{type(e).__name__}: {e}",
                page=page_dom_snapshot(page, limit=35),
            )
            continue

    # Date inputs (native type=date + text/datepickers labelled Date).
    # Prefer JS value-setter so Chrome's native calendar does not open and
    # steal focus from Continue (Indeed GEI "Date *" attestation step).
    date_inputs = list(page.query_selector_all("input[type='date']") or [])
    # Also catch text inputs whose label/placeholder is clearly a date field.
    try:
        for el in page.query_selector_all(
            "input[type='text'], input:not([type]), input[placeholder*='yyyy' i], "
            "input[placeholder*='mm/dd' i], input[aria-label*='date' i]"
        ) or []:
            try:
                if not el.is_visible():
                    continue
                typ = (el.get_attribute("type") or "text").lower()
                if typ == "date":
                    continue  # already in date_inputs
                ctx_l = (_get_question_context(page, el) or "").lower()
                ph = (el.get_attribute("placeholder") or "").lower()
                aria = (el.get_attribute("aria-label") or "").lower()
                name = (el.get_attribute("name") or "").lower()
                blob = f"{ctx_l} {ph} {aria} {name}"
                if "date" not in blob and "yyyy" not in ph and "mm/dd" not in ph:
                    continue
                # Skip name/email/phone that happen to sit near the word date.
                if any(k in blob for k in ("first name", "last name", "email", "phone", "employee id")):
                    if re.sub(r"[\s*]+", " ", ctx_l).strip() not in {"date", "the date"}:
                        continue
                date_inputs.append(el)
            except Exception:
                continue
    except Exception:
        pass

    for date_inp in date_inputs:
        ctx = ""
        hint = ""
        try:
            if not date_inp.is_visible():
                continue
            existing = (date_inp.get_attribute("value") or "").strip()
            # Refill only when empty or clearly not a real ISO/US date.
            if existing and re.fullmatch(r"\d{4}-\d{2}-\d{2}", existing):
                continue
            if existing and re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", existing):
                continue
            ctx = _get_question_context(page, date_inp).lower()
            aria = (date_inp.get_attribute("aria-label") or "").lower()
            name = (date_inp.get_attribute("name") or "").lower()
            ph = (date_inp.get_attribute("placeholder") or "").lower()
            hint = f"{ctx} {aria} {name} {ph}"
            log_training_event("question_detected", job=_current_job_meta,
                               control_type="date", question=ctx or hint,
                               dom=element_dom_snapshot(page, date_inp))
            answer = _safe_date_answer_for_question(hint, desired_start_date_str, today_str)
            if answer is None:
                log_training_event("question_unresolved", job=_current_job_meta,
                                   control_type="date", question=ctx or hint, hint=hint,
                                   reason="no_safe_date_answer",
                                   dom=element_dom_snapshot(page, date_inp))
                continue
            if _fill_date_control(page, date_inp, answer, hint=hint):
                print_lg(f"      [Questions] Filled date: {answer}")
                log_training_event("question_answered", job=_current_job_meta,
                                   control_type="date", question=ctx or hint,
                                   answer=answer, decision_source="rules",
                                   dom=element_dom_snapshot(page, date_inp))
            else:
                log_training_event("question_unresolved", job=_current_job_meta,
                                   control_type="date", question=ctx or hint, hint=hint,
                                   attempted_answer=answer,
                                   reason="date_value_not_accepted_by_control",
                                   dom=element_dom_snapshot(page, date_inp))
        except Exception as e:
            log_training_event("question_answer_failed", job=_current_job_meta,
                               control_type="date", question=locals().get("ctx", "date question"),
                               hint=locals().get("hint", ""), error=f"{type(e).__name__}: {e}",
                               page=page_dom_snapshot(page, limit=35))
            continue

    # Text / number inputs
    for inp in page.query_selector_all(
        "input[type='text'], input[type='number'], input:not([type])"
    ):
        ctx = ""
        hint = ""
        try:
            if not inp.is_visible():
                continue
            if inp.get_attribute("value"):
                continue
            iid         = inp.get_attribute("id") or ""
            iname       = inp.get_attribute("name") or ""
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            aria        = (inp.get_attribute("aria-label") or "").lower()
            ctx         = _get_question_context(page, inp)
            hint        = f"{iid} {iname} {placeholder} {aria} {ctx}".lower()
            # Dedup: skip if we already answered this field key this job.
            field_key = ("input", iid, iname, placeholder, aria, ctx[:120])
            if field_key in _answered_field_keys:
                continue
            dom_snapshot = element_dom_snapshot(page, inp)
            log_training_event("question_detected", job=_current_job_meta,
                               control_type=(inp.get_attribute("type") or "text"),
                               question=ctx or hint, hint=hint,
                               dom=dom_snapshot)

            answer = None
            decision_source = "rules"
            bank_match = find_answer(question=ctx or hint, hint=hint)
            if bank_match:
                answer = bank_match.answer
                decision_source = bank_match.source
            else:
                safe_answer, safe_source = _safe_text_answer_for_question(
                    hint, full_name, desired_start_date_str, today_str
                )
                if safe_answer is not None:
                    answer = safe_answer
                    decision_source = safe_source
            if answer is None:
                if any(k in hint for k in ("company name", "company", "employer name")):
                    index = _employer_field_counts.get("company", 0)
                    answer = ("Vancouver Coastal Health", "Bell")[min(index, 1)]
                    _employer_field_counts["company"] = index + 1
                    decision_source = "configured_work_history"
                elif any(k in hint for k in ("position title", "job title", "title")) and "current" not in hint:
                    index = _employer_field_counts.get("title", 0)
                    answer = ("Porter", "Sales Representative")[min(index, 1)]
                    _employer_field_counts["title"] = index + 1
                    decision_source = "configured_work_history"
            if answer is None:
                # 1. Check salary / amount / compensation fields BEFORE generic numeric fallback
                if any(k in hint for k in ("desired", "expected", "salary", "compensation", "wage", "pay", "amount", "montant")):
                    answer = desired_salary_monthly if "month" in hint else (
                        desired_salary_lakhs if "lakh" in hint else desired_salary_str)
                    decision_source = "desired_salary"
                elif any(k in hint for k in ("current ctc", "current salary", "present salary")):
                    answer = current_ctc_monthly if "month" in hint else (
                        current_ctc_lakhs if "lakh" in hint else current_ctc_str)
                    decision_source = "current_salary"
                elif (inp.get_attribute("type") == "number" or
                      any(k in hint for k in ("number", "valeur numérique", "chiffre"))):
                    if any(k in hint for k in ("year", "how many", "yrs", "months of experience")):
                        answer = _yoe_str
                    else:
                        answer = "0"
                    decision_source = "numeric_field_fallback"
                elif any(k in hint for k in ("year", "how many", "yrs", "months of experience")):
                    answer = _yoe_str
                elif "notice" in hint:
                    answer = notice_period_months if "month" in hint else (
                        notice_period_weeks if "week" in hint else notice_period_str)
                elif any(k in hint for k in ("first name", "given name", "prénom")):
                    answer = first_name
                elif any(k in hint for k in ("last name", "family name", "surname", "nom")):
                    answer = last_name
                elif any(k in hint for k in ("referred by", "referral name", "referrer")):
                    answer = "N/A"
                    decision_source = "no_referral"
                elif any(k in hint for k in ("full name", "fullname", "nom complet", "legal name", "name")):
                    answer = full_name
                elif any(k in hint for k in ("email", "e-mail", "courriel")):
                    _def = (f"{first_name.lower()}.{last_name.lower()}@gmail.com"
                            if first_name and last_name else "")
                    answer = email_address or _def
                elif any(k in hint for k in ("phone", "tel", "mobile", "téléphone")):
                    answer = _local_phone(phone_number) if phone_number else ""
                elif any(k in hint for k in ("start date", "desired start", "available to start", "availability date",
                                             "date available", "available date", "earliest available", "date you can start",
                                             "earliest date", "earliest start", "when can you start", "disponibilité",
                                             "date de début", "disponible")):
                    answer = desired_start_date_str
                elif any(k in hint for k in ("date", "today", "jour")):
                    if any(k in hint for k in ("from", "until", "start date", "end date")):
                        kind = "end" if any(k in hint for k in ("until", "end date", "to date")) else "start"
                        index = _employer_field_counts.get(kind, 0)
                        values = {
                            "start": ("2022-10-01", "2018-04-01"),
                            "end": ("", "2021-08-01"),
                        }
                        answer = values[kind][min(index, 1)]
                        _employer_field_counts[kind] = index + 1
                        decision_source = "configured_work_history"
                        # Skip the generic date rules below.
                        safe_date = "__work_history_handled__"
                    else:
                        safe_date = _safe_date_answer_for_question(hint, desired_start_date_str, today_str)
                    if safe_date is None:
                        # Work-history From/Until fields — leave blank rather than
                        # filling "N/A" which fails YYYY-MM-DD format validation.
                        answer = None
                        decision_source = "skip_unparseable_date"
                    else:
                        answer = safe_date
                        decision_source = "safe_date_rule"
                elif any(k in hint for k in ("sign", "signature")):
                    answer = full_name
                elif any(k in hint for k in ("city", "ville")):
                    answer = current_city or "Surrey, BC"
                elif any(k in hint for k in ("postal", "zip", "code postal")):
                    answer = zipcode or ""
                elif any(k in hint for k in ("street", "address", "rue", "adresse")):
                    answer = street or ""
                elif any(k in hint for k in ("community", "first nation")):
                    answer = "N/A"
                elif "pronoun" in hint:
                    answer = _configured_pronoun_answer()
                    decision_source = "configured_pronouns"
                elif "linkedin" in hint:
                    try:
                        from config.questions import professional_profile_url, website as _ws
                        answer = professional_profile_url or _ws or ""
                    except ImportError:
                        answer = ""
                elif any(k in hint for k in ("website", "portfolio", "blog")):
                    try:
                        from config.questions import website as _ws
                        answer = _ws or ""
                    except ImportError:
                        answer = ""
                elif "headline" in hint:
                    answer = profile_headline or "IT Professional"
                elif any(k in hint for k in ("scale of 1", "confidence", "rating")):
                    try:
                        from config.questions import confidence_level
                        answer = str(confidence_level)
                    except ImportError:
                        answer = "8"
                else:
                    ai_ans = _ai_answer(question=ctx or hint or "text field",
                                        hint=_control_ai_hint(hint, dom_snapshot),
                                        job_context=_current_job_context)
                    if ai_ans:
                        answer = ai_ans
                        decision_source = "ai"
                        print_lg(f"      [AI] Filled text: {ai_ans[:80]}")
                    else:
                        answer = full_name
                        decision_source = "fallback_full_name"
                        _randomly_answered_questions.add((ctx or hint or "unknown", answer, "text"))
            if answer is not None:
                input_type = (inp.get_attribute("type") or "").lower()
                if input_type == "date" or any(k in hint for k in ("date", "calendar", "yyyy", "mm/dd")):
                    # Normalize free-text "today" / AI slop to ISO when possible.
                    ans = str(answer).strip()
                    if ans.lower() in {"today", "todays date", "today's date", "current date"}:
                        ans = today_str
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ans) or "date" in hint:
                        if _fill_date_control(page, inp, ans if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ans) else today_str, hint=hint):
                            print_lg(f"      [Questions] Filled date/text date: {ans}")
                        elif _set_input_value_direct(page, inp, ans):
                            print_lg(f"      [Questions] Filled date (fallback): {ans}")
                        else:
                            _type_into(page, inp, ans)
                    else:
                        _type_into(page, inp, ans)
                else:
                    _type_into(page, inp, answer)
                _answered_field_keys.add(field_key)
                log_training_event("question_answered", job=_current_job_meta,
                                   control_type=input_type or "text",
                                   question=ctx or hint, hint=hint,
                                   answer=answer, answer_len=len(str(answer)),
                                   decision_source=decision_source,
                                   dom=dom_snapshot)
        except Exception as e:
            log_training_event("question_answer_failed", job=_current_job_meta,
                               control_type="text", question=locals().get("ctx", "text question"),
                               hint=locals().get("hint", ""),
                               error=f"{type(e).__name__}: {e}",
                               page=page_dom_snapshot(page, limit=35))
            continue

    # Textareas
    for ta in page.query_selector_all("textarea"):
        ctx_ta = ""
        ta_hint = ""
        try:
            if not ta.is_visible():
                continue
            if (ta.get_attribute("value") or ta.inner_text() or "").strip():
                continue
            ctx_ta  = _get_question_context(page, ta)
            ta_hint = (ta.get_attribute("placeholder") or ta.get_attribute("aria-label") or "").lower()
            ta_id   = ta.get_attribute("id") or ""
            ta_name = ta.get_attribute("name") or ""
            # Dedup: skip if we already answered this textarea this job.
            ta_field_key = ("textarea", ta_id, ta_name, ta_hint, ctx_ta[:120])
            if ta_field_key in _answered_field_keys:
                continue
            ta_dom_snapshot = element_dom_snapshot(page, ta)
            log_training_event("question_detected", job=_current_job_meta,
                               control_type="textarea", question=ctx_ta or ta_hint,
                               hint=ta_hint, dom=ta_dom_snapshot)
            answer = ""
            decision_source = ""
            ta_combined_hint = f"{ctx_ta} {ta_hint}".lower()
            bank_match = find_answer(question=ctx_ta or ta_hint, hint=ta_combined_hint)
            if bank_match:
                answer = bank_match.answer
                decision_source = bank_match.source
                _type_into(page, ta, answer)
            else:
                safe_answer, safe_source = _safe_text_answer_for_question(
                    ta_combined_hint, full_name, desired_start_date_str, today_str
                )
                if safe_answer is not None:
                    answer = safe_answer
                    decision_source = safe_source
                    _type_into(page, ta, answer)
            if not answer:
                if 'cover' in ctx_ta.lower() and cover_letter:
                    answer = cover_letter
                    decision_source = "configured_cover_letter"
                    _type_into(page, ta, answer)
                elif (
                    any(k in ctx_ta.lower() for k in (
                        'tell us about yourself', 'tell me about yourself',
                        'about yourself', 'introduce yourself',
                        'professional summary', 'profile summary',
                        'summary of qualifications', 'brief summary',
                        'short bio', 'professional bio',
                    ))
                    and profile_summary
                ):
                    answer = profile_summary
                    decision_source = "configured_summary"
                    _type_into(page, ta, answer)
            if not answer:
                ai_ans = _ai_answer(question=ctx_ta or ta_hint or "open text",
                                    hint=_control_ai_hint(ta_combined_hint, ta_dom_snapshot),
                                    job_context=_current_job_context)
                if ai_ans:
                    answer = ai_ans
                    decision_source = "ai"
                    _type_into(page, ta, answer)
                    print_lg(f"      [AI] Filled textarea: {ai_ans[:80]}")
                else:
                    # AI retry with explicit "never skip" context
                    retry_question = (
                        f"Answer this application question in 1-3 sentences. "
                        f"You MUST provide a real answer. Question: {ctx_ta or ta_hint or 'Describe yourself'}"
                    )
                    ai_retry = _ai_answer(question=retry_question,
                                          hint=f"Candidate has {_yoe_str}+ years experience.",
                                          job_context=_current_job_context)
                    if ai_retry:
                        answer = ai_retry
                        decision_source = "ai_retry"
                        _type_into(page, ta, answer)
                        print_lg(f"      [AI retry] Filled textarea: {ai_retry[:80]}")
                    else:
                        # Fallback: use profile summary or generate answer
                        if profile_summary:
                            answer = profile_summary
                        else:
                            answer = (
                                f"I am very interested in this opportunity and believe my "
                                f"experience aligns well with the requirements. "
                                f"I have {_yoe_str}+ years of relevant experience and "
                                f"am eager to contribute to your team."
                            )
                        decision_source = "best_guess_never_skip"
                        _type_into(page, ta, answer)
                        print_lg(f"      [Questions] ⚠ Fallback textarea: {answer[:80]}")
            if answer:
                _answered_field_keys.add(ta_field_key)
            log_training_event("question_answered", job=_current_job_meta,
                               control_type="textarea", question=ctx_ta or ta_hint,
                               hint=ta_hint, answer=answer, answer_len=len(str(answer)),
                               decision_source=decision_source,
                               dom=ta_dom_snapshot)
        except Exception as e:
            log_training_event("question_answer_failed", job=_current_job_meta,
                               control_type="textarea", question=locals().get("ctx_ta", "textarea question"),
                               hint=locals().get("ta_hint", ""),
                               error=f"{type(e).__name__}: {e}",
                               page=page_dom_snapshot(page, limit=35))
            continue

    # ── Radio groups ─────────────────────────────────────────────────────────
    # PRIMARY: name-based grouping (Indeed's actual q_{hash} pattern from ghg.html)
    _answer_radios_by_name_group(page)

    # SECONDARY: fieldset / role=radiogroup (standard HTML pattern)
    groups = page.query_selector_all("fieldset, [role='radiogroup']")
    if groups:
        for grp in groups:
            _answer_radio_group(page, grp, full_name, today_str)
    else:
        # FALLBACK: flat radio iteration (legacy)
        _answer_radio_groups_flat(page, full_name, today_str)

    try:
        from jobbots.core.shared_modules.indeed.persistence import log_job_status_event_from_meta
        log_job_status_event_from_meta("answers_drafted", reason="Drafted answers for employer questions")
        log_job_status_event_from_meta("filled", reason="Filled employer questions")
    except Exception:
        pass


def _answer_radio_group(page, grp, full_name: str, today_str: str) -> None:
    try:
        radios = grp.query_selector_all("input[type='radio']")
        if not radios:
            return
        try:
            if any(r.is_checked() for r in radios):
                return
        except Exception:
            pass

        options = []
        for r in radios:
            rid = r.get_attribute("id") or ""
            lbl = grp.query_selector(f'label[for="{rid}"]') if rid else None
            ltext = (lbl.inner_text().strip() if lbl else (r.get_attribute("value") or ""))
            options.append((r, ltext.strip().lower()))

        question_text = ""
        for q_sel in ["legend", "p", "span[class*='question']",
                      "div[class*='question']", "[class*='label']"]:
            el = grp.query_selector(q_sel)
            if el:
                question_text = el.inner_text().lower()
                break
        if not question_text:
            question_text = _get_question_context(page, radios[0])

        q = question_text
        chosen = _pick_radio_by_rules(q.lower(), options)

        if chosen is not None:
            pass

        elif any(k in q for k in ("understand", "agree", "authorize", "consent",
                                 "declare", "certify", "privacy", "i confirm",
                                 "true and complete", "truthful", "misrepresentation")):
            for r, lbl in options:
                if (
                    any(k in lbl for k in ("yes, i confirm", "i confirm", "i understand",
                                           "i agree", "agree", "understand", "yes"))
                    and not any(k in lbl for k in ("do not", "not confirm", "no,"))
                ):
                    chosen = r
                    break
            if chosen is None:
                chosen = options[0][0]

        elif any(k in q for k in ("referred", "referral", "recommended by",
                                  "recommandé", "recommande", "référé", "refere")):
            for r, lbl in options:
                if lbl in ("no", "non", "false"):
                    chosen = r
                    break

        elif any(k in q for k in ("gender", "sex", "sexe", "pronoun")):
            for r, lbl in options:
                if _gender_label_matches_configured(lbl):
                    chosen = r
                    break

        elif any(k in q for k in ("indigenous", "aboriginal", "first nation", "métis", "inuit")):
            for pref in ("prefer not to disclose", "prefer not to say", "prefer not",
                         "decline", "no", "non"):
                for r, lbl in options:
                    if pref in lbl:
                        chosen = r
                        break
                if chosen:
                    break

        elif any(k in q for k in ("ever been an employee", "been an employee of",
                                  "ever worked for", "previously worked for",
                                  "have you worked for",
                                  "previously employed", "former employee",
                                  "current employee")):
            for r, lbl in options:
                if lbl in ("no", "non", "false"):
                    chosen = r
                    break

        elif any(k in q for k in ("disability", "disabled", "handicap")):
            for r, lbl in options:
                if lbl in ("no", "non", "prefer not to say"):
                    chosen = r
                    break

        elif any(k in q for k in ("visible minority", "minorité visible", "race", "ethnicity")):
            ethnicity = str(_configured_ethnicity or "").strip().lower()
            if ethnicity in ("", "decline"):
                ethnicity_prefs = ("refus", "decline", "prefer not", "do not wish")
            elif ethnicity == "hispanic/latino":
                ethnicity_prefs = ("hispanic", "hispanique", "latino")
            elif ethnicity == "asian":
                ethnicity_prefs = ("asian", "asiatique", "south asian")
            else:
                ethnicity_prefs = (ethnicity, "refus", "decline", "prefer not")
            for pref in ethnicity_prefs:
                for r, lbl in options:
                    if pref in lbl:
                        chosen = r
                        break
                if chosen:
                    break

        elif any(k in q for k in ("citizenship", "employment eligibility")):
            from_cfg = ""
            try:
                from config.questions import us_citizenship
                from_cfg = us_citizenship.lower()
            except ImportError:
                pass
            if from_cfg:
                for r, lbl in options:
                    if from_cfg[:6] in lbl or "canadian" in lbl:
                        chosen = r
                        break
            if chosen is None:
                for r, lbl in options:
                    if lbl in ("yes", "oui", "true"):
                        chosen = r
                        break

        elif any(k in q for k in ("veteran", "protected")):
            for r, lbl in options:
                if "not" in lbl:
                    chosen = r
                    break

        elif any(k in q for k in ("commute", "relocat")):
            for r, lbl in options:
                if any(k in lbl for k in ("yes", "commute", "can make")):
                    chosen = r
                    break

        elif any(k in q for k in ("education", "degree", "diploma", "highest level")) or _looks_like_education_options([lbl for _, lbl in options]):
            bachelor_label = _find_bachelors_option([lbl for _, lbl in options])
            if bachelor_label:
                for r, lbl in options:
                    if lbl == bachelor_label:
                        chosen = r
                        break

        elif _is_non_english_language_question(q):
            speaks_language = _candidate_speaks_language(q)
            if speaks_language:
                prefs = ("native", "bilingual", "fluent", "professional", "advanced", "yes", "oui", "true")
            else:
                prefs = ("no proficiency", "none", "no proficiency", "no", "non", "false", "basic")
            for pref in prefs:
                for r, lbl in options:
                    if pref in _norm_choice(lbl):
                        chosen = r
                        break
                if chosen:
                    break

        elif any(k in q for k in ("english", "language", "proficiency", "fluent")):
            for pref in ("advanced", "mother tongue", "native", "fluent", "professional"):
                for r, lbl in options:
                    if pref in lbl:
                        chosen = r
                        break
                if chosen:
                    break

        elif any(k in q for k in ("salary", "pay", "wage", "compensation", "desired")):
            salary_prefs = [str(int(_ds) // 1000) + "k", str(int(_ds) // 1000), "70", "75", "80"]
            for pref in salary_prefs:
                for r, lbl in options:
                    if pref in lbl:
                        chosen = r
                        break
                if chosen:
                    break
            if chosen is None and options:
                chosen = options[min(len(options) // 2, len(options) - 1)][0]

        else:
            opts_text = [lbl for _, lbl in options]
            dom_context = ""
            try:
                dom_context = group.inner_text()
            except Exception:
                pass
            ai_ans = _ai_answer(question=q or "radio question",
                                hint=_radio_ai_hint(q or "radio question", opts_text, dom_context),
                                job_context=_current_job_context, options=opts_text)
            if ai_ans:
                selected_label = _choose_by_ai_answer(ai_ans, opts_text)
                for r, lbl in options:
                    if lbl == selected_label:
                        chosen = r
                        break
            # AI forced-choice retry
            if chosen is None and _aiClient is not None:
                forced_ans = _ai_forced_choice(q or "radio question", opts_text, _current_job_context)
                if forced_ans:
                    selected_label = _choose_by_ai_answer(forced_ans, opts_text)
                    for r, lbl in options:
                        if lbl == selected_label:
                            chosen = r
                            break
                    if chosen:
                        print_lg(f"    [Questions] AI forced-choice radio: {forced_ans!r}")
            # Heuristic fallback only if AI completely unavailable
            if chosen is None:
                chosen = _best_guess_radio_option(q, options)
                if chosen:
                    print_lg(f"    [Questions] ⚠ Heuristic radio fallback (AI unavailable)")

        if chosen is None:
            # Absolute last resort: pick first option
            if options:
                chosen = options[0][0]
                print_lg("    [Questions] ⚠ Absolute last resort — selecting first radio option.")
            else:
                return
        if chosen and not chosen.is_checked():
            chosen.click(force=True)
    except Exception:
        pass


def _answer_radio_groups_flat(page, full_name: str, today_str: str) -> None:
    radios = page.query_selector_all("input[type='radio']")
    seen: set = set()
    for r in radios:
        name_key = r.get_attribute("name") or r.get_attribute("id") or str(id(r))
        if name_key in seen:
            continue
        try:
            group_radios = [
                candidate for candidate in radios
                if (candidate.get_attribute("name") or candidate.get_attribute("id") or str(id(candidate))) == name_key
            ]
            if any(candidate.is_checked() for candidate in group_radios):
                seen.add(name_key)
                continue
        except Exception:
            pass
        rid = r.get_attribute("id") or ""
        lbl = page.query_selector(f'label[for="{rid}"]') if rid else None
        lbl_text = (lbl.inner_text() if lbl else (r.get_attribute("value") or "")).lower()
        ctx = _get_question_context(page, r)
        ctx_l = (ctx or "").lower()
        pick = False
        if any(k in ctx_l for k in ("ever been an employee", "been an employee of",
                                    "ever worked for", "previously worked for",
                                    "been employed, or otherwise engaged",
                                    "employed, or otherwise engaged",
                                    "previously employed", "former employee",
                                    "current employee")):
            pick = lbl_text in ("no", "non", "false")
        elif any(k in ctx_l for k in ("gender", "sex", "sexe")):
            pick = _gender_label_matches_configured(lbl_text)
        elif any(k in ctx_l for k in ("indigenous", "aboriginal", "first nation", "métis", "inuit")):
            pick = any(k in lbl_text for k in ("prefer not", "decline")) or lbl_text in ("no", "non")
        elif any(k in ctx_l for k in ("disability", "minority")):
            pick = any(k in lbl_text for k in ("prefer not", "decline")) or lbl_text in ("no", "non")
        elif any(k in ctx_l for k in ("authorized to work", "eligible", "work in canada", "legal right")):
            pick = lbl_text in ("yes", "oui", "true")
        elif any(k in ctx_l for k in ("based in metro vancouver", "located in metro vancouver",
                                      "live in metro vancouver", "based in vancouver")):
            pick = lbl_text in ("yes", "oui", "true")
        elif (
            any(k in ctx_l for k in ("cuba", "iran", "north korea", "rth korea", "syria", "crimea"))
            and any(k in ctx_l for k in ("citizen", "permanent resident", "export control"))
        ):
            pick = lbl_text in ("no", "non", "false")
        elif any(k in ctx_l for k in ("i confirm", "true and complete", "certify",
                                      "declare", "attest", "misrepresentation")):
            pick = (
                any(k in lbl_text for k in ("yes, i confirm", "i confirm", "yes", "agree"))
                and not any(k in lbl_text for k in ("do not", "not confirm", "no,"))
            )
        if pick:
            if not r.is_checked():
                r.click(force=True)
            seen.add(name_key)


# ─────────────────────────────────────────────────────────────────────────────
# Submission detection  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────
