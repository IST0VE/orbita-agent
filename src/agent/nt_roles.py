"""NT publishes one deterministic report through the existing Orbita pipeline."""

from agent.nt_prompts import prompt_for
from agent.pipeline import Pipeline, Role

UNDERSTAND = Role("understand_task", "01", "Контекст НТ", "Извлечение явных параметров")
INVESTIGATE = Role("investigate", "02", "Исследование", "Гипотезы по агрегированным фактам", reads_files=True)
REPORT = Role("report", "03", "NT Report", "SLA, аномалии и вероятные причины")


def brief(role, task, artifacts):
    return task


PIPELINE = Pipeline(key="nt", title="Нагрузочное тестирование",
    summary="Анализ завершённого НТ: контекст, baseline, SLA, аномалии и отчёт",
    byline="агентом анализа нагрузочного тестирования", roles=(REPORT,),
    prompt_for=prompt_for, brief=brief, one_page=True, rejection_fallback="file")
