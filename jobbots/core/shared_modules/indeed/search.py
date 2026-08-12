from __future__ import annotations

from ._bootstrap import *  # noqa: F403

def _resolve_remote_work_filters() -> list[str]:
    raw = _cfg_indeed_remote_filter
    values: list[str] = []

    if raw:
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in {"remote or hybrid", "remote/hybrid", "remote, hybrid", "both"}:
                values = ["remote", "hybrid"]
            elif "remote" in low and "hybrid" in low:
                values = ["remote", "hybrid"]
            elif "remote" in low:
                values = ["remote"]
            elif "hybrid" in low:
                values = ["hybrid"]
        elif isinstance(raw, (list, tuple, set)):
            values = [str(v).strip().lower() for v in raw if str(v).strip()]

    # Backward compatibility for the older/general on_site config.
    if not values and _cfg_on_site:
        values = [str(v).strip().lower() for v in _cfg_on_site if str(v).strip()]

    normalized: list[str] = []
    for value in values:
        if "remote" in value and "remote" not in normalized:
            normalized.append("remote")
        if "hybrid" in value and "hybrid" not in normalized:
            normalized.append("hybrid")
    return normalized


def _build_search_url(term: str, location_query: str, page_num: int,
                      fromage_days: int = None) -> str:
    loc_clean = (location_query or "").strip()
    is_remote_query = loc_clean.lower() == "remote"
    effective_location = loc_clean
    
    params = {
        "q": term,
        "l": effective_location,
        "start": page_num * 10,
    }
    
    # Standardize radius for city searches (e.g. Vancouver, BC).
    # Default 25km radius is standard on Indeed CA. Skip for empty/remote queries.
    if effective_location and not is_remote_query and "remote" not in effective_location.lower():
        params["radius"] = 25
        
    empty_location_remote_search = False
    
    # Force remote filter if location is empty or literally "Remote".
    if not effective_location or is_remote_query:
        remote_filters = [_REMOTE_WORK_FILTERS["remote"]]
    else:
        remote_filters = [
            _REMOTE_WORK_FILTERS[key]
            for key in _resolve_remote_work_filters()
            if key in _REMOTE_WORK_FILTERS
        ]

    if not effective_location or is_remote_query:
        empty_location_remote_search = True
        params["from"] = "searchOnDesktopSerp"

    if fromage_days:
        params["fromage"] = fromage_days

    if remote_filters:
        params["sc"] = f"0kf:attr({','.join(item['attr'] for item in remote_filters)});"
        if not empty_location_remote_search:
            params["remotejob"] = ",".join(item["remotejob"] for item in remote_filters)

    return f"{INDEED_SEARCH}?{urlencode(params)}"


def _resolve_fromage() -> int:
    val = _cfg_date_posted.strip().lower() if _cfg_date_posted else ""
    return _FROMAGE_MAP.get(val, None)


class Indeed404Error(Exception):
    """Raised when Indeed returns a 404 / 'We can't find this page' response."""


# Fingerprints that reliably identify Indeed's 404 page across locales.
_INDEED_404_SIGNALS = (
    "we can't find this page",
    "nous ne trouvons pas cette page",   # French locale
    "can't find this page",
    "page not found",
)


def _is_indeed_404(page) -> bool:
    """Return True if the current page is Indeed's soft 404 error page."""
    try:
        text = (page.text_content("body") or "").lower()
        return any(sig in text for sig in _INDEED_404_SIGNALS)
    except Exception:
        return False


def _goto_page(page, url: str, timeout: int = 15000):
    if not url:
        return None
    if not url.startswith("http"):
        if url.startswith("/"):
            url = f"https://ca.indeed.com{url}"
        else:
            url = f"https://ca.indeed.com/{url}"
    resp = page.goto(url, wait_until='domcontentloaded', timeout=timeout)
    if _is_indeed_404(page):
        raise Indeed404Error(f"Indeed 404 on: {url}")
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_job_id_from_url(url: str) -> str:
    m = _RE_JK.search(url)
    return m.group(1) if m else ''


def _extract_experience(text: str):
    matches = _RE_EXP.findall(text)
    if not matches:
        return 'Unknown'
    valid = [int(m) for m in matches if int(m) <= 12]
    return max(valid) if valid else 'Unknown'


def _looks_fully_french(text: str) -> bool:
    """
    Catch French-first postings without blocking English jobs that only mention
    French as a requirement. Uses common French job-posting words and accents.
    """
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()
    if len(normalized) < 240:
        return False

    french_terms = (
        " nous ", " vous ", " votre ", " vos ", " notre ", " nos ", " le ",
        " la ", " les ", " des ", " de la ", " du ", " une ", " un ", " aux ",
        " dans ", " pour ", " avec ", " sur ", " qui ", " que ", " dont ",
        " emploi ", " poste ", " candidat", " candidate", " responsabilités",
        " responsabilites", " exigences", " compétences", " competences",
        " expérience", " experience", " connaissance", " connaissances",
        " équipe", " equipe", " veuillez", " français", " francais",
        " bilingue", " horaire", " salaire", " télétravail", " teletravail",
    )
    english_terms = (
        " the ", " and ", " with ", " for ", " you ", " your ", " our ",
        " we ", " job ", " role ", " responsibilities", " requirements",
        " qualifications", " experience", " skills", " team ", " work ",
        " support ", " technical ", " apply ",
    )
    accented_chars = "àâçéèêëîïôûùüÿœæ"

    french_hits = sum(1 for term in french_terms if term in f" {normalized} ")
    english_hits = sum(1 for term in english_terms if term in f" {normalized} ")
    accented_hits = sum(normalized.count(ch) for ch in accented_chars)

    return (
        french_hits >= 10
        and french_hits >= english_hits * 2
        and (accented_hits >= 3 or any(k in normalized for k in ("français", "télétravail", "équipe", "compétences")))
    )


def _check_bad_words(description: str):
    low = description.lower()
    if _looks_fully_french(description):
        return True, "French-language posting"
    for word in bad_words:
        if word.lower() in low:
            return True, f'Contains bad word "{word}"'
    if not security_clearance:
        for kw in ('polygraph', 'clearance', 'secret'):
            if kw in low:
                return True, 'Requires security clearance'
    return False, ""


def _digits_only(raw: str) -> str:
    return re.sub(r'\D', '', raw)


def _local_phone(raw: str) -> str:
    digits = _digits_only(raw)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


def _norm_choice(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _choice_has_digits(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _choose_by_ai_answer(ai_answer: str, option_labels: list[str]) -> str:
    """
    Map a free-form AI answer back to an exact visible option label.
    Handles harmless punctuation/case differences like "Yes." -> "Yes".
    Also maps human yes/no answers onto boolean DOM labels like True/False.
    """
    ai_raw = (ai_answer or "").strip().lower()
    ai_norm = _norm_choice(ai_answer)
    if not ai_norm:
        return ""

    normalized_options = []
    for label in option_labels:
        opt_raw = (label or "").strip().lower()
        opt_norm = _norm_choice(label)
        if not opt_norm:
            continue
        normalized_options.append((label, opt_raw, opt_norm))

    for label, opt_raw, _ in normalized_options:
        if ai_raw == opt_raw:
            return label

    for label, _, opt_norm in normalized_options:
        if ai_norm == opt_norm:
            return label

    boolean_aliases = {
        "yes": {"yes", "oui", "true", "1"},
        "no": {"no", "non", "false", "0"},
        "true": {"yes", "oui", "true", "1"},
        "false": {"no", "non", "false", "0"},
    }
    ai_bool_aliases = boolean_aliases.get(ai_norm)
    if ai_bool_aliases:
        for label, _, opt_norm in normalized_options:
            if opt_norm in ai_bool_aliases:
                return label

    # Numeric choices must match exactly. Without this guard, "50%" can match
    # the earlier option "0%" after punctuation stripping.
    if _choice_has_digits(ai_norm):
        return ""

    ai_tokens = set(ai_norm.split())
    for label, _, opt_norm in normalized_options:
        if _choice_has_digits(opt_norm):
            continue
        opt_tokens = set(opt_norm.split())
        if ai_tokens and opt_tokens and (ai_tokens <= opt_tokens or opt_tokens <= ai_tokens):
            return label
    return ""


def _best_guess_radio_option(question: str, options: list) -> object:
    """
    Never-skip fallback: pick the safest radio option to avoid disqualification.
    `options` is a list of (element, label_text) tuples.
    """
    if not options:
        return None
    q = (question or "").lower()
    opt_labels_lower = [(r, lbl.lower().strip()) for r, lbl in options]

    yes_opts = [r for r, lbl in opt_labels_lower if lbl in ("yes", "oui", "true", "1")]
    no_opts = [r for r, lbl in opt_labels_lower if lbl in ("no", "non", "false", "0")]
    if yes_opts and no_opts:
        if any(k in q for k in ("referred", "referral", "recommended by",
                                "recommandé", "recommande", "référé", "refere")):
            return no_opts[0]
        return yes_opts[0]

    for r, lbl in opt_labels_lower:
        if any(k in lbl for k in ("yes", "agree", "confirm", "understand",
                                  "authorize", "accept", "willing", "available")):
            return r

    return options[0][0]


def _best_guess_dropdown_option(question: str, opts_info: list, opts_text: list) -> str:
    """
    Never-skip fallback: pick the safest dropdown option to avoid disqualification.
    """
    if not opts_text:
        return ""
    q = (question or "").lower()

    if any(k in q for k in ("experience", "years", "how long", "how many")):
        for opt in opts_text:
            if any(c.isdigit() for c in opt):
                return opt
        return opts_text[0]

    opts_lower = [o.lower().strip() for o in opts_text]
    if "yes" in opts_lower:
        if any(k in q for k in ("referred", "referral")):
            idx = opts_lower.index("no") if "no" in opts_lower else 0
        else:
            idx = opts_lower.index("yes")
        return opts_text[idx]

    return opts_text[0]


def _compact_prompt_text(value: str, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...[trimmed]"


def _compact_dom_for_ai(dom_snapshot: dict | None) -> dict:
    if not isinstance(dom_snapshot, dict):
        return {}

    compact = {
        "tag": dom_snapshot.get("tag", ""),
        "input_type": dom_snapshot.get("input_type", ""),
        "attrs": {},
        "labels": [],
    }
    attrs = dom_snapshot.get("attrs") or {}
    if isinstance(attrs, dict):
        for key in (
            "id", "name", "type", "role", "aria-label", "aria-labelledby",
            "aria-describedby", "placeholder", "autocomplete", "data-testid",
        ):
            val = attrs.get(key)
            if val:
                compact["attrs"][key] = _compact_prompt_text(str(val), 120)

    labels = dom_snapshot.get("labels") or []
    if isinstance(labels, list):
        compact["labels"] = [_compact_prompt_text(str(label), 180) for label in labels[:3] if label]

    ancestor = dom_snapshot.get("ancestor") or {}
    if isinstance(ancestor, dict):
        compact["ancestor"] = {
            "tag": ancestor.get("tag", ""),
            "data_testid": ancestor.get("data_testid", ""),
            "text": _compact_prompt_text(str(ancestor.get("text", "")), 260),
        }

    text = dom_snapshot.get("text")
    if text:
        compact["text"] = _compact_prompt_text(str(text), 180)
    return compact


def _radio_ai_hint(question_text: str, option_labels: list[str], dom_context: str = "") -> str:
    """
    Give the AI the same DOM truth the clicker sees, and ask for one exact label.
    The mapper still accepts Yes/No for True/False because local models drift.
    """
    parts = [
        "Choose exactly one of these DOM option labels:",
        ", ".join(repr(label) for label in option_labels),
        "Return only the option label, no explanation.",
    ]
    if question_text:
        parts.append(f"Question text: {_compact_prompt_text(question_text, 420)}")
    if dom_context:
        parts.append(f"DOM context: {_compact_prompt_text(dom_context, 520)}")
    return "\n".join(parts)


def _control_ai_hint(base_hint: str, dom_snapshot: dict | None = None) -> str:
    parts = [
        "Use the DOM context to understand what this form field is asking.",
        "Answer only the value that should be entered into this one field.",
    ]
    if base_hint:
        parts.append(f"Field hint/context: {_compact_prompt_text(base_hint, 420)}")
    if dom_snapshot:
        try:
            dom_text = json.dumps(_compact_dom_for_ai(dom_snapshot), ensure_ascii=False, sort_keys=True)
        except Exception:
            dom_text = str(dom_snapshot)
        parts.append(f"Element DOM snapshot: {_compact_prompt_text(dom_text, 700)}")
    return "\n".join(parts)


def _find_bachelors_option(option_labels: list[str]) -> str:
    for pref in ("bachelor", "baccalaureate"):
        for label in option_labels:
            if pref in (label or "").lower():
                return label
    return ""


def _looks_like_education_options(option_labels: list[str]) -> bool:
    opts_l = " ".join(option_labels or []).lower()
    return (
        bool(_find_bachelors_option(option_labels))
        and any(k in opts_l for k in (
            "no diploma", "secondary school", "high school", "diploma",
            "certificate", "master", "doctoral", "post-doctorate", "degree",
        ))
    )


_NON_ENGLISH_LANGUAGE_NAMES = (
    "french", "français", "francais", "spanish", "español", "espanol",
    "portuguese", "mandarin", "cantonese", "chinese", "hindi", "punjabi",
    "urdu", "arabic", "farsi", "persian", "korean", "japanese", "german",
    "italian", "russian", "ukrainian", "polish", "tagalog", "filipino",
    "vietnamese",
)


def _is_non_english_language_question(question: str) -> bool:
    q = (question or "").lower()
    if "english" in q:
        return False
    if not any(k in q for k in ("speak", "bilingual", "fluent", "proficient", "proficiency", "language")):
        return False
    return any(lang in q for lang in _NON_ENGLISH_LANGUAGE_NAMES)


def _candidate_speaks_language(question: str) -> bool | None:
    q = (question or "").lower()
    try:
        from config.questions import languages as configured_languages
    except ImportError:
        configured_languages = {"English": "Fluent", "Punjabi": "Fluent"}

    configured = {
        str(language).strip().lower()
        for language, proficiency in configured_languages.items()
        if str(language).strip() and str(proficiency).strip()
    }
    mentioned = [
        language for language in ("english", *_NON_ENGLISH_LANGUAGE_NAMES)
        if language in q
    ]
    if not mentioned:
        return None
    return all(language in configured for language in mentioned)


def _configured_languages_summary() -> str:
    try:
        from config.questions import languages as configured_languages
    except ImportError:
        configured_languages = {"English": "Fluent", "Punjabi": "Fluent"}
    return ", ".join(
        f"{language} ({proficiency})"
        for language, proficiency in configured_languages.items()
        if str(language).strip() and str(proficiency).strip()
    )


def _configured_pronoun_answer() -> str:
    gender_value = str(_configured_gender or "").strip().lower()
    if gender_value == "male":
        return "he/him/his"
    if gender_value == "female":
        return "she/her/hers"
    if gender_value == "other":
        return "they/them/theirs"
    return "prefer not to say"


def _gender_label_matches_configured(label: str) -> bool:
    label_norm = _norm_choice(label)
    label_l = (label or "").lower()
    gender_value = str(_configured_gender or "").strip().lower()

    if gender_value == "male":
        return label_norm in {"male", "man", "homme", "masculin"}
    if gender_value == "female":
        return label_norm in {"female", "woman", "femme", "feminin"} or "féminin" in label_l
    if gender_value == "other":
        return label_norm in {
            "other", "autre", "non binary", "nonbinary", "non binaire",
            "gender diverse", "another gender",
        }
    return any(pref in label_norm for pref in (
        "prefer not", "decline", "do not wish", "do not want",
        "self identify", "not disclose", "undeclared",
    ))


def _looks_like_gender_option_set(option_labels: list[str]) -> bool:
    norms = {_norm_choice(label) for label in option_labels if label}
    lows = {(label or "").lower() for label in option_labels if label}
    has_male = bool(norms & {"male", "man", "homme", "masculin"})
    has_female = bool(norms & {"female", "woman", "femme", "feminin"}) or any("féminin" in label for label in lows)
    return has_male and has_female


def _configured_gender_option_label(option_labels: list[str]) -> str:
    for label in option_labels:
        if _gender_label_matches_configured(label):
            return label
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Company blacklist  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

def _get_company_description(page) -> str:
    for sel in [
        "div[data-testid='companyInfo']", "section[data-testid='AboutSection']",
        "#jobDescriptionInfo", "div[class*='companyProfile']", "div[id*='company']",
    ]:
        el = page.query_selector(sel)
        if el:
            txt = el.inner_text().strip()
            if txt:
                return txt
    return ""


def _check_company_blacklist(company_text: str, company_name: str,
                              blacklisted_companies: set) -> tuple:
    if company_name in blacklisted_companies:
        return True, f'Company "{company_name}" is blacklisted'
    low = company_text.lower()
    if not low:
        return False, ""
    for word in about_company_good_words:
        if word.lower() in low:
            return False, ""
    for word in about_company_bad_words:
        if word.lower() in low:
            return True, f'Company description contains bad word "{word}"'
    return False, ""


def _is_no_matching_jobs_page(page) -> bool:
    try:
        body = page.query_selector("body")
        if not body:
            return False
        text = re.sub(r"\s+", " ", body.inner_text() or "").strip().lower()
    except Exception:
        return False

    no_match_markers = (
        "did not match any jobs",
        "didn't match any jobs",
        "does not match any jobs",
        "no jobs found",
    )
    return (
        any(marker in text for marker in no_match_markers) and
        any(marker in text for marker in ("search suggestions:", *_SUGGESTED_JOB_MARKERS))
    )


# ─────────────────────────────────────────────────────────────────────────────
# Job-card scraping  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

def _find_job_cards(page) -> list:
    for sel in _CARD_SELECTORS:
        try:
            cards = page.query_selector_all(sel)
            if cards:
                print_lg(f"  Found {len(cards)} job cards  [{sel}]")
                return cards
            page.wait_for_selector(sel, timeout=900, state='attached')
            cards = page.query_selector_all(sel)
            if cards:
                print_lg(f"  Found {len(cards)} job cards  [{sel}]")
                return cards
        except Exception:
            continue
    return []


def _is_suggested_job_card(card) -> bool:
    """Return True when this card is visually below Indeed's suggested-jobs heading."""
    try:
        marker = card.evaluate(
            """el => {
                const markers = [
                    'similar to jobs you explored',
                    'similar jobs',
                    'recommended jobs',
                    'people also searched',
                ];
                const startsWithMarker = text => {
                    text = (text || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    return markers.some(marker => text.startsWith(marker));
                };

                const cardRect = el.getBoundingClientRect();
                if (!cardRect.width || !cardRect.height) return true;
                const cardTop = cardRect.top + window.scrollY;
                const cardCenterX = cardRect.left + (cardRect.width / 2);

                const headings = [];
                for (const node of document.querySelectorAll('h1,h2,h3,h4,div,section,span')) {
                    const text = node.innerText || node.getAttribute('aria-label') || '';
                    if (!startsWithMarker(text)) continue;

                    const rect = node.getBoundingClientRect();
                    if (!rect.width || !rect.height) continue;
                    const markerCenterX = rect.left + (rect.width / 2);

                    // Keep only markers in the same left-side results column.
                    if (Math.abs(markerCenterX - cardCenterX) > Math.max(cardRect.width, rect.width)) {
                        continue;
                    }

                    headings.push(rect.top + window.scrollY);
                }

                return headings.some(markerTop => cardTop > markerTop);
            }"""
        )
        return bool(marker)
    except Exception:
        return False


def _extract_card_info(card, page) -> tuple:
    """card is a Playwright ElementHandle."""
    job_id = title = company = location = 'Unknown'
    has_easy_apply = False
    job_href = ''

    try:
        job_id = card.get_attribute('data-jk') or ''

        if not job_id:
            for a_sel in ['h2.jobTitle a', 'a.jcs-JobTitle', 'a[data-jk]', 'h2 a']:
                a = card.query_selector(a_sel)
                if a:
                    job_id = a.get_attribute('data-jk') or ''
                    if not job_id:
                        href = a.get_attribute('href') or ''
                        job_id = _extract_job_id_from_url(href)
                    if job_id:
                        break

        if not job_id:
            try:
                job_id = card.evaluate(
                    "el => { let p = el.closest('[data-jk]'); "
                    "return p ? p.getAttribute('data-jk') : ''; }"
                ) or ''
            except Exception:
                pass

        for a_sel in ['h2.jobTitle a', 'a.jcs-JobTitle', 'a[data-jk]', 'h2 a', 'a']:
            a = card.query_selector(a_sel)
            if a:
                job_href = a.get_attribute('href') or ''
                if job_href:
                    break

        for sel in ['h2.jobTitle a', 'a.jcs-JobTitle', 'h2[class*="jobTitle"] span', 'h2.jobTitle']:
            el = card.query_selector(sel)
            if el:
                t = (el.inner_text() or el.get_attribute('aria-label') or '').replace('\n', ' ').strip()
                if t:
                    title = t
                    break

        for sel in ["[data-testid='company-name']", 'span.companyName',
                    'a.companyName', '[class*="companyName"]']:
            el = card.query_selector(sel)
            if el:
                v = el.inner_text().strip()
                if v:
                    company = v
                    break

        for sel in ["[data-testid='text-location']", 'div.companyLocation',
                    'span.companyLocation', '[class*="companyLocation"]']:
            el = card.query_selector(sel)
            if el:
                v = el.inner_text().strip()
                if v:
                    location = v
                    break

        try:
            badge_text = card.inner_text().lower()
            has_easy_apply = 'easily apply' in badge_text or 'easy apply' in badge_text
        except Exception:
            pass

    except Exception:
        pass

    return (job_id or 'Unknown', title or 'Unknown', company or 'Unknown',
            location or 'Unknown', has_easy_apply, job_href)


def prune_boilerplate(text: str) -> str:
    if not text:
        return ""
    import re
    lines = text.splitlines()
    cleaned_lines = []
    
    boilerplate_patterns = [
        r'equal opportunity employer',
        r'affirmative action',
        r'without regard to race',
        r'disability status',
        r'veteran status',
        r'gender identity',
        r'sexual orientation',
        r'national origin',
        r'reasonable accommodation',
        r'cookie compliance',
        r'this website uses cookies',
        r'privacy policy',
        r'physical demands',
        r'lift up to \d+ lbs',
        r'color, religion, sex',
        r'qualified applicants will receive consideration',
        r'decided on the basis of qualifications, merit',
        r'equal opportunity/affirmative action',
        r'all qualified applicants',
        r'subject to a background check',
        r'drug screen',
        r'employment eligibility',
        r'equal employment opportunity',
    ]
    
    regexes = [re.compile(p, re.IGNORECASE) for p in boilerplate_patterns]
    
    for line in lines:
        if not line.strip():
            cleaned_lines.append(line)
            continue
            
        is_boilerplate = False
        for rx in regexes:
            if rx.search(line):
                is_boilerplate = True
                break
        
        if 'cookie' in line.lower() and ('agree' in line.lower() or 'accept' in line.lower() or 'terms' in line.lower()):
            is_boilerplate = True
            
        if not is_boilerplate:
            cleaned_lines.append(line)
            
    result = '\n'.join(cleaned_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()



def _get_job_description(page) -> str:
    def _from_html(html_content: str) -> str:
        if not html_content:
            return ""
        try:
            import asyncio
            from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
            from crawl4ai.content_filter_strategy import PruningContentFilter
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

            async def parse_html(html_str):
                content_filter = PruningContentFilter(threshold=0.48, min_word_threshold=2)
                md_generator = DefaultMarkdownGenerator(content_filter=content_filter)
                config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, markdown_generator=md_generator)
                async with AsyncWebCrawler() as crawler:
                    res = await crawler.arun(url=f"raw:{html_str}", config=config)
                    if res.success:
                        if hasattr(res.markdown, "fit_markdown") and res.markdown.fit_markdown:
                            return res.markdown.fit_markdown
                        return res.markdown
                    return ""

            try:
                loop = asyncio.get_running_loop()
                import nest_asyncio
                nest_asyncio.apply()
                cleaned = loop.run_until_complete(parse_html(html_content))
            except RuntimeError:
                cleaned = asyncio.run(parse_html(html_content))
            if cleaned and cleaned.strip():
                return prune_boilerplate(cleaned).strip()
        except Exception as e:
            try:
                print_lg(f"[Crawl4AI] Warning parsing description: {e}")
            except Exception:
                pass
        return ""

    for sel in [
        "#jobDescriptionText",
        "div.jobsearch-JobComponent-description",
        "[data-testid='jobsearch-JobComponent-description']",
        "#jobDetailsSection",
        "[id*='jobDesc']",
        ".jobsearch-jobDescriptionText",
        ".job-snippet",
        "div#jobDescription",
    ]:
        try:
            el = page.query_selector(sel)
            if el:
                html_content = el.evaluate("el => el.outerHTML")
                cleaned = _from_html(html_content)
                if cleaned:
                    return cleaned
                text = (el.inner_text() or "").strip()
                if text:
                    return prune_boilerplate(text).strip()
        except Exception:
            try:
                el = page.query_selector(sel)
                if el:
                    text = (el.inner_text() or "").strip()
                    if text:
                        return prune_boilerplate(text).strip()
            except Exception:
                pass

    # Indeed 2026 mobile/web layout: description sits under an h* "Full job description"
    # heading with hashed css-* classes (no #jobDescriptionText).
    try:
        text = page.evaluate(
            """() => {
                const heads = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,div,span'));
                const h = heads.find((el) => /^\\s*full job description\\s*$/i.test((el.innerText || '').trim()));
                if (!h) return '';
                let root = h.parentElement;
                for (let i = 0; i < 5 && root; i++) {
                    const t = (root.innerText || '').trim();
                    if (t.length > 180) return t;
                    root = root.parentElement;
                }
                return (h.parentElement && (h.parentElement.innerText || '').trim()) || '';
            }"""
        )
        if text and len(str(text).strip()) > 80:
            return prune_boilerplate(str(text).strip()).strip()
    except Exception:
        pass
    return ""



# ─────────────────────────────────────────────────────────────────────────────
# Job gates + Indeed save helpers
# ─────────────────────────────────────────────────────────────────────────────

# Minimum graded fit score (0-100) for the Groq/LLM job gate to approve a job.
# Tuned to be realistic, not punitive: 55 is the rubric threshold for
# "acceptable reach / entry-level / trainable". Override via env for tuning.
try:
    _JOB_GATE_MIN_FIT_SCORE = int(os.getenv("JOB_GATE_MIN_FIT_SCORE", "55"))
except ValueError:
    _JOB_GATE_MIN_FIT_SCORE = 55
try:
    _GROQ_GATE_RETRY_DELAY_SECONDS = max(
        0, int(os.getenv("GROQ_GATE_RETRY_DELAY_SECONDS", "45"))
    )
except ValueError:
    _GROQ_GATE_RETRY_DELAY_SECONDS = 45
