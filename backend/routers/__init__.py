"""FastAPI routers package."""

from routers.assessment import router as assessment_router
from routers.agents import router as agents_router
from routers.assistant import router as assistant_router
from routers.dashboard import router as dashboard_router
from routers.growth import router as growth_router
from routers.logs import router as logs_router
from routers.onboarding import router as onboarding_router
from routers.personnel import router as personnel_router
from routers.practice import router as practice_router
from routers.performance import router as performance_router
from routers.query import router as query_router
from routers.qa import router as qa_router
from routers.knowledge import router as knowledge_router
from routers.theory_learning import router as theory_learning_router
from routers.task import router as task_router
from routers.training_cycle import router as training_cycle_router
from routers.audit import router as audit_router
from routers.system_settings import router as system_settings_router
from routers.knowledge_feedback import router as knowledge_feedback_router
from routers.tts import router as tts_router

BUSINESS_ROUTERS = [
    agents_router,
    assessment_router,
    growth_router,
    practice_router,
    assistant_router,
    dashboard_router,
    performance_router,
    query_router,
    qa_router,
    personnel_router,
    logs_router,
    knowledge_router,
    theory_learning_router,
    task_router,
    onboarding_router,
    training_cycle_router,
    audit_router,
    knowledge_feedback_router,
    system_settings_router,
    tts_router,
]
