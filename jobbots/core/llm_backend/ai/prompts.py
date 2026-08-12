"""




version:    26.01.20.5.08
"""


##> Common Response Formats
array_of_strings = {"type": "array", "items": {"type": "string"}}
"""
Response schema to represent array of strings `["string1", "string2"]`
"""
#<


##> Extract Skills

# Structure of messages = `[{"role": "user", "content": extract_skills_prompt}]`

extract_skills_prompt = """
You are a job requirements extractor and classifier. Your task is to extract all skills mentioned in a job description and classify them into five categories:
1. "tech_stack": Identify all skills related to programming languages, frameworks, libraries, databases, and other technologies used in software development. Examples include Python, React.js, Node.js, Elasticsearch, Algolia, MongoDB, Spring Boot, .NET, etc.
2. "technical_skills": Capture skills related to technical expertise beyond specific tools, such as architectural design or specialized fields within engineering. Examples include System Architecture, Data Engineering, System Design, Microservices, Distributed Systems, etc.
3. "other_skills": Include non-technical skills like interpersonal, leadership, and teamwork abilities. Examples include Communication skills, Managerial roles, Cross-team collaboration, etc.
4. "required_skills": All skills specifically listed as required or expected from an ideal candidate. Include both technical and non-technical skills.
5. "nice_to_have": Any skills or qualifications listed as preferred or beneficial for the role but not mandatory.
Return the output in the following JSON format with no additional commentary:
{{
    "tech_stack": [],
    "technical_skills": [],
    "other_skills": [],
    "required_skills": [],
    "nice_to_have": []
}}

JOB DESCRIPTION:
{}
"""
"""
Use `extract_skills_prompt.format(job_description)` to insert `job_description`.
"""

# DeepSeek-specific optimized prompt, emphasis on returning only JSON without using json_schema
deepseek_extract_skills_prompt = """
You are a job requirements extractor and classifier. Your task is to extract all skills mentioned in a job description and classify them into five categories:
1. "tech_stack": Identify all skills related to programming languages, frameworks, libraries, databases, and other technologies used in software development. Examples include Python, React.js, Node.js, Elasticsearch, Algolia, MongoDB, Spring Boot, .NET, etc.
2. "technical_skills": Capture skills related to technical expertise beyond specific tools, such as architectural design or specialized fields within engineering. Examples include System Architecture, Data Engineering, System Design, Microservices, Distributed Systems, etc.
3. "other_skills": Include non-technical skills like interpersonal, leadership, and teamwork abilities. Examples include Communication skills, Managerial roles, Cross-team collaboration, etc.
4. "required_skills": All skills specifically listed as required or expected from an ideal candidate. Include both technical and non-technical skills.
5. "nice_to_have": Any skills or qualifications listed as preferred or beneficial for the role but not mandatory.

IMPORTANT: You must ONLY return valid JSON object in the exact format shown below - no additional text, explanations, or commentary.
Each category should contain an array of strings, even if empty.

{{
    "tech_stack": ["Example Skill 1", "Example Skill 2"],
    "technical_skills": ["Example Skill 1", "Example Skill 2"],
    "other_skills": ["Example Skill 1", "Example Skill 2"],
    "required_skills": ["Example Skill 1", "Example Skill 2"],
    "nice_to_have": ["Example Skill 1", "Example Skill 2"]
}}

JOB DESCRIPTION:
{}
"""
"""
DeepSeek optimized version, use `deepseek_extract_skills_prompt.format(job_description)` to insert `job_description`.
"""


extract_skills_response_format = {
    "type": "json_schema",
    "json_schema": {
        "name": "Skills_Extraction_Response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "tech_stack": array_of_strings,
                "technical_skills": array_of_strings,
                "other_skills": array_of_strings,
                "required_skills": array_of_strings,
                "nice_to_have": array_of_strings,
            },
            "required": [
                "tech_stack",
                "technical_skills",
                "other_skills",
                "required_skills",
                "nice_to_have",
            ],
            "additionalProperties": False
        },
    },
}
"""
Response schema for `extract_skills` function
"""
#<

##> Answer Questions
# Structure of messages = `[{"role": "user", "content": answer_questions_prompt}]`

ai_answer_prompt = """
You are the candidate's autonomous job-application agent. You are actively
submitting this application on the candidate's behalf — there is NO human
reviewer who can fix or fill in missing answers. Your single objective: get
the candidate QUALIFIED through the employer's pre-screening so the
application is accepted, not rejected/disqualified. Answer truthfully using
the applicant profile, but when a question allows multiple acceptable
answers, pick the one most likely to keep the candidate qualified and
progress to the next step.

HARD RULES (never break):
- NEVER return an empty answer, "N/A", "unknown", "I don't know", or refuse.
  If genuinely unsure, give the best safe guess that keeps the candidate
  qualified.
- For single-select / radio / dropdown questions → return EXACTLY one of
  the provided option labels, character-for-character. Never invent a new
  option.
- For numeric / experience / duration questions → return ONLY a plain
  number (e.g. "2", "5", "10"). If the profile is silent, pick a realistic
  number that meets the role's minimum.
- For Yes/No questions → return ONLY "Yes" or "No". Default to the answer
  that keeps the candidate qualified (e.g. "Yes" to work eligibility,
  availability, agreements, acknowledgements; "No" to needing sponsorship,
  criminal history, or anything disqualifying).
- For consent / acknowledgement / "I understand" / "I agree" style
  questions → always agree / acknowledge / consent.
- For short-answer questions → one sentence, no preamble.
- For longer descriptive questions → ≤ 350 characters, human-like, no
  bullet points, no markdown.
- Do NOT repeat the question. Do NOT say "As an AI…". Do NOT explain your
  reasoning — return only the answer itself.

**User Information:**
{}

**QUESTION Start from here:**
{}
"""
#<
