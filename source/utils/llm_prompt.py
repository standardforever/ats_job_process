

from typing import Dict, Any


def create_job_page_analysis_prompt(url: str | None, text: str) -> str:
    """
    Creates the analysis prompt with embedded response schema.
    """

    prompt = f"""Analyze the webpage below and classify its job-related status.

URL: {url}

PAGE CONTENT:
{text}


---

## NOTE: DO NOT Hallucinations anyting you can't find from the text 

PAGE CATEGORIES (choose exactly ONE):

1. **jobs_listed**
   - Multiple job postings are directly visible on this page
   - Job titles with links such as Apply, More Info, or View Details are present
   - This represents a full job listings page

2. **navigation_required**
   - No job postings are visible on this page
   - The page indicates jobs exist and requires navigation to find them
   - Examples: "View open roles", "Careers", "We're hiring", "Vacancies", "Opportunities", etc
   - next_action: "navigate"
   - Populate next_action_target

3. **single_job_posting** 
    - A specific job opportunity is described on this page
   This includes BOTH:

   a) **Detailed job postings** with full descriptions:
      - Comprehensive job description, requirements, responsibilities
      - Salary, benefits, qualifications listed
      
   b) **Minimal job postings** with basic information:
      - Just a job title/role and brief description
      - "We're hiring for X role" announcements
      - Simple vacancy notices with contact info to apply or inquire
      - Posts that mention a position and how to apply/get more info

4. **not_job_related**
   - No job, career, or hiring content

5. **job_listings_preview_page**
   - A limited or featured subset of jobs is visible (eg "Featured roles")
   - A link or button exists to view ALL jobs on another page
   - next_action: "navigate"
   - Populate next_action_target with the link/button to the full listings

RULES:
- Links next to job titles (Apply, View Details, More Info) are job_url, not navigation
- If page_category is single_job_posting job_url == {url}
- Navigation is only for finding where jobs are listed
- If SOME jobs are shown AND a link exists to view all jobs, classify as job_listings_preview_page
- Extract ALL jobs visible on the page only


URL RESOLUTION RULE:
- When extracting job_url from links:
  - If the link starts with "/" (e.g. "/opportunities/role-x"):
    • Build the full URL as: scheme + "://" + domain_name + link
    • Example 1:
      Base URL: https://www.site.com/community/opportunities
      Link: /opportunities/family-arts-conference-2026-changemakers-storytellers
      Result: https://www.site.com/opportunities/family-arts-conference-2026-changemakers-storytellers
    • Example 2:
      Base URL: https://job.site.com/string
      Link: /role-x
      Result: https://job.site.com/role-x
  - If the link already starts with "http://" or "https://":
    • Use it as-is
  - Do NOT guess, infer, or modify URLs beyond these rules


JOB ALERT DETECTION RULE:
- If the page explicitly mentions signing up for job alerts, vacancy notifications, or email updates about jobs,
  mark job_alert = true.
- Examples of phrases that indicate job alerts:
  • "Job Alerts"
  • "Sign up for job alerts"
  • "Get notified of vacancies"
  • "Receive vacancy updates"
  • "Email alerts for new roles"
- Do NOT infer job alerts unless explicitly stated in the page text.
- Job alert pages are NOT job listings and do NOT count as job postings.


RESPONSE FORMAT:
- Return ONLY valid JSON
- Do NOT wrap in markdown code blocks (no ```json or ```)
- Do NOT include any text before or after the JSON
- Start directly with {{ and end with }}
- Return the result strictly using the schema below.

RESPONSE SCHEMA:

{{
    "page_category": "jobs_listed" | "job_listings_preview_page" | "navigation_required" | "single_job_posting" | "not_job_related",
    "next_action": "scrape_jobs" | "navigate" | "scrape_single_job" | "stop",
    "confidence": <float 0.0-1.0>,
    "reasoning": string,  // DETAILED explanation of your decision
    "domain_name": "<website main domain>",
    "url": "<main url>",
    "job_alert": boolean | None
    

    "next_action_target": {{
        "url": "<URL or null>",
        "link_text": "<text or null>",
        "element_type": "link" | "button" | null
    }},

    "jobs_listed_on_page": [
        {{
            "title": "<job title>",
            "job_url": "<full URL or null>",
            "path": "<path>"
        }}
    ],
}}
"""
    return prompt





# def get_job_ats_determination(page_text: str, site_domain: str | None, main_domain):
#     return f"""
# You are an expert at detecting Applicant Tracking Systems (ATS) on job posting pages.

# CRITICAL: You must provide a definitive answer OR explicitly state uncertainty.

# ## Your Task:
# Analyze the page and determine:
# 1. Is this an ATS-based application?
# 2. Is this page actually job-related?
# 3. What is your confidence level?

# ## Detection Criteria:

# ### HIGH CONFIDENCE ATS Indicators:
# 1. **External Apply URL**: Apply button/link domain differs from site_domain
#    - Common ATS: workday.com, greenhouse.io, lever.co, icims.com, myworkdayjobs.com, smartrecruiters.com, taleo.net, etc.
#    - Example: site_domain is "tesla.com" but apply link is "tesla.wd5.myworkdayjobs.com" → ATS detected
   
# 2. **Login/Authentication Required**
#     - Page requires login, account creation, or SSO (Single Sign-On)
#     - Keywords: "Sign in to apply", "Create an account", "Login required", "Register to continue", "Please log in"
#     - OAuth providers: "Sign in with Google/LinkedIn/Microsoft"

# 3. **Embedded ATS Forms/iFrames**: iframe elements from external domains

# 4. **ATS Branding**: "Powered by Workday", "Greenhouse ATS", "Lever", etc.
#     - Profile creation: "Complete your profile", "Build your candidate profile"

# ### HIGH CONFIDENCE NON-ATS Indicators:
# 1. **Direct email application**: mailto: links with no external forms
# 2. **Simple contact form**: Native HTML form on same domain
# 3. **Direct application instructions**: "Email resume to..." with no ATS mention

# ### Page Validity Checks (CRITICAL):
# **is_job_related should be FALSE if:**
# - Generic homepage content
# - 404/error page indicators
# - Job listing page (multiple jobs, not single posting)
# - "Position no longer available"/"Job expired"/"Position filled"
# - Generic "Careers" page without specific job details
# - No job title, requirements, or application method visible

# **is_job_related should be TRUE only if:**
# - Specific job title and description present
# - Clear application method exists
# - Job requirements/qualifications listed

# ### Confidence Levels:
# - **high**: Multiple clear indicators, definitive answer
# - **medium**: Some indicators present, reasonably certain
# - **low**: Few or conflicting indicators
# - **uncertain**: Cannot determine, need manual review OR page is not valid job posting

# ### Button/Link Analysis
# - If apply action is a button (not a link), extract the exact button text -> apply_button_text = 'button text' | null
# - If we see things like 'View Details' or "More information" or action button to see more about the job, extract button text -> apply_button_text = button_text | null
# - If apply action is a link, extract the full URL -> apply_url = 'apply button link/url' | null

# ### Application Type Detection
# Determine the application method:
# - **external_ats**: Redirects to external ATS platform
# - **embedded_form**: Form embedded on same page but powered by ATS
# - **native_form**: Direct form on company website (no ATS)
# - **email_application**: Email-based application (mailto: link)
# - **redirect_required**: Needs redirect to determine
# - **login_required**: Must authenticate first
# - **unknown**: Cannot determine from current information

# ## Response Schema:

# {{
#   "is_ats": boolean | null,  // true=ATS, false=non-ATS, null=uncertain
#   "confidence": "high" | "medium" | "low" | "uncertain",
#   "is_job_related": boolean,  // FALSE if expired/listing page/generic page/careers page/home page etc
#   "application_type": "external_ats" | "embedded_form" | "native_form" | "email_application" | "login_required" | "redirect_required" | "unknown",
#   "ats_provider": string | null,
#   "reasoning": string,  // DETAILED explanation of your decision
#   "apply_url": string | null,
#   "apply_button_text": string | null,
#   "detail_button": string | null, # to see more about the job 
#   "requires_scraping": boolean,  // true ONLY if you need to navigate to confirm
#   "indicators_found": list[string],
#   "page_validity_issues": list[string] | null,  // List any issues (404, expired, not job page, etc.)
#   "additional_notes": string | null
# }}

# ## Decision Rules:
# 1. If page is NOT job-related → is_job_related=false, confidence="high"
# 2. If clear ATS indicators → is_ats=true, confidence="high"
# 3. If clear non-ATS → is_ats=false, confidence="high"
# 4. If unclear/conflicting → is_ats=null, confidence="uncertain"
# 5. When uncertain, provide detailed reasoning for manual review

# ## Output Format:
# - Return ONLY valid JSON
# - Start with {{ and end with }}
# - NO markdown code blocks
# - Use null for missing fields

# ## Page Information:
# - **site_domain**: {site_domain}
# - **main_domain**: {main_domain}
# - **page_text**: {page_text}


# Now analyze and return your JSON response.
# """




def get_job_ats_determination(page_text: str, site_domain: str | None, main_domain):
    return f"""
    You are an expert at detecting Applicant Tracking Systems (ATS) and application boundaries on job posting pages.

CRITICAL:
- You must provide a definitive answer OR explicitly state uncertainty.
- For automation purposes, ANY cross-domain application flow is treated as ATS-equivalent.

## Your Task:
Analyze the page and determine:
1. Is this an ATS-equivalent application flow?
2. Is this page a valid, single job posting?
3. What is your confidence level?

---

## CORE DEFINITIONS (IMPORTANT)

### ATS-EQUIVALENT (for this system):
An application flow is considered ATS-equivalent if:
- The apply action redirects to ANY domain different from site_domain
- OR the application requires authentication, profile creation, or multi-step submission
- OR the application is embedded from an external system (iframe, script, widget)

This applies EVEN IF:
- The external site is a partner organisation
- The external site is a programme or charity microsite
- The external site is not a known ATS vendor

---

## DETECTION CRITERIA

### HIGH CONFIDENCE ATS-EQUIVALENT INDICATORS (ANY ONE IS SUFFICIENT):

1. **Cross-Domain Apply URL (OVERRIDING RULE)**
   - Apply button/link domain ≠ site_domain
   - Example:
     - site_domain = "youthmusic.org.uk"
     - apply_url = "https://musinc.org.uk/apply"
     → ATS-equivalent = TRUE

2. **Login / Authentication Required**
   - Keywords: "Sign in to apply", "Create an account", "Register", "Login required"
   - OAuth: Google, LinkedIn, Microsoft, etc.

3. **Embedded External Application**
   - iframe, script, or widget loaded from another domain

4. **Recognised ATS Vendor**
   - workday.com, greenhouse.io, lever.co, icims.com, smartrecruiters.com, taleo.net, etc.

---

### HIGH CONFIDENCE NON-ATS (INTERNAL APPLICATION ONLY):

ALL of the following must be true:
- Apply action stays on the SAME site_domain
- No external redirects
- No authentication required
- One of:
  - Native HTML form
  - mailto: email application
  - Explicit instructions to email/contact internally

If ANY apply step leaves the site_domain → NOT non-ATS.

---

## PAGE VALIDITY CHECKS (CRITICAL)

### is_job_related = FALSE if:
- 404 or error page
- Expired / filled position
- Generic careers page
- Job listings page (multiple jobs)
- Informational page with no application method

### is_job_related = TRUE only if:
- A specific job title is present
- Job description or responsibilities are visible
- A clear application method exists

---

## APPLICATION TYPE CLASSIFICATION

Choose ONE:

- **external_ats**  
  Any cross-domain application flow (vendor or non-vendor)

- **embedded_form**  
  Application embedded on page but powered by external system

- **native_form**  
  Application handled entirely on same site_domain

- **email_application**  
  mailto: link or explicit email instructions

- **login_required**  
  Authentication required before application

- **redirect_required**  
  Apply action exists but destination not visible

- **unknown**  
  Cannot determine from current information

---

## CONFIDENCE LEVELS

- **high**: Clear boundary or indicators present
- **medium**: Strong indicators, minor uncertainty
- **low**: Weak or partial indicators
- **uncertain**: Conflicting signals or insufficient information

---

## BUTTON / LINK EXTRACTION RULES

- If apply action is a link → extract full apply_url
- If apply action is a button → extract exact button text
- If link text is "View Details", "More Info", etc → detail_button
- Never guess missing URLs

---

## RESPONSE SCHEMA (STRICT)

{{
  "is_ats": boolean | null,
  "confidence": "high" | "medium" | "low" | "uncertain",
  "is_job_related": boolean,
  "application_type": "external_ats" | "embedded_form" | "native_form" | "email_application" | "login_required" | "redirect_required" | "unknown",
  "ats_provider": string | null,
  "reasoning": string,
  "apply_url": string | null,
  "apply_button_text": string | null,
  "detail_button": string | null,
  "requires_scraping": boolean,
  "indicators_found": list[string],
  "page_validity_issues": list[string] | null,
  "additional_notes": string | null
}}

---

## FINAL DECISION RULES (NON-NEGOTIABLE)

1. If apply_url domain ≠ site_domain:
   - is_ats = true
   - application_type = "external_ats"
   - confidence = "high"

2. If page is NOT job-related:
   - is_job_related = false
   - is_ats = null
   - confidence = "high"

3. If application stays fully on site_domain:
   - is_ats = false
   - confidence = "high"

4. If signals conflict or are incomplete:
   - is_ats = null
   - confidence = "uncertain"

---

## Page Context:
- site_domain: {site_domain}
- main_domain: {main_domain}
- page_text: {page_text}

Analyze the page and return ONLY valid JSON.
Start with {{ and end with }}.
Do NOT include any other text.

    """
    
    
    
    
    
    
    
    
    
    
    
# def get_job_ats_determination(page_text: str, site_domain: str | None, main_domain):
#   return f"""
# You are an expert at detecting Applicant Tracking Systems (ATS) on job posting pages.

# You will be provided with:
# - **site_domain**: The domain of the job listing page (e.g., "company.com")
# - **page_text**: The full text content of the job page
# - **page_html**: The HTML body of the page (optional, for deeper analysis)

# Your task is to determine if this job posting uses an ATS and return a structured JSON response.

# ## Some OF ATS Detection Criteria:
# NOTE: You are not limited to this Criteria alone, use your INTERNAL REASONING as well

# ### 1. **External Apply URL (High Confidence)**
# - If there's an "Apply" button/link and the URL domain differs from site_domain
# - Common ATS domains: workday.com, greenhouse.io, lever.co, icims.com, myworkdayjobs.com, smartrecruiters.com, taleo.net, ultipro.com, bamboohr.com, jobvite.com, applytojob.com, recruiting.com, etc.
# - Example: site_domain is "tesla.com" but apply link is "tesla.wd5.myworkdayjobs.com" → ATS detected

# ### 2. **Login/Authentication Requirements (High Confidence)**
# - Page requires login, account creation, or SSO (Single Sign-On)
# - Keywords: "Sign in to apply", "Create an account", "Login required", "Register to continue", "Please log in"
# - OAuth providers: "Sign in with Google/LinkedIn/Microsoft"

# ### 3. **Embedded ATS Forms/iFrames (High Confidence)**
# - iframe elements pointing to external domains
# - Embedded application forms from third-party providers
# - Look for iframe src attributes with different domains

# ### 4. **Common ATS Indicators in Text/HTML (Medium-High Confidence)**
# - Brand names: "Powered by Workday", "Greenhouse ATS", "Lever", "iCIMS", "Taleo", "BambooHR", "SmartRecruiters", "Jobvite"
# - Multi-step application: "Step 1 of 3", "Application Progress", "Continue Application"
# - Profile creation: "Complete your profile", "Build your candidate profile"

# ### 5. **Redirect Indicators (Medium Confidence)**
# - Text like "You will be redirected to our application portal"
# - "Apply on [external site name]"
# - "This will open in a new window/tab"

# ### 6. **Application Type Detection**
# Determine the application method:
# - **external_ats**: Redirects to external ATS platform
# - **embedded_form**: Form embedded on same page but powered by ATS
# - **native_form**: Direct form on company website (no ATS)
# - **email_application**: Email-based application (mailto: link)
# - **redirect_required**: Needs redirect to determine
# - **login_required**: Must authenticate first
# - **unknown**: Cannot determine from current information

# ### 7. **Button/Link Analysis**
# - If apply action is a button (not a link), extract the exact button text
# - If apply action is a link, extract the full URL
# - Check for JavaScript-based navigation or dynamic content loading

# ## Response Format:

# Return a JSON object with the following structure:
# JSON SCHEMA:
# {{
#   "is_ats": boolean,  // true if ATS detected, false if native application | email application, null if uncertain
#   "confidence": string,  // "high" | "medium" | "low" | "uncertain"
#   "application_type": string,  // "external_ats" | "embedded_form" | "native_form" | "email_application" | "redirect_required" | "login_required" | "unknown"
#   "ats_provider": string | null,  // e.g., "Workday", "Greenhouse", "Lever", null if unknown
#   "reasoning": string,  // Detailed explanation of your decision
#   "apply_url": string | null,  // Full apply URL if available, null otherwise
#   "apply_button_text": string | null,  // Exact button text if it's a button (not a link)
#   "requires_scraping": boolean,  // true if you need to scrape apply_url to confirm
#   "indicators_found": list[string],  // List of specific indicators that led to this conclusion
#   "additional_notes": string | null,  // Any other relevant observations
#   "is_job_related": bool // true if  the page is related to job listening or job page login or job related else false
# }}


# ## Important Guidelines:

# 1. **Be thorough**: Check multiple indicators before making a determination
# 2. **Domain matching is critical**: Even subdomains of different root domains indicate ATS (e.g., careers.company.com vs apply.atsystem.com)
# 3. **Look for brand names**: ATS providers often have branding/footers
# 4. **Multi-step = likely ATS**: Complex applications usually indicate ATS
# 5. **When uncertain**: Set requires_scraping to true and provide the URL
# 6. **Button vs Link**: Carefully distinguish between clickable buttons and hyperlinks
# 7. **Be specific in reasoning**: Reference actual text/elements you found


# OUTPUT FORMAT:
# - Return ONLY valid JSON
# - Start with {{ and end with }}
# - Do NOT wrap in markdown code blocks (no ```json or ```)
# - No markdown, no explanations, no preamble
# - Use null for missing fields (never use empty strings)

# site_domain: {site_domain}

# page_text: {page_text}


# Now analyze the provided job page and return your JSON response.
# """





