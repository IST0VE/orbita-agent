# Orbita System Analysis CI

System Analysis CI is the deterministic layer on top of the existing package audit.
It runs before AI review: anything that can be proved by code must not depend on an LLM.

## MVP capabilities

- requirement traceability for REQ / FR / NFR / ФТ / НФТ identifiers;
- orphan requirement references and requirements without linked design;
- duplicate requirement declarations;
- REST route consistency across documents;
- invalid JSON examples and unresolved placeholders;
- evidence graph: document <-> requirement <-> endpoint <-> finding;
- deterministic impact traversal;
- accepted baseline for existing debt;
- non-zero CI exit code only for new findings at the selected severity.

Semantic contradictions remain in the AI review layer. A model inference is not silently promoted to a blocking CI fact.

## Local use

Check a package:

    python -m agent.system_ci_cli check input/partial-refund-package --fail-on major

Accept current deterministic debt:

    python -m agent.system_ci_cli baseline input/partial-refund-package --output input/partial-refund-package/.orbita-ci-baseline.json

After that, check only blocks on findings not present in the baseline.

Impact query:

    python -m agent.system_ci_cli impact REQ-12 docs/project --depth 2
    python -m agent.system_ci_cli impact "POST /orders" docs/project --depth 2

## Next product increment

The next layer is PR-aware execution: take changed engineering artifacts, build impact around them, and post the deterministic report to the pull request before optional LLM semantic review.
