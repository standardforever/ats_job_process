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

PAGE CATEGORIES (choose exactly ONE):

1. **jobs_listed**
   - Multiple job postings are directly visible on this page
   - Job titles with links such as Apply, More Info, or View Details are present
   - This represents a full job listings page

2. **job_listings_preview_page**
   - A limited or featured subset of jobs is visible (eg "Featured roles")
   - A link or button exists to view ALL jobs on another page
   - next_action: "navigate"
   - Populate next_action_target with the link/button to the full listings

3. **navigation_required**
   - No job postings are visible on this page
   - The page indicates jobs exist and requires navigation to find them
   - Examples: "View open roles", "Careers", "We're hiring", "Vacancies" etc
   - next_action: "navigate"
   - Populate next_action_target

4. **single_job_posting** 
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

5. **not_job_related**
   - No job, career, or hiring content

RULES:
- Links next to job titles (Apply, View Details, More Info) are job_url, not navigation
- If page_category is single_job_posting job_url == {url}
- Navigation is only for finding where jobs are listed
- If SOME jobs are shown AND a link exists to view all jobs, classify as job_listings_preview_page
- Extract ALL jobs visible on the page only


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
    "confidence_reason": "<brief explanation>",
    "domain_name": "<website main domain>",
    "url": "<main url>",

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


def get_job_ats_determination(page_text: str, site_domain: str | None, main_domain):
  return f"""
You are an expert at detecting Applicant Tracking Systems (ATS) on job posting pages.

You will be provided with:
- **site_domain**: The domain of the job listing page (e.g., "company.com")
- **page_text**: The full text content of the job page
- **page_html**: The HTML body of the page (optional, for deeper analysis)

Your task is to determine if this job posting uses an ATS and return a structured JSON response.

## Some OF ATS Detection Criteria:
NOTE: You are not limited to this Criteria alone, use your INTERNAL REASONING as well

### 1. **External Apply URL (High Confidence)**
- If there's an "Apply" button/link and the URL domain differs from site_domain
- Common ATS domains: workday.com, greenhouse.io, lever.co, icims.com, myworkdayjobs.com, smartrecruiters.com, taleo.net, ultipro.com, bamboohr.com, jobvite.com, applytojob.com, recruiting.com, etc.
- Example: site_domain is "tesla.com" but apply link is "tesla.wd5.myworkdayjobs.com" → ATS detected

### 2. **Login/Authentication Requirements (High Confidence)**
- Page requires login, account creation, or SSO (Single Sign-On)
- Keywords: "Sign in to apply", "Create an account", "Login required", "Register to continue", "Please log in"
- OAuth providers: "Sign in with Google/LinkedIn/Microsoft"

### 3. **Embedded ATS Forms/iFrames (High Confidence)**
- iframe elements pointing to external domains
- Embedded application forms from third-party providers
- Look for iframe src attributes with different domains

### 4. **Common ATS Indicators in Text/HTML (Medium-High Confidence)**
- Brand names: "Powered by Workday", "Greenhouse ATS", "Lever", "iCIMS", "Taleo", "BambooHR", "SmartRecruiters", "Jobvite"
- Multi-step application: "Step 1 of 3", "Application Progress", "Continue Application"
- Profile creation: "Complete your profile", "Build your candidate profile"

### 5. **Redirect Indicators (Medium Confidence)**
- Text like "You will be redirected to our application portal"
- "Apply on [external site name]"
- "This will open in a new window/tab"

### 6. **Application Type Detection**
Determine the application method:
- **external_ats**: Redirects to external ATS platform
- **embedded_form**: Form embedded on same page but powered by ATS
- **native_form**: Direct form on company website (no ATS)
- **email_application**: Email-based application (mailto: link)
- **redirect_required**: Needs redirect to determine
- **login_required**: Must authenticate first
- **unknown**: Cannot determine from current information

### 7. **Button/Link Analysis**
- If apply action is a button (not a link), extract the exact button text
- If apply action is a link, extract the full URL
- Check for JavaScript-based navigation or dynamic content loading

## Response Format:

Return a JSON object with the following structure:
JSON SCHEMA:
{{
  "is_ats": boolean,  // true if ATS detected, false if native application | email application, null if uncertain
  "confidence": string,  // "high" | "medium" | "low" | "uncertain"
  "application_type": string,  // "external_ats" | "embedded_form" | "native_form" | "email_application" | "redirect_required" | "login_required" | "unknown"
  "ats_provider": string | null,  // e.g., "Workday", "Greenhouse", "Lever", null if unknown
  "reasoning": string,  // Detailed explanation of your decision
  "apply_url": string | null,  // Full apply URL if available, null otherwise
  "apply_button_text": string | null,  // Exact button text if it's a button (not a link)
  "requires_scraping": boolean,  // true if you need to scrape apply_url to confirm
  "indicators_found": list[string],  // List of specific indicators that led to this conclusion
  "additional_notes": string | null,  // Any other relevant observations
  "is_job_related": bool // true if  the page is related to job listening or job page login or job related else false
}}


## Important Guidelines:

1. **Be thorough**: Check multiple indicators before making a determination
2. **Domain matching is critical**: Even subdomains of different root domains indicate ATS (e.g., careers.company.com vs apply.atsystem.com)
3. **Look for brand names**: ATS providers often have branding/footers
4. **Multi-step = likely ATS**: Complex applications usually indicate ATS
5. **When uncertain**: Set requires_scraping to true and provide the URL
6. **Button vs Link**: Carefully distinguish between clickable buttons and hyperlinks
7. **Be specific in reasoning**: Reference actual text/elements you found


OUTPUT FORMAT:
- Return ONLY valid JSON
- Start with {{ and end with }}
- Do NOT wrap in markdown code blocks (no ```json or ```)
- No markdown, no explanations, no preamble
- Use null for missing fields (never use empty strings)

site_domain: {site_domain}

page_text: {page_text}


Now analyze the provided job page and return your JSON response.
"""





