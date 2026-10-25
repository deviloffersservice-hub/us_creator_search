"""Niches / roles: how posts get tagged, and where the crawl starts when it wants a niche.

Each niche has:
  keywords - words that tag a post's text with this niche (generic words count less)
  search   - phrases for the best-effort web search for fresh posts
  topics   - LinkedIn's public "top-content" categories (linkedin.com/top-content/<slug>/),
             whose pages list posts by people in that field: a free way into any niche
"""
import re

NICHES: dict[str, dict[str, list[str]]] = {
    "HR & People Ops": {
        "keywords": ["hr", "human resources", "people ops", "people operations", "chro", "hrbp",
                     "employee experience", "employee engagement", "workplace culture", "onboarding",
                     "performance review", "benefits", "compensation", "retention", "employees"],
        "search": ["human resources", "HR leader", "people operations"],
        "topics": ["recruitment-hr", "employee-experience", "organizational-culture"],
    },
    "Recruiting & Talent Acquisition": {
        "keywords": ["recruiter", "recruiting", "recruitment", "talent acquisition", "hiring", "sourcing",
                     "candidates", "candidate", "headhunter", "job seekers", "interview"],
        "search": ["recruiter", "talent acquisition"],
        "topics": ["recruitment-hr"],
    },
    "Venture Capital & Investors": {
        "keywords": ["venture capital", "vc", "vcs", "investor", "investors", "angel investor", "fundraising",
                     "fundraise", "term sheet", "lps", "portfolio company", "seed round", "series a", "valuation"],
        "search": ["venture capital", "angel investor", "fundraising startup"],
        "topics": ["fundraising"],
    },
    "Founders & Startups": {
        "keywords": ["founder", "founders", "startup", "startups", "bootstrapped", "building in public",
                     "co-founder", "cofounder", "early stage", "product market fit", "mvp"],
        "search": ["startup founder", "building in public"],
        "topics": ["innovation"],
    },
    "Software Engineering": {
        "keywords": ["software engineer", "engineer", "engineers", "engineering", "developer", "developers",
                     "coding", "programming", "devops", "backend", "frontend", "open source", "code",
                     "python", "javascript", "cloud", "kubernetes", "api"],
        "search": ["software engineer", "developer"],
        "topics": ["engineering", "technology"],
    },
    "AI & Machine Learning": {
        "keywords": ["ai", "artificial intelligence", "machine learning", "llm", "llms", "genai", "generative ai",
                     "chatgpt", "agents", "agentic", "prompt"],
        "search": ["artificial intelligence", "machine learning"],
        "topics": ["artificial-intelligence"],
    },
    "Data & Analytics": {
        "keywords": ["data", "analytics", "data science", "data scientist", "sql", "dashboard", "data engineering",
                     "bi", "tableau", "power bi"],
        "search": ["data analytics", "data science"],
        "topics": [],
    },
    "Cybersecurity": {
        "keywords": ["cybersecurity", "security", "infosec", "ciso", "breach", "ransomware", "zero trust",
                     "phishing", "threat"],
        "search": ["cybersecurity"],
        "topics": [],
    },
    "Product Management": {
        "keywords": ["product manager", "product management", "product", "roadmap", "pm", "product strategy",
                     "product led", "user research", "discovery"],
        "search": ["product manager", "product management"],
        "topics": [],
    },
    "Design & UX": {
        "keywords": ["ux", "ui", "designer", "design", "user experience", "figma", "design system", "usability"],
        "search": ["UX designer", "product design"],
        "topics": ["design", "user-experience"],
    },
    "Corporate Finance & Accounting": {
        "keywords": ["cfo", "fp&a", "accounting", "accountant", "cpa", "audit", "controller", "finance team",
                     "budget", "forecast", "ebitda", "tax", "bookkeeping", "finance"],
        "search": ["CFO", "accounting", "FP&A"],
        "topics": ["finance", "economics"],
    },
    "Investing & Personal Finance": {
        "keywords": ["investing", "stocks", "stock market", "wealth", "financial advisor", "retirement",
                     "personal finance", "portfolio", "money", "dividend", "401k", "financial planning"],
        "search": ["personal finance", "financial advisor", "investing"],
        "topics": ["finance"],
    },
    "Banking & Fintech": {
        "keywords": ["bank", "banking", "fintech", "payments", "lending", "credit", "wall street",
                     "investment banking", "private equity"],
        "search": ["fintech", "investment banking", "private equity"],
        "topics": ["finance"],
    },
    "Marketing": {
        "keywords": ["marketing", "marketer", "brand", "branding", "seo", "campaign", "cmo", "demand gen",
                     "advertising", "content marketing", "growth marketing", "b2b marketing", "content"],
        "search": ["marketing", "B2B marketing"],
        "topics": ["marketing"],
    },
    "Sales": {
        "keywords": ["sales", "selling", "prospecting", "pipeline", "quota", "cold email", "cold calling",
                     "closing", "sdr", "bdr", "account executive", "revenue", "deals"],
        "search": ["sales", "B2B sales"],
        "topics": ["sales", "negotiation"],
    },
    "Customer Success & Experience": {
        "keywords": ["customer success", "customer experience", "cx", "customer support", "churn",
                     "customer service", "nps"],
        "search": ["customer success"],
        "topics": ["customer-experience"],
    },
    "Leadership & Management": {
        "keywords": ["leadership", "leader", "leaders", "manager", "managers", "management", "ceo", "executive",
                     "executives", "team", "teams", "change management"],
        "search": ["leadership", "executive coach"],
        "topics": ["leadership", "change-management"],
    },
    "Career & Job Search": {
        "keywords": ["career", "careers", "job search", "resume", "job", "laid off", "layoff", "promotion",
                     "networking", "job seekers", "career coach", "personal growth"],
        "search": ["career coach", "job search"],
        "topics": ["career", "networking"],
    },
    "Consulting & Strategy": {
        "keywords": ["consulting", "consultant", "strategy", "business strategy", "advisory", "mckinsey",
                     "transformation"],
        "search": ["management consultant", "business strategy"],
        "topics": ["consulting", "business-strategy"],
    },
    "Operations & Supply Chain": {
        "keywords": ["supply chain", "logistics", "operations", "manufacturing", "procurement", "warehouse",
                     "freight", "inventory"],
        "search": ["supply chain", "logistics"],
        "topics": ["supply-chain-management"],
    },
    "Project Management": {
        "keywords": ["project management", "project manager", "pmp", "agile", "scrum", "program manager"],
        "search": ["project manager", "agile coach"],
        "topics": ["project-management"],
    },
    "Healthcare & Medicine": {
        "keywords": ["healthcare", "health", "medical", "doctor", "physician", "nurse", "nursing", "patient",
                     "patients", "clinical", "hospital", "pharma", "biotech"],
        "search": ["healthcare", "physician", "nurse"],
        "topics": ["healthcare"],
    },
    "Science & Research": {
        "keywords": ["science", "scientist", "research", "researcher", "phd", "lab", "biology", "physics"],
        "search": ["scientist research"],
        "topics": ["science"],
    },
    "Education & Training": {
        "keywords": ["education", "teacher", "teachers", "students", "learning", "school", "university",
                     "edtech", "training", "l&d", "learning and development"],
        "search": ["education", "teacher", "learning and development"],
        "topics": ["education", "training-development"],
    },
    "Real Estate": {
        "keywords": ["real estate", "realtor", "mortgage", "housing", "property", "properties",
                     "commercial real estate", "cre", "home buyers", "broker"],
        "search": ["real estate", "realtor"],
        "topics": ["real-estate"],
    },
    "Legal": {
        "keywords": ["legal", "lawyer", "lawyers", "attorney", "law", "law firm", "compliance", "contract",
                     "litigation", "general counsel"],
        "search": ["attorney", "lawyer"],
        "topics": [],
    },
    "Retail & E-commerce": {
        "keywords": ["ecommerce", "e-commerce", "shopify", "retail", "dtc", "amazon seller", "consumer brand",
                     "cpg", "merchandising"],
        "search": ["ecommerce", "retail"],
        "topics": ["ecommerce", "retail-merchandising"],
    },
    "Hospitality, Travel & Events": {
        "keywords": ["hospitality", "hotel", "hotels", "travel", "tourism", "restaurant", "events",
                     "event planning"],
        "search": ["hospitality", "event planner"],
        "topics": ["hospitality-tourism", "event-planning"],
    },
    "Sustainability & ESG": {
        "keywords": ["sustainability", "climate", "esg", "renewable", "carbon", "clean energy", "net zero",
                     "csr"],
        "search": ["sustainability", "climate"],
        "topics": ["corporate-social-responsibility"],
    },
    "Writing & Content Creation": {
        "keywords": ["writing", "writer", "copywriting", "copywriter", "newsletter", "ghostwriting",
                     "ghostwriter", "storytelling", "content creator", "creator", "creators"],
        "search": ["copywriter", "ghostwriter", "newsletter"],
        "topics": ["writing"],
    },
    "Communication & Soft Skills": {
        "keywords": ["communication", "public speaking", "emotional intelligence", "negotiation", "feedback",
                     "presentation", "soft skills"],
        "search": ["public speaking", "communication coach"],
        "topics": ["communication", "soft-skills-emotional-intelligence"],
    },
    "Productivity & Future of Work": {
        "keywords": ["productivity", "remote work", "hybrid work", "future of work", "habits", "time management",
                     "four day week", "workplace"],
        "search": ["productivity", "remote work"],
        "topics": ["productivity", "future-of-work", "workplace-trends"],
    },
    "Wellness & Mental Health": {
        "keywords": ["wellness", "mental health", "burnout", "mindfulness", "fitness", "wellbeing", "therapy",
                     "therapist", "stress"],
        "search": ["mental health", "wellness coach"],
        "topics": [],
    },
    "Personal Branding & LinkedIn Growth": {
        "keywords": ["personal brand", "personal branding", "linkedin", "audience", "followers", "thought leadership",
                     "linkedin growth"],
        "search": ["personal branding", "LinkedIn tips"],
        "topics": [],
    },
    "DEI & Social Impact": {
        "keywords": ["diversity", "inclusion", "dei", "equity", "belonging", "nonprofit", "social impact",
                     "women in"],
        "search": ["diversity and inclusion", "nonprofit"],
        "topics": [],
    },
}

# Words nearly every post uses nowadays count for less, so e.g. a marketing post that mentions AI
# is still tagged Marketing.
GENERIC = {"ai", "data", "content", "team", "teams", "product", "design", "job", "health", "money", "learning",
           "security", "linkedin", "creator", "creators", "leader", "leaders", "law", "finance", "engineering",
           "code", "cloud", "api", "strategy", "operations", "research", "training", "workplace", "feedback",
           "revenue", "deals", "credit", "portfolio", "investors", "employees", "interview", "hiring",
           "management", "manager", "managers", "career", "events", "travel", "stress", "followers", "audience",
           "benefits", "retention", "candidate", "pipeline", "budget", "forecast", "tax", "discovery", "pm", "cre",
           "broker", "lab", "brand", "bank", "school", "property", "properties", "patient", "patients"}
_PATTERNS = {
    name: [(re.compile(rf"(?<![\w&]){re.escape(k)}(?![\w&])", re.I), 0.3 if k in GENERIC else 1.0)
           for k in spec["keywords"]]
    for name, spec in NICHES.items()
}

# AI comes up in most posts now. Count AI at half strength so e.g. a recruiter writing about AI
# in hiring is tagged Recruiting; AI wins only when the post is mainly about AI itself.
DAMPED = {"AI & Machine Learning": 0.5}

OTHER = "General / Other"
ALL = list(NICHES)


def classify(text: str) -> str:
    if not text:
        return OTHER
    text = text.replace("#", " ").replace("-", " ")
    best, best_score = OTHER, 0.0
    for niche, pats in _PATTERNS.items():
        score = sum(len(p.findall(text)) * w for p, w in pats) * DAMPED.get(niche, 1.0)
        if score > best_score:
            best, best_score = niche, score
    return best


def text_from_post_url(url: str) -> str:
    """The words in a post URL's slug ("jane_how-we-hire-recruiters-activity-..") help tagging."""
    m = re.search(r"/posts/[^_/]+_(.*?)-(?:activity|ugcpost|share)-\d", url, re.I)
    return m.group(1).replace("-", " ") if m else ""


def focus_niches(counts: dict[str, int], k: int, rotation: int) -> list[str]:
    """The k niches with the fewest qualified creators so far (ties broken by a rotating order)."""
    order = ALL[rotation % len(ALL):] + ALL[: rotation % len(ALL)]
    return sorted(order, key=lambda n: counts.get(n, 0))[:k]
