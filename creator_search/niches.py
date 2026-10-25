"""Keyword-based niche tagging from post text. Cheap and transparent; good enough for sorting."""
import re

NICHES: dict[str, list[str]] = {
    "AI & Machine Learning": ["ai", "artificial intelligence", "machine learning", "llm", "genai", "chatgpt", "gpt", "agents", "agentic", "prompt"],
    "Software & Engineering": ["software", "engineering", "developer", "coding", "devops", "cloud", "saas", "api", "python", "javascript"],
    "Data & Analytics": ["data", "analytics", "sql", "dashboard", "data science", "bi"],
    "Cybersecurity": ["cybersecurity", "security", "infosec", "breach", "ransomware", "zero trust"],
    "Marketing": ["marketing", "brand", "branding", "seo", "content", "campaign", "cmo", "growth", "demand gen", "advertising"],
    "Sales": ["sales", "selling", "prospecting", "pipeline", "quota", "b2b", "cold email", "closing", "revenue"],
    "Startups & VC": ["startup", "founder", "venture", "vc", "fundraising", "seed", "series a", "investor", "pitch"],
    "Leadership & Management": ["leadership", "leader", "manager", "management", "ceo", "executive", "team", "culture"],
    "Career & Job Search": ["career", "job search", "resume", "interview", "hiring", "job", "laid off", "layoff", "promotion"],
    "HR & Recruiting": ["hr", "recruiting", "recruiter", "talent", "people ops", "employee", "workplace", "benefits"],
    "Finance & Investing": ["finance", "investing", "stocks", "wealth", "money", "financial", "portfolio", "markets", "cfo", "accounting"],
    "Real Estate": ["real estate", "realtor", "mortgage", "housing", "property", "commercial real estate"],
    "Healthcare & Medicine": ["healthcare", "health", "medical", "doctor", "nurse", "patient", "clinical", "hospital", "pharma"],
    "Wellness & Mental Health": ["wellness", "mental health", "burnout", "mindfulness", "fitness", "wellbeing", "therapy"],
    "Entrepreneurship & Small Business": ["entrepreneur", "small business", "solopreneur", "business owner", "side hustle", "freelance"],
    "Personal Branding & Creator Economy": ["personal brand", "creator", "linkedin", "audience", "newsletter", "ghostwriting", "storytelling"],
    "Product & Design": ["product", "product manager", "ux", "design", "designer", "user research", "roadmap"],
    "E-commerce & Retail": ["ecommerce", "e-commerce", "shopify", "retail", "dtc", "amazon", "consumer"],
    "Education & Learning": ["education", "teacher", "students", "learning", "school", "university", "edtech"],
    "Legal": ["legal", "lawyer", "attorney", "law", "compliance", "contract"],
    "Sustainability & Climate": ["sustainability", "climate", "esg", "renewable", "carbon", "clean energy"],
    "Supply Chain & Operations": ["supply chain", "logistics", "operations", "manufacturing", "procurement"],
    "Productivity & Coaching": ["productivity", "coach", "coaching", "habits", "mindset", "motivation", "self improvement"],
    "DEI & Social Impact": ["diversity", "inclusion", "dei", "equity", "nonprofit", "social impact"],
    "Customer Success & Support": ["customer success", "customer experience", "cx", "support", "retention", "churn"],
}

_PATTERNS = {n: [re.compile(rf"(?<![\w]){re.escape(k)}(?![\w])", re.I) for k in kws] for n, kws in NICHES.items()}

OTHER = "General / Other"


def classify(text: str) -> str:
    if not text:
        return OTHER
    text = text.replace("#", " ")
    best, best_score = OTHER, 0
    for niche, pats in _PATTERNS.items():
        score = sum(len(p.findall(text)) for p in pats)
        if score > best_score:
            best, best_score = niche, score
    return best


# Query words used to seed searches across niches (one niche per query, rotated by run).
SEARCH_TERMS = [kws[0] for kws in NICHES.values()]
