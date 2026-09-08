"""Built-in manifests versioned beside their Python graphs."""

from agent.ui_engine.graph_manifests.agent import MANIFEST as AGENT
from agent.ui_engine.graph_manifests.audit import MANIFEST as AUDIT
from agent.ui_engine.graph_manifests.drawio import MANIFEST as DRAWIO
from agent.ui_engine.graph_manifests.jira import MANIFEST as JIRA
from agent.ui_engine.graph_manifests.prep import MANIFEST as PREP
from agent.ui_engine.graph_manifests.update import MANIFEST as UPDATE

# Все встроенные манифесты одним списком. `graph_id` реестр берёт из самого
# манифеста, а тот — из ключа конвейера, поэтому новый конвейер добавляется
# одной строкой здесь. Раньше их было три: имя в этом файле, имя в `__all__`
# и вызов регистрации в `registry.py` — и все три повторяли ключ графа.
MANIFESTS = (AGENT, AUDIT, DRAWIO, JIRA, PREP, UPDATE)

__all__ = ["MANIFESTS"]
