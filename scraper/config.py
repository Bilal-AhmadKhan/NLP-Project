"""
Registry of FAST-NUCES campuses and degree "families" used by the extractor.

To target a different campus or degree you normally only need the CLI:

    python -m scraper.fast_scraper --campus islamabad --degree se

To add a brand-new campus or degree, add an entry to CAMPUSES / DEGREES below.

How the keywords are used
-------------------------
Keywords are matched as whole words against a *normalised* string (lower-cased,
every non-alphanumeric character turned into a space). So the keyword "cs"
matches the URL ".../cs-faculty/" and the text "BS(CS)", but not "physics".
A link is followed when its URL path or anchor text contains a keyword, which
is what lets the same crawler work on every campus site even though each one
is built differently (e.g. Lahore uses /fsc/, Islamabad uses Faculty-DCS.php,
Karachi uses /department-of-computer-science/, Peshawar uses /cs-faculty/).
"""

# Main university site. Program pages live at {HQ_SITE}/Program/<code>.
HQ_SITE = "https://www.nu.edu.pk"

CAMPUSES = {
    "lahore": {
        "name": "Lahore",
        "site": "https://lhr.nu.edu.pk/",
        # Slug used by HQ pages such as https://www.nu.edu.pk/Campus/Lahore/Research
        "hq_slug": "Lahore",
    },
    "islamabad": {
        "name": "Islamabad",
        "site": "https://isb.nu.edu.pk/",
        "hq_slug": "Islamabad",
    },
    "karachi": {
        "name": "Karachi",
        "site": "https://khi.nu.edu.pk/",
        "hq_slug": "Karachi",
    },
    "peshawar": {
        "name": "Peshawar",
        "site": "https://pwr.nu.edu.pk/",
        "hq_slug": "Peshawar",
    },
    "chiniot-faisalabad": {
        "name": "Chiniot-Faisalabad",
        "site": "https://cfd.nu.edu.pk/",
        "hq_slug": "Chiniot-Faisalabad",
    },
    "multan": {
        "name": "Multan",
        "site": "https://mtn.nu.edu.pk/",
        "hq_slug": None,
    },
}

# Each degree family lists the HQ program codes to pull (the part after
# /Program/ in https://www.nu.edu.pk/Program/BS(CS)) and the keywords used to
# find the matching department pages on a campus website.
DEGREES = {
    "cs": {
        "name": "Computer Science",
        "programs": ["BS(CS)", "MS(CS)", "PhD(CS)"],
        "keywords": [
            "computer science", "cs", "bscs", "mscs", "dcs",
            "fsc", "school of computing", "computing",
        ],
    },
    "se": {
        "name": "Software Engineering",
        "programs": ["BS(SE)", "MS(SPM)"],
        "keywords": [
            "software engineering", "se", "bsse", "dse", "spm",
            "software project management", "fsc", "school of computing",
        ],
    },
    "ds": {
        "name": "Data Science",
        "programs": ["BS(DS)", "MS(DS)"],
        "keywords": [
            "data science", "ds", "bsds", "daids",
            "artificial intelligence data science", "fsc", "school of computing",
        ],
    },
    "ai": {
        "name": "Artificial Intelligence",
        "programs": ["BS(AI)", "MS(AI)"],
        "keywords": [
            "artificial intelligence", "ai", "bsai", "daids",
            "fsc", "school of computing",
        ],
    },
    "cy": {
        "name": "Cyber Security",
        "programs": ["BS(CY)", "MS(CY)"],
        "keywords": [
            "cyber security", "cybersecurity", "cy", "bscy", "dcy",
            "fsc", "school of computing",
        ],
    },
    "ce": {
        "name": "Computer Engineering",
        "programs": ["BS(CE)", "MS(CE)"],
        "keywords": ["computer engineering", "ce", "bsce"],
    },
    "ee": {
        "name": "Electrical Engineering",
        "programs": ["BS(EE)", "MS(EE)", "PhD(EE)"],
        "keywords": ["electrical engineering", "ee", "bsee", "dee"],
    },
    "cv": {
        "name": "Civil Engineering",
        "programs": ["BS(CV)", "MS(CV)", "PhD(CV)"],
        "keywords": ["civil engineering", "civil", "cv", "bscv"],
    },
    "business": {
        "name": "Business / Management",
        "programs": ["BBA", "BS(AF)", "BS(BA)", "BS(FinTech)", "MBA", "MS(BA)", "PhD(MS)"],
        "keywords": [
            "school of management", "management", "business", "fsm", "dms",
            "bba", "mba", "accounting", "finance", "fintech", "business analytics",
        ],
    },
}

# University-wide pages (admissions, fees, rules) that apply to every
# degree/campus. Fetched from HQ_SITE unless --no-general is passed.
GENERAL_HQ_PAGES = [
    "/Admissions/EligibilityCriteria",
    "/Admissions/HowToApply",
    "/Admissions/Schedule",
    "/Admissions/TestPattern",
    "/Admissions/FeeStructure",
    "/Admissions/Scholarship",
    "/Admissions/FAQ",
    "/Student/Grading",
    "/Student/RulesUndergrad",
    "/Student/Calender",
    "/Student/Conduct",
    "/Student/FinancialRules",
]

# Campus-specific pages on HQ_SITE; "{slug}" is replaced by the campus hq_slug.
CAMPUS_HQ_PAGES = [
    "/Campus/{slug}/Research",
    "/Campus/{slug}/PhdSupervisors",
]

# Links on the campus homepage that are worth following even when they don't
# mention the degree (general campus info a student would ask about).
GENERAL_CAMPUS_KEYWORDS = [
    "about", "admission", "admissions", "campus life", "campuslife",
    "societies", "society", "support", "contact", "facilities",
    "hostel", "transport", "library",
]

# File extensions that are never worth fetching.
SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico",
    ".css", ".js", ".zip", ".rar", ".7z", ".mp4", ".mp3", ".avi", ".mov",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".exe", ".apk",
)
