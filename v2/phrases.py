"""Extra web-search phrases per niche, on top of creator_search.niches (v1's list stays untouched).

More phrases per niche means a lane still gets fresh starting posts when the search engine
refuses one query.
"""
from creator_search import niches

EXTRA: dict[str, list[str]] = {
    "HR & People Ops": ["HR director", "employee experience", "CHRO", "workplace culture"],
    "Recruiting & Talent Acquisition": ["technical recruiter", "hiring tips", "recruiting", "headhunter"],
    "Venture Capital & Investors": ["VC investor", "seed investor", "startup investing", "general partner"],
    "Founders & Startups": ["founder story", "bootstrapped founder", "SaaS founder", "startup lessons"],
    "Software Engineering": ["software engineering", "engineering manager", "backend engineer", "devops"],
    "AI & Machine Learning": ["generative AI", "AI agents", "LLM", "AI engineer"],
    "Data & Analytics": ["data scientist", "data engineering", "analytics", "SQL tips"],
    "Cybersecurity": ["CISO", "information security", "security engineer", "ransomware"],
    "Product Management": ["product leader", "product strategy", "PM interview", "product roadmap"],
    "Design & UX": ["UX design", "design leadership", "UI designer", "user research"],
    "Corporate Finance & Accounting": ["controller accounting", "finance leader", "CPA", "tax tips"],
    "Investing & Personal Finance": ["financial planner", "wealth management", "retirement planning", "money tips"],
    "Banking & Fintech": ["banking", "payments fintech", "lending", "wealth tech"],
    "Marketing": ["CMO", "content marketing", "brand marketing", "demand generation"],
    "Sales": ["sales leader", "account executive", "cold outreach", "sales tips"],
    "Customer Success & Experience": ["customer experience", "customer success manager", "CX leader"],
    "Leadership & Management": ["leadership lessons", "CEO", "managing teams", "executive leadership"],
    "Career & Job Search": ["career advice", "resume tips", "job seekers", "interview tips"],
    "Consulting & Strategy": ["strategy consultant", "consulting", "business advisor"],
    "Operations & Supply Chain": ["operations leader", "procurement", "manufacturing", "logistics leader"],
    "Project Management": ["PMP", "program manager", "scrum master", "project management"],
    "Healthcare & Medicine": ["doctor", "healthcare leader", "nursing", "medicine"],
    "Science & Research": ["researcher", "biotech", "scientist", "PhD research"],
    "Education & Training": ["educator", "edtech", "instructional design", "corporate training"],
    "Real Estate": ["real estate agent", "commercial real estate", "real estate investor", "mortgage"],
    "Legal": ["law firm", "general counsel", "legal tech", "employment lawyer"],
    "Retail & E-commerce": ["Shopify", "DTC brand", "Amazon seller", "retail leader"],
    "Hospitality, Travel & Events": ["hotel", "travel industry", "events", "restaurant owner"],
    "Sustainability & ESG": ["ESG", "clean energy", "climate tech", "sustainability leader"],
    "Writing & Content Creation": ["writing tips", "content creator", "storytelling", "author"],
    "Communication & Soft Skills": ["emotional intelligence", "negotiation", "presentation skills", "communication"],
    "Productivity & Future of Work": ["future of work", "time management", "hybrid work", "productivity tips"],
    "Wellness & Mental Health": ["burnout", "mindfulness", "therapist", "wellbeing"],
    "Personal Branding & LinkedIn Growth": ["personal brand", "LinkedIn growth", "thought leadership", "ghostwriter"],
    "DEI & Social Impact": ["inclusion", "social impact", "women in leadership", "belonging"],
}


def for_niche(niche: str) -> list[str]:
    base = niches.NICHES.get(niche, {}).get("search", [])
    return list(dict.fromkeys(base + EXTRA.get(niche, [])))
