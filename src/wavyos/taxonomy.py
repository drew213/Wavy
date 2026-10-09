"""The 23 operating areas and 10 knowledge components from the Lifecycle Client OS spec."""
from __future__ import annotations

AREAS: dict[str, tuple[str, str]] = {
    # key: (group, label)
    "onboarding": ("A", "Client onboarding"),
    "economics": ("A", "Business model and economics"),
    "product_customer": ("A", "Product and customer intelligence"),
    "data_infrastructure": ("A", "Account and data infrastructure"),
    "journey": ("B", "Lifecycle strategy and customer journey"),
    "segmentation": ("B", "Segmentation and audience architecture"),
    "deliverability": ("B", "Deliverability, consent and list health"),
    "list_growth": ("B", "List growth and capture"),
    "flow_audit": ("B", "Flow audit"),
    "flow_architecture": ("B", "Flow architecture and optimization"),
    "campaign_calendar": ("C", "Campaign strategy and calendar"),
    "offers": ("C", "Offers and merchandising"),
    "voice_copy": ("C", "Brand voice and copy"),
    "design_system": ("C", "Design and creative system"),
    "campaign_production": ("C", "Campaign production and deployment"),
    "sms": ("C", "SMS program"),
    "cro_onsite": ("C", "CRO and onsite alignment"),
    "reporting": ("D", "Reporting and diagnosis"),
    "experimentation": ("D", "Experimentation and learning"),
    "client_meetings": ("D", "Biweekly client meetings"),
    "account_management": ("D", "Client communication and account management"),
    "quarterly_planning": ("D", "Quarterly planning and growth roadmap"),
    "knowledge_maintenance": ("D", "Knowledge maintenance and offboarding"),
}

GROUPS = {
    "A": "Onboarding and business understanding",
    "B": "Lifecycle strategy and account health",
    "C": "Campaign planning and creative production",
    "D": "Measurement, client management and growth",
}

COMPONENTS: dict[str, str] = {
    "purpose": "What business outcome does this area support?",
    "facts_rules": "What is true for this client? What exceptions apply?",
    "inputs": "What data, documents and systems must the agent consult?",
    "sop": "What steps should it follow, and when?",
    "decision_rules": "How should it choose between actions?",
    "examples": "What does good work look like?",
    "quality_checklist": "What must be verified before work is complete?",
    "owner_approvals": "Who is accountable? What can the agent do independently?",
    "outputs": "What should it produce, and where should it go?",
    "maintenance": "Who last verified this information, and when should it be reviewed?",
}

# Audit OS check-library areas mapped to operating areas, so check results become insights in the right place.
CHECK_AREA_MAP = {
    "data": "data_infrastructure",
    "capture": "list_growth",
    "flows": "flow_audit",
    "campaigns": "campaign_calendar",
    "deliverability": "deliverability",
    "retention": "journey",
    "creative": "design_system",
    "sms": "sms",
}


def require_area(area: str) -> str:
    if area not in AREAS:
        raise ValueError(f"unknown area {area!r}; expected one of {', '.join(AREAS)}")
    return area


def require_component(component: str) -> str:
    if component not in COMPONENTS:
        raise ValueError(f"unknown component {component!r}; expected one of {', '.join(COMPONENTS)}")
    return component
